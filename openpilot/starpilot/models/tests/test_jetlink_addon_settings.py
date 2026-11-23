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
                  "yielding": False, "modelMismatch": False, "jetson": None, "preparing": "", "lastCheck": "", "lastCheckAt": None,
                  "answered": 40, "timeouts": 1,
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


def test_wake_starts_a_hold_and_shows_the_deadline(owner, monkeypatch, tmp_path):
  import os
  import sys
  import time
  from jetlink.comma import gadget
  from openpilot.starpilot.models import jetlink_wake
  settings, params = owner
  monkeypatch.setattr(jetlink_wake, "RECORD", tmp_path / "wake.json")
  monkeypatch.setattr(gadget, "owner_status", lambda: {"dormant": True})
  started = []
  monkeypatch.setattr(jetlink_wake.subprocess, "Popen", lambda argv, **kw: started.append(argv) or
                      gadget.write_record(jetlink_wake.RECORD, {"pid": os.getpid(), "until": time.monotonic() + 60}))
  monkeypatch.setattr(Addon, "present", lambda self: False)
  params.values["JetlinkMode"] = 2
  with pytest.raises(ValueError):
    settings.configure({"wake": True})
  params.values["JetlinkMode"] = 1
  assert "Waking" in settings.configure({"wake": True})["message"]
  assert "already" in settings.configure({"wake": True})["message"]
  assert started == [[sys.executable, "-m", "openpilot.starpilot.models.jetlink_wake"]]
  view = settings.snapshot()
  assert view["asleep"] and 0 < view["awakeFor"] <= 60


def test_the_hold_gives_the_gadget_back_when_driving_starts(monkeypatch, tmp_path):
  from unittest import mock
  from jetlink.comma import lending
  from openpilot.starpilot.models import jetlink_wake
  monkeypatch.setattr(jetlink_wake, "RECORD", tmp_path / "wake.json")
  monkeypatch.setattr(lending, "hung_up", lambda conn, wait: False)
  loan = mock.Mock()
  assert jetlink_wake.hold(loan, lambda: True) == "the car is onroad"
  loan.close.assert_called_once()
  assert not jetlink_wake.RECORD.exists() and jetlink_wake.awake_for() is None


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


def test_the_wake_reads_the_offroad_param_openpilot_has(monkeypatch, tmp_path):
  # openpilot has IsOffroad and no IsOnroad: asking for that raised and gave the link straight back
  from unittest import mock
  from jetlink.comma import lending
  from openpilot.starpilot.models import jetlink_wake
  monkeypatch.setattr(jetlink_wake, "RECORD", tmp_path / "wake.json")
  monkeypatch.setattr(lending, "SOCKET", tmp_path / "owner.sock")
  (tmp_path / "owner.sock").touch()
  loan = mock.Mock()
  monkeypatch.setattr(lending, "borrow", lambda name, timeout: loan)
  reads = []

  class Params:
    def get_bool(self, key):
      reads.append(key)
      if key != "IsOffroad":
        raise KeyError(key)
      return len(reads) < 3   # parked twice, then a drive starts

  import sys
  import types
  monkeypatch.setitem(sys.modules, "openpilot.common.params", types.SimpleNamespace(Params=Params))
  monkeypatch.setattr(lending, "hung_up", lambda conn, wait: False)
  assert jetlink_wake.main() == 0
  assert set(reads) == {"IsOffroad"} and len(reads) == 3
  loan.close.assert_called_once()


def test_the_wake_is_extended_restarted_and_released_from_galaxy(owner, monkeypatch, tmp_path):
  import os
  import time
  from jetlink.comma import gadget
  from openpilot.starpilot.models import jetlink_wake
  settings, params = owner
  params.values["JetlinkMode"] = 1
  monkeypatch.setattr(jetlink_wake, "RECORD", tmp_path / "wake.json")
  monkeypatch.setattr(Addon, "present", lambda self: False)
  assert "Wake it first" in settings.configure({"wake": "extend"})["message"]
  assert "Nothing" in settings.configure({"wake": "release"})["message"]
  # a wake holding the gadget for another minute
  gadget.write_record(jetlink_wake.RECORD, {"pid": os.getpid(), "until": time.monotonic() + 60})
  assert "16 more minutes" in settings.configure({"wake": "extend"})["message"]
  assert 900 < jetlink_wake.awake_for() <= 960
  assert "15 more minutes" in settings.configure({"wake": "restart"})["message"]
  assert 890 < jetlink_wake.awake_for() <= 900
  # never more than two hours from now
  for _ in range(10):
    jetlink_wake.extend()
  assert jetlink_wake.awake_for() <= jetlink_wake.LONGEST
  assert "Released" in settings.configure({"wake": "release"})["message"]
  assert jetlink_wake.awake_for() is None
  with pytest.raises(ValueError):
    settings.configure({"wake": "forever"})


def test_a_hold_gives_the_gadget_back_when_galaxy_releases_it(monkeypatch, tmp_path):
  from unittest import mock
  from jetlink.comma import lending
  from openpilot.starpilot.models import jetlink_wake
  monkeypatch.setattr(jetlink_wake, "RECORD", tmp_path / "wake.json")
  # Galaxy releases it while the hold waits on the owner
  monkeypatch.setattr(lending, "hung_up", lambda conn, wait: jetlink_wake.release() and False)
  loan = mock.Mock()
  assert jetlink_wake.hold(loan, lambda: False, seconds=600) == "the time is up, or Galaxy released it"
  loan.close.assert_called_once()
