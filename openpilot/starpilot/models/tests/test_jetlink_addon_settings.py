"""Jetson add-on choices in Galaxy: saved for the next boot, shown with their link."""
from jetlink.addons.addon import Addon
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
  monkeypatch.setattr(Addon, "present", lambda self: True)
  monkeypatch.setattr(Addon, "report", lambda self, now=None: None)
  result = settings.configure({"vasm": True})
  assert "Reboot" not in result["message"]
  assert params.values["JetlinkVasm"] is True
  view = settings.snapshot()["addons"]
  assert view["vasm"]["enabled"] and "rebootRequired" not in view["vasm"] and not view["vasm"]["connected"]
  assert not view["signVision"]["enabled"]


def test_addon_link_stats(owner, monkeypatch):
  settings, params = owner
  params.values["JetlinkSignVision"] = True
  monkeypatch.setattr(Addon, "present", lambda self: self.name == "slv")
  monkeypatch.setattr("jetlink.comma.gadget.read_record", lambda path: None)
  monkeypatch.setattr(Addon, "report", lambda self, now=None: {"connected": True, "yielding": False,
                      "link": {"answered": 40, "timeouts": 1}, "round_trips": {"total_ms": 6.5, "jetson_ms": 3.1}})
  view = settings.snapshot()["addons"]["signVision"]
  assert view == {"enabled": True, "present": True, "running": True, "connected": True,
                  "yielding": False, "modelMismatch": False, "jetson": None, "preparing": "", "answered": 40, "timeouts": 1,
                  "roundTripMs": 6.5, "jetsonMs": 3.1}


def test_a_jetson_without_the_comma_model_shows_a_mismatch(owner, monkeypatch):
  settings, params = owner
  params.values["JetlinkVasm"] = True
  monkeypatch.setattr(Addon, "present", lambda self: self.name == "vasm")
  stats = {"connected": True, "missing": [0xAB12CD34], "link": {}, "round_trips": {"n": 0}}
  monkeypatch.setattr(Addon, "report", lambda self, now=None: stats)
  assert settings.snapshot()["addons"]["vasm"]["modelMismatch"]
  stats["connected"] = False
  assert not settings.snapshot()["addons"]["vasm"]["modelMismatch"]


def test_no_report_is_idle(owner, monkeypatch):
  settings, params = owner
  params.values["JetlinkVasm"] = True
  monkeypatch.setattr(Addon, "present", lambda self: True)
  monkeypatch.setattr(Addon, "report", lambda self, now=None: None)
  view = settings.snapshot()["addons"]["vasm"]
  assert not view["running"] and not view["connected"] and not view["modelMismatch"]


@pytest.mark.parametrize("payload", [{"vasm": 1}, {"signVision": "yes"}, {"other": True}])
def test_addon_choices_are_exact(owner, payload):
  with pytest.raises(ValueError):
    owner[0].configure(payload)


def test_wake_starts_jetlinks_hold_and_shows_the_deadline(owner, monkeypatch, tmp_path):
  import os
  import sys
  import time
  from jetlink.comma import gadget, hold
  from openpilot.starpilot.models import jetlink_settings
  settings, params = owner
  monkeypatch.setattr(hold, "HOLD", tmp_path / "hold.json")
  monkeypatch.setattr(gadget, "owner_status", lambda: {"dormant": True})
  started = []
  monkeypatch.setattr(jetlink_settings.subprocess, "Popen", lambda argv, **kw: started.append(argv) or
                      gadget.write_record(hold.HOLD, {"pid": os.getpid(), "until": time.monotonic() + 60}))
  monkeypatch.setattr(Addon, "present", lambda self: False)
  params.values["JetlinkMode"] = 2
  with pytest.raises(ValueError):
    settings.configure({"wake": True})
  params.values["JetlinkMode"] = 1
  assert "Waking" in settings.configure({"wake": True})["message"]
  assert "already" in settings.configure({"wake": True})["message"]
  assert started == [[sys.executable, "-m", "jetlink.comma.hold", "-t", "900", "--parked"]]
  view = settings.snapshot()
  assert view["asleep"] and 0 < view["awakeFor"] <= 60


def test_only_the_models_of_addons_turned_on_go_to_the_jetson(tmp_path):
  import hashlib
  from jetlink.openpilot.settings import FileParams
  from openpilot.starpilot.models.jetlink_adapter import _adapter_class
  addon_models = _adapter_class().addon_models
  params = FileParams(tmp_path)
  assert addon_models(params) == ()
  (tmp_path / "JetlinkSignVision").write_bytes(b"1")
  models = addon_models(params)
  assert [(m.function.iface_class, m.name) for m in models] == [((0xFF, 0x53, 0x4C), "Sign detector"), ((0xFF, 0x53, 0x4C), "Stop sign detector")]
  (tmp_path / "JetlinkVasm").write_bytes(b"1")
  for model in addon_models(params):
    assert hashlib.sha256(model.path.read_bytes()).hexdigest() == model.sha256, f"{model.name} is not the file its code pins"


def test_the_addon_models_are_the_ones_their_code_runs():
  from openpilot.starpilot.speed_limits.vision.model import VisionModelCore
  from openpilot.starpilot.speed_limits.vision.traffic_signs import TrafficSignModelCore
  from openpilot.starpilot.spot_monitor.inference import DEFAULT_MODEL_PATH, MODEL_SHA256
  assets = VisionModelCore.ASSET_DIR
  assert {key: addon.models for key, addon in jetlink_addons.ADDONS.items()} == {
    "vasm": (("V-ASM", DEFAULT_MODEL_PATH, MODEL_SHA256),),
    "signs": (("Sign detector", assets / "traffic_signs_us_detector.onnx", TrafficSignModelCore.DETECTOR_SHA256),
              ("Stop sign detector", assets / "traffic_signs_us_stop_detector.onnx", TrafficSignModelCore.STOP_PROJECTION_SHA256))}


def test_the_jetson_state_of_each_addon(owner, monkeypatch):
  from jetlink.comma import gadget
  settings, params = owner
  params.values["JetlinkVasm"] = True
  monkeypatch.setattr(Addon, "report", lambda self, now=None: None)
  vasm = jetlink_addons.ADDONS["vasm"].models[0][2]
  signs = [sha for _, _, sha in jetlink_addons.ADDONS["signs"].models]
  for server, vasm_state, signs_state in ((None, None, None), ({"device": "orin"}, "unsupported", "unsupported"),
                                          ({"addons": {"ready": [vasm, signs[0]], "failed": []}}, "ready", "missing"),
                                          ({"addons": {"ready": [signs[0]], "failed": [signs[1]]}}, "missing", "failed")):
    monkeypatch.setattr(gadget, "read_record", lambda path, server=server: server)
    view = settings.snapshot()["addons"]
    assert (view["vasm"]["jetson"], view["signVision"]["jetson"]) == (vasm_state, signs_state)
  params.values["JetlinkProgress"] = {"msg": "V-ASM: build 40%", "code": "addon_build", "args": {"name": "V-ASM"}}
  view = settings.snapshot()["addons"]
  assert view["vasm"]["preparing"] == "V-ASM: build 40%" and view["signVision"]["preparing"] == ""


def test_unavailable_snapshot_has_addons():
  broken = JetlinkSettings(params=MemoryParams(), device_type=lambda: (_ for _ in ()).throw(OSError()))
  assert broken.snapshot()["addons"] == {}
