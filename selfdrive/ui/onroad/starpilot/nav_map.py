"""Navigation map: Mapbox raster tiles, the route, the car and turn guidance.

Shown by the Navigation settings panel (destination and route preview) and, in
the Android Auto car view, beside the driving view while onroad. It follows the
car heading-up while driving and shows the desire the driving model is being
fed, so a route turn is visible from the moment the model acts on it.

Kept cheap on purpose: tiles are downloaded and decoded on worker threads, at
most two textures are uploaded per frame, the route is projected with numpy and
only its visible part is drawn, and GPS positions are dead-reckoned between the
planner's 4 Hz updates rather than polled faster.
"""

from __future__ import annotations

import datetime
import json
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pyray as rl

from openpilot.common.params import Params
from openpilot.selfdrive.ui.onroad.starpilot.navigation_card import (
  ASSETS_PATH,
  FALLBACK_ICON,
  _format_distance,
  _modifier_suffix,
  _normalize_maneuver_type,
)
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.starpilot.navigation.destination_store import parse_destination_json
from openpilot.starpilot.navigation.offline_maps import OfflineMaps
from openpilot.starpilot.navigation.map_tiles import (
  TILE_SIZE,
  TileKey,
  DEFAULT_STYLE,
  TileCache,
  TileService,
  default_cache_dir,
  meters_per_world_unit,
  offline_root,
  tiles_covering,
  world_xy,
)
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

MAX_TEXTURES = 40
UPLOADS_PER_FRAME = 2
MAX_FALLBACK_LEVELS = 6
GPS_POLL_SECONDS = 0.2
GPS_STALE_SECONDS = 3.0
DEAD_RECKON_LIMIT = 1.0
NAV_STALE_SECONDS = 3.5
ROUTE_STALE_SECONDS = 30.0  # navigationd republishes the route every few seconds while it has one
TOKEN_REFRESH_SECONDS = 30.0
OFFLINE_STATUS_SECONDS = 2.0
MAP_MAX_FPS = 15.0       # redraw cap when drawing into a cached texture
MAP_IDLE_REDRAW = 1.0    # still redraw this often so the clock and badges stay current

FOLLOW_SPEEDS = (0.0, 8.0, 15.0, 25.0, 35.0)
FOLLOW_ZOOMS = (16.3, 16.0, 15.4, 14.8, 14.3)
IDLE_ZOOM = 15.0
PREVIEW_MAX_ZOOM = 16.0
FOLLOW_ANCHOR_Y = 0.70

MAP_BACKGROUND = rl.Color(20, 26, 38, 255)
ROUTE_CASING = rl.Color(12, 40, 92, 255)
ROUTE_FILL = rl.Color(64, 150, 255, 255)
ROUTE_TRAVELED = rl.Color(120, 130, 150, 200)
ROUTE_ALTERNATE = rl.Color(130, 145, 170, 190)
ROUTE_ALTERNATE_CASING = rl.Color(40, 48, 64, 230)
CAR_FILL = rl.Color(255, 255, 255, 255)
CAR_ACCENT = rl.Color(64, 150, 255, 255)
DEST_FILL = rl.Color(236, 72, 94, 255)
CARD_BG = rl.Color(10, 13, 20, 228)
CARD_BORDER = rl.Color(255, 255, 255, 26)
TEXT = rl.Color(240, 244, 250, 255)
SUBTEXT = rl.Color(170, 180, 196, 255)
DESIRE_ROUTE = rl.Color(52, 199, 120, 255)
DESIRE_DRIVER = rl.Color(64, 150, 255, 255)
DESIRE_HINT = rl.Color(232, 170, 70, 255)
BADGE_WARN = rl.Color(232, 170, 70, 255)

DESIRE_NAMES = {
  1: "Turn left",
  2: "Turn right",
  3: "Lane change left",
  4: "Lane change right",
  5: "Keep left",
  6: "Keep right",
}
TURN_HINT_DISTANCE = 160.0
KEEP_HINT_DISTANCE = 400.0


def desire_line(started: bool, desire: int, nav_desire: int, nav: dict | None) -> tuple[str, str, rl.Color] | None:
  """(label, detail, color) for what the driving model is being told, and why."""
  if not started:
    return None
  name = DESIRE_NAMES.get(desire)
  if name is not None:
    from_route = nav_desire == desire
    return f"Model: {name}", "From the route" if from_route else "From the turn signal", DESIRE_ROUTE if from_route else DESIRE_DRIVER
  if nav is None:
    return None
  modifier, distance = nav["modifier"], nav["distance"]
  if nav["type"] == "turn" and modifier in ("left", "sharpLeft", "right", "sharpRight") and distance <= TURN_HINT_DISTANCE:
    side = "left" if modifier in ("left", "sharpLeft") else "right"
    return f"Route turn {side} ahead", f"Signal {side} and the model will take it", DESIRE_HINT
  if modifier in ("slightLeft", "slightRight") and distance <= KEEP_HINT_DISTANCE:
    side = "left" if modifier == "slightLeft" else "right"
    return f"Keep {side} ahead", f"Nudge the wheel {side} to move over", DESIRE_HINT
  return None


