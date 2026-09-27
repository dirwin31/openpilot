import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.starpilot.system.android_auto.ui import nav_map
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


def test_prepared_render_polls_once_and_next_frame_is_fresh(view, monkeypatch):
  calls = []
  view._tiles = SimpleNamespace(upload=lambda: calls.append("upload"), service=SimpleNamespace(offline=False))
  view._sm = SimpleNamespace(update=lambda _: calls.append("poll"),
                             updated=dict.fromkeys(("navRoute", "navInstruction", "starpilotModelV2"), False))
  # Widget.render normally calls _update_state; exercise that call without GL.
  monkeypatch.setattr(view, "render", lambda rect: (view._update_state(), calls.append("draw")))
  view.update()
  view.render_prepared(nav_map.rl.Rectangle(0, 0, 100, 100))
  assert calls == ["upload", "poll", "draw"]
  view.update()
  assert calls[-2:] == ["upload", "poll"]

  def failed_render(rect):
    raise RuntimeError("draw failed")

  monkeypatch.setattr(view, "render", failed_render)
  with pytest.raises(RuntimeError, match="draw failed"):
    view.render_prepared(nav_map.rl.Rectangle(0, 0, 100, 100))
  view.update()
  assert calls[-2:] == ["upload", "poll"]


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


def test_follow_map_can_keep_raster_labels_north_up(view):
  now = 100.0
  rect = nav_map.rl.Rectangle(20, 30, 800, 600)
  view._gps = nav_map.GpsFix(36.3, -115.3, 180.0, 20.0, now, True)

  heading_camera, heading_anchor, follow = view._target_camera(rect, now)
  assert follow and heading_camera.bearing == 180.0
  assert heading_anchor == (420.0, 450.0)

  view._dirty = False
  view.set_heading_up(False)
  assert view._dirty
  north_camera, north_anchor, follow = view._target_camera(rect, now)
  assert follow and north_camera.bearing == 0.0
  assert north_anchor == (420.0, 330.0)
  assert view._display_bearing == 180.0, "the vehicle marker retains its actual heading"

  view._dirty = False
  view.set_heading_up(False)
  assert not view._dirty, "an unchanged live setting must not force another map redraw"


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


def test_android_auto_motion_does_not_reset_to_delayed_gps_reads(view, monkeypatch):
  monkeypatch.setattr(nav_map.ui_state, "android_auto_car_view", True)
  latitude, longitude, speed = 36.3, -115.3, 20.0
  start_x, _ = world_xy(latitude, longitude)
  units_per_second = speed / nav_map.meters_per_world_unit(latitude)
  positions = []
  for frame in range(60):
    now = 100.0 + frame / 30
    if frame % 6 == 0:  # 5 Hz polling of a 4 Hz producer
      published = 100.0 + math.floor((now - 100.0) * 4 + 1e-6) / 4
      lon = longitude + units_per_second * (published - 100.0) * 360 / nav_map.TILE_SIZE
      view.memory.values["LastGPSPosition"] = gps_state(latitude, lon, bearing=90, speed=speed, updated=published)
      view._poll_gps(now)
    positions.append(view._car_world(now)[0])
    assert math.isclose(positions[-1], start_x + units_per_second * (now - 100.0), abs_tol=1e-10)
  assert np.allclose(np.diff(positions), units_per_second / 30, rtol=1e-6)


@pytest.mark.parametrize("android_auto,updated,expected", [(False, 100., 100.2), (True, 100., 100.),
                                                           (True, 0., 100.2), (True, 101., 100.2)])
def test_gps_time_reference_is_aa_only_with_fallback(view, monkeypatch, android_auto, updated, expected):
  monkeypatch.setattr(nav_map.ui_state, "android_auto_car_view", android_auto)
  view.memory.values["LastGPSPosition"] = gps_state(36.3, -115.3, updated=updated)
  view._poll_gps(100.2)
  assert view._gps.received == expected


def test_stale_fix_is_not_fresh(view):
  view.memory.values["LastGPSPosition"] = gps_state(36.3, -115.3, speed=10.0, updated=10.0)
  view._poll_gps(10.0 + nav_map.GPS_STALE_SECONDS + 1.0)
  assert not view._gps.fresh


