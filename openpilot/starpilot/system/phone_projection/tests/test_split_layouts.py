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
