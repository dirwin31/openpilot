"""Android Auto drive, failure and pairing reports for Bugsink, built from session logs without a network."""

import json
import os
from datetime import UTC, datetime

from openpilot.starpilot.system.android_auto import bugsink_report as br


def write_session(log_dir, number, events, finished=True):
  log_dir.mkdir(parents=True, exist_ok=True)
  path = log_dir / f"session-{number:06d}-20261016-120000.jsonl"
  lines = [{"t": f"2026-10-16T12:{minute:02d}:{second:02d}.000+00:00", "event": name, **values}
           for minute, second, name, values in events]
  if finished:
    lines.append({"t": "2026-10-16T12:59:00.000+00:00", "event": "session_stop"})
  path.write_text("".join(json.dumps(line) + "\n" for line in lines))
  return path


STREAMED = [
  (0, 0, "session_start", {"receiver": "Honda HFL", "trigger": "auto"}),
  (0, 1, "stage", {"state": "connecting_bluetooth"}),
  (0, 3, "stage", {"state": "rfcomm"}),
  (0, 6, "stage", {"state": "joining_wifi"}),
  (0, 9, "stage", {"state": "authenticating"}),
  (0, 10, "discovered", {"head_unit": {"2": ["Honda"], "3": ["Civic"], "4": ["2022"], "7": ["Alpine"], "8": ["HU-1"],
                                       "14": ["Danny's Civic"]}, "channels": []}),
  (0, 11, "projection_ready", {"mode": {"width": 1280, "height": 720, "fps": 30, "margin_width": 0, "margin_height": 0}}),
  (0, 11, "frame_pipeline", {"nv12": True, "async_readback": True, "target_fps": 30, "encoder": "v4l2"}),
  (0, 12, "stage", {"state": "streaming"}),
  (0, 12, "video_acknowledged", {}),
  (0, 42, "stats", {"fps": 30.0, "encode_ms": 6.0, "encode_peak_ms": 9.0, "frame_age_p95_ms": 40, "wifi_dbm": -50,
                    "focused": True, "frames_sent": 900, "encoder_recoveries": 0}),
  (1, 12, "stats", {"fps": 20.0, "encode_ms": 8.0, "encode_peak_ms": 15.0, "frame_age_p95_ms": 90, "wifi_dbm": -70,
                    "focused": True, "frames_sent": 1500, "encoder_recoveries": 1}),
  (1, 42, "stats", {"fps": 0.0, "focused": False}),
]

FAILED = [
  (0, 0, "session_start", {"receiver": "Honda HFL", "trigger": "manual"}),
  (0, 1, "stage", {"state": "connecting_bluetooth"}),
  (0, 4, "stage", {"state": "joining_wifi"}),
  (0, 30, "attempt_failed", {"stage": "joining_wifi", "error": "Joining Wi-Fi: no route to 192.168.1.4 from AA:BB:CC:DD:EE:FF",
                             "kind": "TimeoutError"}),
]


def test_session_record_reports_stages_resolution_and_fps(tmp_path):
  record = br.session_record(write_session(tmp_path, 1, STREAMED))
  assert record["outcome"] == "streamed" and record["transport"] == "wireless"
  assert record["time_to_first_frame_s"] == 12.0
  assert [stage["stage"] for stage in record["stages"]] == ["connecting_bluetooth", "rfcomm", "joining_wifi", "authenticating", "streaming"]
  assert record["stages"][2]["at_s"] == 6.0
  video = record["video"]
  assert video["resolution"] == "1280x720" and video["car_fps"] == 30 and video["target_fps"] == 30 and video["encoder"] == "v4l2"
  assert video["samples"] == 2 and video["fps_avg"] == 25.0 and video["fps_min"] == 20.0  # the unfocused sample is left out
  assert video["encode_peak_ms"] == 15.0 and video["wifi_dbm_min"] == -70 and video["encoder_recoveries"] == 1
  assert record["head_unit"] == {"head_unit_make": "Alpine", "head_unit_model": "HU-1", "car_make": "Honda",
                                 "car_model": "Civic", "car_year": "2022"}


def test_drive_event_groups_sessions_by_head_unit_and_weights_fps(tmp_path):
  sessions = [br.session_record(write_session(tmp_path, 1, STREAMED)), br.session_record(write_session(tmp_path, 2, FAILED))]
  event = br.drive_event(sessions)
  assert event["fingerprint"] == ["android-auto-drive", "wireless", "Alpine HU-1"]
  assert event["level"] == "warning" and event["tags"]["aa.outcome"] == "failed"
  assert event["tags"]["aa.resolution"] == "1280x720" and event["tags"]["aa.fps"] == "25.0"
  drive = event["contexts"]["android_auto_drive"]
  assert drive["sessions"] == 2 and drive["failures"] == 1 and drive["fps_avg"] == 25.0 and drive["resolutions"] == ["1280x720"]
  assert "_report" not in event["contexts"]["android_auto_session_1"]
  assert "Danny's Civic" not in json.dumps(event)


