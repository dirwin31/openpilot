"""The "3D drive render" choice: its saved field, and the comma 3 / 3X screen drawing the render in the camera's place."""
from collections import namedtuple
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.ui import drive_render, onroad, onroad_drive_render
from openpilot.starpilot.ui.onroad_customization import default_document, fit_to_road, validate_document
from openpilot.starpilot.ui.developer_preview import OnroadVisualPreview
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert, OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.presentation import Profile


Rect = namedtuple('Rect', 'x y width height')


def state(choice=None, **changes):
  document = default_document()
  if choice is not None:
    document[drive_render.FIELD] = choice
  return OnroadState(True, True, 20, 80, SpeedLimitObservation(), alert=OnroadAlert('Take Control', 'x', AlertSize.MID),
                     customization=document, **changes)


def view(profile=Profile.LARGE, *, ready=True, projection=None):
  view = object.__new__(onroad.OnroadView)
  view.fonts = NS(profile=profile)
  view.projection_viewport = projection
  view.camera_layer = Mock()
  view.alert = Mock()
  view.drive_render = Mock()
  view.drive_render.ready.return_value = ready
  view._large, view._compact = Mock(), Mock()
  return view


def test_default_is_off_and_absent_from_the_default_document():
  assert drive_render.DEFAULT == drive_render.OFF
  assert drive_render.FIELD not in default_document()
  assert drive_render.normalize(default_document().get(drive_render.FIELD)) == 'off'
  for bad in (None, '', 'Off', 'camera', 1, True, ['device']):
    assert drive_render.normalize(bad) == 'off'


def test_device_only_acts_on_the_large_profile():
  assert drive_render.effective('device', True) == 'device'
  assert drive_render.effective('device', False) == 'off'
  for value in ('off', 'starport'):
    assert drive_render.effective(value, True) == drive_render.effective(value, False) == value
  assert drive_render.choices(True) == ('off', 'starport', 'device')
  assert drive_render.choices(False) == ('off', 'starport')
  assert 'Comma display' in drive_render.description(True) and 'Comma display' not in drive_render.description(False)
  assert all('next connection' in drive_render.description(large) for large in (True, False))


@pytest.mark.parametrize('choice, device, starport', [
  (None, False, False), ('off', False, False), ('starport', False, True), ('device', True, False), ('both', False, False)])
def test_each_choice_names_its_screens(choice, device, starport):
  assert drive_render.on_device(choice) is device
  assert drive_render.on_starport(choice) is starport
  assert drive_render.on_device(choice, large_profile=False) is False


@pytest.mark.parametrize('choice', drive_render.VALUES)
def test_document_keeps_a_valid_choice(choice):
  document = default_document()
  document[drive_render.FIELD] = choice
  validated = validate_document(document)
  assert validated[drive_render.FIELD] == choice
  assert fit_to_road(validated, 1560)[drive_render.FIELD] == choice


@pytest.mark.parametrize('bad', ['', 'Off', 'camera', 'both', None, True, 1, ['device']])
def test_document_rejects_an_unknown_choice(bad):
  document = default_document()
  document[drive_render.FIELD] = bad
  with pytest.raises(ValueError, match='3D drive render'):
    validate_document(document)


def test_read_needs_only_the_param_path(tmp_path):
  import json
  path = tmp_path / 'OnroadCustomizations'
  params = NS(get_param_path=lambda key: str(path))
  assert drive_render.read(params) == 'off'
  for choice in drive_render.VALUES:
    path.write_text(json.dumps({**default_document(), drive_render.FIELD: choice}))
    assert drive_render.read(params) == choice
  path.write_text(json.dumps({**default_document(), drive_render.FIELD: 'camera'}))
  assert drive_render.read(params) == 'off'
  path.write_text('{broken')
  assert drive_render.read(params) == 'off'


@pytest.mark.parametrize('choice', ['device'])
def test_large_device_choice_keeps_the_composition_and_swaps_only_the_camera(choice):
  v = view()
  assert v.drive_render_active(state(choice))
  v.render(state(choice))
  v._large.assert_called_once()
  from inspect import getsource
  source = getsource(onroad.OnroadView._large)
  assert source.index('self.drive_render_active(state)') < source.index('self.camera_layer(content, state)') < \
         source.index('self._ordered_widgets(content, state)'), 'the render takes the camera slot, the widgets stay above'


@pytest.mark.parametrize('choice', [None, 'off', 'starport', 'camera'])
def test_other_choices_keep_the_camera(choice):
  v = view()
  assert not v.drive_render_active(state(choice))
  v.drive_render.ready.assert_not_called()


