"""Car-sized StarPilot UI for Android Auto, rendered offscreen in its own process.

The comma four's own screen uses the compact UI. For the car, this process
renders the full landscape StarPilot interface (the comma 3X layout: road
camera, path, HUD, alerts, sidebar, settings) at the car's resolution in an EGL
pbuffer, without a window, display power, touch hardware or publishers. Frames
go to android_autod through the same bounded shared-memory slot as mirroring,
and car touches arrive as datagrams. Offroad every touch works. Onroad the driving
view and map ignore touches; only the small quick-menu button (Navigate, end route,
home screen, back to driving) and the screens it opens accept them. Destinations are
set on one Navigate screen (car_navigate.py), onroad only below 10 mph of wheel speed.
How the drive is laid out (map beside the driving view, driving view only, map
only, camera on or off) comes from car_screen.json, set in The Galaxy and applied
live.

Started and stopped by android_autod; exits when demand stops or its parent dies.

Approach adapted from yummydirtx/openpilot ``tools/android_auto/native_renderer.py``
(MIT), pinned at 672a16f6183567c0ada53654f8527d97e1a483fa.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

from openpilot.starpilot.system.android_auto.frame_source import FrameProducer, FrameRequest
from openpilot.starpilot.system.android_auto.touch import DEFAULT_TOUCH_SOCKET, TouchEvent, TouchReceiver

LOGICAL_HEIGHT = 1080  # the landscape UI's design height
MIN_LOGICAL_WIDTH = 1600
DEMAND_GRACE = 5.0
STARTUP_DEMAND_WAIT = 15.0
NAV_SPLIT_MIN_WIDTH = 1700  # logical width; narrower car screens keep the full driving view
NAV_SPLIT_FRACTION = 0.42
HOME_ONROAD_TIMEOUT = 45.0  # back to the drive after this long untouched on the home screen
STATE_REFRESH = 5.0
MAP_ONLY_BORDER = 14.0


class NullPubMaster:
  """The comma's own UI already publishes uiDebug/bookmarkButton; a second publisher would collide."""

  def __init__(self, services, *args, **kwargs):
    self.services = services

  def send(self, *args, **kwargs) -> None:
    pass

  def wait_for_readers_to_update(self, *args, **kwargs) -> bool:
    return True

  def all_readers_updated(self, *args, **kwargs) -> bool:
    return True


def logical_size(request: FrameRequest) -> tuple[int, int, float, float]:
  """Logical UI size for the car's visible area, and the x/y scale to physical pixels."""
  visible_w, visible_h = request.width - request.margin_w, request.height - request.margin_h
  width = max(MIN_LOGICAL_WIDTH, round(LOGICAL_HEIGHT * visible_w / visible_h))
  return width, LOGICAL_HEIGHT, visible_w / width, visible_h / LOGICAL_HEIGHT


class TouchInput:
  """Turn normalized car touches into the UI's MouseEvents, withdrawing them onroad."""

  def __init__(self, mouse_event, mouse_pos, logical_w: int, logical_h: int):
    self.MouseEvent, self.MousePos = mouse_event, mouse_pos
    self.logical_w, self.logical_h = logical_w, logical_h
    self.down = False
    self.pos = mouse_pos(0, 0)
    self.accepted = self.refused = 0

  def events(self, touches: list[TouchEvent], allowed: bool, now: float) -> list:
    result = []
    if self.down and not allowed:
      result.append(self._event(now, cancelled=True))
    for touch in touches:
      if touch.kind != "cancel":
        self.pos = self.MousePos(touch.x * self.logical_w, touch.y * self.logical_h)
      if touch.kind == "down":
        if not allowed:
          self.refused += 1
          continue
        if self.down:
          result.append(self._event(now, cancelled=True))
        self.down = True
        self.accepted += 1
        result.append(self._event(now, pressed=True))
      elif not self.down:
        continue
      elif touch.kind == "move":
        result.append(self._event(now))
      elif touch.kind == "up":
        result.append(self._event(now, released=True))
      else:
        result.append(self._event(now, cancelled=True))
    return result

  def _event(self, now: float, pressed: bool = False, released: bool = False, cancelled: bool = False):
    down = not (released or cancelled)
    if not down:
      self.down = False
    return self.MouseEvent(self.pos, 0, pressed, released, down, now, cancelled)


