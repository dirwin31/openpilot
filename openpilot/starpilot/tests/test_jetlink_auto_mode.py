"""The 'auto' Jetlink setting picks the USB or iOS gadget by which kind of host speaks."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jetlink.comma import gadget, owner as comma_owner
from jetlink.openpilot.interface import MODES
from openpilot.starpilot.models.jetlink_settings import MODES as SETTINGS_MODES
from openpilot.starpilot.system.jetlink.lifecycle import selected


class Clock:
  def __init__(self):
    self.now = 1000.0

  def __call__(self):
    return self.now


@pytest.fixture
def auto(monkeypatch, tmp_path):
  clock = Clock()
  cable = {'plugged': True, 'configured': True}
  monkeypatch.setattr(comma_owner.time, 'monotonic', clock)
  monkeypatch.setattr(gadget, 'AUTO', tmp_path / 'auto.json')
  monkeypatch.setattr(gadget, 'port_has_host', lambda: cable['plugged'])
  monkeypatch.setattr(gadget, 'host_attached', lambda: cable['configured'])
  monkeypatch.setattr(gadget, 'log', Mock())
  monkeypatch.setattr(gadget, 'set_dormant', Mock())
  own = comma_owner.Owner.__new__(comma_owner.Owner)
  own.settings = SimpleNamespace(offroad=lambda: True)
  own.cable = SimpleNamespace(held=False)
  own._peer = None
  own.dormant = False
  own.shutting_down = False
  own.had_host = True
  own.auto_ios = gadget.auto_kind() == 'ios'
  own.auto_silent_since = None
  own.auto_wait_scale = 1.0
  own.auto_heard = None
  own.auto_plugged = False
  own.auto_run_failed = False
  own.auto_settled = False
  own.built_ios = False
  return own, clock, cable


def settle(own, clock, seconds):
  """Steps for `seconds`, the gadget rebuilt to whatever auto asks for, as switch_mode would."""
  mode = None
  for _ in range(int(seconds / comma_owner.POLL)):
    mode = own.auto_mode()
    own.built_ios = mode == 'ios'
    clock.now += comma_owner.POLL
  return mode


def test_auto_is_a_setting_everywhere_it_is_read():
  assert MODES[3] == SETTINGS_MODES[3] == 'auto'
  assert selected(SimpleNamespace(get=lambda key: 3))


def test_silent_usb_host_moves_to_ios_only_after_a_run_fails(auto):
  own, clock, _ = auto
  assert settle(own, clock, comma_owner.AUTO_WAIT_USB + 5) == 'usb'   # silence alone: an idle Jetson
  own.auto_run_failed = True    # the provisioning run's hello went unanswered
  assert settle(own, clock, comma_owner.AUTO_WAIT_USB + 5) == 'ios'
  assert own.had_host is False  # so a run goes to meet the host on the new gadget


def test_phone_dial_settles_until_unplugged(auto):
  own, clock, cable = auto
  own.auto_ios = own.built_ios = True
  own.auto_heard = 'ios'
  assert settle(own, clock, 2) == 'ios'
  assert gadget.auto_kind() == 'ios'   # kept for the next owner this boot
  # the app is closed: still a phone, however long it is quiet
  assert settle(own, clock, comma_owner.AUTO_WAIT_MAX * 2) == 'ios'
  # unplugged, and a Mac that never dials plugged in: back to USB after the wait
  cable['plugged'] = cable['configured'] = False
  settle(own, clock, 1)
  cable['plugged'] = cable['configured'] = True
  assert settle(own, clock, comma_owner.AUTO_WAIT_IOS + 5) == 'usb'


def test_a_server_hello_keeps_usb(auto):
  own, clock, _ = auto
  own.auto_heard = 'usb'
  own.auto_run_failed = True
  assert settle(own, clock, comma_owner.AUTO_WAIT_MAX * 2) == 'usb'


def test_nobody_answering_backs_off(auto):
  own, clock, _ = auto
  own.auto_ios = own.built_ios = True
  flips = []
  for _ in range(int(600 / comma_owner.POLL)):
    own.auto_run_failed = True
    before = own.auto_ios
    own.built_ios = own.auto_mode() == 'ios'
    if own.auto_ios != before:
      flips.append(clock.now)
    clock.now += comma_owner.POLL
  gaps = [b - a for a, b in zip(flips, flips[1:], strict=False)]
  assert len(flips) <= 6 and gaps == sorted(gaps)


def test_onroad_never_switches(auto):
  own, clock, _ = auto
  own.settings = SimpleNamespace(offroad=lambda: False)
  own.auto_run_failed = True
  assert settle(own, clock, comma_owner.AUTO_WAIT_MAX * 2) == 'usb'


def test_a_plug_wakes_a_dormant_gadget(auto, monkeypatch):
  own, clock, cable = auto
  cable['plugged'] = False
  own.auto_mode()
  own.dormant = True
  wake = Mock()
  monkeypatch.setattr(own, 'wake', wake, raising=False)
  cable['plugged'] = True
  own.auto_mode()
  wake.assert_called_once_with()
