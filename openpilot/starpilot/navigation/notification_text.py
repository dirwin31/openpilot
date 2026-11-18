"""Turn-by-turn guidance from a navigation app's notification text (iPhone, read over Bluetooth).

The wording here is not yet checked against real iPhone Google Maps notifications; samples are kept for that."""
import json
import re
from datetime import UTC, datetime
import threading
import time
import uuid
from pathlib import Path

from openpilot.starpilot.navigation.phone import OBSERVATION_NS, PhoneNavigationPublisher, _read, observation_limit
from openpilot.starpilot.navigation.wire import IPHONE_SOURCE

# Bundle id -> navigation source. Decided here, never by a field in the payload.
NAVIGATION_APPS = {'com.google.Maps': IPHONE_SOURCE}
KEEP_ALIVE_S = 5.
SAMPLE_LIMIT = 200

METERS = {'ft': .3048, 'feet': .3048, 'foot': .3048, 'yd': .9144, 'yards': .9144, 'yard': .9144, 'mi': 1609.344, 'mile': 1609.344,
          'miles': 1609.344, 'm': 1., 'meters': 1., 'metres': 1., 'meter': 1., 'metre': 1., 'km': 1000., 'kilometers': 1000.,
          'kilometres': 1000.}
FRACTIONS = {'¼': .25, '½': .5, '¾': .75}
DISTANCE = re.compile(r'(?<![\w.])(\d+\s*/\s*\d+|\d+(?:\.\d+)?\s*[¼½¾]?|[¼½¾])\s*(' + '|'.join(sorted(METERS, key=len, reverse=True)) + r')\b',
                      re.IGNORECASE)
# First match wins: (pattern, maneuverType, maneuverModifier). A {side} group fills the modifier.
MANEUVERS = [(re.compile(pattern, re.IGNORECASE), kind, modifier) for pattern, kind, modifier in (
  (r'\bu[- ]?turn\b', 'turn', 'uturn'),
  (r'\broundabout\b|\btraffic circle\b', 'roundabout', ''),
  (r'\b(?:sharp|hard) (?P<side>left|right)\b', 'turn', 'sharp{side}'),
  (r'\b(?:slight|bear) (?P<side>left|right)\b', 'turn', 'slight{side}'),
  (r'\bexit\b', 'off ramp', ''),
  (r'\bramp\b', 'on ramp', ''),
  (r'\bmerge\b', 'merge', ''),
  (r'\b(?:keep|stay) (?P<side>left|right)\b', 'fork', '{side}'),
  (r'\bturn (?P<side>left|right)\b', 'turn', '{side}'),
  (r'\b(?:arrived?|destination (?:is|will be))\b', 'arrive', ''),
  (r'\b(?:continue|head|stay on)\b', 'continue', ''),
)]
ROAD_END = r"\s*(?:[,.;]|\btowards?\b|\bfor\b\s*(?:\d|$)|\bin\s+\d|\bthen\b|$)"
# Google Maps on iPhone: "Use the right 3 lanes to take exit 76B for I-15 S", "Take the Sahara Ave exit".
LANES = re.compile(r'^use\b[^,.;]*?\blanes?\s+to\s+', re.IGNORECASE)
ANY_LANE = re.compile(r'^use any lanes?\b', re.IGNORECASE)
TAKE = re.compile(r"\btake the (?!exit\b|ramp\b)(.+?) (?:exit|ramp)\b", re.IGNORECASE)
# Strongest wording first; a bare "to" is too common to trust.
ROADS = [re.compile(lead + r"\s+(?!the (?:left|right)\b)(.+?)" + ROAD_END, re.IGNORECASE) for lead in (
  r"\b(?:onto|on to)", r"\b(?:stay|continue|head(?: \w+)?) on", r"\btowards?", r"\bexit\s+\w+\s+for", r"\bon")]
RAMP = re.compile(r"^the (?:ramp|exit)(?: (?:to|onto|towards?|for))?\s*", re.IGNORECASE)
RAMP_NAME = re.compile(r"^the (.+?) (?:ramp|exit)$", re.IGNORECASE)
EXIT = re.compile(r'\bexit\s+(\d+\s?[A-Z]?)\b', re.IGNORECASE)


def _distance(text):
  found = DISTANCE.search(text)
  if not found:
    return None, text
  amount, unit = found[1].replace(' ', ''), found[2].lower()
  if '/' in amount:
    top, bottom = amount.split('/')
    value = int(top) / int(bottom) if int(bottom) else None
  else:
    value = float(amount.rstrip('¼½¾') or 0) + FRACTIONS.get(amount[-1], 0.)
  if value is None:
    return None, text
  rest = re.sub(r'^\s*(?:in|after)\s*$', '', text[:found.start()], flags=re.IGNORECASE) + ' ' + text[found.end():]
  return round(value * METERS[unit], 1), re.sub(r'\b(?:in|after)\s*,', ',', rest).strip(' ,.-')


