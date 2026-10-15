"""Jet Link's display is read-only and never retains stale latency."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import threading

import pytest

from openpilot.starpilot.models import jetlink_adapter as adapter
from openpilot.starpilot.system.android_auto.projection_jetlink import JetlinkStatus, ProjectionJetlinkFeed, read_status


@pytest.mark.parametrize('mode,device', [(1, 'Computer'), (2, 'iPhone'), (3, 'Auto: Computer')])
@pytest.mark.parametrize('state,present,reason,active,expected', [
  ('running', True, None, True, 'active'),
  ('ready', True, None, False, 'ready'),
  ('joining', True, None, False, 'connecting'),
  ('retrying', True, None, False, 'connecting'),
  ('unavailable', True, None, False, 'connected'),
  ('running', False, None, True, 'disconnected'),
  ('running', True, 'service stopped', True, 'unavailable'),
])
def test_status_and_device_mode(mode, device, state, present, reason, active, expected):
  params = NS(get=lambda key: mode)
  status = NS(enabled=True, present=present, reason=reason, transport='USB')
  runtime = {'state': state, 'active': active, 'app_latency_ms': 24.6}
  with patch.object(adapter, 'get_jetlink', return_value=NS(status=lambda: status)), patch.object(adapter, 'runtime_status', return_value=runtime):
    result = read_status(params)
    assert result.device == ('Automatic' if mode == 3 and not present else device)
    assert result.state == expected
    assert result.latency_ms == (24.6 if expected == 'active' else None)
    status.transport = 'iOS over USB (172.20.10.1)'
    if mode == 3 and present:
      assert read_status(params).device == 'Auto: iPhone'


@pytest.mark.parametrize('mode', [0, None, True, b'2', 4])
def test_off_does_not_load_transport(mode):
  with patch.object(adapter, 'get_jetlink') as get:
    assert read_status(NS(get=lambda key: mode)) == JetlinkStatus()
    get.assert_not_called()


def test_missing_or_disabled_link_is_unavailable():
  params = NS(get=lambda key: 2)
  with patch.object(adapter, 'get_jetlink', return_value=None):
    assert read_status(params) == JetlinkStatus('iPhone', 'unavailable')
  status = NS(enabled=False, present=True, reason=None, transport='USB')
  with patch.object(adapter, 'get_jetlink', return_value=NS(status=lambda: status)), patch.object(adapter, 'runtime_status') as runtime:
    assert read_status(params) == JetlinkStatus('iPhone', 'unavailable')
    runtime.assert_not_called()


def test_background_polling_stale_expiry_and_disabling():
  arrived = threading.Event()
  calls = []
  def read(params):
    calls.append(threading.get_ident())
    arrived.set()
    return JetlinkStatus('iPhone', 'active', 24.6)
  feed = ProjectionJetlinkFeed(None, read)
  try:
    assert feed.snapshot() == JetlinkStatus()
    assert feed._thread is None
    feed.set_enabled(True)
    assert arrived.wait(1)
    # Join the lock after the reader has published the result.
    feed.close()
    assert feed.snapshot() == JetlinkStatus('iPhone', 'active', 24.6)
    assert calls == [feed._thread.ident]
    assert calls[0] != threading.get_ident()
    with patch('openpilot.starpilot.system.android_auto.projection_jetlink.time.monotonic_ns', return_value=feed._read_at + 2_500_000_001):
      assert feed.snapshot() == JetlinkStatus('iPhone', 'unavailable')
    feed.set_enabled(False)
    assert feed.snapshot() == JetlinkStatus()
    feed.poll()
    assert len(calls) == 1
  finally:
    feed.close()


def test_failed_read_clears_previous_latency():
  feed = ProjectionJetlinkFeed(None, Mock(side_effect=OSError('unreadable')))
  feed._enabled = True
  feed._status = JetlinkStatus('iPhone', 'active', 24.6)
  feed.poll()
  assert feed.snapshot() == JetlinkStatus(state='unavailable')


def test_card_hidden_when_off_disabled_or_alerting(monkeypatch):
  from openpilot.starpilot.ui import onroad_jetlink as ui
  texts = []
  fonts = NS(measure=lambda *args: NS(width=250), draw=lambda text, *args: texts.append(text))
  monkeypatch.setattr(ui.rl, 'draw_rectangle_rounded', lambda *args: None)
  monkeypatch.setattr(ui.rl, 'draw_rectangle_rounded_lines_ex', lambda *args: None)
  widget = ui.JetlinkStatusWidget(fonts)
  placed = {'x': 30, 'y': 650, 'enabled': True}
  state = NS(customization={'layouts': {'large': {'jetlink_status': placed}}}, alert=NS(size='none'))
  widget.render(state)
  assert texts == []
  widget.status = JetlinkStatus('iPhone', 'active', 24.6)
  widget.render(state)
  assert texts == ['Jet Link: iPhone', 'Link up', 'App latency: 24.6 ms']
  texts.clear()
  widget.status = replace(widget.status, state='disconnected', latency_ms=None)
  widget.render(state)
  assert texts[-1] == 'App latency: --'
  texts.clear()
  state.alert.size = 'full'
  widget.render(state)
  assert texts == []
  state.alert.size = 'none'
  placed['enabled'] = False
  widget.render(state)
  assert texts == []
