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
    assert '1 Muted' in draws and not any('THREATS' in text or 'Tap' in text for text in draws)
    assert draws['1 Muted'][4] < 50 + 2, 'the mute note sits in the top edge of the card'
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
    assert '3 Muted  •  +2 more' in labels
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


def test_mute_note_names_quiet_ride_and_mute_memory():
  from openpilot.starpilot.ui.onroad_uniden import mute_note
  assert mute_note(parse_alerts(b'1,00,K,3,0,24.150,F,6')) == 'Q-Ride'
  assert mute_note(parse_alerts(b'1,00,K,3,0,24.150,F,3')) == 'Mute Memory'
  assert mute_note(parse_alerts(b'1,00,K,3,0,24.150,F,3&1,00,X,2,0,10.525,S,4')) == 'Mute Memory', 'learned and saved are both mute memory'
  assert mute_note(parse_alerts(b'1,00,K,3,0,24.150,F,2')) == 'Muted'
  assert mute_note(parse_alerts(b'1,00,K,3,0,24.150,F,6&1,00,X,2,0,10.525,S,3')) == 'Muted', 'mixed reasons read as plain muted'
  assert mute_note(parse_alerts(b'1,00,KA,6,0,35.500,F,1&1,00,K,3,0,24.150,F,6')) == '1 Q-Ride'
  assert mute_note(parse_alerts(b'1,00,KA,6,0,35.500,F,1')) == ''


def test_custom_filters_hide_only_matching_rows():
  from openpilot.starpilot.ui.onroad_uniden import shown_alerts
  alerts = parse_alerts(b'1,00,KA,3,0,34.313,F,1&1,00,KA,3,0,34.7,R,1')
  alerts[0]['filtered'] = True
  assert shown_alerts(alerts, {'hide_filtered': True}) == alerts[1:]
  assert shown_alerts(alerts, {'hide_filtered': False}) == alerts


def test_hidden_widget_keeps_runtime_feed_active():
  feed = Mock()
  widget = UnidenWidget(Mock(), feed)
  widget.runtime_active = True
  state = NS(alert=NS(size='full'), customization={'layouts': {'large': {}}})
  widget.render(state)
  feed.set_enabled.assert_called_once_with(True)


def border_state(**customization):
  return NS(alert=NS(size='full'), customization=customization)


def border_data(packet, **config):
  solid = {'KA': 'solid', 'K': 'solid', 'LASER': 'solid'}
  return {'config': {'enabled': True, 'border_bands': ['KA', 'LASER'], 'border_styles': solid, **config}, 'connected': True,
          'alerts': parse_alerts(packet)}


def test_border_frames_the_view_over_an_openpilot_alert_in_the_chosen_color():
  feed = Mock()
  widget = UnidenWidget(Mock(), feed)
  feed.snapshot.return_value = border_data(b'1,00,KA,3,0,33.785,R,1')
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_lines_ex') as frame:
    widget.render_border(border_state(), 1860, 1080)
    rectangle, width, color = frame.call_args.args
    assert (rectangle.x, rectangle.width, width) == (11, 1860 - 22, 8) and (color.r, color.g, color.b, color.a) == (0xB0, 0x26, 0xFF, 255)
    feed.snapshot.return_value = border_data(b'1,00,KA,3,0,33.785,R,1', border_colors={'KA': 'violet'})
    widget.render_border(border_state(), 1860, 1080)
    assert (frame.call_args.args[2].r, frame.call_args.args[2].g) == (0x8F, 0x7B)


@pytest.mark.parametrize('packet,config', [
  (b'1,00,K,3,0,24.150,F,1', {}),
  (b'1,00,KA,3,0,33.785,R,2', {}),
  (b'0', {}),
])
def test_border_ignores_unchosen_muted_or_absent_alerts(packet, config):
  feed = Mock()
  feed.snapshot.return_value = border_data(packet, **config)
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_lines_ex') as frame:
    UnidenWidget(Mock(), feed).render_border(border_state(), 1860, 1080)
    frame.assert_not_called()


