"""R-series alert snapshots based on AegisX86/UnidenR8wlink PROTOCOL.md (MIT).

The reference hardware is R8w. R4/R8 firmware compatibility needs device testing.
"""
import math
import re

ALERT_UUID = '6eb675ab-8bd1-1b9a-7444-621e52ec6823'
WRITE_UUID = '2c86686a-53dc-25b3-0c4a-f0e10c8dee20'
GATT = 'org.bluez.GattCharacteristic1'
FREQUENCY_BANDS = {'X', 'K', 'KA', 'K POP', 'KA POP'}
BANDS = {'X', 'K', 'KA', 'LASER', 'MRCD', 'MRCT', 'RT3', 'RT4', 'K POP', 'KA POP'}
LOCK_BANDS = ('X', 'K', 'KA', 'LASER')
BORDER_BANDS = ('K', 'KA', 'LASER')
TIMED_BANDS = ('X', 'K', 'KA')
BORDER_STYLES = ('edge', 'solid', 'pulse', 'chase', 'corners', 'direction')
# The Edge style marks the detector by shape (dashes, chevrons), so its colors may share a family with the curve glow.
# Amber and coral match the card's band letters. Purple, violet and yellow stay valid for saved settings.
BORDER_SWATCHES = {'amber': '#FFBA4E', 'coral': '#FF5B65', 'magenta': '#FF1FCE', 'ice': '#9FE8FF', 'lime': '#A6FF00',
                   'white': '#FFFFFF', 'ultraviolet': '#7A1FFF', 'purple': '#B026FF', 'violet': '#8F7BFF', 'yellow': '#E3FF00'}
EDGE_PARTS = ('line', 'dashes', 'glow', 'veil', 'chevrons')
RULE_BOUNDS = {'X': (10.0, 11.0), 'K': (23.0, 25.0), 'KA': (33.0, 36.0)}
DEFAULT_CONFIG = {'enabled': False, 'address': '', 'model': 'R8', 'transparency': 8, 'hide_when_no_alert': True,
                  'auto_mute': False, 'hide_filtered': False, 'hide_below_limit': False, 'hide_q_ride': False, 'hide_mute_memory': False,
                  'frequency_rules': [],
                  'quiet_ride': False, 'quiet_margin': 0, 'quiet_bands': ['X', 'K'],
                  'auto_learn': False, 'learn_bands': ['X', 'K'], 'border_bands': ['KA', 'LASER'],
                  'border_colors': {'KA': 'coral', 'K': 'amber', 'LASER': 'coral'},
                  'border_styles': {'KA': 'edge', 'K': 'edge', 'LASER': 'edge'},
                  'border_edge': {'line': True, 'dashes': True, 'glow': False, 'veil': False, 'chevrons': True},
                  'border_muted_percent': 50,
                  'timed_mute': False, 'timed_mute_seconds': 5, 'timed_mute_strength': 6, 'timed_mute_bands': ['X', 'K', 'KA'],
                  'unmute_on_breakthrough': False}
OPTIONAL_KEYS = set(DEFAULT_CONFIG) - {'enabled', 'address', 'model'}
# Field 7 per PROTOCOL.md; only 1 and 2 are confirmed on hardware, the rest are documented but untested.
MUTE_MODES = {'1': '', '2': 'manual', '3': 'memory', '4': 'auto_memory', '5': 'blocked', '6': 'quiet_ride'}


def model_for_name(name):
  match = re.search(r'(?:^|[^A-Z0-9])(R[48])W?(?=$|[^A-Z0-9])', str(name).upper())
  return match[1] if match else None


