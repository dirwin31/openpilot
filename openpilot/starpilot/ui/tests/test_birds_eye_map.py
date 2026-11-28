import ctypes
import math
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.starpilot.navigation.road_tiles import EXTENT, MOTORWAY, STREET, RoadTile, TileKey, world_xy
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
    self.tiles, self.wanted, self.jobs = {t.key: t for t in tiles}, (), 0

  def want(self, keys):
    self.wanted = tuple(keys)

  def snapshot(self, keys):
    return {k: self.tiles[k] for k in keys if k in self.tiles}

  def submit(self, job):
    self.jobs += 1
    job()

  def close(self):
    pass


class _Feed:
  def __init__(self, fix):
    self.fix, self.reads = fix, 0

  def read(self):
    self.reads += 1
    return SimpleNamespace(fix=self.fix)


class _Clock:
  def __init__(self, t=100.0):
    self.t = t

  def __call__(self):
    return self.t


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


def test_underlay_builds_each_tile_once_off_the_render_path_and_places_its_street_under_the_car():
  clock, reader = _Clock(), _Reader([_north_south_tile()])
  feed = _Feed(_fix(bearing=0.0, speed=0.0))
  underlay = bm.MapUnderlay(reader=reader, feed=feed, clock=clock)
  underlay.decorate = lambda anchor: 'drawn'
  (anchor, pose), = underlay.update()
  assert anchor.drawn == 'drawn' and reader.jobs == 1
  (road_class, line), = anchor.lines
  car = bm.to_car(line, *pose)
  assert road_class == STREET and np.abs(car[:, 1]).max() < 0.5, 'the street runs straight ahead'
  feed.fix = _fix(lat=LAT + 200.0 / 111_000, bearing=0.0, speed=0.0)
  for step in range(1, 4):
    clock.t = 100.0 + step
    (again, moved), = underlay.update()
  assert again is anchor and reader.jobs == 1, 'driving on never rebuilds a tile'
  assert moved[0] - pose[0] == pytest.approx(200.0, abs=2.0)


def test_underlay_reads_gps_at_10_hz_and_dead_reckons_between():
  clock = _Clock()
  feed = _Feed(_fix(bearing=90.0, speed=20.0, at=99.5))
  underlay = bm.MapUnderlay(reader=_Reader([_north_south_tile()]), feed=feed, clock=clock)
  (_, (n0, e0, bearing)), = underlay.update()
  assert bearing == pytest.approx(math.pi / 2)
  clock.t = 100.05
  (_, (n1, e1, _)), = underlay.update()
  assert feed.reads == 1 and e1 - e0 == pytest.approx(1.0, abs=0.01) and n1 == pytest.approx(n0)
  feed.fix = _fix(bearing=200.0, speed=0.3, at=100.2)
  clock.t = 100.2
  assert underlay.update()[0][1][2] == pytest.approx(math.pi / 2), 'a creeping bearing is noise'
  feed.fix = None
  clock.t = 100.4
  assert underlay.update() is None
  assert bm.MapUnderlay(reader=_Reader([]), feed=_Feed(_fix(bearing=None)), clock=_Clock()).update() is None


def test_far_tiles_are_not_placed():
  far = _north_south_tile(lat=LAT + 0.05)
  underlay = bm.MapUnderlay(reader=_Reader([far]), feed=_Feed(_fix(speed=0.0)), clock=_Clock())
  assert underlay.update() == []


def test_map_ops_draw_major_roads_last_and_alignment_eases_onto_the_model_road():
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  from openpilot.selfdrive.ui.tests.test_birds_eye_math import _renderer_with_scene
  line = np.array([(-50.0, 4.0), (0.0, 4.0), (60.0, 4.0)])
  anchor = bm.Anchor(0.0, 0.0, 1.0, ((MOTORWAY, line), (STREET, line + 20.0)), bm.segments_of([(MOTORWAY, line)]))
  cells = ber._map_cells(anchor)
  assert cells.layers[-1][0] == MOTORWAY, 'major roads draw last'
  assert sum(len(layer) for _, layer in cells.layers) == len(cells.centres) + 1, 'one strip per cell and class'
  view, _ = _renderer_with_scene()
  edge = np.ones_like(view.scene.grid)
  view.scene.edges = [edge * -5.0, edge * 5.0]
  for _ in range(200):
    lateral, turn = view._map_alignment(view.scene, [(anchor, (0.0, 0.0, 0.0))])
  assert lateral == pytest.approx(-4.0, abs=0.05) and abs(turn) < ber.MAP_MAX_TURN


def test_map_lines_are_cut_into_cells_and_each_cells_pieces_join_into_one_strip():
  from openpilot.selfdrive.ui.onroad import birds_eye_renderer as ber
  long_road = ber._densify(np.array([(0.0, 0.0), (450.0, 0.0)]), ber.MAP_CELL / 2)
  pieces = [long_road[lo:hi] for lo, hi in ber._cuts(long_road, ber.MAP_CELL)]
  assert len(pieces) >= 4 and all(np.ptp(p[:, 0]) <= ber.MAP_CELL + 1e-6 for p in pieces)
  assert pieces[0][0, 0] == 0.0 and pieces[-1][-1, 0] == 450.0
  assert all(a[-1].tolist() == b[0].tolist() for a, b in zip(pieces[:-1], pieces[1:], strict=True)), 'no gaps'
  bend = np.array([(x, 0.002 * max(x, 0.0) ** 2) for x in np.arange(-150.0, 150.0, 5.0)])
  cells = ber._map_cells(bm.Anchor(0.0, 0.0, 1.0, ((STREET, bend),), bm.segments_of([(STREET, bend)])))
  ends = [np.frombuffer((ctypes.c_float * (n * 2)).from_address(ptr), dtype=np.float32).reshape(-1, 2)
          for _, layer in cells.layers for _, (_, ptr, n) in layer]
  assert len(ends) >= 2
  tails = {tuple(np.round(v, 3)) for strip in ends for v in (strip[-2], strip[-1])}
  heads = [tuple(np.round(v, 3)) for strip in ends for v in (strip[0], strip[1])]
  assert sum(head in tails for head in heads) == 2 * (len(ends) - 1), 'pieces meet edge to edge on a curve'
  lines = [(STREET, np.array([(10.0, 10.0), (10.0, 60.0)])), (STREET, np.array([(30.0, 10.0), (30.0, 60.0)]))]
  cells = ber._map_cells(bm.Anchor(0.0, 0.0, 1.0, tuple(lines), bm.segments_of(lines)))
  ((_, layer),) = cells.layers
  assert len(layer) == 1 and layer[0][1][2] == 2 * 2 + 2 + 2 * 2, 'two ribbons and a two-vertex bridge in one call'
  assert cells.radii[0] >= np.hypot(25.0, 25.0)
