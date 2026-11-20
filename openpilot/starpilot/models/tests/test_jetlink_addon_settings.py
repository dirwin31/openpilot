"""Jetson add-on choices in Galaxy: saved for the next boot, shown with their link."""
from openpilot.starpilot.models import jetlink_addons
from openpilot.starpilot.models.jetlink_settings import JetlinkSettings
from openpilot.starpilot.models.tests.test_jetlink_settings import MemoryParams, TestJetlinkSettings
import pytest


@pytest.fixture
def owner(monkeypatch):
  case = TestJetlinkSettings()
  case.setUp()
  return case.owner, case.params


def test_addons_save_without_a_reboot(owner, monkeypatch):
  settings, params = owner
  params.values["JetlinkMode"] = 1
  monkeypatch.setattr(jetlink_addons, "present", lambda addon: True)
  monkeypatch.setattr(jetlink_addons, "read_stats", lambda addon: None)
  result = settings.configure({"vasm": True})
  assert "Reboot" not in result["message"]
  assert params.values["JetlinkVasm"] is True
  view = settings.snapshot()["addons"]
  assert view["vasm"]["enabled"] and "rebootRequired" not in view["vasm"] and not view["vasm"]["connected"]
  assert not view["signVision"]["enabled"]


def test_addon_link_stats(owner, monkeypatch):
  settings, params = owner
  params.values["JetlinkSignVision"] = True
  monkeypatch.setattr(jetlink_addons, "present", lambda addon: addon == "signs")
  monkeypatch.setattr(jetlink_addons, "read_stats", lambda addon: {"connected": True, "yielding": False,
                      "link": {"answered": 40, "timeouts": 1}, "round_trips": {"total_ms": 6.5, "jetson_ms": 3.1}})
  view = settings.snapshot()["addons"]["signVision"]
  assert view == {"enabled": True, "present": True, "running": True, "connected": True,
                  "yielding": False, "modelMismatch": False, "answered": 40, "timeouts": 1, "roundTripMs": 6.5,
                  "jetsonMs": 3.1}


def test_a_jetson_without_the_comma_model_shows_a_mismatch(owner, monkeypatch):
  settings, params = owner
  params.values["JetlinkVasm"] = True
  monkeypatch.setattr(jetlink_addons, "present", lambda addon: addon == "vasm")
  stats = {"connected": True, "missing": [0xAB12CD34], "link": {}, "round_trips": {"n": 0}}
  monkeypatch.setattr(jetlink_addons, "read_stats", lambda addon: stats)
  assert settings.snapshot()["addons"]["vasm"]["modelMismatch"]
  stats["connected"] = False
  assert not settings.snapshot()["addons"]["vasm"]["modelMismatch"]


def test_no_report_is_idle(owner, monkeypatch):
  settings, params = owner
  params.values["JetlinkVasm"] = True
  monkeypatch.setattr(jetlink_addons, "present", lambda addon: True)
  monkeypatch.setattr(jetlink_addons, "read_stats", lambda addon: None)
  view = settings.snapshot()["addons"]["vasm"]
  assert not view["running"] and not view["connected"] and not view["modelMismatch"]


@pytest.mark.parametrize("payload", [{"vasm": 1}, {"signVision": "yes"}, {"other": True}])
def test_addon_choices_are_exact(owner, payload):
  with pytest.raises(ValueError):
    owner[0].configure(payload)


def test_wake_starts_a_hold_and_shows_the_deadline(owner, monkeypatch, tmp_path):
  import os
  import time
  from jetlink.comma import gadget
  from openpilot.starpilot.models import jetlink_wake
  settings, params = owner
  monkeypatch.setattr(jetlink_wake, "RECORD", tmp_path / "wake.json")
  monkeypatch.setattr(gadget, "owner_status", lambda: {"dormant": True})
  started = []
  monkeypatch.setattr(jetlink_wake.subprocess, "Popen", lambda *a, **kw: started.append(a) or
                      gadget.write_record(jetlink_wake.RECORD, {"pid": os.getpid(), "until": time.monotonic() + 60}))
  monkeypatch.setattr(jetlink_addons, "present", lambda addon: False)
  params.values["JetlinkMode"] = 2
  with pytest.raises(ValueError):
    settings.configure({"wake": True})
  params.values["JetlinkMode"] = 1
  assert "Waking" in settings.configure({"wake": True})["message"]
  assert "already" in settings.configure({"wake": True})["message"] and len(started) == 1
  view = settings.snapshot()
  assert view["asleep"] and 0 < view["awakeFor"] <= 60


def test_the_hold_gives_the_gadget_back_when_driving_starts(monkeypatch, tmp_path):
  from unittest import mock
  from jetlink.comma import lending
  from openpilot.starpilot.models import jetlink_wake
  monkeypatch.setattr(jetlink_wake, "RECORD", tmp_path / "wake.json")
  monkeypatch.setattr(lending, "hung_up", lambda conn, wait: False)
  loan = mock.Mock()
  jetlink_wake.hold(loan, lambda: True)
  loan.close.assert_called_once()
  assert not jetlink_wake.RECORD.exists() and jetlink_wake.awake_for() is None


def test_unavailable_snapshot_has_addons():
  broken = JetlinkSettings(params=MemoryParams(), device_type=lambda: (_ for _ in ()).throw(OSError()))
  assert broken.snapshot()["addons"] == {}