def _decode_tile(data: bytes, extension: str):
  """Runs on a tile worker thread: PNG/JPEG bytes to a CPU-side raylib image."""
  image = rl.load_image_from_memory(extension, data, len(data))
  if image.width <= 0 or image.height <= 0:
    return None
  return image


class TileTextures:
  """GPU textures for decoded tiles, shared by every map in this process."""

  def __init__(self):
    self._params = Params()
    self._token = ""
    self._token_checked = -math.inf
    self._token_lock = threading.Lock()
    self._textures: OrderedDict[TileKey, rl.Texture] = OrderedDict()
    self.offline_maps = OfflineMaps()
    # navtilesd promotes requested driven tiles from the regular cache into the
    # same pinned store used by explicit offline areas.
    offline = TileCache(offline_root(), DEFAULT_STYLE, max_bytes=None)
    regular = TileCache(default_cache_dir(), DEFAULT_STYLE, pinned=offline)
    self._save_viewed = False
    self._save_viewed_read = -math.inf
    self.service = TileService(self._read_token, decode=_decode_tile, cache=regular, write_through=self._save_driven_tile)
    self._offline_status: dict = {}
    self._offline_status_read = -math.inf

  def _save_driven_tile(self, key: TileKey, data: bytes) -> bool | None:
    del data
    now = time.monotonic()
    if now - self._save_viewed_read >= OFFLINE_STATUS_SECONDS:
      self._save_viewed_read = now
      self._save_viewed = self.offline_maps.save_viewed_cache()
    if not self._save_viewed:
      return None
    return self.offline_maps.mark_auto_saved(key)

  def offline_status(self) -> dict:
    now = time.monotonic()
    if now - self._offline_status_read >= OFFLINE_STATUS_SECONDS:
      self._offline_status_read = now
      self._offline_status = self.offline_maps.status()
    return self._offline_status

  @property
  def has_token(self) -> bool:
    return bool(self._read_token())

  def _read_token(self) -> str:
    with self._token_lock:
      now = time.monotonic()
      if now - self._token_checked > TOKEN_REFRESH_SECONDS:
        self._token_checked = now
        try:
          self._token = str(self._params.get("MapboxPublicKey", encoding="utf-8") or "").strip()
        except Exception:
          self._token = ""
      return self._token

  def upload(self) -> int:
    results = self.service.poll(UPLOADS_PER_FRAME)
    for key, image in results:
      texture = rl.load_texture_from_image(image)
      rl.unload_image(image)
      rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      rl.set_texture_wrap(texture, rl.TextureWrap.TEXTURE_WRAP_CLAMP)
      old = self._textures.pop(key, None)
      if old is not None:
        rl.unload_texture(old)
      self._textures[key] = texture
    while len(self._textures) > MAX_TEXTURES:
      key, texture = self._textures.popitem(last=False)
      rl.unload_texture(texture)
      self.service.forget(key)
    return len(results)

  def best(self, key: TileKey) -> tuple[rl.Texture, rl.Rectangle] | None:
    """The tile itself, or the part of the closest loaded ancestor that covers it."""
    candidate: TileKey | None = key
    for depth in range(MAX_FALLBACK_LEVELS + 1):
      if candidate is None:
        return None
      texture = self._textures.get(candidate)
      if texture is not None:
        self._textures.move_to_end(candidate)
        size = TILE_SIZE / (1 << depth)
        mask = (1 << depth) - 1
        return texture, rl.Rectangle((key.x & mask) * size, (key.y & mask) * size, size, size)
      candidate = candidate.parent()
    return None


_shared_tiles: TileTextures | None = None


def shared_tiles() -> TileTextures:
  global _shared_tiles
  if _shared_tiles is None:
    _shared_tiles = TileTextures()
  return _shared_tiles


@dataclass
class Camera:
  x: float = 0.0        # zoom-0 world position shown at the anchor
  y: float = 0.0
  zoom: float = IDLE_ZOOM
  bearing: float = 0.0  # this compass heading points up the screen

  def scale(self, tile_scale: float) -> float:
    return (2.0 ** self.zoom) * tile_scale

  def to_screen(self, wx, wy, anchor: tuple[float, float], tile_scale: float):
    """World (scalars or numpy arrays) to screen. Heading-up rotates by -bearing."""
    s = self.scale(tile_scale)
    dx, dy = (wx - self.x) * s, (wy - self.y) * s
    c, sn = math.cos(math.radians(self.bearing)), math.sin(math.radians(self.bearing))
    return anchor[0] + dx * c + dy * sn, anchor[1] - dx * sn + dy * c

  def to_world(self, sx: float, sy: float, anchor: tuple[float, float], tile_scale: float) -> tuple[float, float]:
    s = self.scale(tile_scale)
    rx, ry = sx - anchor[0], sy - anchor[1]
    c, sn = math.cos(math.radians(self.bearing)), math.sin(math.radians(self.bearing))
    return self.x + (rx * c - ry * sn) / s, self.y + (rx * sn + ry * c) / s


def _angle_delta(target: float, current: float) -> float:
  return (target - current + 540.0) % 360.0 - 180.0


def _route_world(points: Sequence[tuple[float, float]]) -> np.ndarray:
  if not points:
    return np.zeros((0, 2))
  return np.array([world_xy(lat, lon) for lat, lon in points], dtype=np.float64)


