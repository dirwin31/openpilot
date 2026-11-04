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
        code = getattr(error, 'code', 'unavailable')
        if str(error) in ('org.bluez.Error.Failed', 'org.bluez.Error.InProgress', 'org.bluez.Error.NotReady'):
          code = 'reconnecting'
        elif str(error) in ('org.bluez.Error.NotAuthorized', 'org.bluez.Error.AuthenticationFailed'):
          code = 'detector_pairing_required'
        messages = {
          'reconnecting': 'Reconnecting to detector',
          'radio_off': 'Turn on Bluetooth to connect the detector',
          'busy': 'Waiting for Bluetooth to become available',
          'detector_pairing_required': 'Pair or select the detector in Bluetooth settings',
          'session_expired': 'Sign in to Galaxy again',
        }
        # Unknown transport errors remain visible as a failed connection, but
        # D-Bus identifiers are not useful as user-facing status messages.
        result = {'connected': False, 'alerts': [], 'connection_state': code if code in messages else 'unavailable',
                  'error': messages.get(code, 'Unable to read detector alerts. Check the detector connection.')}

      return {**result, 'config': config}
