"""Anonymous Starpilot Auto usage report: is it on, wired or wireless, and how its sessions ended.

Sent once per drive end and once per boot, alongside the existing StarPilot stats. It carries
only enums, counters and the car/head-unit identity strings; never logs, Bluetooth addresses,
Wi-Fi credentials or the identity. The device is identified by a hash of its dongle ID."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

from openpilot.starpilot.system.starpilot_auto import identity as identity_store

SCHEMA = 1
TELEMETRY_URL = "https://telemetry.didesigns.fyi/v1/starpilot-auto/report"  # https endpoint that receives the report; "" = not sent
URL_OVERRIDE_PATH = identity_store.DATA_DIR / "telemetry_url"
STATE_PATH = identity_store.DATA_DIR / "telemetry.json"
MAX_SESSIONS = 20
LOG_SETTLE_S = 120  # a log written this recently may still be a live session
TIMEOUT_S = 10


def telemetry_url(override_path: Path = URL_OVERRIDE_PATH) -> str:
  try:
    override = override_path.read_text().strip()
  except OSError:
    override = ""
  return override or TELEMETRY_URL


def device_hash(dongle_id: str) -> str:
  return hashlib.sha256(f"starpilot-auto:{dongle_id}".encode()).hexdigest()[:32]


def _seconds(start: str, end: str) -> float | None:
  try:
    return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1)
  except (TypeError, ValueError):
    return None


def _session_number(path: Path) -> int:
  match = re.fullmatch(r"session-(\d{6})-.*\.jsonl", path.name)
  return int(match[1]) if match else 0


def session_record(path: Path) -> dict:
  """One session log reduced to enums and counters."""
  from openpilot.starpilot.system.starpilot_auto import compat_report
  events = compat_report.load_events(path)
  report = compat_report.summarize(events)
  started = events[0].get("t", "") if events else ""
  first_frame = next((event["t"] for event in events if event["event"] == "video_acknowledged"), "")
  failures = [event for event in events if event["event"] == "attempt_failed"]
  drops = [event for event in failures if first_frame and event.get("t", "") >= first_frame]

  if first_frame:
    outcome = "dropped" if drops else "streamed"
  elif failures:
    outcome = "failed"
  else:
    outcome = "stopped"

  car = report["car"]
  last_failure = failures[-1] if failures else {}
  record = {
    "seq": _session_number(path),
    "started": started,
    "transport": report["transport"],
    "trigger": report["trigger"],
    "outcome": outcome,
    "furthest_stage": report["furthest_stage"],
    "failed_stage": str(last_failure.get("stage", "")),
    "error_kind": str(last_failure.get("kind", "")),
    "failures": len(failures),
    "drops": len(drops),
    "time_to_first_frame_s": _seconds(started, first_frame) if first_frame else None,
    "streamed_s": _seconds(first_frame, events[-1].get("t", "")) if first_frame else None,
    "head_unit": {key: car[key] for key in ("head_unit_make", "head_unit_model", "head_unit_software_version",
                                            "car_make", "car_model", "car_year") if car.get(key)},
  }
  return record


def _load_state(path: Path) -> dict:
  try:
    state = json.loads(path.read_text())
    return state if isinstance(state, dict) else {}
  except (OSError, ValueError):
    return {}


def _save_state(path: Path, state: dict) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
    temporary_path = Path(temporary.name)
    try:
      json.dump(state, temporary)
      temporary.flush()
      os.fsync(temporary.fileno())
      os.replace(temporary_path, path)
    finally:
      temporary_path.unlink(missing_ok=True)


def build_report(enabled: bool, state: dict, summary: dict, connection: str, log_dir: Path,
                 device_generation: str, now: float | None = None) -> dict | None:
  """The payload to send, or None when there is nothing to say (never used, or the disable was already reported)."""
  last_enabled = state.get("last_enabled")
  if not enabled and last_enabled is not True:
    return None  # never turned on here, or the turn-off was already sent

  now = time.time() if now is None else now
  last_seq = int(state.get("last_seq", 0))
  sessions = []
  logs = sorted(log_dir.glob("session-*.jsonl"), key=identity_store.session_log_order)
  for path in logs:
    number = _session_number(path)
    if number <= last_seq:
      continue
    try:
      if now - path.stat().st_mtime < LOG_SETTLE_S:
        break  # newer logs are newer still; report this one next time
      sessions.append(session_record(path))
    except OSError:
      continue
  sessions = sessions[-MAX_SESSIONS:]

  event = "heartbeat" if last_enabled is enabled else "enabled" if enabled else "disabled"
  head_unit = next((s["head_unit"] for s in reversed(sessions) if s["head_unit"]), {})
  return {
    "schema": SCHEMA,
    "report_id": uuid.uuid4().hex,
    "device": device_hash(summary.get("dongle_id") or ""),
    "event": event,
    "enabled": enabled,
    "connection": connection,
    "device_generation": device_generation,
    "branch": summary.get("branch", ""),
    "commit": str(summary.get("commit", ""))[:10],
    "car_fingerprint": summary.get("car_fingerprint", ""),
    "head_unit": head_unit,
    "sessions": sessions,
  }


def send_report(enabled: bool, summary: dict, connection: str, device_generation: str, url: str | None = None, post=None,
                state_path: Path = STATE_PATH, log_dir: Path | None = None) -> bool:
  """Post the report if there is one; remember what was sent only after the server accepts it."""
  url = telemetry_url() if url is None else url
  if not url:
    return False
  state = _load_state(state_path)
  payload = build_report(enabled, state, summary, connection, log_dir or identity_store.LOG_DIR, device_generation)
  if payload is None:
    return False
  if post is None:
    import requests
    post = requests.post
  response = post(url, json=payload, timeout=TIMEOUT_S, headers={"User-Agent": "starpilot-auto-telemetry/1"})
  if not 200 <= response.status_code < 300:
    raise RuntimeError(f"telemetry endpoint answered HTTP {response.status_code}")
  last_seq = max([int(state.get("last_seq", 0)), *(session["seq"] for session in payload["sessions"])])
  _save_state(state_path, {"last_enabled": enabled, "last_seq": last_seq})
  return True


def send_auto_telemetry() -> None:
  try:
    if not telemetry_url():
      return
    from openpilot.common.params import Params
    from openpilot.system.hardware import HARDWARE
    from openpilot.starpilot.system.diagnostics.bundle import device_summary
    from openpilot.starpilot.system.starpilot_stats import get_device_generation

    send_report(Params().get_bool(identity_store.ENABLED_KEY), device_summary(), identity_store.load_config()["connection"],
                get_device_generation(HARDWARE.get_device_type()))
  except Exception as error:
    print(f"Failed to send Starpilot Auto telemetry: {error}")