def _visible_runs(sx: np.ndarray, sy: np.ndarray, rect: rl.Rectangle, margin: float) -> list[tuple[int, int]]:
  """Index ranges [start, end] of consecutive route segments that touch the (padded) rect."""
  if len(sx) < 2:
    return []
  left, right = rect.x - margin, rect.x + rect.width + margin
  top, bottom = rect.y - margin, rect.y + rect.height + margin
  x0, x1, y0, y1 = sx[:-1], sx[1:], sy[:-1], sy[1:]
  visible = (np.maximum(x0, x1) >= left) & (np.minimum(x0, x1) <= right) & \
            (np.maximum(y0, y1) >= top) & (np.minimum(y0, y1) <= bottom)
  indices = np.flatnonzero(visible)
  if len(indices) == 0:
    return []
  breaks = np.flatnonzero(np.diff(indices) > 1)
  starts = np.concatenate(([indices[0]], indices[breaks + 1]))
  ends = np.concatenate((indices[breaks], [indices[-1]]))
  return [(int(s), int(e) + 1) for s, e in zip(starts, ends, strict=True)]


def _draw_polyline(sx: np.ndarray, sy: np.ndarray, start: int, end: int, styles: Sequence[tuple[float, rl.Color]],
                   caps: tuple[bool, bool] = (False, False), grid: float = 3.0) -> None:
  """Draw points start..end once per (thickness, color) style, e.g. a casing then a fill."""
  xs, ys = sx[start:end + 1], sy[start:end + 1]
  if len(xs) < 2:
    return
  # Drop consecutive points that land in the same few-pixel cell; zoomed-out routes collapse to a handful.
  cells = np.floor(np.stack((xs, ys), axis=1) / grid)
  keep = np.ones(len(xs), dtype=bool)
  keep[1:-1] = np.any(cells[1:-1] != cells[:-2], axis=1)
  points = np.ascontiguousarray(np.stack((xs[keep], ys[keep]), axis=1), dtype=np.float32)
  count = len(points)
  if count < 2:
    return
  pointer = rl.ffi.cast("Vector2 *", rl.ffi.from_buffer(points))
  ends = [rl.Vector2(float(points[i, 0]), float(points[i, 1])) for i, cap in ((0, caps[0]), (-1, caps[1])) if cap]
  for thick, color in styles:
    rl.draw_spline_linear(pointer, count, thick, color)
    for point in ends:
      rl.draw_circle_v(point, thick / 2.0, color)


def _triangle(a: rl.Vector2, b: rl.Vector2, c: rl.Vector2, color: rl.Color) -> None:
  """raylib culls clockwise triangles; order the vertices so any orientation draws."""
  if (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x) > 0:
    b, c = c, b
  rl.draw_triangle(a, b, c, color)


@dataclass
class GpsFix:
  latitude: float
  longitude: float
  bearing: float
  speed: float
  received: float  # local monotonic time of the fix
  fresh: bool


