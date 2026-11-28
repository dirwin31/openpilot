"""The road network under the 3D drive render: StarPilot's road-line tiles, placed by GPS.

Lines are built once per anchor, in metres north and east of it, and placed every frame by a pose, so the map
glides with the car without being rebuilt. Tiles come from disk only; navtilesd downloads them.
"""

from dataclasses import dataclass
import math
import time

import numpy as np

from openpilot.starpilot.navigation.road_tiles import EXTENT, LINK, MAJOR, MINOR, MOTORWAY, STREET, meters_per_tile, \
  tiles_in_radius, world_xy

RADIUS_M = 350.0          # lines within this of the anchor are kept
REANCHOR_M = 120.0        # the anchor moves once the car is this far from it
MAX_DEAD_RECKON_S = 1.5
MIN_BEARING_SPEED = 1.5   # m/s: slower than this the GPS bearing is noise, so the last good one is kept
WIDTHS = {MOTORWAY: 11.0, MAJOR: 8.0, STREET: 6.5, LINK: 5.0, MINOR: 4.0}  # m
ALIGN_X = 12.0            # m ahead: where the map's road is matched to the model's
ALIGN_MAX_Y = 12.0
ALIGN_MAX_ANGLE = math.radians(25.0)


@dataclass(frozen=True, eq=False)
class Anchor:
  x: float                # world tile units
  y: float
  meters: float           # metres per tile here
  lines: tuple            # (road class, (N, 2) metres north/east of the anchor)
  segments: np.ndarray    # (M, 2, 2) every line's segments, for alignment
  generation: int


def local_lines(tiles, ax: float, ay: float, meters: float, radius: float = RADIUS_M) -> list:
  """(road class, (N, 2) north/east metres) for each tile line with a point within `radius` of the anchor."""
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
  """GPS pose and nearby road lines for the renderer; its tile reader and GPS feed start on first use."""

  def __init__(self, reader=None, feed=None, clock=time.monotonic):
    self._reader, self._feed, self.clock = reader, feed, clock
    self.anchor: Anchor | None = None
    self._bearing: float | None = None

  def update(self):
    """(anchor, (north, east, bearing)) for this frame, or None without a usable fix."""
    if self._reader is None:
      from openpilot.starpilot.ui.onroad_map import TileReader
      self._reader = TileReader()
    if self._feed is None:
      from openpilot.starpilot.ui.onroad_map import MapFeed
      self._feed = MapFeed()
    fix = self._feed.read().fix
    if fix is None:
      return None
    speed = fix.speed if math.isfinite(fix.speed) else 0.0
    if fix.bearing is not None and math.isfinite(fix.bearing) and (speed >= MIN_BEARING_SPEED or self._bearing is None):
      self._bearing = math.radians(fix.bearing)
    if self._bearing is None:
      return None
    ahead = speed * min(max(self.clock() - fix.monotonic, 0.0), MAX_DEAD_RECKON_S)
    wx, wy = world_xy(fix.latitude, fix.longitude)
    anchor = self.anchor
    pose = self._pose(anchor, wx, wy, ahead) if anchor is not None else None
    reader = self._reader
    if pose is None or math.hypot(pose[0], pose[1]) > REANCHOR_M or reader.generation != anchor.generation:
      keys = tiles_in_radius(fix.latitude, fix.longitude, RADIUS_M + REANCHOR_M)
      reader.want(keys)
      generation = reader.generation
      meters = meters_per_tile(fix.latitude)
      lines = local_lines(reader.snapshot(keys).values(), wx, wy, meters)
      anchor = self.anchor = Anchor(wx, wy, meters, tuple(lines), segments_of(lines), generation)
      pose = self._pose(anchor, wx, wy, ahead)
    return anchor, pose

  def _pose(self, anchor: Anchor, wx: float, wy: float, ahead: float) -> tuple[float, float, float]:
    b = self._bearing
    north = (anchor.y - wy) * anchor.meters + ahead * math.cos(b)
    east = (wx - anchor.x) * anchor.meters + ahead * math.sin(b)
    return north, east, b

  def close(self) -> None:
    if self._reader is not None:
      self._reader.close()


def new_underlay():
  """A MapUnderlay, or None where map tiles cannot be read."""
  try:
    return MapUnderlay()
  except Exception:
    return None
