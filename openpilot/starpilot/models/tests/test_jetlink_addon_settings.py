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


def test_unavailable_snapshot_has_addons():
  broken = JetlinkSettings(params=MemoryParams(), device_type=lambda: (_ for _ in ()).throw(OSError()))
  assert broken.snapshot()["addons"] == {}