def car_layout(settings: dict, started: bool, on_home: bool, width: int, height: int):
  """(main layout rect or None, map rect or None) in logical pixels.

  Offroad, and onroad once the driver has gone to the home screen, the main
  layout fills the screen. Onroad it follows The Galaxy's car screen settings.
  """
  import pyray as rl
  full = rl.Rectangle(0, 0, width, height)
  if not started or on_home:
    return full, None
  view = settings.get("onroad_view", "split")
  if view == "split" and width < NAV_SPLIT_MIN_WIDTH:
    view = "driving"
  if view == "driving":
    return full, None
  if view == "map":
    return None, full
  map_w = round(min(1100, max(700, width * NAV_SPLIT_FRACTION)))
  if settings.get("map_side") == "left":
    return rl.Rectangle(map_w, 0, width - map_w, height), rl.Rectangle(0, 0, map_w, height)
  return rl.Rectangle(0, 0, width - map_w, height), rl.Rectangle(width - map_w, 0, map_w, height)


class MapPane:
  """The navigation map, cached in its own texture and redrawn only when it changes.

  Most car frames then cost one textured quad for the map instead of its tiles,
  route, markers and text.
  """

  def __init__(self):
    self._map = None
    self._shown = False
    self._texture = None
    self._msaa = None
    self._texture_valid = False
    self.redraws = 0

  def set_shown(self, shown: bool) -> None:
    if shown != self._shown and self._map is not None:
      (self._map.show_event if shown else self._map.hide_event)()
    self._shown = shown

  def _ensure_map(self):
    if self._map is None:
      from openpilot.selfdrive.ui.onroad.starpilot.nav_map import NavMapView
      self._map = NavMapView(show_guidance=True, clip=False)
      self._map.show_event()
    return self._map

  def prepare(self, rect, scale_x: float, scale_y: float, now: float) -> None:
    """Before the frame: redraw into the texture only when something changed."""
    import pyray as rl
    nav_map = self._ensure_map()
    width, height = max(1, round(rect.width * scale_x)), max(1, round(rect.height * scale_y))
    if self._texture is None or (self._texture.texture.width, self._texture.texture.height) != (width, height):
      self._unload_texture()
      from openpilot.system.ui.lib.msaa import MsaaTarget
      self._texture = rl.load_render_texture(width, height)
      rl.set_texture_filter(self._texture.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      self._msaa = MsaaTarget.create(width, height)
      self._texture_valid = False
    nav_map.update()
    if self._texture_valid and not nav_map.needs_redraw(now):
      return
    rl.begin_texture_mode(self._msaa.render_texture if self._msaa is not None else self._texture)
    rl.clear_background(rl.Color(0, 0, 0, 255))
    rl.rl_push_matrix()
    rl.rl_scalef(scale_x, scale_y, 1.0)
    nav_map.render(rl.Rectangle(0, 0, rect.width, rect.height))
    rl.rl_pop_matrix()
    rl.end_texture_mode()
    if self._msaa is not None:
      self._msaa.resolve(self._texture)
    self._texture_valid = True
    self.redraws += 1

  def draw(self, rect) -> None:
    import pyray as rl
    if self._texture is None or not self._texture_valid:
      return
    texture = self._texture.texture
    # Render textures are stored bottom-up; a negative source height flips them back.
    rl.draw_texture_pro(texture, rl.Rectangle(0, 0, texture.width, -texture.height), rect, rl.Vector2(0, 0), 0.0, rl.WHITE)

  def _unload_texture(self) -> None:
    import pyray as rl
    if self._texture is not None:
      rl.unload_render_texture(self._texture)
      self._texture = None
    if self._msaa is not None:
      self._msaa.unload()
      self._msaa = None

  def close(self) -> None:
    self._unload_texture()


class MapOnlyStatus:
  """With the map filling the car screen, keep the drive's essentials on top of it:
  the engagement-coloured border, the current speed and every alert."""

  def __init__(self):
    from openpilot.selfdrive.ui.onroad.alert_renderer import AlertRenderer
    from openpilot.system.ui.lib.application import FontWeight, gui_app
    self._alerts = AlertRenderer()
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)

  def render(self, rect) -> None:
    import pyray as rl
    from openpilot.common.constants import CV
    from openpilot.selfdrive.ui.lib.starpilot_status import get_screen_edge_color
    from openpilot.selfdrive.ui.ui_state import ui_state
    from openpilot.system.ui.lib.text_measure import measure_text_cached
    rl.draw_rectangle_lines_ex(rect, MAP_ONLY_BORDER, get_screen_edge_color(ui_state))

    car_state = ui_state.sm["carState"]
    v_ego = car_state.vEgoCluster if car_state.vEgoCluster > 0 else car_state.vEgo
    speed = max(0.0, v_ego * (CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH))
    speed_text, unit = f"{speed:.0f}", "km/h" if ui_state.is_metric else "mph"
    pill = rl.Rectangle(rect.x + 28, rect.y + rect.height - 28 - 84 - 16 - 132, 170, 132)
    rl.draw_rectangle_rounded(pill, 0.3, 10, rl.Color(10, 13, 20, 228))
    speed_size = measure_text_cached(self._font_bold, speed_text, 76)
    rl.draw_text_ex(self._font_bold, speed_text, rl.Vector2(pill.x + (pill.width - speed_size.x) / 2, pill.y + 14), 76, 0, rl.WHITE)
    unit_size = measure_text_cached(self._font_medium, unit, 28)
    rl.draw_text_ex(self._font_medium, unit, rl.Vector2(pill.x + (pill.width - unit_size.x) / 2, pill.y + 92), 28, 0,
                    rl.Color(170, 180, 196, 255))

    self._alerts.render(rect)