def test_border_skips_locked_out_and_quiet_alerts_and_prefers_laser_and_k_when_chosen():
  feed = Mock()
  widget = UnidenWidget(Mock(), feed)
  data = border_data(b'1,00,K,3,0,24.150,F,1', border_bands=['K'])
  data['alerts'][0]['filtered'] = True
  feed.snapshot.return_value = data
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_lines_ex') as frame:
    widget.render_border(border_state(), 1860, 1080)
    data['alerts'][0].update(filtered=False, quiet=True)
    widget.render_border(border_state(), 1860, 1080)
    frame.assert_not_called()
    data['alerts'][0]['quiet'] = False
    widget.render_border(border_state(), 1860, 1080)
    assert (frame.call_args.args[2].r, frame.call_args.args[2].g) == (0xE3, 0xFF)
    data = border_data(b'1,00,K,3,0,24.150,F,1&1,00,LASER,8,0,5,F,1', border_bands=['K', 'LASER'])
    feed.snapshot.return_value = data
    widget.render_border(border_state(reduceMotion=True), 1860, 1080)
    assert (frame.call_args.args[2].r, frame.call_args.args[2].g) == (0xFF, 0xFF)


def test_laser_frame_pulses_unless_motion_is_reduced():
  feed = Mock()
  feed.snapshot.return_value = border_data(b'1,00,LASER,8,0,5,F,1', border_styles={'KA': 'solid', 'K': 'solid', 'LASER': 'pulse'})
  widget = UnidenWidget(Mock(), feed)
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_lines_ex') as frame, \
       patch('openpilot.starpilot.ui.onroad_uniden.time.monotonic', side_effect=[0., 1 / 6 + .01]):
    widget.render_border(border_state(), 1860, 1080)
    widget.render_border(border_state(), 1860, 1080)
    assert [call.args[2].a for call in frame.call_args_list] == [255, 110]
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_lines_ex') as frame, \
       patch('openpilot.starpilot.ui.onroad_uniden.time.monotonic', return_value=1 / 6 + .01):
    widget.render_border(border_state(reduceMotion=True), 1860, 1080)
    assert frame.call_args.args[2].a == 255


def test_holding_an_alert_locks_it_out_once_and_a_tap_still_mutes():
  feed = Mock()
  data = {'config': {'enabled': True}, 'connected': True, 'can_mute': True, 'address': 'detector', 'model': 'R8',
          'alerts': parse_alerts(b'1,00,KA,3,0,33.785,R,1')}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts_mock(), feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  widget.visible_until = time.monotonic() + 10
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle'):
    widget.touch('down', 40, 60, state)
    widget.render(state)
    feed.lock_here.assert_not_called()
    widget.pressed = (*widget.pressed[:3], time.monotonic() - 1)
    widget.render(state)
    widget.render(state)
    feed.lock_here.assert_called_once_with(data)
    widget.touch('up', 40, 60, state)
    feed.mute.assert_not_called()
    widget.touch('down', 40, 60, state)
    widget.touch('up', 40, 60, state)
    feed.mute.assert_called_once_with(data)


def test_offline_detector_shows_a_badge_even_when_idle_and_notices_replace_the_note():
  feed, fonts = Mock(), fonts_mock()
  data = {'config': {'enabled': True, 'hide_when_no_alert': True}, 'connected': False, 'alerts': [], 'offline': True}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts, feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle'):
    widget.render(state)
    assert [call.args[0] for call in fonts.draw.call_args_list] == ['DETECTOR OFFLINE']
    fonts.reset_mock()
    feed.snapshot.return_value = {**data, 'offline': False, 'connected': True, 'model': 'R8', 'can_mute': True, 'notice': 'Locked out here (1)',
                                  'alerts': parse_alerts(b'1,00,KA,3,0,33.785,R,1')}
    widget.render(state)
    assert 'Locked out here (1)' in [call.args[0] for call in fonts.draw.call_args_list]
    fonts.reset_mock()
    quiet = parse_alerts(b'1,00,K,3,0,24.150,F,1')
    quiet[0]['quiet'] = True
    feed.snapshot.return_value = {**data, 'offline': False, 'connected': True, 'model': 'R8', 'alerts': quiet}
    widget.render(state)
    assert 'Quiet' in [call.args[0] for call in fonts.draw.call_args_list]