class NavMapView(Widget):
  def __init__(self, *, show_guidance: bool = True, clip: bool = True, show_navigation_waiting: bool = False):
    super().__init__()
    self._show_guidance = show_guidance
    self._clip = clip  # off when the map owns its whole render target
    self._show_navigation_waiting = show_navigation_waiting
    self._navigation_requested = False
    self._dirty = True
    self._rendering_prepared = False
    self._animating = False
    self._last_draw = -math.inf
    self._next_draw = -math.inf
    self._overlay_state: tuple | None = None
    self._tiles: TileTextures | None = None
    self._sm = None
    self._params_memory = Params(memory=True)
    self._params = Params()
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)
    self._icons: dict[str, rl.Texture] = {}

    self._camera = Camera()
    self._camera_ready = False
    self._last_frame = time.monotonic()

    self._gps: GpsFix | None = None
    self._last_gps_poll = -math.inf
    self._last_gps_raw = ""
    self._display_bearing = 0.0

    self._route_world = np.zeros((0, 2))
    self._route_key: tuple | None = None
    self._route_received = -math.inf
    self._route_progress = 0
    self._nav: dict | None = None
    self._nav_received = -math.inf
    self._desire = 0
    self._nav_desire = 0

    self._preview_routes: list[np.ndarray] = []
    self._preview_selected = 0
    self._preview_destination: tuple[float, float] | None = None
    self._preview_active = False

  # ── public API ────────────────────────────────────────────────────────────

  def set_preview(self, routes: Sequence[Sequence[tuple[float, float]]], selected: int = 0,
                  destination: tuple[float, float] | None = None) -> None:
    """Show candidate routes (lat, lon lists) north-up, the selected one highlighted."""
    self._preview_routes = [_route_world(route) for route in routes]
    self._preview_selected = max(0, min(selected, len(self._preview_routes) - 1)) if self._preview_routes else 0
    self._preview_destination = destination
    self._preview_active = bool(self._preview_routes) or destination is not None
    self._dirty = True
    if routes:
      # navtilesd saves this route for offline use in the background, even if the car screen closes.
      self._ensure_started()
      try:
        self._tiles.offline_maps.set_preview_route(routes[self._preview_selected])
      except OSError:
        pass

  def clear_preview(self) -> None:
    self._preview_routes = []
    self._preview_destination = None
    self._preview_active = False
    self._dirty = True

  def update(self) -> None:
    """Advance data (tiles, messages, GPS) without drawing; see needs_redraw."""
    self._update_state()

  def render_prepared(self, rect: rl.Rectangle) -> None:
    """Draw after update(), retaining widget layout/input without polling twice."""
    self._rendering_prepared = True
    try:
      self.render(rect)
    finally:
      self._rendering_prepared = False

  def needs_redraw(self, now: float) -> bool:
    """For callers that cache the map in a texture: is a new frame worth drawing?"""
    since = now - self._last_draw
    if since >= MAP_IDLE_REDRAW:
      return True
    # Keep an absolute schedule. Comparing against the last render's start
    # loses an entire car frame whenever preparation or jitter puts us just
    # short of the interval. Slack absorbs that jitter without raising the
    # average redraw budget.
    if now < self._next_draw - 0.25 / MAP_MAX_FPS:
      return False
    gps = self._gps
    moving = gps is not None and gps.fresh and gps.speed > 0.3 and not self._preview_active
    return self._dirty or self._animating or moving

  def _record_draw(self, now: float) -> None:
    self._last_draw = now
    interval = 1.0 / MAP_MAX_FPS
    self._next_draw += interval
    if self._next_draw <= now:
      self._next_draw = now + interval

  @property
  def offline(self) -> bool:
    return self._tiles is not None and self._tiles.service.offline

  # ── state ─────────────────────────────────────────────────────────────────

  def _ensure_started(self) -> None:
    if self._tiles is None:
      self._tiles = shared_tiles()
    if self._sm is None:
      import cereal.messaging as messaging
      self._sm = messaging.SubMaster(["navInstruction", "navRoute", "starpilotModelV2"])

  def _update_state(self) -> None:
    if self._rendering_prepared:
      return
    self._ensure_started()
    if self._tiles.upload():
      self._dirty = True
    self._sm.update(0)
    now = time.monotonic()

    if self._sm.updated["navRoute"]:
      message = self._sm["navRoute"]
      points = [(c.latitude, c.longitude) for c in message.coordinates] if self._sm.valid["navRoute"] else []
      self._route_received = now
      key = (len(points), points[0], points[-1]) if points else None
      if key != self._route_key:
        self._route_key = key
        self._route_world = _route_world(points)
        self._route_progress = 0

    if self._sm.updated["navInstruction"]:
      message = self._sm["navInstruction"]
      if self._sm.valid["navInstruction"]:
        all_maneuvers = list(message.allManeuvers)
        upcoming = all_maneuvers[1] if len(all_maneuvers) > 1 else None
        self._nav = {
          "primary": message.maneuverPrimaryText,
          "secondary": message.maneuverSecondaryText,
          "distance": float(message.maneuverDistance),
          "type": message.maneuverType,
          "modifier": message.maneuverModifier,
          "remaining_distance": float(message.distanceRemaining),
          "remaining_time": float(message.timeRemaining),
          "next_type": upcoming.type if upcoming is not None else "",
          "next_modifier": upcoming.modifier if upcoming is not None else "",
        }
        self._nav_received = now
      else:
        self._nav = None

    if self._sm.updated["starpilotModelV2"]:
      model = self._sm["starpilotModelV2"]
      self._desire = int(model.desire)
      self._nav_desire = int(model.navDesire)
    elif not ui_state.started:
      self._desire = self._nav_desire = 0

    if now - self._last_gps_poll >= GPS_POLL_SECONDS:
      self._last_gps_poll = now
      self._poll_gps(now)
      self._navigation_requested = parse_destination_json(
        self._params.get("NavDestination", encoding="utf-8")
      ) is not None

    overlay = (id(self._nav), self._desire, self._nav_desire, ui_state.started, self._route_key,
               self._gps is not None and self._gps.fresh, self._navigation_requested, self.offline)
    if overlay != self._overlay_state:
      self._overlay_state = overlay
      self._dirty = True

  def _nav_active(self, now: float) -> bool:
    return self._nav is not None and now - self._nav_received < NAV_STALE_SECONDS

  def _poll_gps(self, now: float) -> None:
    raw = self._params_memory.get("LastGPSPosition", encoding="utf-8") or ""
    fresh = True
    if not raw:
      raw = self._params.get("LastGPSPosition", encoding="utf-8") or ""
      fresh = False
    if not raw:
      self._gps = None
      return
    if raw == self._last_gps_raw and self._gps is not None:
      if now - self._gps.received > GPS_STALE_SECONDS:
        self._gps.fresh = False
      return
    self._last_gps_raw = raw
    try:
      state = json.loads(raw) if isinstance(raw, str) else raw
      latitude, longitude = float(state["latitude"]), float(state["longitude"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
      return
    if not (math.isfinite(latitude) and math.isfinite(longitude)) or (abs(latitude) < 1e-6 and abs(longitude) < 1e-6):
      return
    bearing = float(state.get("bearing", 0.0) or 0.0)
    speed = max(0.0, float(state.get("speed", 0.0) or 0.0))
    updated = float(state.get("updatedAtMonotonic", 0.0) or 0.0)
    fresh = fresh and bool(state.get("hasFix", True)) and (updated <= 0.0 or now - updated < GPS_STALE_SECONDS)
    self._gps = GpsFix(latitude, longitude, bearing if math.isfinite(bearing) else 0.0, speed, now, fresh)
    self._update_route_progress()

  def _car_world(self, now: float) -> tuple[float, float] | None:
    gps = self._gps
    if gps is None:
      return None
    x, y = world_xy(gps.latitude, gps.longitude)
    if gps.fresh and gps.speed > 0.5:
      # Move along the heading between fixes so the map glides instead of stepping at 4 Hz.
      dt = min(DEAD_RECKON_LIMIT, now - gps.received)
      meters = gps.speed * dt
      units = meters / meters_per_world_unit(gps.latitude)
      x += math.sin(math.radians(gps.bearing)) * units
      y -= math.cos(math.radians(gps.bearing)) * units
    return x, y

  def _update_route_progress(self) -> None:
    if self._gps is None or len(self._route_world) < 2:
      return
    car = np.array(world_xy(self._gps.latitude, self._gps.longitude))
    start = max(0, self._route_progress - 20)
    window = self._route_world[start:start + 400]
    distances = np.sum((window - car) ** 2, axis=1)
    nearest = start + int(np.argmin(distances))
    if nearest < self._route_progress - 20 or nearest > self._route_progress + 380:
      nearest = int(np.argmin(np.sum((self._route_world - car) ** 2, axis=1)))
    self._route_progress = nearest

  # ── camera ────────────────────────────────────────────────────────────────

  @staticmethod
  def _tile_scale() -> float:
    # Draw tiles near one tile pixel per physical pixel: the car view renders a
    # 1080-high UI into a smaller screen, and map labels shrink with it otherwise.
    return max(1.0, min(2.0, 1.0 / max(0.25, float(getattr(gui_app, "_scale", 1.0) or 1.0))))

  def _target_camera(self, rect: rl.Rectangle, now: float) -> tuple[Camera, tuple[float, float], bool]:
    """(camera, anchor, snap). Preview fits the routes north-up; otherwise follow the car heading-up."""
    tile_scale = self._tile_scale()
    center = (rect.x + rect.width / 2.0, rect.y + rect.height / 2.0)
    car = self._car_world(now)

    if self._preview_active:
      pieces = [route for route in self._preview_routes if len(route)]
      if self._preview_destination is not None:
        pieces.append(np.array([world_xy(*self._preview_destination)]))
      if car is not None:
        pieces.append(np.array([car]))
      if pieces:
        points = np.concatenate(pieces)
        min_x, min_y = points.min(axis=0)
        max_x, max_y = points.max(axis=0)
        pad = 90.0
        span_x, span_y = max(max_x - min_x, 1e-7), max(max_y - min_y, 1e-7)
        fit = min((rect.width - 2 * pad) / (span_x * tile_scale), (rect.height - 2 * pad) / (span_y * tile_scale))
        zoom = max(3.0, min(PREVIEW_MAX_ZOOM, math.log2(max(fit, 1e-9))))
        return Camera((min_x + max_x) / 2.0, (min_y + max_y) / 2.0, zoom, 0.0), center, False

    if car is None:
      return Camera(self._camera.x, self._camera.y, 4.0 if not self._camera_ready else self._camera.zoom, 0.0), center, False

    gps = self._gps
    if gps is not None and gps.fresh and gps.speed > 1.5:
      self._display_bearing = gps.bearing
    speed = gps.speed if gps is not None and gps.fresh else 0.0
    zoom = float(np.interp(speed, FOLLOW_SPEEDS, FOLLOW_ZOOMS)) if self._nav_active(now) or speed > 0.5 else IDLE_ZOOM
    anchor = (center[0], rect.y + rect.height * FOLLOW_ANCHOR_Y)
    return Camera(car[0], car[1], zoom, self._display_bearing), anchor, True

  def _step_camera(self, target: Camera, dt: float, follow: bool) -> None:
    if not self._camera_ready:
      self._camera = Camera(target.x, target.y, target.zoom, target.bearing)
      self._camera_ready = True
      return
    self._animating = abs(target.zoom - self._camera.zoom) > 0.01 or abs(_angle_delta(target.bearing, self._camera.bearing)) > 0.3 or \
      (not follow and abs(target.x - self._camera.x) + abs(target.y - self._camera.y) > 1e-9)
    alpha = 1.0 - math.exp(-dt * 5.0)
    if follow:
      # Position already glides via dead reckoning; lagging it would pull the car off its anchor.
      self._camera.x, self._camera.y = target.x, target.y
    else:
      far = abs(target.x - self._camera.x) + abs(target.y - self._camera.y) > 4.0 / (2.0 ** min(self._camera.zoom, target.zoom))
      if far:
        self._camera.x, self._camera.y = target.x, target.y
      else:
        self._camera.x += (target.x - self._camera.x) * alpha
        self._camera.y += (target.y - self._camera.y) * alpha
    self._camera.zoom += (target.zoom - self._camera.zoom) * alpha
    self._camera.bearing = (self._camera.bearing + _angle_delta(target.bearing, self._camera.bearing) * alpha) % 360.0

  # ── drawing ───────────────────────────────────────────────────────────────

  def _render(self, rect: rl.Rectangle):
    now = time.monotonic()
    dt = max(0.0, min(0.5, now - self._last_frame))
    self._last_frame = now
    self._record_draw(now)
    self._dirty = False
    target, anchor, follow = self._target_camera(rect, now)
    self._step_camera(target, dt, follow)
    camera = self._camera
    tile_scale = self._tile_scale()

    rl.draw_rectangle_rec(rect, MAP_BACKGROUND)
    if self._clip:
      rl.begin_scissor_mode(int(rect.x), int(rect.y), int(rect.width), int(rect.height))
    try:
      if self._gps is not None or self._preview_active:
        self._draw_tiles(rect, camera, anchor, tile_scale)
        self._draw_routes(rect, camera, anchor, tile_scale)
        self._draw_destination(camera, anchor, tile_scale)
        self._draw_car(camera, anchor, tile_scale, now)
    finally:
      if self._clip:
        rl.end_scissor_mode()

    center_message = self._center_message()
    if center_message is not None:
      self._draw_center_message(rect, *center_message)
    self._draw_status(rect)
    if self._show_guidance and not self._preview_active:
      self._draw_guidance(rect, now)
    self._draw_attribution(rect)

  def _draw_tiles(self, rect: rl.Rectangle, camera: Camera, anchor: tuple[float, float], tile_scale: float) -> None:
    level = int(max(0, min(18, round(camera.zoom))))
    corners = [camera.to_world(x, y, anchor, tile_scale) for x, y in (
      (rect.x, rect.y), (rect.x + rect.width, rect.y), (rect.x, rect.y + rect.height), (rect.x + rect.width, rect.y + rect.height))]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    keys = tiles_covering(min(xs), min(ys), max(xs), max(ys), level)
    if len(keys) > 48:  # extreme zoom-out mid-animation: skip a frame of tiles rather than stall
      return

    center_world = camera.to_world(anchor[0], anchor[1], anchor, tile_scale)
    world_tile = TILE_SIZE / (1 << level)
    keys.sort(key=lambda k: ((k.x + 0.5) * world_tile - center_world[0]) ** 2 + ((k.y + 0.5) * world_tile - center_world[1]) ** 2)
    self._tiles.service.want(keys)

    # Draw in tile-local coordinates relative to the camera so float32 never sees huge values.
    scale = (2.0 ** (camera.zoom - level)) * tile_scale
    cx, cy = camera.x * (1 << level), camera.y * (1 << level)
    rl.rl_push_matrix()
    rl.rl_translatef(anchor[0], anchor[1], 0.0)
    rl.rl_rotatef(-camera.bearing, 0.0, 0.0, 1.0)
    rl.rl_scalef(scale, scale, 1.0)
    origin = rl.Vector2(0.0, 0.0)
    for key in keys:
      found = self._tiles.best(key)
      if found is None:
        continue
      texture, source = found
      dest = rl.Rectangle(key.x * TILE_SIZE - cx, key.y * TILE_SIZE - cy, TILE_SIZE + 0.75, TILE_SIZE + 0.75)
      rl.draw_texture_pro(texture, source, dest, origin, 0.0, rl.WHITE)
    rl.rl_pop_matrix()

  def _project(self, points: np.ndarray, camera: Camera, anchor: tuple[float, float], tile_scale: float):
    return camera.to_screen(points[:, 0], points[:, 1], anchor, tile_scale)

  def _draw_routes(self, rect: rl.Rectangle, camera: Camera, anchor: tuple[float, float], tile_scale: float) -> None:
    margin = 40.0
    if self._preview_active:
      for index, route in enumerate(self._preview_routes):
        if index == self._preview_selected or len(route) < 2:
          continue
        sx, sy = self._project(route, camera, anchor, tile_scale)
        for start, end in _visible_runs(sx, sy, rect, margin):
          _draw_polyline(sx, sy, start, end, ((13.0, ROUTE_ALTERNATE_CASING), (7.0, ROUTE_ALTERNATE)))
      selected = self._preview_routes[self._preview_selected] if self._preview_routes else None
      if selected is not None and len(selected) >= 2:
        sx, sy = self._project(selected, camera, anchor, tile_scale)
        last = len(sx) - 1
        for start, end in _visible_runs(sx, sy, rect, margin):
          _draw_polyline(sx, sy, start, end, ((15.0, ROUTE_CASING), (9.0, ROUTE_FILL)), caps=(start == 0, end == last))
      return

    if len(self._route_world) < 2 or time.monotonic() - self._route_received > ROUTE_STALE_SECONDS:
      return
    sx, sy = self._project(self._route_world, camera, anchor, tile_scale)
    last = len(sx) - 1
    split = max(0, min(self._route_progress, last))
    for start, end in _visible_runs(sx, sy, rect, margin):
      if start < split:
        _draw_polyline(sx, sy, start, min(end, split), ((9.0, ROUTE_TRAVELED),))
      if end > split:
        begin = max(start, split)
        _draw_polyline(sx, sy, begin, end, ((18.0, ROUTE_CASING), (11.0, ROUTE_FILL)), caps=(begin == split, end == last))

  def _draw_destination(self, camera: Camera, anchor: tuple[float, float], tile_scale: float) -> None:
    if self._preview_active:
      if self._preview_destination is not None:
        destination = world_xy(*self._preview_destination)
      elif self._preview_routes and len(self._preview_routes[self._preview_selected]):
        destination = tuple(self._preview_routes[self._preview_selected][-1])
      else:
        return
    elif len(self._route_world) and time.monotonic() - self._route_received <= ROUTE_STALE_SECONDS:
      destination = tuple(self._route_world[-1])
    else:
      return
    x, y = camera.to_screen(destination[0], destination[1], anchor, tile_scale)
    rl.draw_circle_v(rl.Vector2(x, y), 20.0, rl.Color(255, 255, 255, 255))
    rl.draw_circle_v(rl.Vector2(x, y), 14.0, DEST_FILL)
    rl.draw_circle_v(rl.Vector2(x, y), 5.0, rl.Color(255, 255, 255, 255))

  def _draw_car(self, camera: Camera, anchor: tuple[float, float], tile_scale: float, now: float) -> None:
    car = self._car_world(now)
    if car is None:
      return
    x, y = camera.to_screen(car[0], car[1], anchor, tile_scale)
    fresh = self._gps is not None and self._gps.fresh
    heading = math.radians((self._display_bearing if fresh else 0.0) - camera.bearing)
    center = rl.Vector2(x, y)
    rl.draw_circle_v(center, 34.0, rl.Color(64, 150, 255, 50 if fresh else 25))
    if not fresh:
      rl.draw_circle_v(center, 13.0, rl.Color(255, 255, 255, 255))
      rl.draw_circle_v(center, 9.0, rl.Color(130, 140, 160, 255))
      return

    def point(forward: float, side: float) -> rl.Vector2:
      return rl.Vector2(x + math.sin(heading) * forward + math.cos(heading) * side,
                        y - math.cos(heading) * forward + math.sin(heading) * side)

    outline = [point(31, 0), point(-22, -23), point(-12, 0), point(-22, 23)]
    inner = [point(24, 0), point(-16, -17), point(-8, 0), point(-16, 17)]
    for shape, color in ((outline, CAR_FILL), (inner, CAR_ACCENT)):
      _triangle(shape[0], shape[1], shape[2], color)
      _triangle(shape[0], shape[2], shape[3], color)

  # ── overlays ──────────────────────────────────────────────────────────────

  def _text(self, text: str, x: float, y: float, size: int, color: rl.Color, bold: bool = False) -> None:
    rl.draw_text_ex(self._font_bold if bold else self._font_medium, text, rl.Vector2(x, y), size, 0, color)

  def _text_width(self, text: str, size: int, bold: bool = False) -> float:
    return measure_text_cached(self._font_bold if bold else self._font_medium, text, size).x

  def _fit_text(self, text: str, size: int, width: float, bold: bool = False) -> str:
    if self._text_width(text, size, bold) <= width:
      return text
    while text and self._text_width(text + "...", size, bold) > width:
      text = text[:-1]
    return text.rstrip() + "..."

  def _card(self, rect: rl.Rectangle) -> None:
    rl.draw_rectangle_rounded(rect, min(0.5, 36.0 / max(rect.height, 1.0)), 12, CARD_BG)
    rl.draw_rectangle_rounded_lines_ex(rect, min(0.5, 36.0 / max(rect.height, 1.0)), 12, 2, CARD_BORDER)

  def _icon(self, maneuver_type: str, modifier: str) -> rl.Texture:
    normalized = _normalize_maneuver_type(maneuver_type)
    if modifier == "uturn":
      name = "direction_uturn.png"
    else:
      suffix = _modifier_suffix(modifier)
      name = f"direction_{normalized}.png" if not suffix else f"direction_{normalized}_{suffix}.png"
    if not (ASSETS_PATH / name).exists():
      name = FALLBACK_ICON
    texture = self._icons.get(name)
    if texture is None:
      texture = rl.load_texture(str(ASSETS_PATH / name))
      rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      self._icons[name] = texture
    return texture

  def _draw_icon(self, texture: rl.Texture, x: float, y: float, size: float) -> None:
    rl.draw_texture_pro(texture, rl.Rectangle(0, 0, texture.width, texture.height),
                        rl.Rectangle(x, y, size, size), rl.Vector2(0, 0), 0.0, rl.WHITE)

  def _draw_center_message(self, rect: rl.Rectangle, title: str, body: str) -> None:
    width = min(rect.width - 80, 720)
    card = rl.Rectangle(rect.x + (rect.width - width) / 2, rect.y + rect.height / 2 - 80, width, 160)
    self._card(card)
    self._text(title, card.x + 36, card.y + 30, 44, TEXT, bold=True)
    self._text(self._fit_text(body, 30, width - 72), card.x + 36, card.y + 94, 30, SUBTEXT)

  def _center_message(self) -> tuple[str, str] | None:
    if self._preview_active:
      return None
    has_fresh_gps = self._gps is not None and self._gps.fresh
    if self._show_navigation_waiting and self._navigation_requested and not has_fresh_gps:
      return "Navigation active", "Waiting for GPS to start your route."
    if self._gps is None:
      return "Waiting for GPS", "The map appears once the car has a location."
    return None

  def _draw_status(self, rect: rl.Rectangle) -> None:
    badges = []
    if self._tiles is not None and not self._tiles.has_token:
      badges.append(("Add a Mapbox key in The Galaxy", BADGE_WARN))
    elif self._tiles is not None and self._tiles.service.offline:
      badges.append(("Offline • cached map", BADGE_WARN))
    elif self._tiles is not None and not ui_state.started:
      route = self._tiles.offline_status().get("route") or {}
      remaining, total = int(route.get("remaining") or 0), int(route.get("total") or 0)
      if remaining > 0 and total > 0:
        badges.append((f"Saving route for offline • {100 * (total - remaining) // total}%", SUBTEXT))
    if self._gps is not None and not self._gps.fresh and not self._preview_active:
      badges.append(("No GPS fix", BADGE_WARN))
    x = rect.x + rect.width - 24
    y = rect.y + 24
    for label, color in badges:
      width = self._text_width(label, 28) + 44
      badge = rl.Rectangle(x - width, y, width, 56)
      self._card(badge)
      self._text(label, badge.x + 22, badge.y + 13, 28, color)
      y += 68

  def _draw_attribution(self, rect: rl.Rectangle) -> None:
    label = "(c) Mapbox (c) OpenStreetMap"
    width = self._text_width(label, 20)
    self._text(label, rect.x + rect.width - width - 14, rect.y + rect.height - 30, 20, rl.Color(220, 226, 236, 150))

  def _draw_guidance(self, rect: rl.Rectangle, now: float) -> None:
    pad = 24.0
    nav = self._nav if self._nav_active(now) else None
    y = rect.y + pad
    card_width = min(rect.width - 2 * pad, 760.0)
    status_width = 0.0
    if self._tiles is not None and (self._tiles.service.offline or (self._gps is not None and not self._gps.fresh)):
      status_width = 330.0
    card_width = min(card_width, rect.width - 2 * pad - status_width)

    if nav is not None and nav["primary"]:
      has_next = bool(nav["next_type"] or nav["next_modifier"])
      height = 196.0 if nav["secondary"] else 168.0
      card = rl.Rectangle(rect.x + pad, y, card_width, height)
      self._card(card)
      icon_size = 112.0
      self._draw_icon(self._icon(nav["type"], nav["modifier"]), card.x + 24, card.y + (height - icon_size) / 2, icon_size)
      text_x = card.x + 24 + icon_size + 24
      text_width = card.x + card.width - text_x - 24
      self._text(_format_distance(nav["distance"], ui_state.is_metric), text_x, card.y + 20, 64, TEXT, bold=True)
      self._text(self._fit_text(nav["primary"], 38, text_width, bold=True), text_x, card.y + 94, 38, TEXT, bold=True)
      if nav["secondary"]:
        self._text(self._fit_text(nav["secondary"], 28, text_width), text_x, card.y + 142, 28, SUBTEXT)
      y += height + 12
      if has_next:
        then = rl.Rectangle(rect.x + pad, y, 250, 72)
        self._card(then)
        self._text("Then", then.x + 24, then.y + 18, 32, SUBTEXT, bold=True)
        self._draw_icon(self._icon(nav["next_type"], nav["next_modifier"]), then.x + 120, then.y + 8, 56)
        y += 84

    desire = desire_line(ui_state.started, self._desire, self._nav_desire, nav)
    if desire is not None:
      label, detail, color = desire
      label_width = self._text_width(label, 34, bold=True)
      width = min(card_width, max(label_width, self._text_width(detail, 26)) + 90)
      chip = rl.Rectangle(rect.x + pad, y, width, 104 if detail else 68)
      self._card(chip)
      rl.draw_circle_v(rl.Vector2(chip.x + 30, chip.y + 34), 10.0, color)
      self._text(label, chip.x + 54, chip.y + 16, 34, color, bold=True)
      if detail:
        self._text(self._fit_text(detail, 26, chip.width - 78), chip.x + 54, chip.y + 60, 26, SUBTEXT)

    if nav is not None:
      self._draw_trip_bar(rect, nav)

  def _draw_trip_bar(self, rect: rl.Rectangle, nav: dict) -> None:
    remaining_time = max(0.0, nav["remaining_time"])
    arrival = datetime.datetime.now() + datetime.timedelta(seconds=remaining_time)
    arrival_text = arrival.strftime("%H:%M") if ui_state.is_metric else arrival.strftime("%I:%M %p").lstrip("0")
    minutes = int(round(remaining_time / 60.0))
    duration = f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{max(1, minutes)} min"
    distance = _format_distance(nav["remaining_distance"], ui_state.is_metric)

    height = 96.0
    width = min(rect.width - 48, 640.0)
    bar = rl.Rectangle(rect.x + (rect.width - width) / 2, rect.y + rect.height - height - 44, width, height)
    self._card(bar)
    self._text(arrival_text, bar.x + 32, bar.y + 22, 50, DESIRE_ROUTE, bold=True)
    detail = f"{duration}  •  {distance}"
    detail_width = self._text_width(detail, 36)
    self._text(detail, bar.x + bar.width - detail_width - 32, bar.y + 30, 36, TEXT)
