"""Android Auto drive, failure and pairing reports for Bugsink (bugsink.firestar.link).

android_autod sends them through the crash-reporting SDK it already runs with, only while parked:

- one drive report per drive: every session since the last report, how far each got and how long each
  connection stage took, the video mode the car chose and the frame rate actually delivered;
- one failure report per new kind of failure (outcome, stage, error class), at most once a day each,
  carrying the session's compatibility report so Bugsink groups them into issues;
- one pairing report per pairing attempt: each step, the time it took and how it ended.

Bluetooth addresses and IP addresses are scrubbed from error text; Wi-Fi credentials (never in the session logs) and the
car's Bluetooth and display names are left out.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from openpilot.starpilot.system.android_auto import identity as identity_store

STATE_PATH = identity_store.DATA_DIR / "bugsink_report.json"
PAIRING_PATH = identity_store.LOG_DIR / "pairing.jsonl"
PAIRING_MAX_BYTES = 64 << 10
INTERVAL_S = 60.0
LOG_SETTLE_S = 120         # a log written this recently may still be a live session, unless it already ended
MAX_SESSIONS = 20          # per drive report; the rest go in the next one
MAX_FAILURE_REPORTS = 5    # per day
FAILURE_WINDOW_S = 24 * 3600
FLUSH_TIMEOUT_S = 10.0
CAR_ENDED = "Head unit ended projection"  # the car closed the projection itself, as it does when it is switched off
FINISHED_EVENT = '"event": "session_stop"'
OUTCOME_RANK = ("streamed", "stopped", "dropped", "failed")

ADDRESS = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:_-]){5}[0-9A-Fa-f]{2}\b")
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def scrub(text: str, limit: int = 300) -> str:
  text = ADDRESS.sub("<address>", str(text))
  return IPV4.sub("<ip>", text)[:limit]


def _seconds(start: str, end: str) -> float | None:
  try:
    return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1)
  except (TypeError, ValueError):
    return None


def _number(value) -> float | None:
  return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _session_number(path: Path) -> int:
  match = re.fullmatch(r"session-(\d{6})-.*\.jsonl", path.name)
  return int(match[1]) if match else 0


def _percentile(values: list[float], fraction: float) -> float | None:
  if not values:
    return None
  ordered = sorted(values)
  return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


# ------------------------------------------------------------------ sessions

def stage_timings(events: list[dict], started: str) -> list[dict]:
  """Each connection stage in the order it was first entered, with seconds since the session started."""
  from openpilot.starpilot.system.android_auto.compat_report import STAGE_ORDER
  seen: dict[str, float | None] = {}
  for event in events:
    state = event.get("state")
    if event["event"] == "stage" and state in STAGE_ORDER and state not in seen:
      seen[state] = _seconds(started, event.get("t", ""))
  return [{"stage": stage, "at_s": at} for stage, at in seen.items()]


def video_summary(events: list[dict]) -> dict:
  """The video mode and the frame rate delivered while the car showed StarPilot, from the 30 s stats lines."""
  ready = next((event for event in reversed(events) if event["event"] == "projection_ready"), {})
  pipeline = next((event for event in reversed(events) if event["event"] == "frame_pipeline"), {})
  mode = ready.get("mode") if isinstance(ready.get("mode"), dict) else {}
  stats = [event for event in events if event["event"] == "stats" and event.get("focused") is not False]
  fps = [value for event in stats if (value := _number(event.get("fps"))) is not None]
  encode = [value for event in stats if (value := _number(event.get("encode_ms"))) is not None]
  peaks = [value for event in stats if (value := _number(event.get("encode_peak_ms"))) is not None]
  ages = [value for event in stats if (value := _number(event.get("frame_age_p95_ms"))) is not None]
  wifi = [value for event in stats if (value := _number(event.get("wifi_dbm"))) is not None]
  summary = {
    "resolution": f"{mode['width']}x{mode['height']}" if mode.get("width") and mode.get("height") else None,
    "car_fps": mode.get("fps"),
    "margins": f"{mode.get('margin_width')}x{mode.get('margin_height')}" if mode else None,
    "target_fps": pipeline.get("target_fps"),
    "encoder": pipeline.get("encoder"),
    "nv12": pipeline.get("nv12"),
    "samples": len(fps),
    "fps_avg": round(sum(fps) / len(fps), 1) if fps else None,
    "fps_min": min(fps) if fps else None,
    "fps_p10": _percentile(fps, 0.1),
    "encode_ms_avg": round(sum(encode) / len(encode), 1) if encode else None,
    "encode_peak_ms": max(peaks) if peaks else None,
    "frame_age_p95_ms_max": max(ages) if ages else None,
    "wifi_dbm_min": min(wifi) if wifi else None,
    "encoder_recoveries": max((int(event.get("encoder_recoveries") or 0) for event in stats), default=0),
    "frames_sent": max((int(event.get("frames_sent") or 0) for event in stats), default=0),
  }
  return {key: value for key, value in summary.items() if value is not None}


def session_record(path: Path) -> dict:
  """One finished session log reduced to its outcome, stage timings, video and the car it talked to."""
  from openpilot.starpilot.system.android_auto import compat_report
  events = compat_report.load_events(path)
  report = compat_report.summarize(events)
  started = events[0].get("t", "") if events else ""
  first_frame = next((event["t"] for event in events if event["event"] == "video_acknowledged"), "")
  # Once the car ends the projection (it was switched off), the reconnect attempts that follow are not drops.
  car_ended = next((index for index, event in enumerate(events) if first_frame and event["event"] == "session_ended"
                    and str(event.get("reason", "")).startswith(CAR_ENDED) and event.get("t", "") >= first_frame), None)
  failures = [event for index, event in enumerate(events) if event["event"] == "attempt_failed" and (car_ended is None or index < car_ended)]
  drops = [event for event in failures if first_frame and event.get("t", "") >= first_frame]
  if first_frame:
    outcome = "dropped" if drops else "streamed"
  elif failures:
    outcome = "failed"
  else:
    outcome = "stopped"

  last_failure = failures[-1] if failures else {}
  car = report["car"]
  return {
    "seq": _session_number(path),
    "name": path.name,
    "started": started,
    "transport": report["transport"],
    "trigger": report["trigger"],
    "outcome": outcome,
    "furthest_stage": report["furthest_stage"],
    "failed_stage": str(last_failure.get("stage", "")),
    "error_kind": str(last_failure.get("kind", "")),
    "error": scrub(last_failure.get("error", "")),
    "failures": len(failures),
    "drops": len(drops),
    "ended_by_car": car_ended is not None,
    "time_to_first_frame_s": _seconds(started, first_frame) if first_frame else None,
    "streamed_s": _seconds(first_frame, events[-1].get("t", "")) if first_frame else None,
    "stages": stage_timings(events, started),
    "video": video_summary(events),
    "focus_lost": report["focus"]["lost"],
    "stalls": report["health"],
    "wifi_security": report["wifi"].get("security") or None,
    "tls": report["tls"].get("version") or None,
    "head_unit": {key: car[key] for key in ("head_unit_make", "head_unit_model", "head_unit_software_version",
                                            "car_make", "car_model", "car_year") if car.get(key)},
    "_report": report,
  }


def _finished(path: Path) -> bool:
  with path.open("rb") as log:
    log.seek(0, os.SEEK_END)
    log.seek(max(0, log.tell() - 512))
    lines = log.read().decode(errors="replace").strip().splitlines()
  return bool(lines) and FINISHED_EVENT in lines[-1]


def new_sessions(log_dir: Path, last_seq: int, now: float) -> list[dict]:
  """Finished sessions newer than ``last_seq``, oldest first; a log still being written stops the scan."""
  sessions = []
  for path in sorted(log_dir.glob("session-*.jsonl"), key=identity_store.session_log_order):
    if _session_number(path) <= last_seq:
      continue
    if len(sessions) >= MAX_SESSIONS:
      break
    try:
      if now - path.stat().st_mtime < LOG_SETTLE_S and not _finished(path):
        break
      sessions.append(session_record(path))
    except OSError:
      continue
  return sessions


def drive_summary(sessions: list[dict]) -> dict:
  """Totals across a drive's sessions; the frame rate is weighted by how many 30 s samples each session had."""
  weighted = [(s["video"]["fps_avg"], s["video"]["samples"]) for s in sessions if s["video"].get("fps_avg") is not None]
  samples = sum(count for _, count in weighted)
  outcome = max((s["outcome"] for s in sessions), key=OUTCOME_RANK.index)
  return {
    "sessions": len(sessions),
    "outcome": outcome,
    "streamed_sessions": sum(1 for s in sessions if s["time_to_first_frame_s"] is not None),
    "streamed_s": round(sum(s["streamed_s"] or 0 for s in sessions), 1),
    "failures": sum(s["failures"] for s in sessions),
    "drops": sum(s["drops"] for s in sessions),
    "fps_avg": round(sum(fps * count for fps, count in weighted) / samples, 1) if samples else None,
    "fps_min": min((s["video"]["fps_min"] for s in sessions if "fps_min" in s["video"]), default=None),
    "resolutions": sorted({s["video"]["resolution"] for s in sessions if s["video"].get("resolution")}),
    "time_to_first_frame_s": next((s["time_to_first_frame_s"] for s in sessions if s["time_to_first_frame_s"] is not None), None),
    "transport": sessions[-1]["transport"],
  }


