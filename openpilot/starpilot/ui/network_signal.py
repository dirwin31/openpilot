"""Cellular and Wi-Fi strength read together; deviceState only carries the link in use. Read-only."""
import json
import threading
import time
from typing import NamedTuple

TECH = {'nr': '5G', 'lte': 'LTE', 'utran': '3G', 'umts': '3G', 'gsm': '2G'}
WANTED_S = 5.
STALE_S = 10.


class Signal(NamedTuple):
  cell: int | None = None  # 1 to 4, None when not connected
  wifi: int | None = None
  active: str | None = None  # 'cell' or 'wifi': the link carrying data
  tech: str = 'LTE'


def steps(percent: float) -> int:
  return 1 if percent < 25 else 2 if percent < 50 else 3 if percent < 75 else 4


def read_signal() -> Signal:
  from openpilot.common.hardware.comma.hardware import MODEM_STATE_PATH, get_default_route_iface, wpa_supplicant_cmd
  cell = wifi = active = iface = None
  tech = 'LTE'
  try:
    with open(MODEM_STATE_PATH) as f:
      modem = json.load(f)
    if modem.get('connected'):
      cell = steps(float(modem.get('signal_quality', 0)))
      tech = TECH.get(modem.get('network_type'), tech)
  except (OSError, ValueError, TypeError, AttributeError):
    pass
  try:
    dbm = int(wpa_supplicant_cmd('SIGNAL_POLL')['RSSI'])
    if -100 < dbm <= 0:
      wifi = steps(120 + max(-100, min(-20, dbm)))
  except Exception:
    pass
  try:
    iface = get_default_route_iface()
  except Exception:
    pass
  if iface and iface.startswith('wlan'):
    active = 'wifi' if wifi else None
  elif cell and not (iface and iface.startswith('eth')):
    active = 'cell'
  return Signal(cell, wifi, active, tech)


class SignalFeed:
  """Polls in the background only while snapshot() keeps being asked; snapshot() never does IO."""
  def __init__(self, read=read_signal, period=2.0):
    self.read, self.period = read, period
    self._lock = threading.Lock()
    self._thread = None
    self._signal = Signal()
    self._read_at = self._wanted_at = 0.

  def poll(self):
    try:
      signal = self.read()
    except Exception:
      signal = Signal()
    with self._lock:
      self._signal, self._read_at = signal, time.monotonic()

  def snapshot(self) -> Signal:
    now = time.monotonic()
    with self._lock:
      self._wanted_at = now
      signal, fresh = self._signal, now - self._read_at <= STALE_S
      if self._thread is None:
        self._thread = threading.Thread(target=self._run, name='network-signal', daemon=True)
        self._thread.start()
    return signal if fresh else Signal()

  def _run(self):
    while True:
      with self._lock:
        wanted = time.monotonic() - self._wanted_at <= WANTED_S
      if wanted:
        self.poll()
      time.sleep(self.period)
