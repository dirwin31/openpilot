"""GATT operations admitted by the existing Bluetooth owner, including its AA lease."""
import time

from openpilot.starpilot.bluetooth.owner import DEVICE, BluetoothOwner, BluetoothRejected
from openpilot.starpilot.uniden.protocol import ALERT_UUID, WRITE_UUID, GATT, alert_key, model_for_name, parse_alerts


def exchange(owner, address, model, session, *, mute=None):
  if model not in ('R4', 'R8'):
    raise ValueError('Unsupported detector model')
  if not owner.lock.acquire(blocking=False):
    raise BluetoothRejected('Bluetooth is busy', code='busy')
  admitted = False
  try:
    lease = getattr(owner, 'phone_lease', None)
    def validate():
      if not owner.session_valid(session):
        raise BluetoothRejected('Session expired', code='session_expired')
      if BluetoothOwner._admission_busy(owner):
        raise BluetoothRejected('Finish Bluetooth discovery or pairing first', code='busy')
      if lease is not None and (owner.phone_lease is not lease or lease.released or lease.session is not None or
                               lease.enabled is None or not lease.enabled() or lease.selected_receiver is None or
                               lease.selected_receiver().upper() != lease.receiver or address == lease.receiver):
        raise BluetoothRejected('Android Auto Bluetooth owner changed', code='busy')
    validate()
    if lease is None:
      owner._admission_enter()
      admitted = True
    client = owner._client()
    adapter, props, sender = client.adapter_identity()
    if props.get('Powered') is not True or props.get('Discovering') is True:
      raise BluetoothRejected('Bluetooth is off or discovering', code='busy')
    if lease is not None and (adapter, sender) != (lease.adapter_path, lease.bluez_sender):
      raise BluetoothRejected('Android Auto adapter changed', code='changed')
    objects = client._objects()
    device = next(((p, i[DEVICE]) for p, i in objects.items() if DEVICE in i and
                   i[DEVICE].get('Address', '').upper() == address and p.startswith(adapter + '/')), None)
    if device is None:
      raise BluetoothRejected('Pair the selected detector first', code='changed')
    path, props = device
    if props.get('Paired') is not True or props.get('Blocked') is True or model_for_name(props.get('Name')) != model:
      raise BluetoothRejected('Choose a paired detector with the matching model', code='changed')
    if props.get('Connected') is not True:
      if mute is not None:
        raise BluetoothRejected('Detector disconnected; mute was not sent', code='changed')
      retries = getattr(owner, '_uniden_retries', {})
      owner._uniden_retries = retries
      if time.monotonic() < retries.get(address, 0):
        raise BluetoothRejected('Waiting to reconnect detector', code='busy')
      retries[address] = time.monotonic() + 15
      validate()
      client._call(path, DEVICE, 'Connect', timeout=3.0)
      # Service discovery is asynchronous. Read only after a subsequent ready snapshot.
      raise BluetoothRejected('Connecting detector', code='busy')
    if props.get('ServicesResolved') is not True:
      raise BluetoothRejected('Waiting for detector services', code='busy')
    paths = {p[GATT].get('UUID', '').lower(): name for name, p in objects.items() if name.startswith(path + '/') and GATT in p}
    if ALERT_UUID not in paths:
      raise BluetoothRejected('Detector alert characteristic is unavailable', code='changed')
    def read():
      return parse_alerts(client._call(paths[ALERT_UUID], GATT, 'ReadValue', 'a{sv}', ({},), timeout=2.0)[0], model)
    alerts = read()
    observed_at = time.monotonic()
    validate()
    current_adapter, current_props, current_sender = client.adapter_identity()
    if (current_adapter, current_sender) != (adapter, sender) or current_props.get('Powered') is not True:
      raise BluetoothRejected('Bluetooth adapter changed', code='changed')
    current = client._objects().get(path, {}).get(DEVICE, {})
    if current.get('Paired') is not True or current.get('Connected') is not True:
      raise BluetoothRejected('Detector connection changed', code='changed')
    command = ''
    if mute is not None:
      at = mute.get('observed_at')
      if type(at) not in (float, int) or not 0 <= time.monotonic() - at < 3 or not alerts or alert_key(alerts) != mute.get('alerts'):
        raise BluetoothRejected('Alert changed; tap the current alert', code='changed')
      if WRITE_UUID not in paths:
        raise BluetoothRejected('Detector mute characteristic is unavailable', code='changed')
      if not all(a['muted'] for a in alerts):
        validate()
        client._call(paths[WRITE_UUID], GATT, 'WriteValue', 'aya{sv}',
                     (b'BTreqMUTE:1', {'type': ('s', 'command')}), timeout=2.0)
        command = 'sent_unconfirmed'
        # A write without response is not a mute acknowledgment. Only alert packets confirm it.
    return {'connected': True, 'address': address, 'model': model, 'alerts': alerts,
            'observed_at': observed_at, 'can_mute': WRITE_UUID in paths, 'command': command}
  finally:
    if admitted:
      owner._admission_leave()
    owner.lock.release()