def test_android_auto_map_shows_active_navigation_waiting_for_gps(view):
  view._show_navigation_waiting = True
  view._navigation_requested = True

  assert view._center_message() == ("Navigation active", "Waiting for GPS to start your route.")
  view._gps = nav_map.GpsFix(36.3, -115.3, 0.0, 0.0, 0.0, False)
  assert view._center_message() == ("Navigation active", "Waiting for GPS to start your route.")
  view._gps.fresh = True
  assert view._center_message() is None


def test_route_progress_tracks_the_nearest_point(view):
  points = [(36.3, -115.3 + i * 0.001) for i in range(50)]
  view._route_world = nav_map._route_world(points)
  view.memory.values["LastGPSPosition"] = gps_state(36.3, -115.3 + 30 * 0.001, updated=5.0)
  view._poll_gps(5.0)
  assert view._route_progress == 30


def test_route_slice_caches_bounds_and_rebuilds_for_a_new_route(view):
  rect, camera, anchor = nav_map.rl.Rectangle(0, 0, 100, 100), nav_map.Camera(zoom=0), (0, 0)
  view._route_world = np.column_stack((np.arange(-10000, 10000), np.full(20000, 50)))
  selected = view._route_slice(rect, camera, anchor, 1.0, 40)
  assert selected.stop - selected.start < 400
  assert selected.start <= 9960 and selected.stop > 10140
  bounds = view._route_bounds
  camera.x = 1000
  moved = view._route_slice(rect, camera, anchor, 1.0, 40)
  assert moved.start > selected.start
  assert view._route_bounds is bounds
  view._route_world = view._route_world + 30000
  assert view._route_slice(rect, camera, anchor, 1.0, 40) == slice(0, 0)
  assert view._route_bounds is not bounds


@pytest.mark.parametrize("bearing", [0, 37, 90, 180, 245])
def test_route_slice_keeps_crossing_segments_and_reentry(view, bearing):
  rect, camera, anchor = nav_map.rl.Rectangle(20, 30, 100, 100), nav_map.Camera(zoom=5.5, bearing=bearing), (70, 80)
  # Cross the viewport at a chunk boundary, leave it, then return much later.
  screen = np.full((700, 2), -5000.0)
  screen[128:500] = (5000, 5000)
  view._route_world = np.array([camera.to_world(x, y, anchor, 1.5) for x, y in screen])
  selected = view._route_slice(rect, camera, anchor, 1.5, 40)
  assert selected.start <= 127 and selected.stop > 500


@pytest.mark.parametrize("android_auto,points", [(False, 10000), (True, 2000)])
def test_native_map_and_short_routes_do_not_build_route_bounds(view, monkeypatch, android_auto, points):
  monkeypatch.setattr(nav_map.ui_state, "android_auto_car_view", android_auto)
  view._route_world = np.column_stack((np.arange(points), np.arange(points)))
  view._route_received = nav_map.time.monotonic()
  monkeypatch.setattr(nav_map, "_draw_polyline", lambda *args, **kwargs: None)
  view._draw_routes(nav_map.rl.Rectangle(0, 0, 100, 100), nav_map.Camera(zoom=0), (0, 0), 1.0)
  assert view._route_bounds is None