def _head_unit_label(head_unit: dict) -> str:
  return " ".join(head_unit.get(key, "") for key in ("head_unit_make", "head_unit_model")).strip() or "unknown head unit"


def _car_label(head_unit: dict) -> str:
  return " ".join(head_unit.get(key, "") for key in ("car_year", "car_make", "car_model")).strip() or "unknown car"


def _public(session: dict) -> dict:
  return {key: value for key, value in session.items() if not key.startswith("_")}


def drive_event(sessions: list[dict]) -> dict:
  summary = drive_summary(sessions)
  head_unit = next((s["head_unit"] for s in reversed(sessions) if s["head_unit"]), {})
  video = next((s["video"] for s in reversed(sessions) if s["video"].get("resolution")), {})
  label = _head_unit_label(head_unit)
  fps = f"{summary['fps_avg']} fps" if summary["fps_avg"] is not None else "no video"
  return {
    "message": f"Android Auto drive: {summary['outcome']}, {fps} ({label})",
    "level": "info" if summary["outcome"] == "streamed" else "warning",
    "fingerprint": ["android-auto-drive", summary["transport"] or "unknown", label],
    "tags": {"aa.report": "drive", "aa.outcome": summary["outcome"], "aa.transport": summary["transport"] or "unknown",
             "aa.head_unit": label, "aa.car": _car_label(head_unit), "aa.resolution": video.get("resolution", "none"),
             "aa.encoder": video.get("encoder", "unknown"),
             "aa.fps": str(summary["fps_avg"]) if summary["fps_avg"] is not None else "none"},
    "contexts": {"android_auto_drive": summary, "android_auto_head_unit": head_unit,
                 **{f"android_auto_session_{index + 1}": _public(session) for index, session in enumerate(sessions)}},
  }


