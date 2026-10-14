"""A boot with the iPad attached gets one reconnect to reach super-speed."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from jetlink.comma import gadget, port


@pytest.fixture
def usb(monkeypatch):
  calls = []
  speeds = {'maximum_speed': 'super-speed'}
  monkeypatch.setattr(port, 'run_udc', lambda command: calls.append(command) or True)
  monkeypatch.setattr(port.time, 'sleep', Mock())
  monkeypatch.setattr(gadget, 'bound_udc', lambda: 'a600000.dwc3')
  monkeypatch.setattr(gadget, 'UDC_PATH', Path('/udc'))
  monkeypatch.setattr(gadget, 'read', lambda path: speeds.get(Path(path).name, ''))
  monkeypatch.setattr(gadget, 'log', Mock())
  usb_port = port.Port(frozenset())
  return usb_port, calls, speeds


def test_late_start_at_high_speed_reconnects_once(usb):
  usb_port, calls, _ = usb
  usb_port.late_start = True
  assert usb_port.retrain('high-speed')
  assert calls == ['stop', 'start']
  assert not usb_port.retrain('high-speed')
  assert calls == ['stop', 'start']


@pytest.mark.parametrize('late,speed,maximum', [
  (False, 'high-speed', 'super-speed'),   # plugged in after boot: left alone
  (True, 'super-speed', 'super-speed'),   # already fast
  (True, 'high-speed', 'high-speed'),     # a controller that cannot do better
  (True, None, 'super-speed'),
])
def test_no_reconnect_without_a_late_high_speed_start(usb, late, speed, maximum):
  usb_port, calls, speeds = usb
  speeds['maximum_speed'] = maximum
  usb_port.late_start = late
  assert not usb_port.retrain(speed)
  assert calls == []


def test_a_new_plug_gets_its_own_retry(usb, monkeypatch):
  usb_port, calls, _ = usb
  usb_port.late_start = True
  assert usb_port.retrain('high-speed')
  monkeypatch.setattr(port.gadget, 'read', lambda path: '')
  usb_port._unplugged()
  assert not usb_port.late_start and not usb_port.retrained