def vehicle_speed(ui_state) -> float | None:
  """The car's own wheel speed in m/s (never GPS), or None without a recent carState."""
  sm = ui_state.sm
  if not sm.recv_frame["carState"] or not sm.alive["carState"]:
    return None
  return sm["carState"].vEgo


def navigation_speed(ui_state, environ=os.environ) -> float | None:
  """The speed the destination lock checks. A Desktop Head Unit session is pinned
  below the limit: the comma is on a desk, with no wheel speed to read."""
  from openpilot.starpilot.system.android_auto.car_screen import DHU_ENV
  if environ.get(DHU_ENV) == "1":
    return 0.0
  return vehicle_speed(ui_state)


class OnroadControls:
  """The quick menu, the Navigate screen, and where car touches go.

  Each touch is routed when the finger goes down: to the menu if it starts on the
  button (or anywhere while the menu is open), to the main layout offroad, on the
  home screen or on the Navigate screen, otherwise nowhere.
  """

  def __init__(self, main_layout, params=None, params_memory=None, clock=time.monotonic, navigate_screen_factory=None):
    from openpilot.common.params import Params
    from openpilot.selfdrive.ui.layouts.main import MainState
    from openpilot.starpilot.navigation.destination_store import NavigationDestinationStore
    from openpilot.starpilot.system.android_auto.car_menu import CarQuickMenu
    from openpilot.starpilot.system.android_auto.car_navigate import CarNavigateCard
    self.main_layout = main_layout
    self._MainState = MainState
    self._params = params or Params()
    self.store = NavigationDestinationStore(self._params, params_memory or Params(memory=True))
    self._clock = clock
    self.menu = CarQuickMenu(go_home=self.go_home, go_driving=self.go_driving,
                             open_navigate=self.open_navigate, cancel_navigation=self.cancel_navigation, on_open=self.refresh)
    self.target: str | None = None
    self.last_touch = clock()
    self._state_read = -STATE_REFRESH
    self._started = False
    self.nav_allowed = True
    self.nav_open = False
    self._nav_return_home = False
    self._navigate_screen_factory = navigate_screen_factory
    self._navigate_screen = None
    # On the car's home screen the Navigate card (Home / Work, Start, Other destination)
    # replaces the Navigate button and the Personal Records card.
    self.nav_card = CarNavigateCard(start=self.start_favorite, open_other=self.open_navigate, end_route=self.cancel_navigation)
    home = getattr(main_layout, "_layouts", {}).get(MainState.HOME)
    self._home = home
    if home is not None:
      home.set_navigate_callback(self.open_navigate)
      home.nav_card = self.nav_card

  @property
  def navigate_screen(self):
    if self._navigate_screen is None:
      factory = self._navigate_screen_factory
      if factory is None:
        from openpilot.starpilot.system.android_auto.car_navigate import CarNavigateScreen as factory
      self._navigate_screen = factory(self._route_started, self.close_navigate, self.open_offline_maps)
    return self._navigate_screen

  def on_home(self, started: bool) -> bool:
    return started and self.main_layout._current_mode != self._MainState.ONROAD

  def go_home(self) -> None:
    self.last_touch = self._clock()
    self.main_layout._set_current_layout(self._MainState.HOME)
    self.main_layout._sidebar.set_visible(True)
    self.menu.on_home = True
    self.menu.corner = "right"

  def go_driving(self) -> None:
    self.main_layout._set_mode_for_state()
    self.menu.on_home = False
    self.menu.corner = "left"

  def open_navigate(self) -> None:
    if not self.nav_allowed or self.nav_open:
      return
    self.menu.close()
    self.last_touch = self._clock()
    self._nav_return_home = not self._started or self.on_home(self._started)
    self.nav_open = True
    self.navigate_screen.show_event()

  def close_navigate(self, to_driving: bool = False) -> None:
    if not self.nav_open:
      return
    self.nav_open = False
    self._pop_overlays()
    self.navigate_screen.hide_event()
    self.refresh()
    if self._started and (to_driving or not self._nav_return_home):
      self.go_driving()

  def open_offline_maps(self) -> None:
    """From the Navigate screen to Settings > Offline Maps; its Back returns to the drive or home."""
    self.close_navigate()
    self.last_touch = self._clock()
    self.main_layout.open_starpilot_panel("OFFLINE_MAPS")
    self.menu.on_home = True
    self.menu.corner = "right"

  def _route_started(self) -> None:
    self.close_navigate(to_driving=True)

  def _pop_overlays(self) -> None:
    """Close a keyboard or dialog the Navigate screen left open over the main layout."""
    from openpilot.system.ui.lib.application import gui_app
    stack = gui_app._nav_stack
    if self.main_layout not in stack:
      return
    while len(stack) > 1 and stack[-1] is not self.main_layout:
      gui_app.pop_widget()

  def start_favorite(self, favorite: dict) -> None:
    """Home card Start: set the route, then open the drive in the chosen car screen layout."""
    if not self.nav_allowed:
      return
    self.store.set_destination(favorite)
    self.refresh()
    if self._started:
      self.go_driving()

  def cancel_navigation(self) -> None:
    self.store.clear_navigation()
    self.refresh()

  def refresh(self) -> None:
    from openpilot.starpilot.navigation.destination_store import routing_configured
    self._state_read = self._clock()
    self.menu.routing_ok = routing_configured(self._params)
    destination = self.store.active_destination()
    self.menu.nav_active = destination is not None
    self.menu.destination_name = str((destination or {}).get("place_name") or (destination or {}).get("name") or "")
    self.menu.on_home = self.on_home(self._started)
    self.nav_card.routing_ok = self.menu.routing_ok
    self.nav_card.destination_name = self.menu.destination_name
    self.nav_card.set_favorites(self.store.favorite_destinations())

  def update(self, started: bool, speed_ms: float | None = 0.0) -> None:
    from openpilot.starpilot.system.android_auto.car_navigate import locked_text, speed_allows_navigation
    now = self._clock()
    self._started = started
    self.nav_allowed = speed_allows_navigation(started, speed_ms)
    lock = "" if self.nav_allowed else locked_text()
    self.menu.locked_text = lock
    self.nav_card.locked_text = lock
    if self.nav_open and started:
      critical = self.main_layout._critical_full_alert_active()
      if not self.nav_allowed or critical or now - self.last_touch > HOME_ONROAD_TIMEOUT:
        self.close_navigate(to_driving=True)
    if now - self._state_read >= STATE_REFRESH:
      self.refresh()
    if not started:
      self.menu.close()
      return
    if self.on_home(started) and not self.nav_open:
      critical = self.main_layout._critical_full_alert_active()
      if critical or now - self.last_touch > HOME_ONROAD_TIMEOUT:
        self.go_driving()
    self.menu.on_home = self.on_home(started)
    self.menu.corner = "right" if self.menu.on_home else "left"

  def full_screen(self, started: bool) -> bool:
    """Whether the main layout (or the Navigate screen) fills the car screen instead of the drive layout."""
    return self.nav_open or self.on_home(started)

  def route(self, touches, touch_input, started: bool, screen) -> tuple[list, list]:
    """(events for the main layout or Navigate screen, events for the menu)."""
    layout_events, menu_events = [], []
    layout_ok = not started or self.full_screen(started)
    if self.target == "layout" and not layout_ok:
      layout_events += touch_input.events([], allowed=False, now=self._clock())
      self.target = None
    for touch in touches:
      if touch.kind == "down":
        x, y = touch.x * touch_input.logical_w, touch.y * touch_input.logical_h
        if started and not self.nav_open and self.menu.captures(x, y, screen):
          self.target = "menu"
        elif layout_ok:
          self.target = "layout"
        else:
          self.target = None
        self.last_touch = self._clock()
      events = touch_input.events([touch], allowed=self.target is not None, now=self._clock())
      (menu_events if self.target == "menu" else layout_events).extend(events)
    return layout_events, menu_events


