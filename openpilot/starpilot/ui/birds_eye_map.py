"""The road network under the 3D drive render: StarPilot's road-line tiles, placed by GPS.

Each tile's lines are built once, in metres north and east of its corner, and placed every frame by a pose, so the
map glides with the car without being rebuilt. Tiles come from disk only; navtilesd downloads them.
"""

from dataclasses import dataclass
import math
import time

import numpy as np

from openpilot.starpilot.navigation.road_tiles import EXTENT, LINK, MAJOR, MINOR, MOTORWAY, STREET, lat_lon, meters_per_tile, \
  tiles_in_radius, world_xy

RADIUS_M = 400.0          # tiles touching this radius around the car are loaded
NEAR_M = 300.0            # tiles this close to the car are drawn
MAX_DEAD_RECKON_S = 1.5
FEED_PERIOD_S = 0.1       # GPS is read at 10 Hz; frames between reads dead-reckon from the last fix
KEYS_PERIOD_S = 0.5
MIN_BEARING_SPEED = 1.5   # m/s: slower than this the GPS bearing is noise, so the last good one is kept
TILE_CACHE = 12           # decoded tiles kept: the ring around the car plus a few just left behind
WIDTHS = {MOTORWAY: 11.0, MAJOR: 8.0, STREET: 6.5, LINK: 5.0, MINOR: 4.0}  # m
ALIGN_X = 12.0            # m ahead: where the map's road is matched to the model's
ALIGN_MAX_Y = 12.0
ALIGN_MAX_ANGLE = math.radians(25.0)


@dataclass(eq=False)
class Anchor:
  x: float                # world tile units of the frame's origin (a tile's top-left corner)
  y: float
  meters: float           # metres per tile here
  lines: tuple            # (road class, (N, 2) metres north/east of the origin)
  segments: np.ndarray    # (M, 2, 2) every line's segments, for alignment
  drawn: object = None    # the renderer's draw data, built with the geometry off the render thread


def local_lines(tiles, ax: float, ay: float, meters: float, radius: float = math.inf) -> list:
  """(road class, (N, 2) north/east metres) for each tile line with a point within `radius` of the origin."""
  out = []
  for tile in tiles:
    for road_class, (points, offsets) in tile.lines.items():
      north = (ay - (tile.key.y + points[:, 1] / EXTENT)) * meters
      east = (tile.key.x + points[:, 0] / EXTENT - ax) * meters
      local = np.stack((north, east), axis=1)
      near = np.maximum(np.abs(north), np.abs(east)) < radius
      for lo, hi in zip(offsets[:-1], offsets[1:], strict=True):
        if hi - lo >= 2 and near[lo:hi].any():
          out.append((int(road_class), local[lo:hi]))
  return out


def tile_anchor(tile) -> Anchor:
  """One tile's lines in metres from its top-left corner."""
  latitude, _ = lat_lon(tile.key.x + 0.5, tile.key.y + 0.5, tile.key.z)
  meters = meters_per_tile(latitude, tile.key.z)
  lines = local_lines([tile], tile.key.x, tile.key.y, meters)
  return Anchor(float(tile.key.x), float(tile.key.y), meters, tuple(lines), segments_of(lines))


def segments_of(lines) -> np.ndarray:
  parts = [np.stack((line[:-1], line[1:]), axis=1) for _, line in lines]
  return np.concatenate(parts) if parts else np.empty((0, 2, 2))


def to_car(points, north: float, east: float, bearing: float, lateral: float = 0.0, turn: float = 0.0) -> np.ndarray:
  """North/east metres -> the car frame (x forward, y right), turned by `turn` and shifted `lateral` right."""
  m = pose_matrix(north, east, bearing, lateral, turn)
  p = np.asarray(points, dtype=np.float64)
  return p @ m[:2, :2].T + m[:2, 3]


def pose_matrix(north: float, east: float, bearing: float, lateral: float = 0.0, turn: float = 0.0) -> np.ndarray:
  """4x4 column-vector transform of to_car, for the GPU (z kept)."""
  theta = turn - bearing
  c, s = math.cos(theta), math.sin(theta)
  m = np.eye(4)
  m[:2, :2] = ((c, -s), (s, c))
  m[0, 3] = -(c * north - s * east)
  m[1, 3] = -(s * north + c * east) + lateral
  return m


