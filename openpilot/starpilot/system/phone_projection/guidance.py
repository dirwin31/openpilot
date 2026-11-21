"""Turn-by-turn the projecting phone sends its head unit (the comma), handed to StarPilot as phone guidance.

Android Auto sends the navigation status service: a status, the next step (maneuver, road, lanes) and the
distance to it. These become the same payload a companion app posts, so navigationd, the turn intents and
the car's own cluster use them unchanged, behind the existing "forward phone guidance" setting.

Wire layout is the inverse of StarPort's encoders in ``starport/nav_status.py`` (the comma playing the phone).
The newer state/position messages carry Android ``Maneuver.TYPE_*`` and lanes and are preferred; the older
turn message is used only for its road name, since its event/side numbering is not certain.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable

from openpilot.starpilot.system.starport.wire import one, parse_fields, signed

NAV_START, NAV_STOP = 0x8001, 0x8002
NAV_STATUS, NAV_TURN, NAV_DISTANCE, NAV_STATE, NAV_POSITION = 0x8003, 0x8004, 0x8005, 0x8006, 0x8007
STATUS_ACTIVE, STATUS_INACTIVE, STATUS_REROUTING = 1, 2, 3
TYPE_ENUM = 1  # StarPort's numbering; image options are sent too, so either reading has what it needs
IMAGE_SIZE, IMAGE_DEPTH = 256, 16
MIN_INTERVAL_MS = 1000

ANDROID_AUTO_SOURCE = 'androidAuto'
KEEP_ALIVE_S = 5.
MAX_SESSIONS = 8  # per phone connection; the handoff refuses everything after 256 retired sessions

# Lane shapes, left to right in the step's lane list.
SHAPE_SIDES = {2: 'left', 3: 'right', 4: 'left', 5: 'right', 6: 'left', 7: 'right', 8: 'left', 9: 'right'}
SIDED_TYPES = ('fork', 'off ramp', 'on ramp', 'merge')


def _maneuver_table() -> dict[int, tuple[str, str]]:
  table = {1: ('continue', ''), 2: ('continue', ''), 3: ('fork', 'left'), 4: ('fork', 'right'), 11: ('turn', 'uturn'),
           12: ('turn', 'uturn'), 29: ('merge', ''), 36: ('continue', 'straight')}
  for base, modifiers in ((5, ('slightLeft', 'slightRight')), (7, ('left', 'right')), (9, ('sharpLeft', 'sharpRight'))):
    table[base], table[base + 1] = ('turn', modifiers[0]), ('turn', modifiers[1])
  for value in range(13, 21):
    table[value] = ('on ramp', 'left' if value % 2 else 'right')
  for value in range(21, 25):
    table[value] = ('off ramp', 'left' if value % 2 else 'right')
  table.update({25: ('fork', 'left'), 26: ('fork', 'right'), 27: ('merge', 'left'), 28: ('merge', 'right')})
  for value in (30, 31, 32, 33, 34, 35, 43, 44):
    table[value] = ('roundabout', '')
  for value in (39, 40, 41, 42):
    table[value] = ('arrive', '')
  return table


MANEUVERS = _maneuver_table()
PHRASES = {'turn': 'Turn', 'continue': 'Continue', 'on ramp': 'Take the ramp', 'off ramp': 'Take the exit', 'fork': 'Keep',
           'merge': 'Merge', 'roundabout': 'Enter the roundabout', 'arrive': 'Arrive at your destination'}
MODIFIER_WORDS = {'left': 'left', 'right': 'right', 'slightLeft': 'slight left', 'slightRight': 'slight right',
                  'sharpLeft': 'sharp left', 'sharpRight': 'sharp right', 'uturn': 'U-turn'}


def maneuver(value: int) -> tuple[str, str]:
  """Android Maneuver.TYPE_* -> (maneuverType, maneuverModifier) in phone guidance's vocabulary."""
  return MANEUVERS.get(int(value or 0), ('', ''))


def _messages(fields: dict, number: int) -> list[dict]:
  out = []
  for raw in fields.get(number, []):
    if isinstance(raw, bytes):
      try:
        out.append(parse_fields(raw))
      except ValueError:
        pass
  return out


def _text(fields: dict, number: int) -> str:
  value = one(fields, number)
  return value.decode(errors='replace').strip()[:500] if isinstance(value, bytes) else ''


