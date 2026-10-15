"""Read-only Jet Link status, polled away from Android Auto's drawing thread."""
from dataclasses import dataclass
import threading
import time


@dataclass(frozen=True)
class JetlinkStatus:
  device: str = ''
  state: str = 'off'
  latency_ms: float | None = None


def read_status(params):
  mode = params.get('JetlinkMode')
  if type(mode) is not int or mode not in (1, 2, 3):
    return JetlinkStatus()
  device = {1: 'Computer', 2: 'iPhone', 3: 'Automatic'}[mode]
  from openpilot.starpilot.models.jetlink_adapter import get_jetlink, runtime_status
  link = get_jetlink()
  if link is None:
    return JetlinkStatus(device, 'unavailable')
  status = link.status()
  if mode == 3 and status.present:
    device = 'Auto: iPhone' if status.transport.startswith('iOS') else 'Auto: Computer'
  if not status.enabled or status.reason:
    return JetlinkStatus(device, 'unavailable')
  if not status.present:
    return JetlinkStatus(device, 'disconnected')
  runtime = runtime_status()
  if runtime.get('active') is True:
    return JetlinkStatus(device, 'active', runtime.get('app_latency_ms'))
  state = runtime.get('state')
  return JetlinkStatus(device, 'ready' if state == 'ready' else 'connecting' if state in ('joining', 'retrying') else 'connected')


class ProjectionJetlinkFeed:
  """Only poll while placed. The renderer takes immutable snapshots without file IO."""
  def __init__(self, params, read=read_status):
    self.params, self.read = params, read
    self._lock = threading.Lock()
    self._stop = threading.Event()
    self._thread = None
    self._enabled = False
    self._status = JetlinkStatus()
    self._read_at = 0

  def set_enabled(self, enabled):
    with self._lock:
      self._enabled = enabled
      if not enabled:
        self._status = JetlinkStatus()
    if enabled and self._thread is None:
      self._thread = threading.Thread(target=self._run, name='aa-jetlink-status', daemon=True)
      self._thread.start()

  def poll(self):
    with self._lock:
      if not self._enabled:
        return
    try:
      status = self.read(self.params)
    except Exception:
      status = JetlinkStatus(state='unavailable')
    with self._lock:
      if self._enabled:
        self._status = status
        self._read_at = time.monotonic_ns()

  def snapshot(self):
    with self._lock:
      status, read_at = self._status, self._read_at
    if status.state != 'off' and time.monotonic_ns() - read_at > 2_500_000_000:
      return JetlinkStatus(status.device, 'unavailable')
    return status

  def _run(self):
    while not self._stop.is_set():
      self.poll()
      self._stop.wait(1.0)

  def close(self):
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=2)