def test_failure_event_hides_names_and_addresses(tmp_path):
  session = br.session_record(write_session(tmp_path, 1, FAILED))
  event = br.failure_event(session)
  assert event["message"] == "Android Auto failed at joining_wifi: TimeoutError"
  assert event["fingerprint"] == ["android-auto-failure", "failed", "joining_wifi", "TimeoutError"]
  text = json.dumps(event)
  for secret in ("AA:BB:CC:DD:EE:FF", "192.168.1.4", "Honda HFL"):
    assert secret not in text
  assert "Stages: connecting_bluetooth > joining_wifi" in event["contexts"]["android_auto_compat_report"]["text"]


def test_send_reports_once_and_rate_limits_failures(tmp_path):
  logs, state = tmp_path / "logs", tmp_path / "state.json"
  write_session(logs, 1, STREAMED)
  for number in range(2, 4):
    write_session(logs, number, FAILED)
  captured, flushes = [], []

  def capture(**event):
    captured.append(event)
    return True

  queued = br.send_reports(capture, lambda: flushes.append(1), state_path=state, log_dir=logs, pairing_path=tmp_path / "p.jsonl")
  assert queued == 2 and len(flushes) == 1  # one drive report, and one failure report for two failures of the same kind
  assert [event["tags"]["aa.report"] for event in captured] == ["drive", "failure"]
  assert json.loads(state.read_text())["last_seq"] == 3

  assert br.send_reports(capture, state_path=state, log_dir=logs, pairing_path=tmp_path / "p.jsonl") == 0
  write_session(logs, 4, FAILED)
  captured.clear()
  br.send_reports(capture, state_path=state, log_dir=logs, pairing_path=tmp_path / "p.jsonl")
  assert [event["tags"]["aa.report"] for event in captured] == ["drive"]  # the same failure was reported today already


def test_unaccepted_reports_are_retried(tmp_path):
  logs, state = tmp_path / "logs", tmp_path / "state.json"
  write_session(logs, 1, STREAMED)
  assert br.send_reports(lambda **_: False, state_path=state, log_dir=logs, pairing_path=tmp_path / "p.jsonl") == 0
  assert not state.exists()
  assert br.send_reports(lambda **_: True, state_path=state, log_dir=logs, pairing_path=tmp_path / "p.jsonl") == 1


def test_live_session_waits_until_finished(tmp_path):
  logs = tmp_path / "logs"
  path = write_session(logs, 1, STREAMED, finished=False)
  assert br.new_sessions(logs, 0, datetime.now(UTC).timestamp()) == []
  old = datetime.now(UTC).timestamp() - br.LOG_SETTLE_S - 1
  os.utime(path, (old, old))
  assert len(br.new_sessions(logs, 0, datetime.now(UTC).timestamp())) == 1


def test_pairing_attempts_are_journaled_and_reported(tmp_path):
  journal = tmp_path / "pairing.jsonl"
  clock = iter([0.0, 0.0, 4.0, 9.5, 12.0, 12.0])
  attempt = br.PairingAttempt(180.0, clock=lambda: next(clock), sink=lambda record: br.record_pairing(record, journal))
  attempt.step("pair_requested")
  attempt.step("status", state="paired", prompt=False, approved=True)
  attempt.finish("paired")
  records = br.new_pairings(0, journal)
  assert len(records) == 1 and records[0]["result"] == "paired" and records[0]["duration_s"] == 12.0
  assert [step["step"] for step in records[0]["steps"]] == ["opened", "pair_requested", "status", "paired"]

  captured = []
  assert br.send_reports(lambda **event: captured.append(event) or True, state_path=tmp_path / "s.json",
                         log_dir=tmp_path / "logs", pairing_path=journal) == 1
  assert captured[0]["fingerprint"] == ["android-auto-pairing", "paired"] and captured[0]["level"] == "info"
  assert br.new_pairings(json.loads((tmp_path / "s.json").read_text())["pairing_seq"], journal) == []


def test_pairing_journal_stays_bounded(tmp_path):
  journal = tmp_path / "pairing.jsonl"
  for index in range(400):
    br.record_pairing({"result": "expired", "steps": [{"step": "opened", "at_s": 0}] * 4, "index": index}, journal)
  assert journal.stat().st_size <= br.PAIRING_MAX_BYTES
  assert br.new_pairings(0, journal)[-1]["seq"] == 400