@pytest.mark.parametrize("fps", [15, 30])
def test_cached_map_keeps_redraw_budget_with_preparation_delay(view, fps):
  view._animating = True
  drawn = []
  for frame in range(fps * 10):
    # Data updates and texture setup happen after the car loop samples time.
    now = 100.0 + frame / fps + (0.001 if frame % 3 == 0 else 0.0)
    if view.needs_redraw(now):
      drawn.append(frame)
      view._record_draw(now + 0.003)
      view._dirty = False
  assert len(drawn) == 150
  assert set(np.diff(drawn)) == {fps // 15}


def test_cached_map_stays_idle_and_does_not_catch_up_after_a_stall(view):
  view._record_draw(100.0)
  view._dirty = False
  assert not view.needs_redraw(100.5)
  assert view.needs_redraw(101.0)
  view._record_draw(101.0)
  view._animating = True
  assert not view.needs_redraw(101.01)
  assert view.needs_redraw(101.07)


def test_follow_zoom_ignores_speed_wobble_and_stops(view):
  start = view._follow_zoom(20.0, 0.0)
  now = 0.0
  # Cruising with ordinary +/-3 m/s variation leaves the zoom alone.
  for i in range(100):
    now += 0.2
    assert view._follow_zoom(20.0 + 3.0 * math.sin(i / 3.0), now) == start
  # A brief stop, as at a light, does not dive the map in.
  for _ in range(10):
    now += 0.2
    assert abs(view._follow_zoom(0.0, now) - start) <= nav_map.ZOOM_HOLD
  # A lost fix holds the zoom instead of snapping elsewhere.
  held = view._follow_zoom(None, now + 0.2)
  assert view._follow_zoom(None, now + 5.0) == held
  # A sustained change in speed still gets there.
  for _ in range(200):
    now += 0.2
    zoom = view._follow_zoom(35.0, now)
  assert abs(zoom - nav_map.FOLLOW_ZOOMS[-1]) < nav_map.ZOOM_HOLD


def test_republished_one_hz_fix_keeps_gliding(view, monkeypatch):
  """The planner rewrites a 1 Hz fix every 0.25 s with a new timestamp; the car must not step back."""
  monkeypatch.setattr(nav_map.ui_state, "android_auto_car_view", True)
  latitude, longitude, speed = 36.3, -115.3, 20.0
  start_x, _ = world_xy(latitude, longitude)
  units_per_second = speed / nav_map.meters_per_world_unit(latitude)
  positions = []
  for frame in range(90):
    now = 100.0 + frame / 30
    if frame % 6 == 0:  # 5 Hz polling
      published = 100.0 + math.floor((now - 100.0) * 4 + 1e-6) / 4  # 4 Hz republish
      fix_time = 100.0 + math.floor(now - 100.0 + 1e-6)              # 1 Hz fix
      lon = longitude + units_per_second * (fix_time - 100.0) * 360 / nav_map.TILE_SIZE
      view.memory.values["LastGPSPosition"] = gps_state(latitude, lon, bearing=90, speed=speed, updated=published)
      view._poll_gps(now)
    positions.append(view._car_world(now)[0])
  steps = np.diff(positions)
  assert np.all(steps > 0), "never steps back or stalls"
  assert np.allclose(steps, units_per_second / 30, rtol=0.05)


def test_new_fix_correction_is_eased_in(view, monkeypatch):
  monkeypatch.setattr(nav_map.ui_state, "android_auto_car_view", True)
  latitude, longitude, speed = 36.3, -115.3, 20.0
  view.memory.values["LastGPSPosition"] = gps_state(latitude, longitude, bearing=90, speed=speed, updated=100.0)
  view._poll_gps(100.0)
  predicted = view._car_world(101.0)
  # The next fix lands 5 m short of where dead reckoning had the car.
  meters = speed - 5.0
  lon = longitude + meters / nav_map.meters_per_world_unit(latitude) * 360 / nav_map.TILE_SIZE
  view.memory.values["LastGPSPosition"] = gps_state(latitude, lon, bearing=90, speed=speed, updated=101.0)
  view._poll_gps(101.0)
  assert math.isclose(view._car_world(101.0)[0], predicted[0], rel_tol=0, abs_tol=1e-12), "no jump at the new fix"
  gap = predicted[0] - world_xy(latitude, lon)[0]
  settled = view._car_world(102.5)
  target = world_xy(latitude, lon)[0] + 1.5 * speed / nav_map.meters_per_world_unit(latitude)
  assert abs(settled[0] - target) < 0.02 * gap, "the new fix wins after a moment"


def test_large_gps_jump_snaps(view, monkeypatch):
  monkeypatch.setattr(nav_map.ui_state, "android_auto_car_view", True)
  view.memory.values["LastGPSPosition"] = gps_state(36.3, -115.3, bearing=90, speed=20.0, updated=100.0)
  view._poll_gps(100.0)
  view.memory.values["LastGPSPosition"] = gps_state(36.31, -115.3, bearing=90, speed=20.0, updated=101.0)
  view._poll_gps(101.0)
  assert view._car_world(101.0) == world_xy(36.31, -115.3)


def test_route_splits_under_the_car_between_vertices(view, monkeypatch):
  monkeypatch.setattr(nav_map.ui_state, "android_auto_car_view", False)
  # A long straight segment: vertices 1 km apart.
  points = [(36.3, -115.3 + i * 0.011) for i in range(4)]
  view._route_world = nav_map._route_world(points)
  view._route_received = 10.0
  car_lon = -115.3 + 0.011 * 1.3
  view.memory.values["LastGPSPosition"] = gps_state(36.3, car_lon, bearing=90, speed=0.0, updated=10.0)
  view._poll_gps(10.0)
  segment, point = view._route_split(10.0)
  assert segment == 1
  assert math.isclose(point[0], world_xy(36.3, car_lon)[0], rel_tol=0, abs_tol=1e-9)

  drawn = []
  monkeypatch.setattr(nav_map, "_draw_polyline", lambda sx, sy, start, end, styles, caps=(False, False):
                      drawn.append((styles[-1][1], sx[start:end + 1].copy())))
  camera = nav_map.Camera(*world_xy(36.3, car_lon), 12.0, 0.0)
  view._draw_routes(nav_map.rl.Rectangle(-2000, -2000, 4000, 4000), camera, (0.0, 0.0), 1.0, 10.0)
  traveled = [xs for color, xs in drawn if color == nav_map.ROUTE_TRAVELED]
  ahead = [xs for color, xs in drawn if color == nav_map.ROUTE_FILL]
  assert math.isclose(traveled[0][-1], 0.0, abs_tol=1e-6) and math.isclose(ahead[0][0], 0.0, abs_tol=1e-6)


class _FakeSV(SimpleNamespace):
  pass


class _FakeGnssSM(dict):
  def __init__(self):
    super().__init__()
    self.updated = {"gpsLocationExternal": False, "qcomGnss": False}

  def update(self, _timeout):
    pass


def _measurement(source, states):
  report = SimpleNamespace(source=source, sv=[_FakeSV(observationState=state) for state in states])
  return SimpleNamespace(which=lambda: "measurementReport", measurementReport=report)


def test_gps_acquisition_counts_tracked_satellites_per_constellation():
  sm = _FakeGnssSM()
  acquisition = nav_map.GpsAcquisition(sm_factory=lambda: sm)
  acquisition.update(10.0)
  assert acquisition.satellites(10.0) is None and acquisition.since == 10.0
  sm.updated["qcomGnss"] = True
  sm["qcomGnss"] = _measurement(0, [5, 5, 4, 1, 0])  # GPS: three tracked
  acquisition.update(11.0)
  sm["qcomGnss"] = _measurement(1, [5, 2])           # GLONASS: one tracked
  acquisition.update(12.0)
  assert acquisition.satellites(12.0) == 4
  assert acquisition.satellites(16.5) == 1, "a stale constellation report stops counting"
  sm.updated = {"gpsLocationExternal": True, "qcomGnss": False}
  sm["gpsLocationExternal"] = SimpleNamespace(satelliteCount=9)
  acquisition.update(13.0)
  assert acquisition.satellites(13.0) == 9, "u-blox reports its own count"


def test_acquisition_progress_fills_with_satellites():
  assert nav_map.acquisition_progress(None) == nav_map.acquisition_progress(0) > 0
  assert nav_map.acquisition_progress(3) == 0.5
  assert nav_map.acquisition_progress(12) == 1.0


def test_finding_gps_shows_progress_onroad_only(view, monkeypatch):
  monkeypatch.setattr(nav_map.ui_state, "started", True)
  view._acquire_state = (3, 75)
  assert view._center_message() == ("Finding GPS", "3 satellites locked  •  1:15", 0.5)
  monkeypatch.setattr(nav_map.ui_state, "started", False)
  assert view._center_message() == ("Waiting for GPS", "The map appears once the car has a location.")
