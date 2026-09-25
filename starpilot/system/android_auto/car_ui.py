"""Car-sized StarPilot UI for Android Auto, rendered offscreen in its own process.

The comma four's own screen uses the compact UI. For the car, this process
renders the full landscape StarPilot interface (the comma 3X layout: road
camera, path, HUD, alerts, sidebar, settings) at the car's resolution in an EGL
pbuffer, without a window, display power, touch hardware or publishers. Frames
go to android_autod through the same bounded shared-memory slot as mirroring,
and car touches arrive as datagrams. Touches are honoured only offroad, the same
rule the browser streamer uses: nobody changes settings from the car screen while
driving.

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
NAV_PARAM_REFRESH = 2.0


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


class NavSplit:
  """Onroad, put the navigation map beside the driving view on a wide car screen.

  Gated by the "Navigation Widgets" toggle. The map is display-only here: car
  touches are already refused onroad.
  """

  def __init__(self, logical_w: int, logical_h: int):
    self.logical_w, self.logical_h = logical_w, logical_h
    self._enabled = False
    self._checked = -NAV_PARAM_REFRESH
    self._map = None
    self._shown = False
    self._texture = None
    self._msaa = None
    self._texture_valid = False
    self.redraws = 0

  def rects(self, started: bool, now: float):
    """(driving rect, map rect) when the split applies, else None."""
    import pyray as rl
    if now - self._checked >= NAV_PARAM_REFRESH:
      self._checked = now
      from openpilot.selfdrive.ui.ui_state import ui_state
      self._enabled = self.logical_w >= NAV_SPLIT_MIN_WIDTH and ui_state.params.get_bool("NavigationUI")
    active = started and self._enabled
    if active != self._shown and self._map is not None:
      (self._map.show_event if active else self._map.hide_event)()
    self._shown = active
    if not active:
      return None
    map_w = round(min(1100, max(700, self.logical_w * NAV_SPLIT_FRACTION)))
    return (rl.Rectangle(0, 0, self.logical_w - map_w, self.logical_h),
            rl.Rectangle(self.logical_w - map_w, 0, map_w, self.logical_h))

  def _ensure_map(self):
    if self._map is None:
      from openpilot.selfdrive.ui.onroad.starpilot.nav_map import NavMapView
      self._map = NavMapView(show_guidance=True, clip=False)
      self._map.show_event()
    return self._map

  def prepare(self, rect, scale_x: float, scale_y: float, now: float) -> None:
    """Before the frame: redraw the map into its own texture only when it changed.

    Most car frames then cost one textured quad for the map instead of its
    tiles, route, markers and text.
    """
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
  main_layout = MainLayout()
  nav_split = NavSplit(logical_w, logical_h)

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

      events = touch.events(receiver.drain(), allowed=not ui_state.started, now=now)
      gui_app._mouse_events = events
      if events:
        gui_app._last_mouse_event = events[-1]
      ui_state.update()
      split = nav_split.rects(ui_state.started, now)
      ui_state.nav_map_beside_road = split is not None
      if split is not None:
        nav_split.prepare(split[1], scale_x, scale_y, now)

      rl.begin_texture_mode(msaa.render_texture if msaa is not None else content)
      rl.clear_background(rl.Color(6, 6, 15, 255))
      rl.rl_push_matrix()
      rl.rl_scalef(scale_x, scale_y, 1.0)
      for tick in list(gui_app._nav_stack_ticks):
        tick()
      viewport = rl.Rectangle(0, 0, logical_w, logical_h)
      widgets = gui_app._nav_stack[-gui_app._nav_stack_widgets_to_render:]
      if len(widgets) > 1 and widgets[-1].covers_background(viewport):
        widgets = widgets[-1:]
      for widget in widgets:
        if widget is main_layout and split is not None:
          widget.render(split[0])
          nav_split.draw(split[1])
        else:
          widget.render(viewport)
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
    nav_split.close()
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