def neutralize_side_effects() -> None:
  """Must run before any UI module is imported."""
  os.environ["BIG"] = "1"  # landscape layout; read by application.py at import
  for name in ("cereal.messaging", "openpilot.cereal.messaging"):
    try:
      module = __import__(name, fromlist=["PubMaster"])
      module.PubMaster = NullPubMaster
    except ImportError:
      pass


def wait_for_request(producer: FrameProducer) -> FrameRequest:
  deadline = time.monotonic() + STARTUP_DEMAND_WAIT
  while time.monotonic() < deadline:
    producer._next_open_check = 0.0
    request = producer.pending_request()
    if request is not None:
      return request
    time.sleep(0.1)
  raise TimeoutError("android_autod never requested car frames")


def run(frames_path: str, touch_path: str) -> int:
  if os.geteuid() == 0:
    raise RuntimeError("The car UI must run as the comma user, not root")
  parent = os.getppid()
  try:
    os.nice(10)  # never compete with openpilot's own processes
  except OSError:
    pass
  neutralize_side_effects()
  producer = FrameProducer(frames_path)
  request = wait_for_request(producer)
  logical_w, logical_h, scale_x, scale_y = logical_size(request)
  visible_w, visible_h = request.width - request.margin_w, request.height - request.margin_h

  from openpilot.starpilot.system.android_auto.headless_egl import HeadlessContext
  context = HeadlessContext(request.width, request.height)
  import pyray as rl
  from openpilot.system.ui.lib.application import MouseEvent, MousePos, gui_app
  from openpilot.selfdrive.ui.ui_state import device, ui_state

  gui_app._width, gui_app._height = logical_w, logical_h
  gui_app._scale = scale_y
  gui_app._render_texture = None
  gui_app._load_fonts()
  gui_app._set_styles()
  gui_app._patch_text_functions()
  gui_app._patch_scissor_mode()
  device.update = lambda: None               # display power and brightness belong to the comma's own UI
  ui_state.prime_state.start = lambda: None  # no second comma API poller
  ui_state.ui_params.start()
  from openpilot.selfdrive.ui.layouts.main import MainLayout
  from openpilot.starpilot.system.android_auto.car_screen import CarScreenSettings
  main_layout = MainLayout()
  map_pane = MapPane()
  car_settings = CarScreenSettings()
  controls = OnroadControls(main_layout)
  map_status: MapOnlyStatus | None = None

  content = rl.load_render_texture(visible_w, visible_h)
  from openpilot.system.ui.lib.msaa import MsaaTarget, install_watertight_shapes
  msaa = MsaaTarget.create(visible_w, visible_h)
  if msaa is not None:
    install_watertight_shapes()
  output = rl.load_render_texture(request.width, request.height)
  touch = TouchInput(MouseEvent, MousePos, logical_w, logical_h)
  # A few widgets (list buttons, StarPilot sliders) poll raylib's pointer directly;
  # without a window it would stay at 0,0, so report the car touch position instead.
  rl.get_mouse_position = lambda: rl.Vector2(touch.pos.x, touch.pos.y)
  receiver = TouchReceiver(touch_path)
  last_demand = time.monotonic()
  stop = {"flag": False}
  signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
  print(f"car ui {logical_w}x{logical_h} -> {visible_w}x{visible_h} in {request.width}x{request.height}", flush=True)
  try:
    while not stop["flag"] and os.getppid() == parent:
      now = time.monotonic()
      pending = producer.pending_request(now)
      if pending is None:
        if now - last_demand > DEMAND_GRACE:
          return 0
        time.sleep(0.05)
        continue
      last_demand = now
      if pending != request:
        return 3  # new geometry: android_autod starts a fresh renderer
      now_ns = time.monotonic_ns()
      if not producer.due(request, now_ns):
        time.sleep(0.002)
        continue

      viewport = rl.Rectangle(0, 0, logical_w, logical_h)
      ui_state.update()
      started = ui_state.started
      controls.update(started, navigation_speed(ui_state))
      layout_events, menu_events = controls.route(receiver.drain(), touch, started, viewport)
      settings = car_settings.poll()
      main_rect, map_rect = car_layout(settings, started, controls.full_screen(started), logical_w, logical_h)
      ui_state.nav_map_beside_road = main_rect is not None and map_rect is not None
      ui_state.car_camera_off = started and not settings["camera"]
      map_pane.set_shown(map_rect is not None)
      if map_rect is not None:
        map_pane.prepare(map_rect, scale_x, scale_y, now)

      rl.begin_texture_mode(msaa.render_texture if msaa is not None else content)
      rl.clear_background(rl.Color(6, 6, 15, 255))
      rl.rl_push_matrix()
      rl.rl_scalef(scale_x, scale_y, 1.0)
      gui_app._mouse_events = layout_events
      if layout_events:
        gui_app._last_mouse_event = layout_events[-1]
      for tick in list(gui_app._nav_stack_ticks):
        tick()
      widgets = gui_app._nav_stack[-gui_app._nav_stack_widgets_to_render:]
      if len(widgets) > 1 and widgets[-1].covers_background(viewport):
        widgets = widgets[-1:]
      for widget in widgets:
        if widget is main_layout and controls.nav_open:
          controls.navigate_screen.render(viewport)
        elif widget is main_layout:
          if main_rect is not None:
            widget.render(main_rect)
          if map_rect is not None:
            map_pane.draw(map_rect)
            if main_rect is None:
              map_status = map_status or MapOnlyStatus()
              map_status.render(map_rect)
        else:
          widget.render(viewport)
      if started and not controls.nav_open:
        gui_app._mouse_events = menu_events
        controls.menu.render(viewport)
      rl.rl_pop_matrix()
      rl.end_texture_mode()
      if msaa is not None:
        msaa.resolve(content)

      # Centre inside the car's margins; drawing with a positive source height
      # flips on the GPU so the readback is top-down for the encoder.
      rl.begin_texture_mode(output)
      rl.clear_background(rl.Color(6, 6, 15, 255))
      rl.draw_texture_pro(content.texture, rl.Rectangle(0, 0, visible_w, visible_h),
                          rl.Rectangle(request.margin_w // 2, request.margin_h // 2, visible_w, visible_h),
                          rl.Vector2(0, 0), 0.0, rl.WHITE)
      rl.end_texture_mode()
      gui_app._populate_render_texture_cache()
      image = rl.load_image_from_texture(output.texture)
      try:
        producer.publish(request, rl.ffi.buffer(image.data, request.width * request.height * 4), now_ns)
      finally:
        rl.unload_image(image)
      gui_app._frame += 1
    return 0
  finally:
    receiver.close()
    map_pane.close()
    if msaa is not None:
      msaa.unload()
    rl.unload_render_texture(content)
    rl.unload_render_texture(output)
    context.close()


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--frames", required=True)
  parser.add_argument("--touch", default=DEFAULT_TOUCH_SOCKET)
  args = parser.parse_args()
  return run(args.frames, args.touch)


if __name__ == "__main__":
  sys.exit(main())
