"""Bounded polling and mute delivery off the rendering thread; no queued command replay."""
import threading
import time

from openpilot.starpilot.uniden.protocol import alert_key

ENDPOINT = '/api/bluetooth/uniden'


class DetectorFeed:
  def __init__(self, call=None, clock=time.monotonic):
    self.call, self.clock = call, clock
    self.lock = threading.Lock()
    self.stop = threading.Event()
    self.thread = None
    self.enabled = False
    self.requested_at = 0
    self.state = {}
    self.pending = None

  def set_enabled(self, enabled):
    with self.lock:
      self.enabled = enabled
      self.requested_at = self.clock()
      if not enabled:
        self.state, self.pending = {}, None
      if enabled and self.thread is None:
        self.thread = threading.Thread(target=self.run, name='uniden-display', daemon=True)
        self.thread.start()

  def snapshot(self):
    with self.lock:
      state = self.state
    at = state.get('observed_at', -1e9)
    if state.get('connected') and not 0 <= self.clock() - at < 3:
      return {**state, 'connected': False, 'alerts': [], 'error': 'Detector data unavailable'}
    return state

  def mute(self, state):
    with self.lock:
      if self.enabled and self.pending is None and state.get('connected') and state.get('can_mute') and state.get('alerts'):
        self.pending = {'operation': 'mute', 'address': state['address'], 'observed_at': state['observed_at'],
                        'alerts': alert_key(state['alerts'])}

  def poll(self):
    with self.lock:
      if not self.enabled or self.clock() - self.requested_at > 1:
        self.state, self.pending = {}, None
        return
      payload, self.pending = self.pending or {'operation': 'status'}, None
    try:
      if self.call is None:
        from openpilot.starpilot.galaxy_bluetooth.phone_ui import galaxy_call
        self.call = galaxy_call()
      status, result = self.call('POST', ENDPOINT, payload)
      if status != 200:
        raise RuntimeError(result.get('error', 'Detector unavailable'))
    except Exception as error:
      result = {'connected': False, 'alerts': [], 'error': str(error)[:100]}
    with self.lock:
      if self.enabled:
        self.state = {**result, 'config': result.get('config', self.state.get('config', {}))}

  def run(self):
    while not self.stop.is_set():
      self.poll()
      self.stop.wait(0.5)

  def close(self):
    self.set_enabled(False)
    self.stop.set()
    if self.thread is not None:
      self.thread.join(timeout=1)