def parse_lanes(step: dict) -> list[dict]:
  """[{'sides': {'left', ...}, 'highlighted': bool}] left to right; a lane is highlighted if any arrow is."""
  lanes = []
  for lane in _messages(step, 3)[:16]:
    directions = _messages(lane, 1)[:8]
    lanes.append({'sides': {SHAPE_SIDES[s] for d in directions if (s := one(d, 1)) in SHAPE_SIDES},
                  'highlighted': any(bool(one(d, 2)) for d in directions)})
  return lanes


def lane_side(lanes: list[dict]) -> str:
  """The side of the road the highlighted lanes sit on, when they are all on one side of the middle."""
  marked = [i for i, lane in enumerate(lanes) if lane['highlighted']]
  if not marked or len(marked) == len(lanes):
    return ''
  middle = (len(lanes) - 1) / 2
  if all(i < middle for i in marked):
    return 'left'
  if all(i > middle for i in marked):
    return 'right'
  return ''


def parse_state(data: bytes) -> dict | None:
  """NavigationState: steps[0].{maneuver.type, road.name, lanes}."""
  steps = _messages(parse_fields(data), 1)
  if not steps:
    return None
  step = steps[0]
  maneuvers, roads = _messages(step, 1), _messages(step, 2)
  kind, modifier = maneuver(one(maneuvers[0], 1, 0) if maneuvers else 0)
  lanes = parse_lanes(step)
  if kind in SIDED_TYPES and not modifier:
    modifier = lane_side(lanes)
  return {'maneuverType': kind, 'maneuverModifier': modifier, 'roadName': _text(roads[0], 1) if roads else '', 'lanes': lanes}


def parse_position(data: bytes) -> dict:
  """NavigationCurrentPosition: step_distance.{distance.meters, time}, destination_distances[0]."""
  fields = parse_fields(data)
  out = {}
  for step in _messages(fields, 1)[:1]:
    for distance in _messages(step, 1)[:1]:
      if one(distance, 1) is not None:
        out['distanceMeters'] = max(0, signed(one(distance, 1)))
  for destination in _messages(fields, 2)[:1]:
    for distance in _messages(destination, 1)[:1]:
      if one(distance, 1) is not None:
        out['remainingMeters'] = max(0, signed(one(distance, 1)))
    if one(destination, 3) is not None:
      out['remainingSeconds'] = max(0, signed(one(destination, 3)))
  return out


def instruction_text(step: dict) -> str:
  road = step.get('roadName', '')
  if road:
    return road
  phrase = PHRASES.get(step.get('maneuverType', ''), 'Continue')
  word = MODIFIER_WORDS.get(step.get('maneuverModifier', ''))
  return f'{phrase} {word}' if word and step.get('maneuverType') in ('turn', 'fork', 'merge') else phrase