def parse(sender, text):
  """-> navigation fields for one prompt, or None when the words carry no maneuver, distance or road."""
  words = ' '.join(part.strip() for part in (sender, text) if part and part.strip())
  if not words:
    return None
  distance, rest = _distance(words)
  lanes = LANES.match(rest)
  core = rest[lanes.end():] if lanes else rest
  kind = modifier = ''
  for pattern, kind_, modifier_ in MANEUVERS:
    found = pattern.search(core)
    if found:
      side = (found.groupdict().get('side') or '').lower()
      kind, modifier = kind_, modifier_.format(side=side.capitalize() if modifier_.startswith(('sharp', 'slight')) else side)
      break
  road = ''
  if kind != 'arrive':
    taken = TAKE.search(core)
    for found in [taken] if taken else (pattern.search(core) for pattern in ROADS):
      if found and RAMP.sub('', found[1]).strip():
        road = RAMP.sub('', found[1]).strip()
        road = RAMP_NAME.sub(r'\1', road)
        break
  exit_number = EXIT.search(core)
  if not kind and distance is None:
    return None
  # Lane advice is worth reading, so a lane prompt keeps its whole sentence.
  result = {'instruction': (rest if lanes and not ANY_LANE.match(rest) else road or rest)[:500], 'maneuverType': kind, 'maneuverModifier': modifier}
  if distance is not None and kind != 'arrive':
    result['distanceMeters'] = distance
  if road:
    result['roadName'] = road[:500]
  if exit_number:
    result['exitNumber'] = exit_number[1].replace(' ', '').upper()
  return result


class NotificationNavigation:
  """Feeds a navigation app's notifications into phone guidance. A companion app that is sending guidance keeps priority."""
  def __init__(self, publisher=None, enabled=lambda: True, mono_clock=time.monotonic_ns, timer=threading.Timer, samples=None):
    self.publisher = publisher or PhoneNavigationPublisher()
    self.enabled = enabled
    self.clock = mono_clock
    self.timer = timer
    self.samples = Path(samples) if samples else None
    self.lock = threading.Lock()
    self.session = None
    self.mine = None  # our last session, still in the handoff after its step expired
    self.sequence = 0
    self.current = None  # (payload without ageMs, observed ns)

  def offer(self, payload):
    """True when the notification belongs to directions and must not be shown as a toast."""
    source = NAVIGATION_APPS.get(payload.get('app')) if payload.get('source') == 'ios' else None
    if source is None or not self.enabled():
      return False
    sender = payload.get('sender', '')
    # iPhone titles are often just the app's name or Google Maps' fixed "Navigation Guidance".
    generic = sender.strip().casefold() in (payload.get('appName', '').strip().casefold(), 'navigation guidance')
    parsed = parse('' if generic else sender, payload.get('text', ''))
    self._sample(payload, parsed)
    if parsed is None:
      # Updates and quiet prompts from a navigation app arrive here too; none of them is a message.
      return True
    with self.lock:
      now = self.clock()
      if self._foreign(now):
        return True
      ended = parsed['maneuverType'] == 'arrive'
      if self.session is None or self._retired():
        self.session, self.sequence = uuid.uuid4().hex, 0
        self.mine = self.session
      self.sequence += 1
      fields = {'session': self.session, 'sequence': self.sequence, 'active': not ended, 'source': source,
                **({'instruction': ''} if ended else {**parsed, 'untilPassed': 'distanceMeters' not in parsed})}
      observed = now - int(payload['ageMs'] * 1e6)
      try:
        accepted = self.publisher.publish({**fields, 'ageMs': payload['ageMs']})['accepted']
      except (ValueError, OSError):
        accepted = False
      self.current = (fields, observed) if accepted and not ended else None
      if ended or not accepted:
        self.session = None
      if self.current is not None:
        self._schedule(self.sequence)
    return True

  def _foreign(self, now):
    # A companion app keeps one session per trip, goes inactive between prompts and is refused for good once
    # retired, so anything it sent within an observation counts as it guiding.
    old = _read(self.publisher.path)
    return bool(old is not None and old['session'] not in (self.session, self.mine) and 0 <= now - old['received'] <= OBSERVATION_NS)

  def _retired(self):
    old = _read(self.publisher.path)
    return old is not None and old['session'] != self.session

  def _schedule(self, sequence):
    timer = self.timer(KEEP_ALIVE_S, self._keep_alive, args=(sequence,))
    timer.daemon = True
    timer.start()

  def _keep_alive(self, sequence):
    # Renews the receipt lease only; the observation keeps its original time and still expires.
    with self.lock:
      if self.current is None or self.sequence != sequence:
        return
      fields, observed = self.current
      now = self.clock()
      age_ms = (now - observed) / 1e6
      if age_ms * 1e6 > observation_limit(fields):
        # Our session carries on, so the next prompt is not mistaken for another app's guidance.
        self.current = None
        return
      if self._foreign(now) or self._retired():
        self.current = self.session = None
        return
      try:
        if not self.publisher.publish({**fields, 'ageMs': age_ms})['accepted']:
          self.current = self.session = None
          return
      except (ValueError, OSError):
        self.current = self.session = None
        return
      self._schedule(sequence)

  def _sample(self, payload, parsed):
    # Real wording for tuning the parser; road prompts only, capped, on the comma.
    if self.samples is None:
      return
    try:
      lines = self.samples.read_text().splitlines()[-(SAMPLE_LIMIT - 1):] if self.samples.exists() else []
      row = {'at': datetime.now(UTC).isoformat(timespec='seconds'), 'parsed': parsed,
             **{key: payload.get(key, '') for key in ('ageMs', 'app', 'category', 'sender', 'text')}}
      lines.append(json.dumps(row))
      self.samples.parent.mkdir(parents=True, exist_ok=True)
      self.samples.write_text('\n'.join(lines) + '\n')
    except OSError:
      pass
