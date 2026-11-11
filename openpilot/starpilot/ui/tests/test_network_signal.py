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


@pytest.mark.parametrize('style', ['ring', 'horizontal', 'vertical'])
def test_every_signal_style_shrinks_to_30_percent(style):
  from openpilot.starpilot.ui.onroad_customization import SIGNAL_SIZES, widget_size
  document = default_document()
  placed = document['layouts']['large'][SIGNAL_WIDGET]
  assert placed['scale'] == 100
  placed.update(size=style, scale=30)
  document = validate_document(document)
  width, height = SIGNAL_SIZES[style]
  assert widget_size(document, 'large', SIGNAL_WIDGET) == (round(width * .3), round(height * .3))
  for bad in (29, 101, 50.0):
    document['layouts']['large'][SIGNAL_WIDGET]['scale'] = bad
    with pytest.raises(ValueError):
      validate_document(document)


def test_older_signal_layouts_default_to_full_size():
  document = default_document()
  del document['layouts']['large'][SIGNAL_WIDGET]['scale']
  assert validate_document(document)['layouts']['large'][SIGNAL_WIDGET]['scale'] == 100


def test_starport_signal_scale_is_validated():
  from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, validate_layout_for_viewport
  from openpilot.starpilot.ui.onroad_customization import SIGNAL_SIZES
  layout = default_layout_for_viewport((1920, 1080))
  del layout['widgets'][SIGNAL_WIDGET]['scale']
  assert validate_layout_for_viewport(layout, (1920, 1080))['widgets'][SIGNAL_WIDGET]['scale'] == 100
  # At 30% the vertical card fits where the full-size one would not.
  layout['widgets'][SIGNAL_WIDGET].update(size='vertical', scale=30, y=1080 - 30 - round(SIGNAL_SIZES['vertical'][1] * .3))
  assert validate_layout_for_viewport(layout, (1920, 1080))['widgets'][SIGNAL_WIDGET]['y'] == layout['widgets'][SIGNAL_WIDGET]['y']
  layout['widgets'][SIGNAL_WIDGET]['scale'] = 20
  with pytest.raises(ValueError):
    validate_layout_for_viewport(layout, (1920, 1080))


def test_signal_draws_at_its_size_percentage():
  from types import SimpleNamespace as NS
  from unittest.mock import MagicMock
  from openpilot.starpilot.ui.onroad_customization import SIGNAL_SCALE
  from openpilot.starpilot.ui.onroad_signal import SignalWidget
  fonts = MagicMock()
  fonts.vertical_ink.return_value = (0, 10)
  fonts.measure.return_value = NS(width=20)
  widget = SignalWidget(fonts)
  widget.preview = Signal(2, 4, 'wifi')
  document = default_document()
  document['layouts']['large'][SIGNAL_WIDGET].update(enabled=True, scale=30)
  with patch('openpilot.starpilot.ui.onroad_signal.rl') as rl:
    widget.render(NS(customization=document, alert=NS(size='none')))
  assert rl.draw_circle_v.call_args_list[0].args[1] == pytest.approx(44 * SIGNAL_SCALE * .3)
  assert all(call.args[2] == pytest.approx(14 * SIGNAL_SCALE * .3) for call in fonts.draw.call_args_list)
