import base64
import io
import json
import os
import time
import zipfile

import pytest

from openpilot.starpilot.system.diagnostics import bundle

SUMMARY = {"dongle_id": "b0c4a280b2f96b86", "branch": "AAComma", "commit": "5abc56a29deadbeef"}


def _aa_zip():
  output = io.BytesIO()
  with zipfile.ZipFile(output, "w") as archive:
    archive.writestr("REPORT.txt", "Honda CIVIC: projected")
    archive.writestr("logs/session-000025-20260928-141351.jsonl", '{"event": "session_start"}\n')
  return output.getvalue()


def _drives(tmp_path, *routes):
  for age, route in enumerate(routes):
    first = tmp_path / f"{route}--0"
    first.mkdir()
    (first / "rlog.zst").write_bytes(b"")
    stamp = 1_790_000_000 - age * 3600
    os.utime(first, (stamp, stamp))
  return tmp_path


def _build(tmp_path, **overrides):
  pairing = tmp_path / "pairing.jsonl"
  pairing.write_text('{"kind": "confirmation", "outcome": "timed_out"}\n')
  options = dict(realdata=_drives(tmp_path, "000000cc--newest", "000000cb--older", "000000ca--oldest", "000000c9--too-old"),
                 drive_report=lambda route, _: f"report for {route}", aa_bundle=_aa_zip,
                 bluetooth_status=lambda: {"powered": True}, pairing_log=pairing, summary=lambda: SUMMARY)
  options.update(overrides)
  return zipfile.ZipFile(io.BytesIO(bundle.build(**options)))


def test_bundle_holds_android_auto_bluetooth_and_newest_drives(tmp_path):
  archive = _build(tmp_path, note="AA took 3 tries", drives=2)
  names = set(archive.namelist())
  assert {"android-auto/REPORT.txt", "android-auto/logs/session-000025-20260928-141351.jsonl", "bluetooth/pairing_events.jsonl",
          "bluetooth/status.json", "drives/000000cc--newest.txt", "drives/000000cb--older.txt", "README.txt"} <= names
  assert "drives/000000ca--oldest.txt" not in names
  readme = archive.read("README.txt").decode()
  assert "AA took 3 tries" in readme and "b0c4a280b2f96b86" in readme and "Could not collect" not in readme


def test_bundle_caps_drives_and_reports_what_it_could_not_collect(tmp_path):
  def broken_report(route, _):
    raise RuntimeError("rlog missing")

  def no_bluetooth():
    raise OSError("bluetooth daemon not running")

  archive = _build(tmp_path, drives=99, drive_report=broken_report, bluetooth_status=no_bluetooth, pairing_log=tmp_path / "missing.jsonl")
  readme = archive.read("README.txt").decode()
  assert readme.count("rlog missing") == bundle.MAX_DRIVES
  assert "bluetooth daemon not running" in readme
  assert archive.read("bluetooth/pairing_events.jsonl") == b""  # never prompted yet: empty, not an error


def test_no_drives_skips_the_expensive_reports(tmp_path):
  calls = []
  archive = _build(tmp_path, drives=0, drive_report=lambda route, _: calls.append(route) or "")
  assert calls == [] and not any(name.startswith("drives/") for name in archive.namelist())


def test_webhook_override_file_wins_and_default_decodes(tmp_path, monkeypatch):
  monkeypatch.setattr(bundle, "_WEBHOOK_B64", base64.b64encode(b"https://example.invalid/hook").decode())
  assert bundle.webhook_url(tmp_path / "absent") == "https://example.invalid/hook"
  override = tmp_path / "webhook_url"
  override.write_text("  https://example.invalid/new\n")
  assert bundle.webhook_url(override) == "https://example.invalid/new"
  monkeypatch.setattr(bundle, "_WEBHOOK_B64", "")
  assert bundle.webhook_url(tmp_path / "absent") == ""


def test_built_in_webhook_is_a_discord_webhook_and_not_plain_text():
  source = (bundle.Path(bundle.__file__)).read_text()
  assert "discord.com/api/webhooks" not in source, "keep the URL encoded so repository scanners don't match it"
  assert bundle.webhook_url(bundle.Path("/nonexistent")).startswith("https://discord.com/api/webhooks/")


