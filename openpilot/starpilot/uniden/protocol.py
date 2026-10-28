"""R-series alert snapshots based on AegisX86/UnidenR8wlink PROTOCOL.md (MIT).

The reference hardware is R8w. R4/R8 firmware compatibility needs device testing.
"""
import math
import re

ALERT_UUID = '6eb675ab-8bd1-1b9a-7444-621e52ec6823'
WRITE_UUID = '2c86686a-53dc-25b3-0c4a-f0e10c8dee20'
GATT = 'org.bluez.GattCharacteristic1'
BANDS = {'X', 'K', 'KA', 'LASER', 'MRCD', 'MRCT', 'RT3', 'RT4', 'K POP', 'KA POP'}
DEFAULT_CONFIG = {'enabled': False, 'address': '', 'model': 'R8', 'transparency': 8, 'hide_when_no_alert': True}
DISPLAY_KEYS = {'transparency', 'hide_when_no_alert'}


def model_for_name(name):
  match = re.search(r'(?:^|[^A-Z0-9])(R[48])W?(?=$|[^A-Z0-9])', str(name).upper())
  return match[1] if match else None


def normalize_config(value):
  if type(value) is not dict or set(value) - set(DEFAULT_CONFIG) or not (set(DEFAULT_CONFIG) - DISPLAY_KEYS) <= set(value):
    raise ValueError('Invalid detector configuration')
  config = {**DEFAULT_CONFIG, **value}
  if type(config['transparency']) is not int or not 0 <= config['transparency'] <= 100:
    raise ValueError('Background transparency must be 0–100%')
  if type(config['hide_when_no_alert']) is not bool:
    raise ValueError('Hide when no alert must be a boolean')
  if type(config['enabled']) is not bool or config['model'] not in ('R4', 'R8'):
    raise ValueError('Choose R4 or R8')
  if type(config['address']) is not str or (config['address'] and not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', config['address'])):
    raise ValueError('Invalid detector address')
  if config['enabled'] and not config['address']:
    raise ValueError('Choose a paired detector')
  config['address'] = config['address'].upper()
  return config


def parse_alerts(payload, model='R8'):
  text = bytes(payload).decode('ascii').strip('\x00\r\n ')
  if not text or len(text) > 4096:
    raise ValueError('Empty or oversized alert snapshot')
  alerts = []
  for segment in text.split('&'):
    if segment == '0':
      continue
    f = segment.split(',')
    if len(f) not in (8, 9) or f[0] != '1' or f[2].upper() not in BANDS or f[7] not in ('1', '2', '3', '4', '5', '6'):
      raise ValueError('Unsupported alert packet')
    strength = int(f[3])
    if not 1 <= strength <= 8:
      raise ValueError('Invalid signal strength')
    band = f[2].upper()
    frequency = None
    if band not in ('LASER', 'RT3', 'RT4'):
      frequency = float(f[5])
      if not math.isfinite(frequency) or not 0 < frequency < 100:
        raise ValueError('Invalid frequency')
    alerts.append({'band': band, 'strength': strength, 'frequency': frequency,
                   'direction': {'F': 'front', 'S': 'side', 'R': 'rear'}.get(f[6], '') if model == 'R8' else '',
                   'muted': f[7] != '1'})
  return sorted(alerts, key=lambda a: (a['band'] != 'LASER', -a['strength']))


def alert_key(alerts):
  """Stable across strength changes; never use the detector's usually-zero alert ID."""
  return sorted([[a['band'], a['frequency'], a['direction']] for a in alerts], key=str)
