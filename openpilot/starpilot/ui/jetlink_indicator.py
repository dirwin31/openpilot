"""Jetlink's home-screen icon: whether the remote device is connected or driving."""

from __future__ import annotations

import logging
from collections.abc import Callable

# off: no icon. waiting: Jetlink is on, no remote device on the cable yet.
# connected: the remote device is on the cable. active: the big model drives.
# unavailable: Jetlink is on but cannot run (the offroad alert says why).
STATES = ("off", "waiting", "connected", "active", "unavailable")
READ_INTERVAL_NS = 1_000_000_000

# RGBA per state, drawn by the home screen
COLORS = {
  "waiting": (160, 160, 160, 255),
  "connected": (255, 255, 255, 230),
  "active": (70, 200, 110, 255),
  "unavailable": (255, 150, 60, 255),
}


def read_state(params) -> str:
  from openpilot.starpilot.system.jetlink.lifecycle import selected
  if not selected(params):
    return "off"   # the default: nothing heavier than one param read
  from openpilot.starpilot.models.jetlink_adapter import get_jetlink, runtime_status
  link = get_jetlink()
  if link is None:
    return "unavailable"
  status = link.status()
  if not status.enabled:
    return "off"   # a Chestnut is fitted, or the device cannot present the gadget
  if runtime_status().get("active") is True:
    return "active"
  if status.reason:
    return "unavailable"
  return "connected" if status.present else "waiting"


class JetlinkIndicator:
  """Jetlink's status reads files, so the home screen asks at most once a second."""

  def __init__(self, read: Callable[[object], str] = read_state, interval_ns: int = READ_INTERVAL_NS):
    self._read = read
    self._interval_ns = interval_ns
    self._read_ns: int | None = None
    self._state = "off"

  def state(self, params, now_ns: int) -> str:
    if self._read_ns is None or not 0 <= now_ns - self._read_ns < self._interval_ns:
      self._read_ns = now_ns
      try:
        self._state = self._read(params)
      except Exception:
        logging.getLogger(__name__).exception("Jetlink home status unavailable")
        self._state = "unavailable"
    return self._state

