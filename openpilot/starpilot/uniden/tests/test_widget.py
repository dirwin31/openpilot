import time
import pytest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.onroad_uniden import UnidenWidget
from openpilot.starpilot.uniden.protocol import parse_alerts



@pytest.fixture(autouse=True)
def outline_guard(monkeypatch):
  outline = Mock()
  monkeypatch.setattr('openpilot.starpilot.ui.onroad_uniden.outline', outline)
  return outline


def fonts_mock():
  fonts = Mock()
  fonts.measure.side_effect = lambda text, role, size: NS(width=len(text) * size * .55, height=size * 1.2)
  return fonts


def test_touch_requires_visible_fresh_card_and_cancels_drag_or_changed_alert():
  feed = Mock()
  data = {'connected': True, 'can_mute': True, 'address': 'detector', 'alerts': parse_alerts(b'1,00,KA,3,0,33.785,R,1')}
  feed.snapshot.return_value = data
  widget = UnidenWidget(Mock(), feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  assert not widget.touch('down', 40, 60, state)
  widget.visible_until = time.monotonic() + 10
  assert widget.touch('down', 40, 60, state)
  assert widget.touch('up', 42, 62, state)
  feed.mute.assert_called_once_with(data)
  feed.mute.reset_mock()
  widget.touch('down', 40, 60, state)
  widget.touch('move', 200, 60, state)
  widget.touch('up', 40, 60, state)
  feed.mute.assert_not_called()
  widget.touch('down', 40, 60, state)
  feed.snapshot.return_value = {**data, 'address': 'another'}
  widget.touch('up', 40, 60, state)
  feed.mute.assert_not_called()
  feed.snapshot.return_value = data
  widget.touch('down', 40, 60, state)
  state.alert.size = 'full'
  widget.touch('up', 40, 60, state)
  feed.mute.assert_not_called()


def test_preview_does_not_start_detector_io_and_r4_has_no_arrows():
  feed, fonts = Mock(), fonts_mock()
  feed.snapshot.return_value = {'config': {'enabled': True}, 'connected': True, 'model': 'R4',
                                'alerts': parse_alerts(b'1,00,K,3,0,24.150,F,1', 'R4')}
  widget = UnidenWidget(fonts, feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle') as arrows:
    widget.render(state)
    feed.set_enabled.assert_called_once_with(False)
    arrows.assert_not_called()
    feed.snapshot.return_value['model'] = 'R8'
    feed.snapshot.return_value['alerts'][0]['direction'] = 'front'
    widget.render(state)
    assert arrows.call_count == 1


def test_transparency_only_changes_background_and_quiet_toggle_controls_visibility():
  feed, fonts = Mock(), fonts_mock()
  widget = UnidenWidget(fonts, feed)
  data = {'config': {'enabled': True, 'transparency': 100, 'hide_when_no_alert': True}, 'connected': True, 'model': 'R8',
          'alerts': parse_alerts(b'1,00,KA,3,0,33.785,R,1'), 'can_mute': True}
  feed.snapshot.return_value = data
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded') as rounded, \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle') as arrows:
    for transparency, alpha in ((0, 255), (50, 128), (100, 0)):
      rounded.reset_mock()
      data['config']['transparency'] = transparency
      widget.render(state)
      assert rounded.call_args_list[0].args[-1].a == alpha
      assert all(call.args[-1].a == 255 for call in fonts.draw.call_args_list)
      assert all(call.args[-1].a == 255 for call in arrows.call_args_list)
    data['alerts'] = []
    for connected in (True, False):
      data['connected'] = connected
      for hide in (True, False):
        data['config']['hide_when_no_alert'] = hide
        fonts.reset_mock()
        rounded.reset_mock()
        widget.render(state)
        if hide:
          fonts.draw.assert_not_called()
          rounded.assert_not_called()
          assert widget.visible_until == 0
        else:
          labels = [call.args[0] for call in fonts.draw.call_args_list]
          assert ('Detector ready' if connected else 'Detector disconnected') in labels
        assert not widget.touch('down', 40, 60, state)
    feed.mute.assert_not_called()


def test_one_row_per_threat_overflow_arrows_and_touch_bounds():
  from openpilot.starpilot.ui.onroad_uniden import card_size
  feed, fonts = Mock(), fonts_mock()
  alerts = parse_alerts(b'1,00,KA,6,0,35.500,R,1&1,00,K,3,0,24.150,F,2')
  data = {'config': {'enabled': True}, 'connected': True, 'can_mute': True, 'model': 'R8', 'alerts': alerts}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts, feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  assert card_size(alerts[:1]) == (460, 76), 'a single threat is one slim row'
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle') as arrows:
    widget.render(state)
    draws = {call.args[0]: call.args for call in fonts.draw.call_args_list}
    assert '35.500 GHz' in draws and '24.150 GHz' in draws
    assert '1 muted' in draws and not any('THREATS' in text or 'Tap' in text for text in draws)
    assert draws['1 muted'][4] < 50 + 2, 'the mute note sits in the top edge of the card'
    band, frequency = draws['KA'], draws['35.500 GHz']
    assert abs((band[4] + 36 * .6) - (frequency[4] + 28 * .6)) < 1, 'band and frequency share one centre line'
    assert arrows.call_count == 2
    assert widget.touch('down', 480, 170, state)
    assert widget.touch('up', 480, 170, state)
    feed.mute.assert_called_once_with(data)
    assert not widget.touch('down', 480, 190, state)
    data['alerts'] = alerts * 3
    fonts.reset_mock()
    widget.render(state)
    labels = [call.args[0] for call in fonts.draw.call_args_list]
    assert '3 muted  •  +2 more' in labels
    assert labels.count('35.500 GHz') == 2
    assert card_size(data['alerts']) == (460, 256)
    data['model'] = 'R4'
    arrows.reset_mock()
    widget.render(state)
    arrows.assert_not_called()


def test_all_muted_says_so_in_the_outline(outline_guard):
  feed, fonts = Mock(), fonts_mock()
  data = {'config': {'enabled': True}, 'connected': True, 'model': 'R8', 'alerts': parse_alerts(b'1,00,KA,3,0,33.785,R,2')}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts, feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle'):
    widget.render(state)
    assert 'Muted' in [call.args[0] for call in fonts.draw.call_args_list]
    gap = outline_guard.call_args.args[5]
    assert gap[0] < 30 + 24 < gap[1], 'the outline opens around the note'
    data['alerts'] = parse_alerts(b'1,00,KA,3,0,33.785,R,1')
    fonts.reset_mock()
    widget.render(state)
    assert 'Muted' not in [call.args[0] for call in fonts.draw.call_args_list]
    assert outline_guard.call_args.args[5:] == (None,)


def test_theme_display_settings_override_legacy_bluetooth_settings():
  feed, fonts = Mock(), Mock()
  feed.snapshot.return_value = {'config': {'enabled': True, 'transparency': 0, 'hide_when_no_alert': True},
                                'connected': True, 'alerts': []}
  widget = UnidenWidget(fonts, feed)
  placement = {'enabled': True, 'x': 30, 'y': 50, 'transparency': 75, 'hide_when_no_alert': False}
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': placement}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded') as rounded:
    widget.render(state)
    assert rounded.call_args_list[0].args[-1].a == 64
    assert 'Detector ready' in [call.args[0] for call in fonts.draw.call_args_list]
    placement['hide_when_no_alert'] = True
    rounded.reset_mock()
    widget.render(state)
    rounded.assert_not_called()


