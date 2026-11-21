"""A file per CarPlay connection recording what the iPhone sends about navigation, read after the drive."""
from __future__ import annotations

import json
import threading
import time
from collections import Counter
from pathlib import Path

from openpilot.starpilot.system.phone_projection.settings import ROOT

LOG_DIR = ROOT / "logs"
KEEP_FILES = 40
DISTANCE_EVERY_S = 15.0


def nav_app(source: str) -> str:
  """The navigation app behind the iPhone's route-guidance source name."""
  name = source.lower()
  if "google" in name:
    return "googleMaps"
  if "waze" in name:
    return "waze"
  if name in ("maps", "apple maps") or "apple" in name:
    return "appleMaps"
  return "other" if name else ""


def _plain(value, depth: int = 0):
  if depth > 6:
    return str(value)[:200]
  if isinstance(value, dict):
    return {str(k): _plain(v, depth + 1) for k, v in list(value.items())[:40]}
  if isinstance(value, (list, tuple)):
    return [_plain(v, depth + 1) for v in value[:40]]
  if isinstance(value, bytes):
    return value[:64].hex()
  if isinstance(value, (str, int, float, bool)) or value is None:
    return value if not isinstance(value, str) else value[:300]
  return str(value)[:200]


class GuidanceLog:
  def __init__(self, phone: str = "", directory: Path = LOG_DIR, keep: int = KEEP_FILES):
    self.directory, self.keep = directory, keep
    self.lock = threading.Lock()
    self.path: Path | None = None
    self.started = time.monotonic()
    self.received: Counter[str] = Counter()
    self.apps: set[str] = set()
    self.published = 0
    self.steps = 0
    self.last_distance = 0.0
    self.closed = False
    self.event("session_start", phone=phone)

  def event(self, name: str, **fields) -> None:
    line = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "s": round(time.monotonic() - self.started, 2), "event": name,
            **{k: _plain(v) for k, v in fields.items()}}
    with self.lock:
      if self.closed:
        return
      try:
        if self.path is None:
          self.directory.mkdir(parents=True, exist_ok=True)
          self._prune()
          self.path = self.directory / f"carplay-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        with open(self.path, "a") as f:
          f.write(json.dumps(line, separators=(",", ":")) + "\n")
      except OSError:
        pass

  def _prune(self) -> None:
    files = sorted(self.directory.glob("carplay-*.jsonl"))
    for old in files[:max(0, len(files) - self.keep + 1)]:
      old.unlink(missing_ok=True)

  def rx(self, message_id: int) -> None:
    key = f"0x{message_id:04x}"
    self.received[key] += 1
    if self.received[key] == 1:
      self.event("iap2_first_rx", message=key)

  def source(self, name: str) -> None:
    app = nav_app(name)
    if app and app not in self.apps:
      self.apps.add(app)
      self.event("nav_app", app=app, source=name)

  def distance_due(self) -> bool:
    now = time.monotonic()
    if now - self.last_distance >= DISTANCE_EVERY_S:
      self.last_distance = now
      return True
    return False

  def close(self, reason: str = "") -> None:
    self.event("session_end", reason=reason, received=dict(self.received), apps=sorted(self.apps),
               steps=self.steps, published=self.published, guidance=bool(self.received.get("0x5201")))
    with self.lock:
      self.closed = True
