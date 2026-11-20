"""StarPilot's glue for the Jetson add-ons: the net asks the Jetson while the setting is on, its report kept for Galaxy."""
from types import SimpleNamespace

import numpy as np
import pytest

from jetlink.addons import link as link_mod
from openpilot.starpilot.models import jetlink_addons

SHA = "ab12cd34" + "0" * 56
OUTPUTS = (("output0", (1, 3)),)


class Settings:
  def __init__(self, *on):
    self.on = set(on)

  def get_bool(self, key):
    return key in self.on


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
  monkeypatch.setattr(link_mod, "_shared", {})


def interface(tmp_path, exists=True):
  if exists:
    (tmp_path / "ep0").write_bytes(b"")
  return SimpleNamespace(name="vasm", ep0=tmp_path / "ep0", ep_out=tmp_path / "ep1", ep_in=tmp_path / "ep2")


def cpu():
  return SimpleNamespace(setInput=lambda *a, **k: None, forward=lambda *a: np.zeros((1, 3), np.float32))


def test_the_plain_net_while_the_setting_is_off(monkeypatch):
  net = object()
  assert jetlink_addons.with_jetson(net, "vasm", SHA, OUTPUTS, params=Settings()) is net


def test_wraps_before_the_gadget_has_the_interface(tmp_path, monkeypatch):
  # the model may load before jetlinkd builds the gadget: the link looks on each call
  monkeypatch.setattr(jetlink_addons, "gadget_function", lambda addon: interface(tmp_path, exists=False))
  net = jetlink_addons.with_jetson(cpu(), "vasm", SHA, OUTPUTS, scaled_u8=True, params=Settings("JetlinkVasm"))
  assert isinstance(net, link_mod.JetsonNet) and net.tag == 0xAB12CD34 and net.scaled_u8
  net.setInput(np.zeros((1, 3, 4, 4), np.float32))
  assert net.forward().shape == (1, 3) and net.stats == {"host": 0, "local": 1}


def test_one_link_per_addon_reporting_to_its_stats(tmp_path, monkeypatch):
  monkeypatch.setattr(jetlink_addons, "gadget_function", lambda addon: interface(tmp_path))
  written = []
  monkeypatch.setattr(jetlink_addons, "write_stats", lambda addon, report: written.append((addon, report)))
  on = Settings("JetlinkVasm")
  a = jetlink_addons.with_jetson(cpu(), "vasm", SHA, OUTPUTS, params=on)
  b = jetlink_addons.with_jetson(cpu(), "vasm", SHA, OUTPUTS, params=on)
  assert a.link is b.link
  a.link.report_to({"n": 1})
  assert written == [("vasm", {"n": 1})]


def test_the_plain_net_on_any_failure(monkeypatch):
  def broken(addon):
    raise RuntimeError("no gadget")
  monkeypatch.setattr(jetlink_addons, "gadget_function", broken)
  net = object()
  assert jetlink_addons.with_jetson(net, "signs", SHA, (), params=Settings("JetlinkSignVision")) is net


def test_declares_only_enabled_addons():
  assert jetlink_addons.gadget_functions(Settings()) == ()
  assert [f.name for f in jetlink_addons.gadget_functions(Settings("JetlinkVasm"))] == ["vasm"]
  both = jetlink_addons.gadget_functions(Settings("JetlinkVasm", "JetlinkSignVision"))
  assert [(f.name, f.iface_class) for f in both] == [("vasm", (0xFF, 0x56, 0x41)), ("slv", (0xFF, 0x53, 0x4C))]


def test_the_adapter_declares_them_from_the_params_store(tmp_path, monkeypatch):
  from openpilot.starpilot.models.jetlink_adapter import _adapter_class
  (tmp_path / "d").mkdir()
  (tmp_path / "d" / "JetlinkSignVision").write_text("1")
  monkeypatch.setenv("PARAMS_ROOT", str(tmp_path))
  monkeypatch.delenv("OPENPILOT_PREFIX", raising=False)
  assert [f.name for f in _adapter_class().owner_config().functions] == ["slv"]