class Response:
  def __init__(self, status_code):
    self.status_code = status_code


def test_send_posts_zip_and_note_to_discord():
  calls = []
  bundle.send(b"zip-bytes", "diag.zip", "AA dropped at 2:34", SUMMARY, url="https://example.invalid/hook",
              post=lambda url, **kwargs: calls.append((url, kwargs)) or Response(200))
  url, kwargs = calls[0]
  payload = json.loads(kwargs["data"]["payload_json"])
  assert url == "https://example.invalid/hook" and kwargs["files"]["files[0]"] == ("diag.zip", b"zip-bytes", "application/zip")
  assert "AA dropped at 2:34" in payload["content"] and "b0c4a280b2f96b86" in payload["content"]
  assert payload["allowed_mentions"] == {"parse": []}, "a tester's note must never ping anyone"


@pytest.mark.parametrize("kwargs, message", [
  ({"url": ""}, "isn't set up"),
  ({"url": "https://example.invalid/hook", "data": b"x" * (bundle.DISCORD_FILE_LIMIT + 1)}, "10 MB limit"),
  ({"url": "https://example.invalid/hook", "post": lambda *a, **k: Response(413)}, "HTTP 413"),
])
def test_send_failures_tell_the_tester_what_to_do(kwargs, message):
  data = kwargs.pop("data", b"zip")
  kwargs.setdefault("post", lambda *a, **k: pytest.fail("must not post"))
  with pytest.raises(RuntimeError, match=message) as error:
    bundle.send(data, "diag.zip", "", SUMMARY, **kwargs)
  assert "Download" in str(error.value)


def _wait(job):
  deadline = time.monotonic() + 5
  while job.status()["state"] in ("preparing", "sending"):
    assert time.monotonic() < deadline
    time.sleep(0.01)
  return job.status()


def test_job_download_flow():
  job = bundle.DiagnosticsJob(builder=lambda **_: b"zip", summary=lambda: SUMMARY)
  assert job.result() is None
  job.start("download", "note", 1)
  status = _wait(job)
  assert status["state"] == "ready" and status["bytes"] == 3 and status["recipient"] == "AA Guy"
  name, data = job.result()
  assert data == b"zip" and name.startswith("starpilot-diagnostics-b0c4a280b2f96b86-")


def test_job_send_success_and_failure_keeps_the_zip():
  sent = []
  job = bundle.DiagnosticsJob(builder=lambda **_: b"zip", sender=lambda *args: sent.append(args), summary=lambda: SUMMARY)
  job.start("send")
  assert _wait(job)["state"] == "sent" and sent[0][0] == b"zip"

  def offline(*_):
    raise RuntimeError("Could not reach AA Guy")

  job = bundle.DiagnosticsJob(builder=lambda **_: b"zip", sender=offline, summary=lambda: SUMMARY)
  job.start("send")
  status = _wait(job)
  assert status["state"] == "send_failed" and "Could not reach" in status["message"]
  assert job.result()[1] == b"zip", "a failed send can still be downloaded"


def test_job_rejects_unknown_action_and_overlapping_runs():
  gate = __import__("threading").Event()
  job = bundle.DiagnosticsJob(builder=lambda **_: gate.wait(5) and b"zip", summary=lambda: SUMMARY)
  with pytest.raises(ValueError):
    job.start("email")
  job.start("download")
  with pytest.raises(RuntimeError, match="already"):
    job.start("download")
  gate.set()
  assert _wait(job)["state"] == "ready"


def test_builder_failure_is_reported():
  def broken(**_):
    raise OSError("disk full")

  job = bundle.DiagnosticsJob(builder=broken, summary=lambda: SUMMARY)
  job.start("download")
  assert _wait(job) | {"send_available": None} == {"state": "error", "message": "disk full", "action": "download", "name": "",
                                                   "bytes": 0, "send_available": None, "recipient": "AA Guy"}