class HeadUnitGuidance:
  """One phone connection's guidance, published into the phone-guidance handoff and kept alive while it lasts."""
  def __init__(self, publisher=None, source: str = ANDROID_AUTO_SOURCE, log: Callable[[str], None] = print,
               mono_clock=time.monotonic_ns, timer=threading.Timer):
    if publisher is None:
      from openpilot.starpilot.navigation.phone import PhoneNavigationPublisher
      publisher = PhoneNavigationPublisher()
    self.publisher, self.source, self.log, self.clock, self.timer = publisher, source, log, mono_clock, timer
    self.lock = threading.Lock()
    self.session: str | None = None
    self.sessions = 0
    self.sequence = 0
    self.status = 0
    self.modern = False  # the phone sends the newer state message; the old turn message is then ignored
    self.step: dict = {}
    self.position: dict = {}
    self.published: tuple[dict, int] | None = None  # (fields without ageMs, observed ns)
    self.ticking = False  # one keep-alive timer chain at a time
    self.closed = False

  # ---- from the phone -------------------------------------------------------------------------------------
  def handle(self, kind: int, data: bytes) -> None:
    try:
      fields = parse_fields(data)
      with self.lock:
        if self.closed:
          return
        if kind == NAV_STATUS:
          self.status = signed(one(fields, 1, 0) or 0)
          if self.status == STATUS_INACTIVE:
            self.step, self.position = {}, {}
            self._end()
          return
        if kind == NAV_STATE:
          state = parse_state(data)
          if state is None:
            return
          self.modern = True
          if (state['maneuverType'], state['maneuverModifier'], state['roadName']) != tuple(
              self.step.get(key) for key in ('maneuverType', 'maneuverModifier', 'roadName')):
            self.position = {}  # a new step: the old distance belongs to the last one
          self.step = state
        elif kind == NAV_TURN and not self.modern:
          road = _text(fields, 1)
          if road != self.step.get('roadName'):
            self.step, self.position = {'maneuverType': '', 'maneuverModifier': '', 'roadName': road, 'lanes': []}, {}
        elif kind == NAV_POSITION:
          self.position = {**self.position, **parse_position(data)}
        elif kind == NAV_DISTANCE and not self.modern:
          if one(fields, 1) is not None:
            self.position = {**self.position, 'distanceMeters': max(0, signed(one(fields, 1)))}
        else:
          return
        if self.status != STATUS_REROUTING and self.step:
          self._publish()
    except (ValueError, TypeError) as error:
      self.log(f"phone guidance: unreadable message {kind:#x}: {error}")

  # ---- to the handoff ----------------------------------------------------------------------------------------
  def payload(self) -> dict:
    fields = {'source': self.source, 'instruction': instruction_text(self.step),
              'maneuverType': self.step.get('maneuverType', ''), 'maneuverModifier': self.step.get('maneuverModifier', '')}
    if self.step.get('roadName'):
      fields['roadName'] = self.step['roadName']
    for key, limit in (('distanceMeters', 1_000_000), ('remainingMeters', 20_000_000), ('remainingSeconds', 604_800)):
      if key in self.position:
        fields[key] = min(self.position[key], limit)
    if fields['maneuverType'] == 'arrive':
      fields.pop('distanceMeters', None)  # the step distance is the destination's; totals carry it
    return fields

  def _foreign(self, now: int) -> bool:
    from openpilot.starpilot.navigation.phone import OBSERVATION_NS, _read
    old = _read(self.publisher.path)
    # Our own source is never foreign: after the phone reconnects, the last connection's session is replaced at once.
    return bool(old is not None and old['session'] != self.session and old.get('source') != self.source and
                0 <= now - old['received'] <= OBSERVATION_NS)

  def _publish(self) -> None:
    fields = self.payload()
    if self.published is not None and self.published[0] == {**fields, 'session': self.session, 'sequence': self.sequence,
                                                             'active': True}:
      return
    now = self.clock()
    # Another guidance producer that is live keeps the handoff; ours resumes once it goes quiet.
    if self._foreign(now):
      self.published = None
      return
    if self.session is None or self._retired():
      if self.sessions >= MAX_SESSIONS:
        return
      self.session, self.sequence, self.sessions = uuid.uuid4().hex, 0, self.sessions + 1
    self.sequence += 1
    fields = {**fields, 'session': self.session, 'sequence': self.sequence, 'active': True}
    if self._send(fields, 0):
      if self.published is None:
        self.log(f"phone guidance: {self.source} publishing")
      self.published = (fields, now)
      if not self.ticking:
        self._schedule()
    else:
      self.published = self.session = None

  def _retired(self) -> bool:
    from openpilot.starpilot.navigation.phone import _read
    old = _read(self.publisher.path)
    return old is not None and old['session'] != self.session

  def _send(self, fields: dict, age_ms: float) -> bool:
    try:
      return bool(self.publisher.publish({**fields, 'ageMs': age_ms})['accepted'])
    except (ValueError, OSError) as error:
      self.log(f"phone guidance: not published: {error}")
      return False

  def _schedule(self) -> None:
    self.ticking = True
    timer = self.timer(KEEP_ALIVE_S, self._keep_alive)
    timer.daemon = True
    timer.start()

  def _keep_alive(self) -> None:
    # Renews the receipt lease only; the observation keeps its time and still expires if the phone goes quiet.
    from openpilot.starpilot.navigation.phone import observation_limit
    with self.lock:
      self.ticking = False
      if self.closed or self.published is None:
        return
      fields, observed = self.published
      age_ms = (self.clock() - observed) / 1e6
      if age_ms * 1e6 > observation_limit(fields) or self._foreign(self.clock()) or self._retired():
        self.published = None
        return
      if not self._send(fields, age_ms):
        self.published = self.session = None
        return
      self._schedule()

  def _end(self) -> None:
    if self.published is None or self.session is None:
      self.published = None
      return
    if not self._retired():
      self.sequence += 1
      self._send({'session': self.session, 'sequence': self.sequence, 'active': False, 'source': self.source,
                  'instruction': ''}, 0)
    self.published = None

  def close(self) -> None:
    with self.lock:
      if not self.closed:
        self._end()
        self.closed = True


