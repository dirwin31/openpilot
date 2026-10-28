import time
import pytest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.onroad_uniden import UnidenWidget
from openpilot.starpilot.uniden.protocol import parse_alerts



@pytest.fixture(autouse=True)
def outline_guard(monkeypatch):
  monkeypatch.setattr('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded_lines_ex', Mock())


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
  feed, fonts = Mock(), Mock()
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
  feed, fonts = Mock(), Mock()
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


def test_multi_threat_rows_overflow_arrows_and_expanded_touch_bounds():
  from openpilot.starpilot.ui.onroad_uniden import card_size
  feed, fonts = Mock(), Mock()
  alerts = parse_alerts(b'1,00,KA,6,0,35.500,R,1&1,00,K,3,0,24.150,F,2')
  data = {'config': {'enabled': True}, 'connected': True, 'can_mute': True, 'model': 'R8', 'alerts': alerts}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts, feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle') as arrows:
    widget.render(state)
    labels = [call.args[0] for call in fonts.draw.call_args_list]
    assert 'UNIDEN R8  •  2 THREATS' in labels
    assert '35.500 GHz' in labels and '24.150 GHz' in labels
    assert 'Live' not in labels and 'Muted' in labels
    assert arrows.call_count == 2
    assert widget.touch('down', 620, 210, state)
    assert widget.touch('up', 620, 210, state)
    feed.mute.assert_called_once_with(data)
    assert not widget.touch('down', 620, 230, state)
    data['alerts'] = alerts * 3
    fonts.reset_mock()
    widget.render(state)
    labels = [call.args[0] for call in fonts.draw.call_args_list]
    assert '+2 more' in labels
    assert labels.count('35.500 GHz') == 2
    assert card_size(data['alerts']) == (620, 260)
    data['model'] = 'R4'
    arrows.reset_mock()
    widget.render(state)
    arrows.assert_not_called()


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
  from openpilot.starpilot.system.android_auto.projection_layout import (
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
    for field, invalid in (('transparency', True), ('transparency', 101), ('hide_when_no_alert', 1)):
      old = placement[field]
      placement[field] = invalid
      with pytest.raises(ValueError):
        validate(document)
      placement[field] = old
    placement.pop('transparency')
    placement.pop('hide_when_no_alert')
    result = validate(document)
    migrated = result['widgets']['uniden_alerts'] if 'widgets' in result else result['layouts']['large']['uniden_alerts']
    assert migrated['transparency'] == 8
    assert migrated['hide_when_no_alert'] is True


@pytest.mark.parametrize(('direction', 'count'), [('front', 1), ('rear', 1), ('side', 2), ('', 0)])
def test_only_active_direction_arrows_are_drawn(direction, count):
  from openpilot.starpilot.ui.onroad_uniden import direction_arrows
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle') as arrows:
    direction_arrows(100, 100, direction, 'accent', 18)
    assert arrows.call_count == count
    assert all(call.args[-1] == 'accent' for call in arrows.call_args_list)
