"""Layouts used beside a phone screen: the comma 3/3X half and the StarPort car half."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import pytest

from openpilot.starpilot.system.phone_projection import comma_layout
from openpilot.starpilot.system.phone_projection import settings as projection_settings
from openpilot.starpilot.system.phone_projection.car_split import car_layout
from openpilot.starpilot.system.starport.display_profile import SUPPORTED_SIZES
from openpilot.starpilot.system.starport.projection_geometry import projection_geometry
from openpilot.starpilot.system.starport.projection_layout import (
  default_layout_for_viewport, layout_metadata_for_viewport, placement_size, validate_layout_for_viewport,
)
from openpilot.starpilot.ui.layout_preview_transport import validate_preview_document
from openpilot.starpilot.ui.onroad_customization import default_document, fit_to_road, widget_size

CORE = ('current_speed', 'cruise_limits', 'speed_limit_actions', 'steering_wheel')


def overlaps(a, b):
  return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def split_viewports():
  seen = set()
  for width, height in SUPPORTED_SIZES:
    for margins in ((0, 0), (0, height // 8)):
      geometry = projection_geometry(width, height, *margins)
      for side in projection_settings.SIDES:
        seen.add(car_layout(geometry, side).viewport)
  return sorted(seen)


@pytest.mark.parametrize('viewport', split_viewports())
def test_starport_split_defaults_fit_every_car_half(viewport):
  metadata = layout_metadata_for_viewport(viewport)
  layout = default_layout_for_viewport(viewport)
  assert validate_layout_for_viewport(json.loads(json.dumps(layout)), viewport)['canvas'] == {'width': viewport[0], 'height': viewport[1]}
  bounds = metadata['bounds']
  boxes = {}
  for key, placed in layout['widgets'].items():
    w, h = placement_size(key, metadata['widgets'][key], placed)
    assert bounds['x'] <= placed['x'] and placed['x'] + w <= bounds['x'] + bounds['width'], key
    assert bounds['y'] <= placed['y'] and placed['y'] + h <= bounds['y'] + bounds['height'], key
    boxes[key] = (placed['x'], placed['y'], w, h)
  for index, key in enumerate(CORE):
    for other in CORE[index + 1:]:
      if {key, other} != {'cruise_limits', 'speed_limit_actions'}:
        assert not overlaps(boxes[key], boxes[other]), (key, other)


def test_starport_split_canvas_stays_within_pixels_of_the_drawn_half():
  from openpilot.starpilot.system.phone_projection.car_split import layout_viewport
  for width, height in SUPPORTED_SIZES:
    for margins in ((0, 0), (0, height // 8), (width // 9, 0)):
      geometry = projection_geometry(width, height, *margins)
      canvas = layout_viewport(geometry)
      for origin in ((0, 0), (1, 1), (3, 0), (margins[0] // 2, margins[1] // 2)):
        for side in projection_settings.SIDES:
          drawn = car_layout(geometry, side, origin).viewport
          assert drawn[1] == canvas[1] and abs(drawn[0] - canvas[0]) <= 4, (width, height, margins, origin, side)


def test_full_car_screen_defaults_unchanged():
  layout = default_layout_for_viewport((1920, 1080))
  assert (layout['widgets']['current_speed']['x'], layout['widgets']['steering_wheel']['x']) == (670, 1648)


def test_comma_split_defaults_fit_the_half_and_draw_unchanged():
  layout, order = comma_layout.validate(comma_layout.defaults())
  assert order is None
  document = default_document()
  document['layouts']['large'] = layout
  boxes = {key: (placed['x'], placed['y'], *widget_size(document, 'large', key)) for key, placed in layout.items()}
  for key, (x, y, w, h) in boxes.items():
    assert 0 <= x and x + w <= comma_layout.WIDTH and 0 <= y and y + h <= comma_layout.HEIGHT, key
  assert not overlaps(boxes['current_speed'], boxes['steering_wheel'])
  assert not overlaps(boxes['current_speed'], boxes['cruise_limits'])
  full = comma_layout.customization(default_document(), layout, None)
  fitted = fit_to_road(full, comma_layout.WIDTH)['layouts']['large']
  for key in layout:
    # The torque arc is a pixel wider than the half's margins, so it may be nudged by that much.
    assert abs(fitted[key]['x'] - full['layouts']['large'][key]['x']) <= (2 if key == 'torque_bar' else 0), key


def test_comma_split_rejects_widgets_past_the_half():
  layout = comma_layout.defaults()
  layout['steering_wheel']['x'] = 1000
  with pytest.raises(ValueError):
    comma_layout.validate(layout)


def test_comma_split_round_trip_and_cache():
  layout = comma_layout.defaults()
  layout['steering_wheel'].update(x=700, y=400)
  with tempfile.TemporaryDirectory() as root:
    cache = comma_layout.LayoutCache(Path(root))
    assert cache.get()[0] == comma_layout.defaults()
    checked, order = comma_layout.validate(layout)
    (Path(root) / comma_layout.FILE_NAME).write_bytes(comma_layout.encode(checked, order))
    cache._checked = 0
    assert cache.get()[0]['steering_wheel']['x'] == 700
    (Path(root) / comma_layout.FILE_NAME).write_text('{"version": 1, "layout": "broken"}')
    cache._checked = 0
    assert cache.get()[0] == comma_layout.defaults()


def test_comma_split_metadata_is_only_the_half():
  metadata = comma_layout.metadata()
  profile = metadata['profiles']['large']
  assert metadata['split'] is True and list(metadata['profiles']) == ['large']
  assert (profile['width'], profile['height']) == (comma_layout.WIDTH, comma_layout.HEIGHT)
  assert all('anchorShift' not in widget and 'expandable' not in widget for widget in profile['widgets'].values())
  assert profile['widgets']['current_speed']['default']['x'] == comma_layout.defaults()['current_speed']['x']


def test_split_preview_document_checks_the_same_every_time():
  from openpilot.starpilot.galaxy.phone_screen_layout import _document
  document = {**_document(default_document(), comma_layout.defaults(), None), 'phoneSplit': True}
  once = validate_preview_document(json.loads(json.dumps(document)))
  twice = validate_preview_document(json.loads(json.dumps(once)))
  assert once == twice and once['phoneSplit'] is True
  assert once['layouts']['large'] == comma_layout.defaults()


class Params:
  def __init__(self, root):
    self.root = root

  def get_param_path(self, key):
    return str(self.root / key)

  def get_bool(self, key):
    return True


class TestPhoneScreenLayoutOwner(unittest.TestCase):
  def setUp(self):
    from openpilot.starpilot.galaxy.phone_screen_layout import PhoneScreenLayoutOwner
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.parked = True
    self.owner = PhoneScreenLayoutOwner(Params(self.root), lambda: self.parked, lambda: True,
                                        source=comma_layout.LayoutSource(self.root / 'phone'), settings_root=self.root / 'phone')

  def test_defaults_then_save_only_the_arrangement(self):
    value = self.owner.snapshot()
    self.assertTrue(value['editable'])
    self.assertEqual(list(value['document']['layouts']), ['large'])
    self.assertEqual(value['phoneScreen']['width'], 1080)
    document = copy.deepcopy(value['document'])
    document['layouts']['large']['steering_wheel'].update(x=700)
    document['palette']['text'] = '#FF0000FF'  # shared theme colors are not part of this layout
    saved = self.owner.save({'revision': value['revision'], 'document': document}, session_valid=lambda: True)
    self.assertEqual(saved['document']['layouts']['large']['steering_wheel']['x'], 700)
    self.assertEqual(saved['document']['palette']['text'], default_document()['palette']['text'])
    self.assertNotEqual(saved['revision'], value['revision'])

  def test_rejects_stale_revision_and_outside_half(self):
    from openpilot.starpilot.galaxy.onroad_layout import LayoutChanged
    value = self.owner.snapshot()
    document = copy.deepcopy(value['document'])
    document['layouts']['large']['steering_wheel'].update(x=1000)
    with self.assertRaises(ValueError):
      self.owner.save({'revision': value['revision'], 'document': document}, session_valid=lambda: True)
    with self.assertRaises(LayoutChanged):
      self.owner.save({'revision': 'old', 'document': value['document']}, session_valid=lambda: True)

  def test_not_editable_without_a_large_screen(self):
    from openpilot.starpilot.galaxy.phone_screen_layout import PhoneScreenLayoutOwner
    owner = PhoneScreenLayoutOwner(Params(self.root), lambda: True, lambda: False, source=comma_layout.LayoutSource(self.root))
    value = owner.snapshot()
    self.assertFalse(value['editable'])
    self.assertIn('comma 3', value['reason'])


class TestStarPortSplitOwner(unittest.TestCase):
  def test_split_owner_edits_the_car_half_separately(self):
    from openpilot.starpilot.galaxy.projection_layout import ProjectionLayoutOwner
    from openpilot.starpilot.system.starport.display_profile import record_screen
    from openpilot.starpilot.system.starport.projection_layout import ProjectionLayoutSource
    with tempfile.TemporaryDirectory() as temp:
      root = Path(temp)
      screen = {'version': 1, 'width': 1920, 'height': 1080, 'margin_width': 0, 'margin_height': 0, 'fps': 60, 'config_index': 0}
      record_screen(screen, root / 'screen.json')
      owner = ProjectionLayoutOwner(Params(root), lambda: True, ProjectionLayoutSource(root / 'layouts/split.json'), root / 'screen.json',
                                    require_enabled=False, split=True, settings_root=root / 'phone')
      value = owner.snapshot()
      self.assertEqual(value['document']['canvas'], {'width': 960, 'height': 1080})
      self.assertEqual(value['phoneScreen'], {'enabled': False, 'side': 'right', 'width': 960, 'height': 1080})
      document = copy.deepcopy(value['document'])
      document['widgets']['steering_wheel']['x'] = 500
      saved = owner.save({'revision': value['revision'], 'document': document}, session_valid=lambda: True)
      self.assertEqual(saved['document']['widgets']['steering_wheel']['x'], 500)
      self.assertTrue((root / 'layouts/split.json').exists())
      self.assertFalse((root / 'layouts/document.json').exists())


def _low_cruise(y):
  layout = comma_layout.defaults()
  layout['speed_limit_actions']['enabled'] = False  # so only the bottom edge limits the cruise card
  layout['cruise_limits']['y'] = y
  return layout


def test_comma_split_checks_the_cruise_card_with_the_sources_setting():
  lowest = comma_layout.HEIGHT - 30 - widget_size(default_document(), 'large', 'cruise_limits')[1]  # the Big bounds
  comma_layout.validate(_low_cruise(lowest))
  comma_layout.validate(_low_cruise(lowest), None, {'speedSources': False, 'speedSourcesCard': True})
  for card in (False, True):
    sources = {'speedSources': True, 'speedSourcesCard': card}
    # The sources row (or its own card) hangs below the cruise card: it must end inside the half, counted once.
    lowest = comma_layout.HEIGHT - widget_size({**default_document(), **sources}, 'large', 'cruise_limits')[1]
    comma_layout.validate(_low_cruise(lowest), None, sources)
    with pytest.raises(ValueError):
      comma_layout.validate(_low_cruise(lowest + 1), None, sources)


def test_comma_split_cache_rechecks_when_the_sources_setting_changes():
  y = comma_layout.HEIGHT - 30 - widget_size(default_document(), 'large', 'cruise_limits')[1]
  with tempfile.TemporaryDirectory() as root:
    checked, order = comma_layout.validate(_low_cruise(y))
    (Path(root) / comma_layout.FILE_NAME).write_bytes(comma_layout.encode(checked, order))
    cache = comma_layout.LayoutCache(Path(root))
    off = {**default_document(), 'speedSources': False}
    assert cache.get(off)[0]['cruise_limits']['y'] == y
    # No file change and inside the one-second check window: the sources switch alone must recheck the saved layout.
    on = {**default_document(), 'speedSources': True}
    assert cache.get(on)[0] == comma_layout.checked_defaults(on)[0]
    assert cache.get(off)[0]['cruise_limits']['y'] == y


def test_map_tiles_run_for_either_car_layout(monkeypatch):
  from openpilot.starpilot.navigation import navtilesd
  from openpilot.starpilot.system.starport import projection_layout
  with tempfile.TemporaryDirectory() as temp:
    full, split = Path(temp) / 'document.json', Path(temp) / 'split.json'
    monkeypatch.setattr(projection_layout, 'DOCUMENT_PATH', full)
    monkeypatch.setattr(projection_layout, 'SPLIT_DOCUMENT_PATH', split)

    def write(path, enabled):
      path.write_text(json.dumps({'widgets': {'nav_map': {'enabled': enabled}}}))

    assert not navtilesd._map_enabled()
    write(full, False)
    split.write_text('not json')
    assert not navtilesd._map_enabled()
    write(split, True)
    assert navtilesd._map_enabled()
    write(split, False)
    write(full, True)
    assert navtilesd._map_enabled()
    full.write_text('{"widgets": []}')
    assert not navtilesd._map_enabled()


class _PreviewHandler:
  def __init__(self):
    self.responses = []

  def settings_session(self):
    return 'me'

  def json(self, status, body):
    self.responses.append((status, body))

  def respond(self, status, *args, **kwargs):
    self.responses.append((status, args[0]))


def test_projection_preview_checks_each_canvas_against_its_own_layout(monkeypatch):
  from types import SimpleNamespace as NS
  from openpilot.starpilot.galaxy import batch_routes
  screen = {'version': 1, 'width': 1920, 'height': 1080, 'margin_width': 0, 'margin_height': 0, 'fps': 60, 'config_index': 0}
  sent = []
  monkeypatch.setattr(batch_routes, 'request_preview', lambda payload, _socket: sent.append(payload) or b'png')
  with tempfile.TemporaryDirectory() as temp:
    projection = NS(snapshot=lambda: {'available': True, 'reason': None, 'screen': screen, 'metadata': {'width': 1920, 'height': 1080}})
    layouts = NS(params=Params(Path(temp)), parked=lambda: True)
    split = NS(viewport=lambda _screen: (960, 1080))

    def preview(document, split_owner=lambda: split):
      handler = _PreviewHandler()
      batch_routes.preview_layout(handler, {'document': document, 'profile': 'projection', 'scene': 'engaged'},
                                  lambda: projection, lambda: layouts, None, split_owner)
      return handler.responses[-1][0]

    assert preview(default_layout_for_viewport((960, 1080))) == 200
    assert sent[-1]['document']['layout']['canvas'] == {'width': 960, 'height': 1080}
    assert preview(default_layout_for_viewport((1920, 1080))) == 200
    assert sent[-1]['document']['layout']['canvas'] == {'width': 1920, 'height': 1080}
    # Without a split owner every layout must be the full car screen.
    assert preview(default_layout_for_viewport((960, 1080)), None) == 400
    # A split canvas that is not the current car half is refused.
    assert preview(default_layout_for_viewport((1000, 1080))) == 400
    assert len(sent) == 2


def test_comma_half_hides_the_developer_sidebar_beside_the_phone():
  from dataclasses import dataclass, replace
  from types import SimpleNamespace as NS
  from openpilot.starpilot.ui.layout_preview_renderer import sample_state
  from openpilot.starpilot.ui.runtime_app import StarShellSession

  @dataclass(frozen=True)
  class Snapshot:
    onroad: object

  state = sample_state('engaged', default_document())
  state = replace(state, appearance=replace(state.appearance, show_developer_sidebar=True))
  with tempfile.TemporaryDirectory() as root:
    session = NS(_phone_layout=comma_layout.LayoutCache(Path(root)))
    split = StarShellSession._phone_split_snapshot(session, Snapshot(state)).onroad
  assert split.appearance.show_developer_sidebar is False
  assert split.customization['layouts']['large'] == comma_layout.anchored(comma_layout.defaults())
  assert state.appearance.show_developer_sidebar is True