CARPLAY_SOURCE = 'carPlay'
ROUTE_GUIDANCE_UPDATE, ROUTE_GUIDANCE_MANEUVER_UPDATE = 0x5201, 0x5202
ROUTE_NONE, ROUTE_ARRIVED, ROUTE_REROUTING = 0, 2, 5


def _carplay_table() -> dict[int, tuple[str, str]]:
  """Apple's iAP2 RouteGuidanceManeuverType -> (maneuverType, maneuverModifier)."""
  table = {1: ('turn', 'left'), 2: ('turn', 'right'), 3: ('continue', 'straight'), 4: ('turn', 'uturn'), 5: ('continue', ''),
           6: ('roundabout', ''), 7: ('roundabout', ''), 8: ('off ramp', ''), 9: ('on ramp', ''), 11: ('continue', ''),
           13: ('fork', 'left'), 14: ('fork', 'right'), 18: ('turn', 'uturn'), 19: ('turn', 'uturn'), 20: ('turn', 'left'),
           21: ('turn', 'right'), 22: ('off ramp', 'left'), 23: ('off ramp', 'right'), 26: ('turn', 'uturn'),
           47: ('turn', 'sharpLeft'), 48: ('turn', 'sharpRight'), 49: ('turn', 'slightLeft'), 50: ('turn', 'slightRight'),
           51: ('fork', ''), 52: ('fork', 'left'), 53: ('fork', 'right')}
  table.update(dict.fromkeys((10, 12, 24, 25, 27), ('arrive', '')))
  table.update(dict.fromkeys(range(28, 47), ('roundabout', '')))
  return table


CARPLAY_MANEUVERS = _carplay_table()


def _iap_params(payload: bytes) -> dict[int, bytes]:
  from openpilot.starpilot.system.carplay.iap2.wire import parse_params
  out: dict[int, bytes] = {}
  for pid, value in parse_params(payload):
    out.setdefault(pid, value)
  return out


def _iap_text(value: bytes | None) -> str:
  return value.split(b'\0', 1)[0].decode(errors='replace').strip()[:500] if value else ''


def _iap_uint(value: bytes | None) -> int | None:
  return int.from_bytes(value, 'big') if value else None


class CarPlayGuidance(HeadUnitGuidance):
  """CarPlay route guidance (iAP2 0x5201 route and 0x5202 maneuver updates) as phone guidance."""
  def __init__(self, publisher=None, log: Callable[[str], None] = print, **kwargs):
    super().__init__(publisher, source=CARPLAY_SOURCE, log=log, **kwargs)
    self.maneuvers: dict[int, dict] = {}
    self.current: int | None = None

  def handle_frame(self, message_id: int, payload: bytes) -> None:
    try:
      params = _iap_params(payload)
      with self.lock:
        if self.closed:
          return
        if message_id == ROUTE_GUIDANCE_MANEUVER_UPDATE:
          index, kind = _iap_uint(params.get(1)), _iap_uint(params.get(3))
          if index is None or kind is None:
            return
          maneuver_type, modifier = CARPLAY_MANEUVERS.get(kind, ('', ''))
          self.maneuvers[index] = {'maneuverType': maneuver_type, 'maneuverModifier': modifier,
                                   'roadName': _iap_text(params.get(4)), 'lanes': []}
        elif message_id == ROUTE_GUIDANCE_UPDATE:
          state = _iap_uint(params.get(1))
          if state in (ROUTE_NONE, ROUTE_ARRIVED):
            self.maneuvers, self.current, self.step, self.position = {}, None, {}, {}
            self._end()
            return
          if state is not None:
            self.status = STATUS_REROUTING if state == ROUTE_REROUTING else STATUS_ACTIVE
          current = params.get(13)
          if current is not None and len(current) >= 2:
            index = int.from_bytes(current[:2], 'big')
            if index != self.current:
              self.current, self.position = index, {}
          for pid, key, size in ((10, 'distanceMeters', 4), (7, 'remainingMeters', 4), (6, 'remainingSeconds', 8)):
            value = params.get(pid)
            if value is not None and len(value) >= size:
              self.position[key] = int.from_bytes(value[:size], 'big')
        else:
          return
        step = self.maneuvers.get(self.current) if self.current is not None else None
        if step is None:
          return
        self.step = step
        if self.status != STATUS_REROUTING:
          self._publish()
    except (ValueError, TypeError, OSError) as error:
      self.log(f"phone guidance: unreadable CarPlay message {message_id:#x}: {error}")
