from types import SimpleNamespace

import pytest

from openpilot.starpilot.system.android_auto import car_screen, car_ui
from openpilot.starpilot.system.android_auto.touch import TouchEvent


# ── car_screen.json ───────────────────────────────────────────────────────────

def test_settings_default_validate_and_round_trip(tmp_path):
  path = tmp_path / "car_screen.json"
  assert car_screen.load(path) == car_screen.DEFAULTS
  saved = car_screen.save({"onroad_view": "map", "map_side": "left", "camera": False, "bogus": 1}, path)
  assert saved == {"onroad_view": "map", "map_side": "left", "camera": False}
  assert car_screen.load(path) == saved
  assert car_screen.normalize({"onroad_view": "sideways", "camera": "yes"}) == car_screen.DEFAULTS
  path.write_text("{not json")
  assert car_screen.load(path) == car_screen.DEFAULTS


def test_settings_reload_live_when_the_file_changes(tmp_path):
  path = tmp_path / "car_screen.json"
  clock = [0.0]
  watcher = car_screen.CarScreenSettings(path, clock=lambda: clock[0])
  assert watcher.poll()["onroad_view"] == "split"
  car_screen.save({"onroad_view": "driving"}, path)
  assert watcher.poll()["onroad_view"] == "split", "checked at most once a second"
  clock[0] += car_screen.RELOAD_SECONDS
  assert watcher.poll()["onroad_view"] == "driving"
  path.unlink()
  clock[0] += car_screen.RELOAD_SECONDS
  assert watcher.poll() == car_screen.DEFAULTS


# ── layout ────────────────────────────────────────────────────────────────────

def rect_tuple(rect):
  return None if rect is None else (rect.x, rect.y, rect.width, rect.height)


def layout(settings, started=True, on_home=False, width=1920):
  main, map_rect = car_ui.car_layout({**car_screen.DEFAULTS, **settings}, started, on_home, width, 1080)
  return rect_tuple(main), rect_tuple(map_rect)


def test_split_puts_the_map_on_the_chosen_side():
  main, map_rect = layout({"onroad_view": "split", "map_side": "right"})
  assert main[0] == 0 and map_rect[0] == main[2] and main[2] + map_rect[2] == 1920 and 700 <= map_rect[2] <= 1100
  main, map_rect = layout({"onroad_view": "split", "map_side": "left"})
  assert map_rect[0] == 0 and main[0] == map_rect[2] and main[2] + map_rect[2] == 1920


def test_driving_only_and_map_only():
  assert layout({"onroad_view": "driving"}) == ((0, 0, 1920, 1080), None)
  assert layout({"onroad_view": "map"}) == (None, (0, 0, 1920, 1080))


def test_narrow_screens_offroad_and_the_home_screen_get_the_full_layout():
  assert layout({"onroad_view": "split"}, width=1600) == ((0, 0, 1600, 1080), None)
  assert layout({"onroad_view": "map"}, started=False) == ((0, 0, 1920, 1080), None)
  assert layout({"onroad_view": "map"}, on_home=True) == ((0, 0, 1920, 1080), None)


# ── onroad controls ───────────────────────────────────────────────────────────

class FakeParams:
  def __init__(self, values=None):
    self.values = dict(values or {})

  def get(self, key, encoding=None, **kwargs):
    return self.values.get(key)

  def get_bool(self, key):
    return bool(self.values.get(key))

  def put(self, key, value):
    self.values[key] = value

  def remove(self, key):
    self.values.pop(key, None)


@pytest.fixture
def controls():
  import json
  from openpilot.selfdrive.ui.layouts.main import MainState

  class FakeMainLayout:
    _current_mode = MainState.ONROAD
    critical = False

    def __init__(self):
      self._sidebar = SimpleNamespace(visible=False, set_visible=lambda v: setattr(self._sidebar, "visible", v))

    def _set_current_layout(self, mode):
      self._current_mode = mode

    def _set_mode_for_state(self):
      self._current_mode = MainState.ONROAD
      self._sidebar.visible = False

    def _critical_full_alert_active(self):
      return self.critical

  favorites = [
    {"id": "w", "name": "Office", "latitude": 36.1, "longitude": -115.2, "is_work": True},
    {"id": "h", "name": "House", "latitude": 36.3, "longitude": -115.3, "is_home": True},
  ]
  params = FakeParams({"MapboxSecretKey": "sk", "FavoriteDestinations": json.dumps(favorites)})
  clock = [100.0]
  result = car_ui.OnroadControls(FakeMainLayout(), params=params, params_memory=FakeParams(), clock=lambda: clock[0])
  result.clock = clock
  result.params = params
  return result


def touch_input():
  from openpilot.system.ui.lib.application import MouseEvent, MousePos
  return car_ui.TouchInput(MouseEvent, MousePos, 1920, 1080)


def screen():
  import pyray as rl
  return rl.Rectangle(0, 0, 1920, 1080)