def failure_signature(session: dict) -> str:
  return f"{session['outcome']}:{session['failed_stage'] or session['furthest_stage'] or 'start'}:{session['error_kind'] or 'none'}"


def failure_event(session: dict) -> dict:
  from openpilot.starpilot.system.android_auto import compat_report
  stage = session["failed_stage"] or session["furthest_stage"] or "start"
  kind = session["error_kind"] or "no error"
  report = dict(session["_report"])
  report["car"] = {key: value for key, value in report["car"].items() if key not in ("bluetooth_name", "display_name")}
  report["errors"] = [{**error, "error": scrub(error.get("error", ""))} for error in report["errors"]]
  report["ended"] = [scrub(reason) for reason in report["ended"]]
  report["tls"] = {key: value for key, value in report["tls"].items() if key != "head_unit"}
  return {
    "message": f"Android Auto {session['outcome']} at {stage}: {kind}",
    "level": "error" if session["outcome"] == "failed" else "warning",
    "fingerprint": ["android-auto-failure", session["outcome"], stage, kind],
    "tags": {"aa.report": "failure", "aa.outcome": session["outcome"], "aa.stage": stage, "aa.error_kind": kind,
             "aa.transport": session["transport"] or "unknown", "aa.head_unit": _head_unit_label(session["head_unit"]),
             "aa.car": _car_label(session["head_unit"])},
    "contexts": {"android_auto_session": _public(session),
                 "android_auto_compat_report": {"text": scrub(compat_report.render_text(report), 8000)}},
  }


# ------------------------------------------------------------------ pairing

def record_pairing(record: dict, path: Path = PAIRING_PATH) -> None:
  """Append one finished pairing attempt; the journal keeps only its newest lines."""
  try:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    seq = 1 + max((json.loads(line).get("seq", 0) for line in lines if line.strip()), default=0)
    lines.append(json.dumps({"seq": seq, **record}, default=str))
    while len(lines) > 1 and sum(len(line) + 1 for line in lines) > PAIRING_MAX_BYTES:
      lines.pop(0)
    _write_atomic(path, "\n".join(lines) + "\n")
  except (OSError, ValueError):
    pass


def new_pairings(last_seq: int, path: Path = PAIRING_PATH) -> list[dict]:
  try:
    lines = path.read_text().splitlines()
  except OSError:
    return []
  records = []
  for line in lines:
    try:
      record = json.loads(line)
    except ValueError:
      continue
    if isinstance(record, dict) and int(record.get("seq", 0)) > last_seq:
      records.append(record)
  return records


