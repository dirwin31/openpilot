import math
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.starpilot.navigation.road_tiles import EXTENT, MOTORWAY, STREET, RoadTile, TileKey, meters_per_tile, world_xy
from openpilot.starpilot.ui import birds_eye_map as bm

LAT, LON = 36.1, -115.15


def _north_south_tile(lat=LAT, lon=LON):
  """One tile holding a straight street running north-south through (lat, lon)."""
  wx, wy = world_xy(lat, lon)
  key = TileKey(14, int(wx), int(wy))
  px = (wx - key.x) * EXTENT
  points = np.array([(px, 0.0), (px, EXTENT * 0.5), (px, EXTENT - 1.0)], dtype=np.float32)
  return RoadTile(key, {STREET: (points, np.array([0, 3], dtype=np.int32))})


class _Reader:
  def __init__(self, tiles):
    self.tiles, self.generation, self.wanted = {t.key: t for t in tiles}, 1, ()

  def want(self, keys):
    self.wanted = tuple(keys)

  def snapshot(self, keys):
    return {k: self.tiles[k] for k in keys if k in self.tiles}

  def close(self):
    pass


class _Feed:
  def __init__(self, fix):
    self.fix = fix

  def read(self):
    return SimpleNamespace(fix=self.fix)


def _fix(lat=LAT, lon=LON, bearing=0.0, speed=10.0, at=100.0):
  return SimpleNamespace(latitude=lat, longitude=lon, bearing=bearing, speed=speed, monotonic=at)


def test_to_car_turns_north_and_east_into_forward_and_right():
  assert bm.to_car([(10.0, 0.0)], 0.0, 0.0, 0.0) == pytest.approx(np.array([[10.0, 0.0]]))
  assert bm.to_car([(0.0, 10.0)], 0.0, 0.0, math.pi / 2) == pytest.approx(np.array([[10.0, 0.0]]), abs=1e-9)
  assert bm.to_car([(10.0, 0.0)], 0.0, 0.0, math.pi / 2) == pytest.approx(np.array([[0.0, -10.0]]), abs=1e-9), 'north is left when facing east'
  assert bm.to_car([(5.0, 2.0)], 5.0, 0.0, 0.0, lateral=1.5) == pytest.approx(np.array([[0.0, 3.5]]))
  m = bm.pose_matrix(3.0, -4.0, 0.7, lateral=0.5, turn=0.1)
  assert (np.array([8.0, 1.0, 0.0, 1.0]) @ m.T)[:2] == pytest.approx(bm.to_car([(8.0, 1.0)], 3.0, -4.0, 0.7, 0.5, 0.1)[0])


def test_road_at_finds_the_nearest_forward_line():
  segments = np.array([[(0.0, 3.0), (30.0, 3.0)], [(0.0, -1.0), (30.0, 1.0)], [(12.0, -20.0), (12.0, 20.0)]])
  y, angle = bm.road_at(segments)
  assert y == pytest.approx(-1.0 + 2.0 * 12.0 / 30.0) and angle == pytest.approx(math.atan(2.0 / 30.0))
  assert bm.road_at(segments[2:]) is None, 'a cross street is not the road ahead'
  assert bm.road_at(np.empty((0, 2, 2))) is None


def test_underlay_places_the_street_under_the_car_and_keeps_the_anchor_while_close():
  feed = _Feed(_fix(bearing=0.0, speed=0.0))
  underlay = bm.MapUnderlay(reader=_Reader([_north_south_tile()]), feed=feed, clock=lambda: 100.0)
  anchor, pose = underlay.update()
  assert pose == pytest.approx((0.0, 0.0, 0.0))
  (road_class, line), = anchor.lines
  car = bm.to_car(line, *pose)
  assert road_class == STREET and np.abs(car[:, 1]).max() < 0.5, 'the street runs straight ahead'
  mpt = meters_per_tile(LAT)
  feed.fix = _fix(lat=LAT + 50.0 / 111_000, bearing=0.0, speed=0.0)
  again, moved = underlay.update()
  assert again is anchor and moved[0] == pytest.approx(50.0, abs=1.0)
  feed.fix = _fix(lat=LAT + 200.0 / 111_000, bearing=0.0, speed=0.0)
  far, re = underlay.update()
  assert far is not anchor and math.hypot(re[0], re[1]) < 1.0 and mpt > 0


def test_underlay_dead_reckons_and_keeps_its_bearing_when_slow():
  feed = _Feed(_fix(bearing=90.0, speed=20.0, at=99.5))
  underlay = bm.MapUnderlay(reader=_Reader([]), feed=feed, clock=lambda: 100.0)
  _, (north, east, bearing) = underlay.update()
  assert bearing == pytest.approx(math.pi / 2) and east == pytest.approx(10.0, abs=0.1) and abs(north) < 0.1
  feed.fix = _fix(bearing=200.0, speed=0.3, at=100.0)
  assert underlay.update()[1][2] == pytest.approx(math.pi / 2), 'a creeping bearing is noise'
  feed.fix = None
  assert underlay.update() is None
  assert bm.MapUnderlay(reader=_Reader([]), feed=_Feed(_fix(bearing=None)), clock=lambda: 100.0).update() is None


def test_map_ops_draw_major_roads_last_and_alignment_eases_onto_the_model_road():
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  from openpilot.selfdrive.ui.tests.test_birds_eye_math import _renderer_with_scene
  line = np.array([(-50.0, 4.0), (0.0, 4.0), (60.0, 4.0)])
  anchor = bm.Anchor(0.0, 0.0, 1.0, ((MOTORWAY, line), (STREET, line + 20.0)), bm.segments_of([(MOTORWAY, line)]), 1)
  ops = ber._map_ops(anchor, 1.0)
  strips = [op for op in ops if op[0] is not ber._keep]
  assert len(strips) == 2 and strips[-1][3].r == ber.MAP_RGB[MOTORWAY][0]
  view, _ = _renderer_with_scene()
  edge = np.ones_like(view.scene.grid)
  view.scene.edges = [edge * -5.0, edge * 5.0]
  for _ in range(200):
    lateral, turn = view._map_alignment(view.scene, anchor, (0.0, 0.0, 0.0))
  assert lateral == pytest.approx(-4.0, abs=0.05) and abs(turn) < ber.MAP_MAX_TURN
