import sys
from types import ModuleType, SimpleNamespace

import pytest

from openpilot.starpilot.system.android_auto import car_screen, car_ui
from openpilot.starpilot.system.android_auto.touch import TouchEvent


# ── car_screen.json ───────────────────────────────────────────────────────────

def test_settings_default_validate_and_round_trip(tmp_path):
  path = tmp_path / "car_screen.json"
  assert car_screen.load(path) == car_screen.DEFAULTS
  saved = car_screen.save({"onroad_view": "map", "map_side": "left", "camera": False,
                           "blind_spot_monitors": False, "blind_spot_min_speed_ms": 8.0, "bogus": 1}, path)
  assert saved == {"onroad_view": "map", "map_side": "left", "camera": False,
                   "blind_spot_monitors": False, "blind_spot_min_speed_ms": 8.0}
  assert car_screen.load(path) == saved
  assert car_screen.normalize({"onroad_view": "sideways", "camera": "yes", "blind_spot_monitors": "yes",
                               "blind_spot_min_speed_ms": -1}) == car_screen.DEFAULTS
  path.write_text("{not json")
  assert car_screen.load(path) == car_screen.DEFAULTS


def test_blind_spot_monitors_support_off_always_and_minimum_speed():
  assert car_screen.blind_spot_monitors_visible(car_screen.DEFAULTS, None)
  assert not car_screen.blind_spot_monitors_visible({**car_screen.DEFAULTS, "blind_spot_monitors": False}, 30.0)
  settings = {**car_screen.DEFAULTS, "blind_spot_min_speed_ms": 10.0}
  assert not car_screen.blind_spot_monitors_visible(settings, None)
  assert not car_screen.blind_spot_monitors_visible(settings, 9.99)
  assert car_screen.blind_spot_monitors_visible(settings, 10.0)


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

    def open_starpilot_panel(self, key):
      self.opened_panel = key
      self._current_mode = MainState.SETTINGS

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
  result = car_ui.OnroadControls(FakeMainLayout(), params=params, params_memory=FakeParams(), clock=lambda: clock[0],
                                 navigate_screen_factory=FakeNavigateScreen)
  result.clock = clock
  result.params = params
  return result


class FakeNavigateScreen:
  def __init__(self, on_started, on_close, on_offline_maps):
    self.on_started, self.on_close, self.on_offline_maps = on_started, on_close, on_offline_maps
    self.events = []

  def show_event(self):
    self.events.append("show")

  def hide_event(self):
    self.events.append("hide")


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


def row_keys(controls):
  return [row.key for row in controls.menu.rows()]


def test_menu_puts_navigate_first(controls):
  controls.update(True)
  assert row_keys(controls) == ["navigate", "home"]
  assert controls.menu.rows()[0].enabled


def test_navigate_opens_the_navigate_screen_and_takes_touches(controls):
  touches = touch_input()
  controls.update(True, 0.0)
  controls.menu.open = True
  controls.menu.activate("navigate")
  assert controls.nav_open and not controls.menu.open
  assert controls.navigate_screen.events == ["show"]
  assert controls.full_screen(True)
  button = controls.menu.button_rect(screen())
  layout_events, menu_events = controls.route(tap(button.x + 10, button.y + 10), touches, True, screen())
  assert len(layout_events) == 2 and menu_events == [], "the menu button is hidden under the Navigate screen"

  controls.navigate_screen.on_close()
  assert not controls.nav_open and controls.navigate_screen.events == ["show", "hide"]
  assert not controls.on_home(True)


def test_starting_a_route_returns_to_the_drive_even_from_the_home_screen(controls):
  controls.update(True, 0.0)
  controls.go_home()
  controls.open_navigate()
  controls.navigate_screen.on_close()
  assert controls.on_home(True), "Back returns to where the driver came from"
  controls.open_navigate()
  controls.navigate_screen.on_started()
  assert not controls.nav_open and not controls.on_home(True)