def test_theme_settings_roundtrip_migration_and_projection_isolation():
  import pytest
  from openpilot.starpilot.ui.onroad_customization import default_document, validate_document
  from openpilot.starpilot.system.starport.projection_layout import (
    default_layout_for_viewport, validate_layout_for_viewport, projection_customization)
  native = default_document()
  pos = native['layouts']['large']['uniden_alerts']
  pos.update(transparency=41, hide_when_no_alert=False)
  assert validate_document(native)['layouts']['large']['uniden_alerts'] == pos
  viewport = (1860, 1080)
  projected = default_layout_for_viewport(viewport)
  other = projected['widgets']['uniden_alerts']
  other.update(transparency=82, hide_when_no_alert=True)
  saved = validate_layout_for_viewport(projected, viewport)
  assert projection_customization(saved, native)['layouts']['large']['uniden_alerts']['transparency'] == 82
  assert native['layouts']['large']['uniden_alerts']['transparency'] == 41
  for document, placement, validate in ((native, pos, validate_document),
                                        (projected, other, lambda value: validate_layout_for_viewport(value, viewport))):
    for field, invalid in (('transparency', True), ('transparency', 101), ('hide_when_no_alert', 1), ('hide_quiet_ride', 1),
                           ('hide_mute_memory', None), ('scale', 49), ('scale', 151), ('scale', 1.0)):
      old = placement[field]
      placement[field] = invalid
      with pytest.raises(ValueError):
        validate(document)
      placement[field] = old
    for field in ('transparency', 'hide_when_no_alert', 'hide_quiet_ride', 'hide_mute_memory', 'scale'):
      placement.pop(field)
    result = validate(document)
    migrated = result['widgets']['uniden_alerts'] if 'widgets' in result else result['layouts']['large']['uniden_alerts']
    assert migrated['transparency'] == 8
    assert migrated['hide_when_no_alert'] is True
    assert (migrated['hide_quiet_ride'], migrated['hide_mute_memory'], migrated['scale']) == (False, False, 100)