def test_offline_badge_moves_to_the_top_of_the_view_when_the_widget_is_off_or_covered():
  feed, fonts = Mock(), fonts_mock()
  feed.snapshot.return_value = {'config': {'enabled': True}, 'connected': False, 'alerts': [], 'offline': True}
  widget = UnidenWidget(fonts, feed)
  covered = NS(alert=NS(size='full'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'):
    widget.render_border(covered, 1860, 1080)
    assert [call.args[0] for call in fonts.draw.call_args_list] == ['DETECTOR OFFLINE']
    assert fonts.draw.call_args.args[3] == (1860 - 330) / 2 + 24
    fonts.reset_mock()
    shown = NS(alert=NS(size='none'), customization=covered.customization)
    widget.render_border(shown, 1860, 1080)
    fonts.draw.assert_not_called()


def test_onroad_view_draws_the_border_after_the_rest_of_the_frame():
  from openpilot.starpilot.ui.onroad import OnroadView
  from openpilot.starpilot.ui.presentation import Profile
  order = []
  view = object.__new__(OnroadView)
  view.uniden = Mock(runtime_active=False)
  view.uniden.render_border.side_effect = lambda *args: order.append(('border', args))
  view.fonts, view.camera_layer, view.projection_viewport = NS(profile=Profile.LARGE), None, (1500, 1000)
  view._large = lambda state: order.append('large')
  view.render(NS(camera_available=False, viewport_width=1860))
  assert order[0] == 'large' and order[1][0] == 'border' and order[1][1][1:] == (1500, 1000)


def test_perimeter_spans_run_clockwise_and_wrap():
  from openpilot.starpilot.ui.onroad_uniden import perimeter_spans
  assert perimeter_spans(100, 50, 0, 40) == [('top', 0, 40)]
  assert perimeter_spans(100, 50, 80, 60) == [('top', 80, 100), ('right', 0, 40)]
  assert perimeter_spans(100, 50, 290, 30) == [('top', 0, 20), ('left', 40, 50)]
  assert sum(end - begin for _, begin, end in perimeter_spans(100, 50, 140, 150)) == 150


def test_side_rects_sit_on_their_own_edge():
  from openpilot.starpilot.ui.onroad_uniden import side_rect
  frame = NS(x=10, y=20, width=100, height=50)
  top, right = side_rect(frame, 8, 'top', 0, 30), side_rect(frame, 8, 'right', 0, 30)
  bottom, left = side_rect(frame, 8, 'bottom', 0, 30), side_rect(frame, 8, 'left', 0, 30)
  assert (top.x, top.y, top.width, top.height) == (10, 20, 30, 8)
  assert (right.x, right.y, right.width, right.height) == (102, 20, 8, 30)
  assert (bottom.x, bottom.y, bottom.width, bottom.height) == (80, 62, 30, 8)
  assert (left.x, left.y, left.width, left.height) == (10, 40, 8, 30)


def style_run(styles, colors, packet, reduce_motion=False, moments=(0., .5)):
  feed = Mock()
  feed.snapshot.return_value = border_data(packet, border_styles=styles, border_colors=colors, border_bands=['KA', 'K', 'LASER'])
  widget = UnidenWidget(Mock(), feed)
  rectangles, lines = [], []
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rec', side_effect=lambda r, c: rectangles.append((r, c))), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_lines_ex', side_effect=lambda r, t, c: lines.append(c)), \
       patch('openpilot.starpilot.ui.onroad_uniden.time.monotonic', side_effect=list(moments)):
    for _ in moments:
      widget.render_border(border_state(reduceMotion=reduce_motion), 1860, 1080)
  return widget, rectangles, lines


ALL = {'KA': 'solid', 'K': 'solid', 'LASER': 'solid'}
HUES = {'KA': 'yellow', 'K': 'violet', 'LASER': 'purple'}


def test_corners_draw_four_brackets_in_the_band_color():
  _, rectangles, lines = style_run({**ALL, 'LASER': 'corners'}, HUES, b'1,00,LASER,8,0,5,F,1', moments=(0.,))
  assert len(rectangles) == 8 and not lines
  assert all((c.r, c.g, c.b) == (0xB0, 0x26, 0xFF) for _, c in rectangles)
  assert {(r.x, r.y) for r, _ in rectangles} >= {(11, 11), (11, 1080 - 11 - 8), (1860 - 11 - 8, 11)}


def test_chase_draws_a_dim_ring_with_a_bright_segment_that_advances_and_follows_strength():
  slow, fast = {}, {}
  for table, strength in ((slow, 1), (fast, 8)):
    widget, rectangles, lines = style_run({**ALL, 'KA': 'chase'}, HUES, f'1,00,KA,{strength},0,33.785,R,1'.encode(), moments=(1., 1.1, 1.2))
    table['phase'], table['lines'], table['rects'] = widget.chase[0], lines, rectangles
  assert fast['phase'] > slow['phase'] > 0
  assert all(c.a == 70 for c in slow['lines']) and slow['rects'] and all(c.a == 255 and (c.r, c.g) == (0xE3, 0xFF) for _, c in slow['rects'])


def test_chase_and_pulse_fall_back_to_a_solid_frame_when_motion_is_reduced():
  for style in ('chase', 'pulse'):
    _, rectangles, lines = style_run({**ALL, 'KA': style}, HUES, b'1,00,KA,3,0,33.785,R,1', reduce_motion=True, moments=(0.1, .3))
    assert not rectangles and [c.a for c in lines] == [255, 255]


def test_each_band_has_its_own_color_and_style():
  _, ka_rects, _ = style_run({'KA': 'pulse', 'K': 'corners', 'LASER': 'corners'}, {'KA': 'yellow', 'K': 'purple', 'LASER': 'violet'},
                             b'1,00,LASER,8,0,5,F,1', moments=(0.,))
  assert len(ka_rects) == 8 and all((c.r, c.g, c.b) == (0x8F, 0x7B, 0xFF) for _, c in ka_rects)
  _, rectangles, lines = style_run({'KA': 'pulse', 'K': 'corners', 'LASER': 'corners'}, {'KA': 'yellow', 'K': 'purple', 'LASER': 'violet'},
                                   b'1,00,KA,3,0,33.785,R,1', moments=(0., .2))
  assert not rectangles and [(c.r, c.g, c.a) for c in lines] == [(0xE3, 0xFF, 255), (0xE3, 0xFF, 110)]


def test_direction_style_lights_the_edge_the_signal_comes_from():
  styles = {**ALL, 'KA': 'direction'}
  for letter, edges in (('F', {(11, 11)}), ('R', {(11, 1080 - 11 - 8)}), ('S', {(11, 11), (1860 - 11 - 8, 11)})):
    _, rectangles, lines = style_run(styles, HUES, f'1,00,KA,3,0,33.785,{letter},1'.encode(), moments=(0.,))
    assert {(r.x, r.y) for r, _ in rectangles} == edges and [c.a for c in lines] == [70]
    assert all((c.r, c.g, c.a) == (0xE3, 0xFF, 255) for _, c in rectangles)


def test_direction_style_chases_when_the_detector_gives_no_direction():
  feed = Mock()
  data = border_data(b'1,00,K,4,0,24.150,F,1', border_styles={**ALL, 'K': 'direction'}, border_bands=['K'])
  data['alerts'] = parse_alerts(b'1,00,K,4,0,24.150,F,1', 'R4')
  feed.snapshot.return_value = data
  widget = UnidenWidget(Mock(), feed)
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rec') as rectangles, \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_lines_ex'), \
       patch('openpilot.starpilot.ui.onroad_uniden.time.monotonic', side_effect=[1., 1.1]):
    widget.render_border(border_state(), 1860, 1080)
    widget.render_border(border_state(), 1860, 1080)
  assert widget.chase[0] > 0 and rectangles.called


