"""What the CarPlay and Android Auto runners share with projectiond."""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openpilot.starpilot.system.phone_projection.audio import MusicOutput
from openpilot.starpilot.system.phone_projection.ipc import Touch
from openpilot.starpilot.system.phone_projection.settings import ROOT, Settings

STATE_DIR = ROOT / "state"
HOTSPOT_STATUS = Path("/tmp/starpilot-galaxy-hotspot-status.json")
HOTSPOT_INTERFACE = "galaxy0"
HOTSPOT_ADDRESS = "172.31.254.1"
MAX_TOUCHES = 2


@dataclass
class Context:
  publisher: object
  panel: tuple[int, int]
  settings: Callable[[], Settings]
  audio: MusicOutput
  bluez: object
  log: Callable[[str], None] = print
  fps: int = 60  # what CarPlay is asked for; 30 is enough when only the car screen shows it
  stop: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True)
class Hotspot:
  ssid: str
  passphrase: str
  channel: int
  bssid: bytes

  @property
  def bssid_text(self) -> str:
    return ":".join(f"{b:02x}" for b in self.bssid)


def hotspot(status_path: Path = HOTSPOT_STATUS, settings_source=None, interface: str = HOTSPOT_INTERFACE) -> Hotspot | None:
  """The Galaxy hotspot phones join, or None while it is off."""
  try:
    status = json.loads(status_path.read_text())
    if not status.get("active") or time.monotonic() - float(status.get("updated", 0)) > 30:
      return None
    if settings_source is None:
      from openpilot.starpilot.galaxy.hotspot import HotspotSettings
      settings_source = HotspotSettings()
    config = settings_source.read()
    mac = Path(f"/sys/class/net/{interface}/address").read_text().strip()
    return Hotspot(config["ssid"], config["password"], int(status.get("channel") or 0), bytes.fromhex(mac.replace(":", "")))
  except (OSError, ValueError, KeyError, TypeError):
    return None


class TouchTracker:
  """Pointer ids from the screen mapped onto a phone's touch slots, with each slot's last position."""

  def __init__(self, slots: int = MAX_TOUCHES):
    self.slots: dict[int, int] = {}
    self.positions: dict[int, tuple[float, float]] = {}
    self.count = slots

  def apply(self, touch: Touch) -> tuple[int, bool] | None:
    """(slot, down) for this touch, or None when every slot is busy."""
    slot = self.slots.get(touch.pointer)
    if slot is None:
      if touch.kind != "down":
        return None
      free = [s for s in range(self.count) if s not in self.slots.values()]
      if not free:
        return None
      slot = self.slots[touch.pointer] = free[0]
    down = touch.kind in ("down", "move")
    self.positions[slot] = (touch.x, touch.y)
    if not down:
      self.slots.pop(touch.pointer, None)
    return slot, down

  def active(self) -> dict[int, tuple[float, float]]:
    return {slot: self.positions[slot] for slot in self.slots.values() if slot in self.positions}

  def reset(self) -> None:
    self.slots.clear()
    self.positions.clear()