def road_at(segments: np.ndarray, x: float = ALIGN_X, max_y: float = ALIGN_MAX_Y, max_angle: float = ALIGN_MAX_ANGLE):
  """(y, heading) where the nearest roughly forward map line crosses x ahead, from car-frame segments, or None."""
  if not len(segments):
    return None
  p0, p1 = segments[:, 0], segments[:, 1]
  dx, dy = p1[:, 0] - p0[:, 0], p1[:, 1] - p0[:, 1]
  with np.errstate(divide='ignore', invalid='ignore'):
    t = (x - p0[:, 0]) / dx
    y = p0[:, 1] + t * dy
    angle = np.arctan(dy / dx)
  ok = (np.abs(dx) > 1e-6) & (t >= 0) & (t <= 1) & (np.abs(y) < max_y) & (np.abs(angle) < max_angle)
  if not ok.any():
    return None
  i = np.flatnonzero(ok)[np.argmin(np.abs(y[ok]))]
  return float(y[i]), float(angle[i])


class MapUnderlay:
  """The tiles near the car, each with its pose; its tile reader and GPS feed start on first use.

  Each tile's geometry (and the renderer's draw data, via `decorate`) is built once, on the tile reader's thread,
  so driving never rebuilds anything on the render thread."""

  def __init__(self, reader=None, feed=None, clock=time.monotonic):
    self._reader, self._feed, self.clock = reader, feed, clock
    self.decorate = None
    self.geometry: dict = {}
    self._bearing: float | None = None
    self._fix = None
    self._read_at = -math.inf
    self._keys: tuple = ()
    self._keys_at = -math.inf
    self._building = False

  def update(self):
    """[(anchor, (north, east, bearing))] for the built tiles near the car, or None without a usable fix."""
    if self._reader is None:
      from openpilot.starpilot.ui.onroad_map import TileReader
      self._reader = TileReader(cache=TILE_CACHE)
    if self._feed is None:
      from openpilot.starpilot.ui.onroad_map import MapFeed
      self._feed = MapFeed()
    now = self.clock()
    if now - self._read_at >= FEED_PERIOD_S:
      self._read_at, self._fix = now, self._feed.read().fix
    fix = self._fix
    if fix is None:
      return None
    speed = fix.speed if math.isfinite(fix.speed) else 0.0
    if fix.bearing is not None and math.isfinite(fix.bearing) and (speed >= MIN_BEARING_SPEED or self._bearing is None):
      self._bearing = math.radians(fix.bearing)
    if self._bearing is None:
      return None
    reader = self._reader
    if now - self._keys_at >= KEYS_PERIOD_S:
      self._keys_at, self._keys = now, tuple(tiles_in_radius(fix.latitude, fix.longitude, RADIUS_M))
      reader.want(self._keys)
      self.geometry = {key: anchor for key, anchor in self.geometry.items() if key in self._keys}
      missing = [tile for key, tile in reader.snapshot(self._keys).items() if key not in self.geometry]
      if missing and not self._building:
        self._building = True
        reader.submit(lambda: self._build(missing))
    b = self._bearing
    ahead = speed * min(max(now - fix.monotonic, 0.0), MAX_DEAD_RECKON_S)
    wx, wy = world_xy(fix.latitude, fix.longitude)
    placed = []
    for anchor in list(self.geometry.values()):
      north = (anchor.y - wy) * anchor.meters + ahead * math.cos(b)
      east = (wx - anchor.x) * anchor.meters + ahead * math.sin(b)
      # Distance from the car to the tile's square: north in [-meters, 0], east in [0, meters].
      gap = math.hypot(max(north, 0.0, -anchor.meters - north), max(-east, 0.0, east - anchor.meters))
      if gap < NEAR_M:
        placed.append((anchor, (north, east, b)))
    return placed

  def _build(self, tiles) -> None:
    try:
      for tile in tiles:
        anchor = tile_anchor(tile)
        if self.decorate is not None:
          anchor.drawn = self.decorate(anchor)
        self.geometry[tile.key] = anchor
    finally:
      self._building = False

  def close(self) -> None:
    if self._reader is not None:
      self._reader.close()


def new_underlay():
  """A MapUnderlay, or None where map tiles cannot be read."""
  try:
    return MapUnderlay()
  except Exception:
    return None