def pairing_event(record: dict) -> dict:
  result = str(record.get("result", "unknown"))
  return {
    "message": f"Android Auto pairing {result}",
    "level": "info" if result == "paired" else "warning",
    "fingerprint": ["android-auto-pairing", result],
    "tags": {"aa.report": "pairing", "aa.pairing": result},
    "contexts": {"android_auto_pairing": {key: (scrub(value) if isinstance(value, str) else value)
                                          for key, value in record.items()}},
  }


class PairingAttempt:
  """The steps of one pairing window, with seconds since it opened; ``finish`` writes it to the journal."""

  def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic, sink: Callable[[dict], None] = record_pairing):
    self._clock, self._sink = clock, sink
    self._opened = clock()
    self.record: dict = {"started": datetime.now().astimezone().isoformat(timespec="seconds"), "window_s": seconds, "steps": []}
    self.step("opened")

  def step(self, name: str, **values) -> None:
    self.record["steps"].append({"step": name, "at_s": round(self._clock() - self._opened, 1), **values})

  def finish(self, result: str, **values) -> None:
    self.step(result, **values)
    self._sink({**self.record, "result": result, "duration_s": round(self._clock() - self._opened, 1)})


# ------------------------------------------------------------------ sending

def _load_state(path: Path) -> dict:
  try:
    state = json.loads(path.read_text())
    return state if isinstance(state, dict) else {}
  except (OSError, ValueError):
    return {}


def _write_atomic(path: Path, text: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
    temporary_path = Path(temporary.name)
    try:
      temporary.write(text)
      temporary.flush()
      os.fsync(temporary.fileno())
      os.replace(temporary_path, path)
    finally:
      temporary_path.unlink(missing_ok=True)


def send_reports(capture: Callable[..., bool], flush: Callable[[], None] = lambda: None, *, state_path: Path = STATE_PATH,
                 log_dir: Path | None = None, pairing_path: Path = PAIRING_PATH, now: float | None = None) -> int:
  """Send what is new since the last call; returns how many reports were queued.

  State is saved only for reports the SDK accepted, so a report that could not be queued is tried again next time."""
  now = datetime.now(UTC).timestamp() if now is None else now
  state = _load_state(state_path)
  sent_failures = {key: float(at) for key, at in (state.get("failures") or {}).items()
                   if isinstance(at, (int, float)) and 0 <= now - at < FAILURE_WINDOW_S}
  uploads = [float(at) for at in state.get("failure_uploads") or [] if isinstance(at, (int, float)) and 0 <= now - at < FAILURE_WINDOW_S]
  last_seq, pairing_seq = int(state.get("last_seq", 0)), int(state.get("pairing_seq", 0))
  queued = 0

  for record in new_pairings(pairing_seq, pairing_path):
    if not capture(**pairing_event(record)):
      break
    pairing_seq, queued = int(record["seq"]), queued + 1

  sessions = new_sessions(log_dir or identity_store.LOG_DIR, last_seq, now)
  if sessions and capture(**drive_event(sessions)):
    queued += 1
    last_seq = sessions[-1]["seq"]
    for session in reversed(sessions):
      signature = failure_signature(session)
      if session["outcome"] not in ("failed", "dropped") or signature in sent_failures or len(uploads) >= MAX_FAILURE_REPORTS:
        continue
      if capture(**failure_event(session)):
        sent_failures[signature] = now
        uploads.append(now)
        queued += 1

  if queued:
    flush()
    _write_atomic(state_path, json.dumps({"last_seq": last_seq, "pairing_seq": pairing_seq,
                                          "failures": sent_failures, "failure_uploads": uploads}))
  return queued


class Reporter:
  """Background thread in android_autod: every minute, while parked, sends what is new."""

  def __init__(self, parked: Callable[[], bool], interval: float = INTERVAL_S):
    self._parked, self._interval = parked, interval
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None

  def start(self) -> Reporter:
    self._thread = threading.Thread(target=self._run, name="aa_bugsink_report", daemon=True)
    self._thread.start()
    return self

  def close(self) -> None:
    self._stop.set()

  def _run(self) -> None:
    from openpilot.system import sentry
    while not self._stop.wait(self._interval):
      try:
        if self._parked():
          send_reports(sentry.capture_report, lambda: sentry.flush(FLUSH_TIMEOUT_S))
      except Exception as error:
        print(json.dumps({"event": "bugsink_report_failed", "error": str(error)}), flush=True)
