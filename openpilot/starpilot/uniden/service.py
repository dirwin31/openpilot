"""Configuration and request admission shared by Galaxy and both onroad displays."""
import threading

from openpilot.starpilot.bluetooth.owner import BluetoothRejected, BluetoothUnavailable
from openpilot.starpilot.uniden.protocol import DEFAULT_CONFIG, normalize_config


class DetectorService:
  def __init__(self, params, parked):
    self.params, self.parked = params, parked
    self.lock = threading.Lock()

  def config(self):
    try:
      return normalize_config(self.params.get('UnidenConfig') or DEFAULT_CONFIG)
    except (TypeError, ValueError):
      return dict(DEFAULT_CONFIG)

  def request(self, payload, exchange, *, valid=lambda: True):
    if type(payload) is not dict or payload.get('operation') not in ('status', 'configure', 'mute'):
      raise ValueError('Invalid detector request')
    operation = payload['operation']
    fields = {'operation'} | ({'config'} if operation == 'configure' else {'address', 'observed_at', 'alerts'} if operation == 'mute' else set())
    if set(payload) != fields:
      raise ValueError('Invalid detector request fields')
    with self.lock:
      if not valid():
        raise BluetoothRejected('Session expired', code='session_expired')
      config = self.config()
      if operation == 'configure':
        if not self.parked():
          raise BluetoothRejected('Park before selecting a detector', code='park_required')
        config = normalize_config(payload['config'])
        self.params.put('UnidenConfig', config)
        return {'config': config, 'connected': False, 'alerts': []}
      if not config['enabled']:
        return {'config': config, 'connected': False, 'alerts': []}
      if operation == 'mute' and payload['address'] != config['address']:
        raise BluetoothRejected('Selected detector changed', code='changed')
      try:
        result = exchange(config, payload if operation == 'mute' else None)
      except (BluetoothRejected, BluetoothUnavailable, OSError, RuntimeError, ValueError) as error:
        if operation == 'mute':
          raise
        result = {'connected': False, 'alerts': [], 'error': str(error)[:160]}
      return {**result, 'config': config}