def test_home_screen_navigate_button_opens_the_same_screen():
  from openpilot.selfdrive.ui.layouts.main import MainState

  class Home:
    def __init__(self):
      self.nav_card = None
      self.callback = None

    def set_navigate_callback(self, callback):
      self.callback = callback

  home = Home()
  main_layout = SimpleNamespace(_layouts={MainState.HOME: home}, _current_mode=MainState.HOME)
  controls = car_ui.OnroadControls(main_layout, params=FakeParams({"MapboxSecretKey": "sk"}), params_memory=FakeParams(),
                                   navigate_screen_factory=FakeNavigateScreen)
  assert home.callback == controls.open_navigate
  assert home.nav_card is controls.nav_card, "the Navigate card replaces Personal Records on the car"
  controls.update(False)
  home.callback()
  assert controls.nav_open and controls.full_screen(False)


MPH = 0.44704


@pytest.mark.parametrize("started, speed, allowed", [
  (False, None, True),      # offroad: always
  (True, 0.0, True),
  (True, 9.9 * MPH, True),
  (True, 10.0 * MPH, False),
  (True, -12.0 * MPH, False),  # reversing fast still counts
  (True, None, False),      # no wheel speed onroad: treat as moving
])
def test_speed_gate_uses_wheel_speed_below_10_mph(started, speed, allowed):
  from openpilot.starpilot.system.android_auto.car_navigate import speed_allows_navigation
  assert speed_allows_navigation(started, speed) is allowed


def test_moving_locks_navigate_and_closes_the_screen(controls):
  controls.update(True, 5 * MPH)
  controls.open_navigate()
  assert controls.nav_open
  controls.update(True, 15 * MPH)
  assert not controls.nav_open and not controls.on_home(True)
  row = controls.menu.rows()[0]
  assert row.key == "navigate" and not row.enabled and "10 mph" in row.subtitle
  controls.open_navigate()
  assert not controls.nav_open, "can't be opened while moving"
  controls.update(True, 3 * MPH)
  assert controls.menu.rows()[0].enabled


def test_navigate_screen_closes_when_idle_or_on_a_critical_alert(controls):
  controls.update(True, 0.0)
  controls.open_navigate()
  controls.clock[0] += car_ui.HOME_ONROAD_TIMEOUT + 1
  controls.update(True, 0.0)
  assert not controls.nav_open
  controls.open_navigate()
  controls.main_layout.critical = True
  controls.update(True, 0.0)
  assert not controls.nav_open


def test_ending_a_route_works_at_any_speed(controls):
  controls.params.values["NavDestination"] = '{"name": "House", "place_name": "House", "latitude": 36.3, "longitude": -115.3}'
  controls.update(True, 40 * MPH)
  assert row_keys(controls) == ["navigate", "cancel", "home"]
  assert "House" in controls.menu.destination_name
  controls.menu.activate("cancel")
  assert "NavDestination" not in controls.params.values


def test_menu_home_back_and_cancel(controls):
  controls.menu.activate("home")
  assert controls.on_home(True) and controls.main_layout._sidebar.visible
  controls.update(True)
  assert controls.menu.on_home
  assert row_keys(controls)[-1] == "driving"
  controls.menu.activate("driving")
  assert not controls.on_home(True)

  controls.params.values["NavDestination"] = '{"name": "House", "place_name": "House", "latitude": 36.3, "longitude": -115.3}'
  controls.clock[0] += car_ui.STATE_REFRESH
  controls.update(True)
  assert controls.menu.nav_active and "cancel" in row_keys(controls)
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
  row = controls.menu.rows()[0]
  assert row.key == "navigate" and not row.enabled and "Mapbox" in row.subtitle


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


def test_android_auto_map_enables_navigation_waiting_state(monkeypatch):
  created = []

  class FakeMap:
    def __init__(self, **kwargs):
      created.append(kwargs)

    def show_event(self):
      pass

  module = ModuleType("openpilot.selfdrive.ui.onroad.starpilot.nav_map")
  module.NavMapView = FakeMap
  monkeypatch.setitem(sys.modules, module.__name__, module)

  pane = car_ui.MapPane()
  assert pane._ensure_map() is pane._map
  assert created == [{"show_guidance": True, "clip": False, "show_navigation_waiting": True}]



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


# ── home screen Navigate card ─────────────────────────────────────────────────

def test_card_offers_home_and_work(controls):
  controls.update(True, 0.0)
  card = controls.nav_card
  assert card.home["name"] == "House" and card.work["name"] == "Office"
  assert not card.allowed("start"), "Start waits for Home or Work"
  card.activate("work")
  assert card.selected == "work" and card.allowed("start")
  card.activate("work")
  assert card.selected is None, "tapping again deselects"


