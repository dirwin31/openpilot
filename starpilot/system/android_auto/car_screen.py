"""How the Android Auto car view lays out the drive: settings from The Galaxy.

Kept in its own file beside config.json because the Android Auto supervisor
rewrites config.json from memory during a session; these settings change while
connected and car_ui re-reads them within a second, so they apply live.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path

from openpilot.starpilot.system.android_auto.identity import DATA_DIR

CAR_SCREEN_PATH = DATA_DIR / "car_screen.json"
ONROAD_VIEWS = ("split", "driving", "map")  # map + driving view, driving view only, map only
MAP_SIDES = ("right", "left")
MAX_BLIND_SPOT_SPEED_MS = 60.0
DEFAULTS = {
  "onroad_view": "split",
  "map_side": "right",
  "camera": True,
  "blind_spot_monitors": True,
  "blind_spot_min_speed_ms": 0.0,
}
RELOAD_SECONDS = 1.0
# Set by tools/android_auto/dhu_device.py for a Desktop Head Unit session; the car view
# then treats the car as below the 10 mph destination lock.
DHU_ENV = "STARPILOT_ANDROID_AUTO_DHU"


def normalize(raw: object) -> dict:
  settings = dict(DEFAULTS)
  if isinstance(raw, dict):
    if raw.get("onroad_view") in ONROAD_VIEWS:
      settings["onroad_view"] = raw["onroad_view"]
    if raw.get("map_side") in MAP_SIDES:
      settings["map_side"] = raw["map_side"]
    if isinstance(raw.get("camera"), bool):
      settings["camera"] = raw["camera"]
    if isinstance(raw.get("blind_spot_monitors"), bool):
      settings["blind_spot_monitors"] = raw["blind_spot_monitors"]
    minimum_speed = raw.get("blind_spot_min_speed_ms")
    if isinstance(minimum_speed, (int, float)) and not isinstance(minimum_speed, bool) and math.isfinite(minimum_speed):
      if 0.0 <= minimum_speed <= MAX_BLIND_SPOT_SPEED_MS:
        settings["blind_spot_min_speed_ms"] = float(minimum_speed)
  return settings


def blind_spot_monitors_visible(settings: dict, speed_ms: float | None) -> bool:
  """Whether Android Auto-specific blind-spot visuals should be drawn at this speed."""
  if not settings.get("blind_spot_monitors", True):
    return False
  minimum = float(settings.get("blind_spot_min_speed_ms", 0.0))
  return minimum <= 0.0 or (speed_ms is not None and speed_ms >= minimum)


def load(path: Path | None = None) -> dict:
  try:
    return normalize(json.loads((path or CAR_SCREEN_PATH).read_text()))
  except (OSError, ValueError):
    return dict(DEFAULTS)


def save(settings: dict, path: Path | None = None) -> dict:
  path = path or CAR_SCREEN_PATH
  clean = normalize(settings)
  path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
  fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".car-screen-")
  try:
    with os.fdopen(fd, "w") as handle:
      json.dump(clean, handle, indent=2)
    os.replace(temporary, path)
  except BaseException:
    try:
      os.unlink(temporary)
    except OSError:
      pass
    raise
  return clean


class CarScreenSettings:
  """The current settings, re-read when the file changes (checked about once a second)."""

  def __init__(self, path: Path | None = None, clock=time.monotonic):
    self.path = path or CAR_SCREEN_PATH
    self._clock = clock
    self._checked = -RELOAD_SECONDS
    self._stamp: tuple | None = None
    self.current = dict(DEFAULTS)

  def poll(self) -> dict:
    now = self._clock()
    if now - self._checked < RELOAD_SECONDS:
      return self.current
    self._checked = now
    try:
      stat = self.path.stat()
      stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
      stamp = None
    if stamp != self._stamp:
      self._stamp = stamp
      self.current = load(self.path) if stamp is not None else dict(DEFAULTS)
    return self.current
