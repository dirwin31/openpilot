import json
import os
from pathlib import Path

import pytest

from openpilot.starpilot.system.starpilot_auto import telemetry

SUMMARY = {"dongle_id": "b0c4a280b2f96b86", "branch": "AAComma", "commit": "5abc56a29deadbeef", "car_fingerprint": "HONDA_CIVIC_2022"}


def t(second):
  return f"2026-09-29T10:00:{second:02d}.000+00:00"


def write_log(directory: Path, number: int, events: list[tuple], age: float = 3600) -> Path:
  path = directory / f"session-{number:06d}-20260929-100000.jsonl"
  path.write_text("\n".join(json.dumps({"t": t(second), "event": name, **values}) for second, name, values in events) + "\n")
  stamp = os.path.getmtime(path) - age
  os.utime(path, (stamp, stamp))
  return path


STREAMING = [
  (0, "session_start", {"receiver": "Honda HFL", "trigger": "auto"}),
  (1, "stage", {"state": "rfcomm"}),
  (5, "bootstrap_version", {"major": 1, "minor": 7, "head_unit": {"head_unit_make": "Honda", "head_unit_model": "Display Audio",
                                                                   "car_make": "Honda"}}),
  (9, "stage", {"state": "streaming"}),
  (12, "video_acknowledged", {}),
  (42, "session_ended", {}),
]


def test_session_record_streamed(tmp_path):
  record = telemetry.session_record(write_log(tmp_path, 7, STREAMING))
  assert record["seq"] == 7 and record["outcome"] == "streamed" and record["transport"] == "wireless"
  assert record["trigger"] == "auto" and record["furthest_stage"] == "streaming"
  assert record["time_to_first_frame_s"] == 12.0 and record["streamed_s"] == 30.0 and record["drops"] == 0
  assert record["head_unit"] == {"head_unit_make": "Honda", "head_unit_model": "Display Audio", "car_make": "Honda"}


def test_session_record_dropped_and_failed(tmp_path):
  dropped = STREAMING[:-1] + [(30, "attempt_failed", {"stage": "streaming", "error": "link lost", "kind": "ConnectionResetError"})]
  record = telemetry.session_record(write_log(tmp_path, 1, dropped))
  assert record["outcome"] == "dropped" and record["drops"] == 1 and record["error_kind"] == "ConnectionResetError"

  failed = [(0, "session_start", {"receiver": "usb"}), (1, "stage", {"state": "usb_accessory"}),
            (3, "attempt_failed", {"stage": "usb_accessory", "error": "no handshake", "kind": "BootstrapTimeout"})]
  record = telemetry.session_record(write_log(tmp_path, 2, failed))
  assert record["outcome"] == "failed" and record["transport"] == "wired" and record["failed_stage"] == "usb_accessory"
  assert record["failures"] == 1 and record["time_to_first_frame_s"] is None and "error" not in record


def test_session_record_holds_no_free_text(tmp_path):
  failed = [(0, "session_start", {"receiver": "Danny's Civic"}),
            (3, "attempt_failed", {"stage": "rfcomm", "error": "AA:BB:CC:DD:EE:FF refused", "kind": "OSError"})]
  text = json.dumps(telemetry.session_record(write_log(tmp_path, 1, failed)))
  assert "AA:BB" not in text and "Danny" not in text


def report(tmp_path, enabled, state=None, now=None):
  return telemetry.build_report(enabled, state or {}, SUMMARY, "wireless", tmp_path, "C3X", now=now)


def test_never_enabled_sends_nothing(tmp_path):
  assert report(tmp_path, False) is None


def test_enabled_reports_sessions_once(tmp_path):
  write_log(tmp_path, 1, STREAMING)
  write_log(tmp_path, 2, STREAMING)
  first = report(tmp_path, True)
  assert first["event"] == "enabled" and [s["seq"] for s in first["sessions"]] == [1, 2]
  assert first["device"] == telemetry.device_hash("b0c4a280b2f96b86") and "b0c4a280" not in json.dumps(first)
  assert first["connection"] == "wireless" and first["device_generation"] == "C3X" and first["car_fingerprint"] == "HONDA_CIVIC_2022"
  assert first["head_unit"]["head_unit_model"] == "Display Audio"

  later = report(tmp_path, True, {"last_enabled": True, "last_seq": 1})
  assert later["event"] == "heartbeat" and [s["seq"] for s in later["sessions"]] == [2]


def test_live_log_is_held_back(tmp_path):
  write_log(tmp_path, 1, STREAMING)
  write_log(tmp_path, 2, STREAMING, age=5)
  assert [s["seq"] for s in report(tmp_path, True)["sessions"]] == [1]


def test_disable_is_reported_once(tmp_path):
  payload = report(tmp_path, False, {"last_enabled": True, "last_seq": 4})
  assert payload["event"] == "disabled" and payload["enabled"] is False
  assert report(tmp_path, False, {"last_enabled": False, "last_seq": 4}) is None
  assert report(tmp_path, True, {"last_enabled": False, "last_seq": 4})["event"] == "enabled"


class Response:
  def __init__(self, status_code):
    self.status_code = status_code


def test_state_advances_only_after_the_server_accepts(tmp_path):
  logs, state_path = tmp_path / "logs", tmp_path / "telemetry.json"
  logs.mkdir()
  write_log(logs, 3, STREAMING)
  sent = []

  def send(status):
    return telemetry.send_report(True, SUMMARY, "wireless", "C3X", url="https://example.invalid/t", state_path=state_path, log_dir=logs,
                                 post=lambda url, **kwargs: sent.append((url, kwargs)) or Response(status))

  with pytest.raises(RuntimeError):
    send(500)
  assert not state_path.exists()
  assert send(200) is True
  assert json.loads(state_path.read_text()) == {"last_enabled": True, "last_seq": 3}
  assert sent[0][1]["json"]["sessions"][0]["seq"] == 3 and sent[0][1]["json"]["report_id"] != sent[1][1]["json"]["report_id"]
  assert send(200) is True and sent[2][1]["json"]["sessions"] == []


def test_no_url_means_no_request(tmp_path):
  assert telemetry.send_report(True, SUMMARY, "wireless", "C3X", url="", state_path=tmp_path / "s.json", post=lambda *a, **k: 1 / 0) is False