def test_card_start_goes_to_the_drive_layout(controls):
  controls.update(True, 0.0)
  controls.go_home()
  card = controls.nav_card
  card.activate("home")
  card.activate("start")
  assert "House" in controls.params.values["NavDestination"]
  assert not controls.on_home(True), "Start opens the drive in the chosen car screen layout"
  assert card.selected is None and card.destination_name == "House" and card.allowed("end")
  card.activate("end")
  assert "NavDestination" not in controls.params.values and not card.destination_name


def test_card_start_offroad_sets_the_route_for_the_next_drive(controls):
  controls.update(False)
  controls.nav_card.activate("work")
  controls.nav_card.activate("start")
  assert "Office" in controls.params.values["NavDestination"]


def test_card_other_opens_the_navigate_page(controls):
  controls.update(True, 0.0)
  controls.go_home()
  controls.nav_card.activate("other")
  assert controls.nav_open


def test_card_locks_above_10_mph_but_can_end_a_route(controls):
  controls.params.values["NavDestination"] = '{"name": "House", "place_name": "House", "latitude": 36.3, "longitude": -115.3}'
  controls.update(True, 0.0)
  card = controls.nav_card
  card.activate("work")
  controls.update(True, 20 * MPH)
  assert "10 mph" in card.status_text()
  assert not any(card.allowed(key) for key in ("home", "work", "start", "other"))
  card.activate("start")
  assert "House" in controls.params.values["NavDestination"], "Start does nothing while moving"
  card.activate("end")
  assert "NavDestination" not in controls.params.values


def test_card_without_a_work_favorite(controls):
  import json
  controls.params.values["FavoriteDestinations"] = json.dumps([{"id": "h", "name": "House", "latitude": 36.3, "longitude": -115.3,
                                                                "is_home": True}])
  controls.update(True, 0.0)
  card = controls.nav_card
  assert card.work is None and not card.allowed("work") and card.allowed("home")


def test_dhu_session_is_pinned_below_the_limit():
  from openpilot.starpilot.system.android_auto.car_screen import DHU_ENV
  sm = SimpleNamespace(recv_frame={"carState": 0}, alive={"carState": False})
  ui_state = SimpleNamespace(sm=sm)
  assert car_ui.navigation_speed(ui_state, environ={}) is None, "a real car without carState stays locked"
  assert car_ui.navigation_speed(ui_state, environ={DHU_ENV: "1"}) == 0.0


# ── Navigate screen ───────────────────────────────────────────────────────────

class FakePage:
  def __init__(self, on_started=None, **kwargs):
    from openpilot.starpilot.navigation.destination_store import same_destination
    self._same = same_destination
    self._draft_destination = None
    self._active_destination = None
    self._search_results = []
    self._favorites = []
    self._recent_destinations = []
    self._query = ""
    self._search_loading = False
    self._search_error = ""
    self._preview_routes = []
    self._preview_route_index = 0
    self._selected_favorite = None
    self.targets = []

  def _same_destination(self, left, right):
    return self._same(left, right)

  def _favorite_for_destination(self, destination):
    return next((f for f in self._favorites if self._same(f, destination)), None)

  @staticmethod
  def _duration_text(seconds):
    return f"{round(seconds / 60)} min"

  def _activate_navigation_target(self, target):
    self.targets.append(target)


@pytest.fixture
def nav_screen(monkeypatch):
  from openpilot.selfdrive.ui.layouts.settings.starpilot import navigation
  from openpilot.starpilot.system.android_auto.car_navigate import CarNavigateScreen
  monkeypatch.setattr(navigation, "StarPilotNavigationLayout", FakePage)
  closed = []
  screen = CarNavigateScreen(on_started=lambda: None, on_close=lambda: closed.append(True), on_offline_maps=lambda: closed.append("offline"))
  screen.closed = closed
  return screen