@pytest.mark.parametrize(('direction', 'count'), [('front', 1), ('rear', 1), ('side', 2), ('', 0)])
def test_only_active_direction_arrows_are_drawn(direction, count):
  from openpilot.starpilot.ui.onroad_uniden import direction_arrows
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle') as arrows:
    direction_arrows(100, 100, direction, 'accent', 18)
    assert arrows.call_count == count
    assert all(call.args[-1] == 'accent' for call in arrows.call_args_list)


def test_quiet_ride_and_mute_memory_alerts_can_be_left_off_the_card():
  from openpilot.starpilot.ui.onroad_uniden import shown_alerts
  alerts = parse_alerts(b'1,00,KA,6,0,35.500,F,1&1,00,K,3,0,24.150,F,6&1,00,K,2,0,24.125,R,3&1,00,X,2,0,10.525,S,4&1,00,K,4,0,24.200,F,2')
  assert sorted(a['mute_mode'] for a in alerts) == ['', 'auto_memory', 'manual', 'memory', 'quiet_ride']
  assert shown_alerts(alerts, {}) == alerts
  assert {a['mute_mode'] for a in shown_alerts(alerts, {'hide_quiet_ride': True})} == {'', 'memory', 'auto_memory', 'manual'}
  assert {a['mute_mode'] for a in shown_alerts(alerts, {'hide_mute_memory': True})} == {'', 'quiet_ride', 'manual'}
  feed, fonts = Mock(), fonts_mock()
  quiet = parse_alerts(b'1,00,K,3,0,24.150,F,6')
  data = {'config': {'enabled': True}, 'connected': True, 'can_mute': True, 'model': 'R8', 'alerts': quiet}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts, feed)
  placement = {'enabled': True, 'x': 30, 'y': 50, 'hide_quiet_ride': True, 'hide_when_no_alert': True}
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': placement}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded') as rounded:
    widget.render(state)
    rounded.assert_not_called()
    assert widget.visible_until == 0 and not widget.touch('down', 40, 60, state), 'a hidden Quiet Ride alert takes no taps'
    placement['hide_when_no_alert'] = False
    widget.render(state)
    assert 'No active alerts' in [call.args[0] for call in fonts.draw.call_args_list]


def test_scale_grows_the_card_and_its_touch_area():
  from openpilot.starpilot.ui.onroad_uniden import card_scale
  assert (card_scale({}), card_scale({'scale': 150}), card_scale({'scale': 500}), card_scale({'scale': 'big'})) == (1., 1.5, 1.5, 1.)
  feed, fonts = Mock(), fonts_mock()
  data = {'config': {'enabled': True}, 'connected': True, 'can_mute': True, 'model': 'R8',
          'alerts': parse_alerts(b'1,00,KA,6,0,35.500,F,1')}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts, feed)
  placement = {'enabled': True, 'x': 30, 'y': 50, 'scale': 150}
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': placement}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl') as rl:
    widget.render(state)
    rl.rl_translatef.assert_called_once_with(30, 50, 0)
    rl.rl_scalef.assert_called_once_with(1.5, 1.5, 1)
    assert rl.rl_pop_matrix.call_count == 1
  assert widget.touch('down', 30 + 460 * 1.5 - 5, 50 + 76 * 1.5 - 5, state)
  widget.touch('cancel', 0, 0, state)
  placement['scale'] = 100
  assert not widget.touch('down', 30 + 460 * 1.5 - 5, 50 + 76 * 1.5 - 5, state)
