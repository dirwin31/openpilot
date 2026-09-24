"""Offline map planning and the small amount of state the UI and navtilesd share.

Two kinds of saved tiles:
  * Route tiles: the corridor along a route plus street-level detail around its
    turns and destination. They live in the regular tile cache and age out
    least-recently-used first when it fills.
  * Offline areas: everything within a radius, downloaded on Wi-Fi and pinned
    until the area is deleted. Areas refresh themselves every few months.

State is plain JSON files beside the tiles, each written atomically by one side:
  offline/areas/<id>.json      area definitions (UI writes, navtilesd deletes)
  offline/status.json          download progress (navtilesd writes)
  offline/preview_route.json   the route being previewed in the UI (UI writes)
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openpilot.starpilot.navigation.map_tiles import (
  TILE_SIZE,
  TileKey,
  corridor_tiles,
  default_cache_dir,
  offline_root,
  tiles_covering,
  world_xy,
)

ROUTE_ZOOMS = (12, 14, 15)       # overview, highway and city follow views
DETAIL_ZOOM = 16                 # street-level view the map uses at low speed
DETAIL_RADIUS_M = 300.0
TURN_ANGLE_DEGREES = 35.0
MAX_DETAIL_POINTS = 150

AREA_MIN_ZOOM = 8
AVERAGE_TILE_BYTES = 30_000      # navigation-night 512 px tiles: ~17 KB suburban, ~42 KB downtown
OFFLINE_MAX_BYTES = 2 * 1024 ** 3
AREA_REFRESH_SECONDS = 90 * 24 * 3600
PREVIEW_ROUTE_MAX_AGE = 30 * 60

# (radius km, max zoom): street detail near home down to regional coverage for a trip.
AREA_PRESETS = ((10.0, 16), (30.0, 15), (60.0, 14), (150.0, 13))


def _now() -> float:
  return time.time()  # noqa: TID251 - persisted timestamps must survive reboots


def _write_json(path: Path, payload: Any) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
  with os.fdopen(fd, "w") as handle:
    json.dump(payload, handle)
  os.replace(temp_name, path)


def _read_json(path: Path) -> Any:
  try:
    return json.loads(path.read_text())
  except (OSError, ValueError):
    return None


# ── tile planning ──────────────────────────────────────────────────────────

def _bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
  lat1, lat2 = math.radians(a[0]), math.radians(b[0])
  dlon = math.radians(b[1] - a[1])
  x = math.sin(dlon) * math.cos(lat2)
  y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
  return math.degrees(math.atan2(x, y)) % 360.0


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
  lat = math.radians((a[0] + b[0]) / 2.0)
  dx = math.radians(b[1] - a[1]) * math.cos(lat) * 6371000.0
  dy = math.radians(b[0] - a[0]) * 6371000.0
  return math.hypot(dx, dy)


def turn_points(points: Sequence[tuple[float, float]], min_angle: float = TURN_ANGLE_DEGREES) -> list[tuple[float, float]]:
  """Where a route geometry changes direction sharply: its turns, forks and ramps.

  Headings are measured over ~25 m either side so curves drawn with many short
  segments don't register as turns.
  """
  if len(points) < 3:
    return []
  turns = []
  for i in range(1, len(points) - 1):
    j = i - 1
    while j > 0 and _distance_m(points[j], points[i]) < 25.0:
      j -= 1
    k = i + 1
    while k < len(points) - 1 and _distance_m(points[i], points[k]) < 25.0:
      k += 1
    if _distance_m(points[j], points[i]) < 5.0 or _distance_m(points[i], points[k]) < 5.0:
      continue
    change = abs((_bearing(points[i], points[k]) - _bearing(points[j], points[i]) + 540.0) % 360.0 - 180.0)
    if change >= min_angle and (not turns or _distance_m(turns[-1], points[i]) > DETAIL_RADIUS_M):
      turns.append(points[i])
  return turns


def detail_tiles(points: Iterable[tuple[float, float]], zoom: int = DETAIL_ZOOM, radius_m: float = DETAIL_RADIUS_M) -> list[TileKey]:
  keys: list[TileKey] = []
  seen: set[TileKey] = set()
  for latitude, longitude in points:
    cx, cy = world_xy(latitude, longitude)
    meters_per_unit = 40075016.686 * math.cos(math.radians(latitude)) / TILE_SIZE
    r = radius_m / meters_per_unit
    for key in tiles_covering(cx - r, cy - r, cx + r, cy + r, zoom):
      if key not in seen:
        seen.add(key)
        keys.append(key)
  return keys


def route_tiles(points: Sequence[tuple[float, float]]) -> list[TileKey]:
  """Tiles for driving a route offline: street detail at the destination and
  turns first (small and most needed), then the corridor in driving order."""
  if not points:
    return []
  focus = [points[-1]] + turn_points(points)[:MAX_DETAIL_POINTS] + [points[0]]
  keys = detail_tiles(focus)
  seen = set(keys)
  corridor = corridor_tiles([world_xy(lat, lon) for lat, lon in points], zooms=ROUTE_ZOOMS)
  keys += [key for key in corridor if key not in seen]
  return keys


def area_tiles(latitude: float, longitude: float, radius_km: float, max_zoom: int, min_zoom: int = AREA_MIN_ZOOM) -> list[TileKey]:
  """Tiles within a radius, coarse zooms first and each zoom from the centre out,
  so an interrupted download is still useful."""
  cx, cy = world_xy(latitude, longitude)
  meters_per_unit = 40075016.686 * math.cos(math.radians(latitude)) / TILE_SIZE
  r = radius_km * 1000.0 / meters_per_unit
  keys: list[TileKey] = []
  for zoom in range(min_zoom, max_zoom + 1):
    scale = (1 << zoom) / TILE_SIZE
    tiles = [key for key in tiles_covering(cx - r, cy - r, cx + r, cy + r, zoom)
             if math.hypot((key.x + 0.5) / scale - cx, (key.y + 0.5) / scale - cy) <= r + 0.75 / scale]
    tiles.sort(key=lambda key: ((key.x + 0.5) / scale - cx) ** 2 + ((key.y + 0.5) / scale - cy) ** 2)
    keys += tiles
  return keys


def estimate_area(latitude: float, longitude: float, radius_km: float, max_zoom: int) -> tuple[int, int]:
  """(tile count, approximate bytes)."""
  count = len(area_tiles(latitude, longitude, radius_km, max_zoom))
  return count, count * AVERAGE_TILE_BYTES


def format_bytes(size: float) -> str:
  if size >= 1024 ** 3:
    return f"{size / 1024 ** 3:.1f} GB"
  if size >= 1024 ** 2:
    return f"{size / 1024 ** 2:.0f} MB"
  return f"{max(size, 0) / 1024:.0f} KB"


# ── shared state ───────────────────────────────────────────────────────────

@dataclass
class OfflineArea:
  id: str
  name: str
  latitude: float
  longitude: float
  radius_km: float
  max_zoom: int
  created: float
  update_requested: float = 0.0
  deleted: bool = False
  allow_metered: bool = False  # "Download now": use any connection, not just unmetered Wi-Fi

  def tiles(self) -> list[TileKey]:
    return area_tiles(self.latitude, self.longitude, self.radius_km, self.max_zoom)


class OfflineMaps:
  def __init__(self, base: Path | None = None):
    self.base = Path(base or default_cache_dir())
    self.root = offline_root(self.base)
    self.areas_dir = self.root / "areas"
    self.status_path = self.root / "status.json"
    self.preview_path = self.root / "preview_route.json"

  # areas (UI side)
  def areas(self, include_deleted: bool = False) -> list[OfflineArea]:
    areas = []
    try:
      paths = sorted(self.areas_dir.glob("*.json"))
    except OSError:
      return []
    for path in paths:
      raw = _read_json(path)
      if not isinstance(raw, dict):
        continue
      try:
        area = OfflineArea(**{field: raw[field] for field in OfflineArea.__dataclass_fields__ if field in raw})
      except TypeError:
        continue
      if include_deleted or not area.deleted:
        areas.append(area)
    return sorted(areas, key=lambda area: area.created)

  def add_area(self, name: str, latitude: float, longitude: float, radius_km: float, max_zoom: int) -> OfflineArea:
    area = OfflineArea(uuid.uuid4().hex[:12], name, float(latitude), float(longitude), float(radius_km), int(max_zoom), _now())
    self._save_area(area)
    return area

  def request_update(self, area_id: str) -> None:
    for area in self.areas():
      if area.id == area_id:
        area.update_requested = _now()
        self._save_area(area)

  def allow_metered(self, area_id: str) -> None:
    for area in self.areas():
      if area.id == area_id:
        area.allow_metered = True
        self._save_area(area)

  def delete_area(self, area_id: str) -> None:
    """Marks the area; navtilesd removes its tiles (keeping any another area shares) and then the record."""
    for area in self.areas():
      if area.id == area_id:
        area.deleted = True
        self._save_area(area)

  def forget_area(self, area_id: str) -> None:
    try:
      (self.areas_dir / f"{area_id}.json").unlink()
    except OSError:
      pass

  def _save_area(self, area: OfflineArea) -> None:
    _write_json(self.areas_dir / f"{area.id}.json", asdict(area))

  # progress (navtilesd side)
  def status(self) -> dict[str, Any]:
    raw = _read_json(self.status_path)
    return raw if isinstance(raw, dict) else {}

  def write_status(self, status: dict[str, Any]) -> None:
    _write_json(self.status_path, status)

  # the route being previewed (UI side)
  def set_preview_route(self, points: Sequence[tuple[float, float]]) -> None:
    _write_json(self.preview_path, {"at": _now(), "points": [[round(lat, 6), round(lon, 6)] for lat, lon in points]})

  def preview_route(self) -> list[tuple[float, float]]:
    raw = _read_json(self.preview_path)
    if not isinstance(raw, dict) or _now() - float(raw.get("at", 0.0)) > PREVIEW_ROUTE_MAX_AGE:
      return []
    try:
      return [(float(lat), float(lon)) for lat, lon in raw.get("points", [])]
    except (TypeError, ValueError):
      return []