def test_screen_lists_results_favorites_and_recents_with_full_addresses(nav_screen):
  from openpilot.selfdrive.ui.layouts.settings.starpilot.navigation import SearchResult
  page = nav_screen.page
  page._search_results = [SearchResult("Blue Bottle Coffee", "1 Ferry Building, San Francisco, CA 94111, United States", 37.8, -122.4)]
  page._favorites = [
    {"id": "o", "name": "Office", "place_name": "500 Howard St, San Francisco", "latitude": 37.7, "longitude": -122.3, "is_work": True},
    {"id": "g", "name": "Gym", "latitude": 37.6, "longitude": -122.2},
  ]
  page._recent_destinations = [{"name": "Airport", "place_name": "Airport", "latitude": 37.6, "longitude": -122.4}]
  page._draft_destination = {"name": "Gym", "latitude": 37.6, "longitude": -122.2}

  sections = dict(nav_screen.list_rows())
  assert list(sections) == ["Results", "Favorites", "Recent"]
  result = sections["Results"][0]
  assert result.target == "result:0" and result.subtitle.startswith("1 Ferry Building")
  office, gym = sorted(sections["Favorites"], key=lambda row: row.title != "Office")
  assert office.badge == "Work" and office.subtitle == "500 Howard St, San Francisco"
  assert gym.selected and gym.subtitle == "" and gym.target == "favorite:g"
  assert sections["Recent"][0].subtitle == "", "no subtitle that just repeats the title"
  assert nav_screen.notice() is None


def test_screen_notices(nav_screen):
  page = nav_screen.page
  assert nav_screen.notice()[0] == "No places yet"
  page._query = "zzzz"
  assert nav_screen.notice()[0] == "No matches"
  page._search_loading = True
  assert nav_screen.notice()[0] == "Searching…"


def test_screen_route_and_favorite_chips(nav_screen):
  page = nav_screen.page
  page._draft_destination = {"name": "Gym", "latitude": 37.6, "longitude": -122.2}
  page._preview_routes = [SimpleNamespace(total_duration=600), SimpleNamespace(total_duration=900)]
  page._preview_route_index = 1
  assert nav_screen.route_chips() == [("route:0", "Fastest · 10 min", False), ("route:1", "Route 2 · 15 min", True)]
  assert [chip[1:] for chip in nav_screen.favorite_chips()] == [("Save", False), ("Home", False), ("Work", False)]
  page._favorites = [{"id": "g", "name": "Gym", "latitude": 37.6, "longitude": -122.2, "is_home": True}]
  assert [chip[1:] for chip in nav_screen.favorite_chips()] == [("Saved", True), ("Home", True), ("Work", False)]
  page._preview_routes = page._preview_routes[:1]
  assert nav_screen.route_chips() == [], "no chips without a choice"


def test_screen_taps_activate_and_drags_scroll(nav_screen):
  import pyray as rl
  from openpilot.system.ui.lib.application import MouseEvent, MousePos
  nav_screen._list_rect = rl.Rectangle(0, 200, 800, 600)
  nav_screen._content_height = 1400
  nav_screen._targets = [("back", rl.Rectangle(0, 0, 80, 80), False), ("favorite:g", rl.Rectangle(0, 300, 800, 108), True)]

  nav_screen._handle_mouse_press(MousePos(100, 350))
  nav_screen._handle_mouse_release(MousePos(100, 352))
  assert nav_screen.page.targets == ["favorite:g"]

  nav_screen._handle_mouse_press(MousePos(100, 350))
  nav_screen._handle_mouse_event(MouseEvent(MousePos(100, 250), 0, False, False, True, 0.0))
  nav_screen._handle_mouse_release(MousePos(100, 250))
  assert nav_screen.page.targets == ["favorite:g"], "a drag scrolls instead of tapping"
  assert nav_screen.scroll == 100

  nav_screen._handle_mouse_press(MousePos(40, 40))
  nav_screen._handle_mouse_release(MousePos(40, 40))
  assert nav_screen.closed == [True]


def test_navigate_screen_links_to_offline_maps_settings(controls, nav_screen):
  nav_screen.activate("offline_maps")
  assert nav_screen.closed == ["offline"]

  controls.update(True, 0.0)
  controls.open_navigate()
  controls.navigate_screen.on_offline_maps()
  assert not controls.nav_open and controls.main_layout.opened_panel == "OFFLINE_MAPS"
  assert controls.on_home(True), "Settings takes touches onroad like the home screen"
  touches = touch_input()
  layout_events, _ = controls.route(tap(900, 500), touches, True, screen())
  assert len(layout_events) == 2
