"""How the Android Auto car view lays out the drive: settings from The Galaxy.

Kept in its own file beside config.json because the Android Auto supervisor
rewrites config.json from memory during a session; these settings change while
connected and car_ui re-reads them within a second, so they apply live.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from openpilot.starpilot.system.android_auto.identity import DATA_DIR

CAR_SCREEN_PATH = DATA_DIR / "car_screen.json"
ONROAD_VIEWS = ("split", "driving", "map")  # map + driving view, driving view only, map only
MAP_SIDES = ("right", "left")
DEFAULTS = {"onroad_view": "split", "map_side": "right", "camera": True}
RELOAD_SECONDS = 1.0


def normalize(raw: object) -> dict:
  settings = dict(DEFAULTS)
  if isinstance(raw, dict):
    if raw.get("onroad_view") in ONROAD_VIEWS:
      settings["onroad_view"] = raw["onroad_view"]
    if raw.get("map_side") in MAP_SIDES:
      settings["map_side"] = raw["map_side"]
    if isinstance(raw.get("camera"), bool):
      settings["camera"] = raw["camera"]
  return settings


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