def normalize_config(value):
  if type(value) is not dict or set(value) - set(DEFAULT_CONFIG) or not (set(DEFAULT_CONFIG) - OPTIONAL_KEYS) <= set(value):
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
  for key in ('auto_mute', 'hide_filtered', 'hide_below_limit', 'hide_q_ride', 'hide_mute_memory'):
    if type(config[key]) is not bool:
      raise ValueError('Filter toggles must be boolean')
  config['frequency_rules'] = normalize_rules(config['frequency_rules'])
  for key in ('quiet_ride', 'auto_learn'):
    if type(config[key]) is not bool:
      raise ValueError('Quiet Ride and learning toggles must be boolean')
  if type(config['quiet_margin']) is not int or not 0 <= config['quiet_margin'] <= 15:
    raise ValueError('Quiet Ride margin must be 0–15')
  for key in ('timed_mute', 'unmute_on_breakthrough'):
    if type(config[key]) is not bool:
      raise ValueError('Timed mute toggles must be boolean')
  for key, low, high, message in (('timed_mute_seconds', 1, 60, 'Mute delay must be 1–60 seconds'),
                                  ('timed_mute_strength', 2, 8, 'Warn again at strength 2–8, or never')):
    # 0 for the strength means never warn again.
    if type(config[key]) is not int or not (low <= config[key] <= high or (key == 'timed_mute_strength' and config[key] == 0)):
      raise ValueError(message)
  for key, allowed in (('quiet_bands', LOCK_BANDS), ('learn_bands', LOCK_BANDS), ('border_bands', BORDER_BANDS),
                       ('timed_mute_bands', TIMED_BANDS)):
    config[key] = normalize_bands(config[key], allowed)
  for key, choices, message in (('border_colors', BORDER_SWATCHES, 'Choose a border color from the list'),
                                ('border_styles', BORDER_STYLES, 'Choose a border style from the list')):
    chosen = config[key]
    if type(chosen) is not dict or set(chosen) != set(BORDER_BANDS) or any(type(v) is not str or v not in choices for v in chosen.values()):
      raise ValueError(message)
  edge = config['border_edge']
  if type(edge) is not dict or set(edge) != set(EDGE_PARTS) or any(type(v) is not bool for v in edge.values()):
    raise ValueError('Edge parts must be on or off')
  if type(config['border_muted_percent']) is not int or not 0 <= config['border_muted_percent'] <= 100:
    raise ValueError('Muted brightness must be 0–100%')
  config['border_edge'] = dict(edge)
  config['address'] = config['address'].upper()
  return config


def normalize_bands(bands, allowed):
  if type(bands) is not list or any(type(band) is not str or band not in allowed for band in bands):
    raise ValueError('Choose from ' + ', '.join(allowed))
  return [band for band in allowed if band in bands]


def parse_alerts(payload, model='R8'):
  text = bytes(payload).decode('ascii').strip('\x00\r\n ')
  if not text or len(text) > 4096:
    raise ValueError('Empty or oversized alert snapshot')
  alerts = []
  for segment in text.split('&'):
    if segment == '0':
      continue
    f = segment.split(',')
    if len(f) not in (8, 9) or f[0] != '1' or f[2].upper() not in BANDS:
      raise ValueError('Unsupported alert packet')
    strength = int(f[3])
    if not 1 <= strength <= 8:
      raise ValueError('Invalid signal strength')
    band = f[2].upper()
    frequency = None
    if band in FREQUENCY_BANDS:
      frequency = float(f[5])
      if not math.isfinite(frequency) or not 0 < frequency < 100:
        raise ValueError('Invalid frequency')
    alerts.append({'band': band, 'strength': strength, 'frequency': frequency,
                   'direction': {'F': 'front', 'S': 'side', 'R': 'rear'}.get(f[6], '') if model == 'R8' else '',
                   'muted': f[7] in ('2', '3', '4', '5', '6'), 'mute_mode': MUTE_MODES.get(f[7], '')})
  return sorted(alerts, key=lambda a: (a['band'] != 'LASER', -a['strength']))


def alert_key(alerts):
  """Stable across strength changes; never use the detector's usually-zero alert ID."""
  return sorted([[a['band'], a['frequency'], a['direction']] for a in alerts], key=str)


def normalize_rules(rules):
  if type(rules) is not list or len(rules) > 32:
    raise ValueError('Use at most 32 frequency rules')
  result = []
  for rule in rules:
    if type(rule) is not dict or set(rule) != {'enabled', 'label', 'band', 'frequency', 'tolerance_mhz'}:
      raise ValueError('Invalid frequency rule')
    if type(rule['enabled']) is not bool or type(rule['label']) is not str or len(rule['label']) > 60:
      raise ValueError('Use a label of at most 60 characters')
    bounds = RULE_BOUNDS
    band = rule['band']
    if type(band) is not str or band not in bounds:
      raise ValueError('Choose X, K or KA band')
    frequency, tolerance = rule['frequency'], rule['tolerance_mhz']
    if type(frequency) not in (int, float) or not math.isfinite(frequency) or not bounds[band][0] <= frequency <= bounds[band][1]:
      raise ValueError('Frequency must be in the selected band, in GHz')
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or not 0 <= tolerance <= 10:
      raise ValueError('Tolerance must be 0–10 MHz')
    result.append(dict(rule))
  return result


def matches_rule(alert, rules):
  frequency = alert.get('frequency')
  return frequency is not None and any(rule['enabled'] and alert['band'] == rule['band'] and
    abs(frequency - rule['frequency']) <= rule['tolerance_mhz'] / 1000 + 1e-9 for rule in rules)