def test_a_stronger_signal_lights_more_of_the_outline():
  from openpilot.starpilot.ui.onroad_uniden import chase_lap, chase_share
  assert chase_share(1) < chase_share(8) < .3 and chase_lap(8) < chase_lap(1)
  def lit(strength):
    _, rectangles, _ = style_run({**ALL, 'KA': 'chase'}, HUES, f'1,00,KA,{strength},0,33.785,R,1'.encode(), moments=(1., 1.))
    return sum(r.width * r.height for r, _ in rectangles) / 2
  assert lit(8) > lit(1)


def test_a_strong_timed_mute_signal_relights_the_frame_but_a_timed_one_does_not():
  from openpilot.starpilot.ui.onroad_uniden import threat
  alert = parse_alerts(b'1,00,K,7,0,24.150,F,2')[0]
  assert threat([{**alert, 'timed': True}], ['K']) is None
  assert threat([alert], ['K']) is None
  assert threat([{**alert, 'breakthrough': True}], ['K'])
  assert threat([{**alert, 'breakthrough': True, 'quiet': True}], ['K']) is None


def test_card_note_names_timed_mute_and_strong_signals():
  feed, fonts = Mock(), fonts_mock()
  widget = UnidenWidget(fonts, feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  def notes(**flags):
    items = parse_alerts(b'1,00,K,3,0,24.150,F,2')
    items[0].update(flags)
    feed.snapshot.return_value = {'config': {'enabled': True}, 'connected': True, 'model': 'R8', 'alerts': items}
    fonts.reset_mock()
    widget.render(state)
    return [call.args[0] for call in fonts.draw.call_args_list]
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle'):
    assert 'Timed mute' in notes(timed=True)
    assert 'Strong signal' in notes(breakthrough=True, timed=False)