def test_compact_profile_ignores_a_saved_device_choice():
  v = view(Profile.COMPACT)
  assert not v.drive_render_active(state('device'))
  v.render(state('device'))
  v._compact.assert_called_once()


def test_projection_preview_reverse_camera_and_failed_build_keep_the_camera():
  for v, current in ((view(projection=(1860, 1080)), state('device')),
                     (view(), state('device', visual_preview=OnroadVisualPreview(layout_sample=True))),
                     (view(), state('device', reverse_driver_camera=True)),
                     (view(ready=False), state('device'))):
    assert not v.drive_render_active(current)
  unset = view()
  del unset.drive_render
  assert not unset.drive_render_active(state('device'))


def test_layer_that_raises_while_drawing_gives_the_camera_back():
  render_view = Mock()
  render_view.render.side_effect = RuntimeError('bad frame')
  layer = onroad_drive_render.DriveRenderLayer('fonts', NS(), factory=Mock(return_value=render_view))
  assert layer.ready()
  with patch('openpilot.common.swaglog.cloudlog'):
    layer.render(NS(x=30, y=30, width=1800, height=1020), state('device'))
  assert not layer.ready() and layer.view is None


def test_layer_builds_once_and_fills_the_content_with_the_view():
  render_view = Mock()
  factory = Mock(return_value=render_view)
  ui = NS(has_longitudinal_control=False)
  layer = onroad_drive_render.DriveRenderLayer('fonts', ui, factory=factory)
  factory.assert_not_called()
  assert layer.ready() and layer.ready()
  factory.assert_called_once_with('fonts', ui)
  current = state('device', metric=False)
  current.customization['roadColors']['large'] = {'path': '#11223344'}
  content = NS(x=30, y=30, width=1800, height=1020)
  layer.render(content, current)
  render_view.render.assert_called_once_with(content)
  assert (render_view.panel, render_view.opacity, render_view.metric, render_view.long_control) == (False, 1.0, False, False)
  assert render_view.speed_limit_sign is False, "the layout's speed widget shows the limit"
  assert render_view.road_style == {'path': '#11223344'} and render_view.parent_clip is content
  layer.close()
  assert layer.view is None


def test_layer_remembers_a_failed_build():
  factory = Mock(side_effect=OSError('no native library'))
  layer = onroad_drive_render.DriveRenderLayer('fonts', NS(), factory=factory)
  with patch('openpilot.common.swaglog.cloudlog'):
    assert not layer.ready() and not layer.ready()
  factory.assert_called_once()
  layer.close()


def test_native_large_session_wires_the_layer_and_keeps_widgets_live():
  from pathlib import Path
  source = Path(onroad.__file__).with_name('runtime_app.py').read_text()
  assert 'self.view.onroad.drive_render = DriveRenderLayer(self.fonts, ui_state)' in source
  assert '_drive_render_active' not in source, 'widgets, taps and PiP stay live over the render'


def test_hidden_widgets_take_no_taps():
  from openpilot.starpilot.ui import onroad_state
  current = replace(state('device'), experimental_available=True, alert=OnroadAlert())
  for target in ('personality', 'wheel'):
    personality = target == 'personality'
    drawn, hidden = onroad_state.OnroadInput(Mock()), onroad_state.OnroadInput(Mock())
    hidden.widgets_hidden = lambda _state: True
    bounds = (0, 0, 100, 100) if personality else None
    with patch.object(onroad_state, 'personality_bounds', return_value=bounds):
      with patch.object(onroad_state, '_inside_widget', return_value=not personality):
        drawn.press(50, 50, current)
        hidden.press(50, 50, current)
    assert (drawn._personality_press is not None) is personality and (drawn._press is not None) is not personality
    assert not hidden.claimed, f'the {target} takes no tap while the black screen hides it'


def test_wheel_press_ends_when_the_wheel_is_hidden_under_it():
  from openpilot.starpilot.ui import onroad_state
  current = replace(state('device'), experimental_available=True, alert=OnroadAlert())
  emit, shown = Mock(), [False]
  touch = onroad_state.OnroadInput(emit)
  touch.widgets_hidden = lambda _state: shown[0]
  with patch.object(onroad_state, 'personality_bounds', return_value=None):
    with patch.object(onroad_state, '_inside_widget', return_value=True):
      touch.press(50, 50, current)
      assert touch._press is not None
      shown[0] = True
      touch.move(50, 50, current)
  assert touch._press is None and not touch.claimed
  emit.assert_not_called()
