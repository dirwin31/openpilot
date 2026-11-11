import json
from unittest.mock import patch

import pytest

from openpilot.common.hardware.comma import hardware
from openpilot.starpilot.ui import network_signal
from openpilot.starpilot.ui.network_signal import Signal, SignalFeed, read_signal, steps
from openpilot.starpilot.ui.onroad_customization import SIGNAL_WIDGET, default_document, validate_document


def read(tmp_path, modem, rssi, iface):
  path = tmp_path / 'modem'
  if modem is not None:
    path.write_text(json.dumps(modem))
  poll = {'RSSI': str(rssi)} if rssi is not None else {}
  with patch.object(hardware, 'MODEM_STATE_PATH', str(path)), patch.object(hardware, 'wpa_supplicant_cmd', return_value=poll), \
       patch.object(hardware, 'get_default_route_iface', return_value=iface):
    return read_signal()


def test_steps_match_device_strength_bands():
  assert [steps(p) for p in (0, 24, 25, 49, 50, 74, 75, 100)] == [1, 1, 2, 2, 3, 3, 4, 4]


def test_reads_both_radios_and_the_link_in_use(tmp_path):
  modem = {'connected': True, 'signal_quality': 60, 'network_type': 'nr'}
  assert read(tmp_path, modem, -45, 'wlan0') == Signal(3, 4, 'wifi', '5G')
  assert read(tmp_path, modem, None, 'ppp0') == Signal(3, None, 'cell', '5G')
  assert read(tmp_path, {'connected': False, 'signal_quality': 60}, -88, 'wlan0') == Signal(None, 2, 'wifi')
  assert read(tmp_path, None, None, None) == Signal()


def test_feed_goes_blank_when_the_reading_is_old():
  feed = SignalFeed(read=lambda: Signal(4, None, 'cell'), period=3600)
  feed._thread = object()  # no background polling
  feed.poll()
  assert feed.snapshot() == Signal(4, None, 'cell')
  feed._read_at -= network_signal.STALE_S + 1
  assert feed.snapshot() == Signal()


def test_signal_is_an_opt_in_big_widget_with_background_opacity():
  document = default_document()
  placed = document['layouts']['large'][SIGNAL_WIDGET]
  assert placed['enabled'] is False and placed['opacity'] == 85
  assert SIGNAL_WIDGET not in document['layouts']['compact']
  del document['layouts']['large'][SIGNAL_WIDGET]
  assert validate_document(document)['layouts']['large'][SIGNAL_WIDGET]['enabled'] is False


def test_signal_style_picks_size_and_older_layouts_default_to_ring():
  from openpilot.starpilot.ui.onroad_customization import SIGNAL_SIZES, widget_size
  document = default_document()
  placed = document['layouts']['large'][SIGNAL_WIDGET]
  assert placed['size'] == 'ring'
  del placed['size']
  assert validate_document(document)['layouts']['large'][SIGNAL_WIDGET]['size'] == 'ring'
  placed.update(size='vertical')
  document = validate_document(document)
  assert widget_size(document, 'large', SIGNAL_WIDGET) == SIGNAL_SIZES['vertical']
  document['layouts']['large'][SIGNAL_WIDGET]['size'] = 'square'
  with pytest.raises(ValueError):
    validate_document(document)


def test_starport_signal_style_is_validated_and_pulled_on_screen():
  from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, validate_layout_for_viewport
  from openpilot.starpilot.ui.onroad_customization import SIGNAL_SIZES
  layout = default_layout_for_viewport((1920, 1080))
  del layout['widgets'][SIGNAL_WIDGET]['size']
  assert validate_layout_for_viewport(layout, (1920, 1080))['widgets'][SIGNAL_WIDGET]['size'] == 'ring'
  layout['widgets'][SIGNAL_WIDGET].update(size='horizontal', x=1920 - 30 - SIGNAL_SIZES['ring'][0])
  placed = validate_layout_for_viewport(layout, (1920, 1080))['widgets'][SIGNAL_WIDGET]
  assert placed['x'] == 1920 - 30 - SIGNAL_SIZES['horizontal'][0]
  layout['widgets'][SIGNAL_WIDGET]['size'] = 'big'
  with pytest.raises(ValueError):
    validate_layout_for_viewport(layout, (1920, 1080))