def tap(x, y):
  return [TouchEvent("down", x / 1920, y / 1080), TouchEvent("up", x / 1920, y / 1080)]


def test_onroad_taps_on_the_drive_go_nowhere_but_the_button_opens_the_menu(controls):
  touches = touch_input()
  layout_events, menu_events = controls.route(tap(900, 500), touches, True, screen())
  assert layout_events == [] and menu_events == []

  button = controls.menu.button_rect(screen())
  layout_events, menu_events = controls.route(tap(button.x + 10, button.y + 10), touches, True, screen())
  assert layout_events == [] and [e.left_pressed for e in menu_events] == [True, False]

  controls.menu.open = True
  layout_events, menu_events = controls.route(tap(900, 500), touches, True, screen())
  assert layout_events == [] and len(menu_events) == 2, "an open menu takes every touch"


def test_offroad_and_home_screen_touches_reach_the_layout(controls):
  touches = touch_input()
  layout_events, menu_events = controls.route(tap(900, 500), touches, False, screen())
  assert len(layout_events) == 2 and menu_events == []
  controls.go_home()
  assert controls.on_home(True)
  layout_events, _ = controls.route(tap(900, 500), touches, True, screen())
  assert len(layout_events) == 2


def test_leaving_the_home_screen_cancels_a_held_touch(controls):
  touches = touch_input()
  controls.go_home()
  controls.route([TouchEvent("down", 0.5, 0.5)], touches, True, screen())
  controls.go_driving()
  layout_events, _ = controls.route([], touches, True, screen())
  assert [e.cancelled for e in layout_events] == [True]


def test_menu_lists_favorites_home_first_and_navigates(controls):
  controls.menu.open = True
  controls.update(True)
  assert [f["name"] for f in controls.menu.favorites] == ["House", "Office"]
  controls.menu.activate("favorites")
  controls.menu.activate("favorite:0")
  destination = controls.params.values["NavDestination"]
  assert "House" in destination
  assert not controls.menu.open and not controls.on_home(True)


def test_menu_home_back_and_cancel(controls):
  controls.menu.activate("home")
  assert controls.on_home(True) and controls.main_layout._sidebar.visible
  controls.update(True)
  assert controls.menu.on_home
  assert controls.menu.rows()[0].key == "driving"
  controls.menu.activate("driving")
  assert not controls.on_home(True)

  controls.params.values["NavDestination"] = '{"name": "House", "place_name": "House", "latitude": 36.3, "longitude": -115.3}'
  controls.clock[0] += car_ui.FAVORITES_REFRESH
  controls.update(True)
  assert controls.menu.nav_active and "cancel" in [row.key for row in controls.menu.rows()]
  controls.menu.activate("cancel")
  assert "NavDestination" not in controls.params.values


def test_home_screen_returns_to_the_drive_when_idle_or_on_a_critical_alert(controls):
  controls.go_home()
  controls.clock[0] += car_ui.HOME_ONROAD_TIMEOUT - 1
  controls.update(True)
  assert controls.on_home(True)
  controls.clock[0] += 2
  controls.update(True)
  assert not controls.on_home(True)

  controls.go_home()
  controls.main_layout.critical = True
  controls.update(True)
  assert not controls.on_home(True)


def test_navigation_needs_a_secret_key(controls):
  controls.params.values.pop("MapboxSecretKey")
  controls.menu.open = True
  controls.update(True)
  controls.menu.activate("favorites")
  assert controls.menu.rows()[1].title == "Navigation isn't set up"


def test_map_pane_show_and_hide_events():
  class FakeMap:
    def __init__(self):
      self.events = []

    def show_event(self):
      self.events.append("show")

    def hide_event(self):
      self.events.append("hide")

  pane = car_ui.MapPane()
  pane._map = FakeMap()
  pane.set_shown(True)
  pane.set_shown(True)
  pane.set_shown(False)
  assert pane._map.events == ["show", "hide"]



def test_opening_the_menu_shows_current_navigation_state_at_once(controls):
  controls.update(True)
  assert not controls.menu.nav_active
  controls.params.values["NavDestination"] = '{"name": "House", "place_name": "House", "latitude": 36.3, "longitude": -115.3}'
  controls.menu.activate("button")  # no update() in between: the rows must already be right
  assert controls.menu.open and "cancel" in [row.key for row in controls.menu.rows()]


def test_button_moves_off_the_sidebar_flag_on_the_home_screen(controls):
  left = controls.menu.button_rect(screen())
  assert left.x < 200
  controls.go_home()
  right = controls.menu.button_rect(screen())
  assert right.x + right.width > 1800
  controls.menu.open = True
  panel = controls.menu.panel_rect(screen())
  assert panel.x + panel.width <= 1920 and panel.x > 1000, "the panel opens toward the screen from the right corner"
  controls.go_driving()
  assert controls.menu.button_rect(screen()).x < 200
