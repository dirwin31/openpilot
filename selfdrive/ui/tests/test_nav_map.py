import json
import math

import numpy as np
import pytest

from openpilot.selfdrive.ui.onroad.starpilot import nav_map
from openpilot.starpilot.navigation.map_tiles import world_xy


class FakeParams:
  def __init__(self, values=None):
    self.values = values or {}

  def get(self, key, encoding=None):
    return self.values.get(key)


@pytest.fixture
def view(monkeypatch):
  memory, persistent = FakeParams(), FakeParams()
  created = []

  def params(memory_flag=False):
    created.append(memory_flag)
    return memory if memory_flag else persistent

  monkeypatch.setattr(nav_map, "Params", lambda memory=False: params(memory))
  monkeypatch.setattr(nav_map.gui_app, "font", lambda _: None)
  widget = nav_map.NavMapView()
  widget.memory, widget.persistent = memory, persistent
  return widget


def gps_state(latitude, longitude, bearing=0.0, speed=0.0, updated=None, has_fix=True):
  return json.dumps({"latitude": latitude, "longitude": longitude, "bearing": bearing, "speed": speed,
                     "hasFix": has_fix, "updatedAtMonotonic": updated or 0.0})


def test_camera_round_trips_with_rotation():
  camera = nav_map.Camera(100.25, 200.5, 15.3, 37.0)
  anchor = (400.0, 700.0)
  x, y = camera.to_screen(100.2501, 200.4998, anchor, 1.5)
  wx, wy = camera.to_world(x, y, anchor, 1.5)
  assert math.isclose(wx, 100.2501, abs_tol=1e-9) and math.isclose(wy, 200.4998, abs_tol=1e-9)


@pytest.mark.parametrize("bearing", [0.0, 90.0, 180.0, 245.0])
def test_heading_up_puts_the_road_ahead_above_the_car(bearing):
  latitude, longitude = 36.3, -115.3
  car = world_xy(latitude, longitude)
  ahead_lat = latitude + math.cos(math.radians(bearing)) * 0.001
  ahead_lon = longitude + math.sin(math.radians(bearing)) * 0.001 / math.cos(math.radians(latitude))
  ahead = world_xy(ahead_lat, ahead_lon)
  camera = nav_map.Camera(car[0], car[1], 16.0, bearing)
  x, y = camera.to_screen(ahead[0], ahead[1], (500.0, 500.0), 1.0)
  assert abs(x - 500.0) < 2.0
  assert y < 450.0


def test_visible_runs_keep_long_segments_that_cross_the_view():
  rect = nav_map.rl.Rectangle(0, 0, 100, 100)
  # Both ends far outside, the segment passes straight through the view.
  sx, sy = np.array([-5000.0, 5000.0, 5000.0]), np.array([50.0, 50.0, 9000.0])
  assert nav_map._visible_runs(sx, sy, rect, 10.0) == [(0, 1)]
  assert nav_map._visible_runs(np.array([500.0, 600.0]), np.array([500.0, 600.0]), rect, 10.0) == []


def test_visible_runs_split_when_the_route_leaves_and_returns():
  rect = nav_map.rl.Rectangle(0, 0, 100, 100)
  sx = np.array([10.0, 20.0, 900.0, 950.0, 30.0, 40.0])
  sy = np.array([10.0, 20.0, 900.0, 950.0, 30.0, 40.0])
  runs = nav_map._visible_runs(sx, sy, rect, 0.0)
  assert runs[0][0] == 0 and runs[-1][1] == 5
  assert len(runs) == 2


def test_desire_line_names_the_source():
  label, detail, color = nav_map.desire_line(True, 2, 2, None)
  assert label == "Model: Turn right" and detail == "From the route" and color == nav_map.DESIRE_ROUTE
  label, detail, color = nav_map.desire_line(True, 1, 0, None)
  assert label == "Model: Turn left" and detail == "From the turn signal" and color == nav_map.DESIRE_DRIVER


def test_desire_line_hints_before_a_route_turn():
  nav = {"type": "turn", "modifier": "sharpLeft", "distance": 90.0}
  label, detail, _ = nav_map.desire_line(True, 0, 0, nav)
  assert label == "Route turn left ahead" and "Signal left" in detail
  assert nav_map.desire_line(True, 0, 0, dict(nav, distance=900.0)) is None
  keep = {"type": "fork", "modifier": "slightRight", "distance": 300.0}
  assert nav_map.desire_line(True, 0, 0, keep)[0] == "Keep right ahead"


def test_desire_line_is_hidden_offroad():
  assert nav_map.desire_line(False, 2, 2, {"type": "turn", "modifier": "right", "distance": 10.0}) is None


def test_gps_prefers_live_fix_and_dead_reckons(view, monkeypatch):
  now = 1000.0
  view.memory.values["LastGPSPosition"] = gps_state(36.3, -115.3, bearing=90.0, speed=20.0, updated=now)
  view._poll_gps(now)
  assert view._gps.fresh
  start = view._car_world(now)
  later = view._car_world(now + 0.5)
  assert later[0] > start[0], "heading east moves the car east between fixes"
  assert math.isclose(later[1], start[1], abs_tol=1e-9)
  capped = view._car_world(now + 10.0)
  assert capped == view._car_world(now + nav_map.DEAD_RECKON_LIMIT)


def test_gps_falls_back_to_last_known_position(view):
  view.persistent.values["LastGPSPosition"] = gps_state(36.3, -115.3)
  view._poll_gps(50.0)
  assert view._gps is not None and not view._gps.fresh
  assert view._car_world(51.0) == world_xy(36.3, -115.3)


def test_stale_fix_is_not_fresh(view):
  view.memory.values["LastGPSPosition"] = gps_state(36.3, -115.3, speed=10.0, updated=10.0)
  view._poll_gps(10.0 + nav_map.GPS_STALE_SECONDS + 1.0)
  assert not view._gps.fresh


def test_route_progress_tracks_the_nearest_point(view):
  points = [(36.3, -115.3 + i * 0.001) for i in range(50)]
  view._route_world = nav_map._route_world(points)
  view.memory.values["LastGPSPosition"] = gps_state(36.3, -115.3 + 30 * 0.001, updated=5.0)
  view._poll_gps(5.0)
  assert view._route_progress == 30
