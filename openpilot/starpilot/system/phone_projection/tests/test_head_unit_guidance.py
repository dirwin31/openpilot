"""Android Auto turn-by-turn sent to the comma head unit becomes phone guidance for navigationd and the intents."""
import time

import pytest

from openpilot.starpilot.navigation.phone import PhoneNavigationPublisher, read_phone_navigation, select_navigation
from openpilot.starpilot.system.phone_projection import guidance as g
from openpilot.starpilot.system.phone_projection.android.session import Channels, HeadUnitConfig, service_discovery_response
from openpilot.starpilot.system.starport import nav_status as ns
from openpilot.starpilot.system.starport.wire import field, parse_fields


class Timer:
  started: list = []

  def __init__(self, delay, fn, args=()):
    self.fn, self.daemon = fn, False

  def start(self):
    Timer.started.append(self)


class StarPortSession:
  """StarPort's phone side, captured: what a phone sends a head unit that offers the navigation service."""
  def __init__(self):
    self.channels = [{'id': 8, 'services': [ns.NAVIGATION_SERVICE], 'navigation': {}}]
    self.sent = []

  def send(self, channel, kind, body=b'', control=False):
    if channel == 8 and kind >= ns.NAV_STATUS:
      self.sent.append((kind, body))


class Feed:
  def __init__(self, value):
    self.value = value

  def snapshot(self):
    return self.value


def phone_messages(snapshot):
  session, now = StarPortSession(), [100.0]
  stream = ns.NavStatus.create(session, lambda *a, **k: None, feed=Feed(snapshot), clock=lambda: now[0])
  stream.service()
  stream.handle(8, ns.MSG_CHANNEL_OPEN_RESPONSE, {1: [0]})
  stream.handle(8, ns.CLUSTER_START, {})
  now[0] += 1
  stream.service()
  return session.sent


@pytest.fixture
def unit(tmp_path):
  Timer.started = []
  publisher = PhoneNavigationPublisher(path=tmp_path / 'phone.json')
  return g.HeadUnitGuidance(publisher=publisher, log=lambda m: None, timer=Timer), publisher.path


def state(maneuver_type, road='', lanes=()):
  lane_fields = b''.join(field(3, b''.join(field(1, field(1, shape) + field(2, int(lit))) for shape, lit in lane)) for lane in lanes)
  return field(1, field(1, field(1, maneuver_type)) + (field(2, field(1, road)) if road else b'') + lane_fields)


def position(meters, remaining=None, seconds=None):
  body = field(1, field(1, field(1, meters) + field(2, str(meters)) + field(3, 1)) + field(2, 10))
  if remaining is not None:
    body += field(2, field(1, field(1, remaining)) + field(3, seconds))
  return body


def test_head_unit_offers_navigation_and_asks_for_it():
  services = [parse_fields(s) for s in parse_fields(service_discovery_response(HeadUnitConfig(3, 1920, 1080, 0, 0)))[1]]
  nav = next(s for s in services if 8 in s)
  assert nav[1] == [Channels().navigation]
  # StarPort reads the descriptor the way a phone would.
  assert ns.parse_navigation(nav[8][0])['min_interval_ms'] == g.MIN_INTERVAL_MS


@pytest.mark.parametrize('value,expected', [
  (7, ('turn', 'left')), (10, ('turn', 'sharpRight')), (5, ('turn', 'slightLeft')), (12, ('turn', 'uturn')),
  (4, ('fork', 'right')), (24, ('off ramp', 'right')), (15, ('on ramp', 'left')), (27, ('merge', 'left')),
  (36, ('continue', 'straight')), (2, ('continue', '')), (34, ('roundabout', '')), (41, ('arrive', '')),
  (0, ('', '')), (37, ('', '')),
])
def test_android_maneuvers_use_phone_guidance_words(value, expected):
  from openpilot.starpilot.navigation.phone import MODIFIERS, TYPES
  assert g.maneuver(value) == expected
  assert expected[0] in TYPES and expected[1] in MODIFIERS


def test_starport_guidance_round_trips_into_navigation(unit):
  guidance, path = unit
  snapshot = {'state': 'guiding', 'metric': True, 'road': 'Main Street', 'type': 'turn', 'modifier': 'left',
              'distance': 400.0, 'remaining': 8000.0, 'duration': 800.0}
  for kind, body in phone_messages(snapshot):
    guidance.handle(kind, body)
  now = time.monotonic_ns()
  phone = read_phone_navigation(now, path)
  assert phone['source'] == 'androidAuto' and phone['active']
  assert (phone['instruction'], phone['maneuverType'], phone['maneuverModifier'], phone['distanceMeters']) == \
         ('Main Street', 'turn', 'left', 400)
  selected = select_navigation({'status': 'noDestination'}, phone, now_ns=now, drive_id=now - 1)
  assert selected['source'] == 'phone:androidAuto' and selected['status'] == 'guiding'
  assert selected['instruction']['maneuverModifier'] == 'left'
  assert len(Timer.started) == 1  # the lease is kept alive while the phone guides


def test_lanes_pick_the_side_of_an_unsided_fork(unit):
  guidance, path = unit
  four_lanes = [[(1, False)], [(1, False)], [(3, True)], [(3, True)]]
  step = g.parse_state(state(29, 'I-15 S', four_lanes))
  assert step['maneuverType'] == 'merge' and step['maneuverModifier'] == 'right'
  assert [lane['highlighted'] for lane in step['lanes']] == [False, False, True, True]
  assert g.lane_side([{'highlighted': True}, {'highlighted': True}]) == ''  # every lane: no side


def test_distance_and_totals_update_with_new_sequences(unit):
  guidance, path = unit
  guidance.handle(g.NAV_STATUS, field(1, g.STATUS_ACTIVE))
  guidance.handle(g.NAV_STATE, state(8, 'Elm Street'))
  first = read_phone_navigation(time.monotonic_ns(), path)
  assert first['maneuverModifier'] == 'right' and 'distanceMeters' not in first
  guidance.handle(g.NAV_POSITION, position(250, remaining=4000, seconds=360))
  second = read_phone_navigation(time.monotonic_ns(), path)
  assert second['sequence'] > first['sequence']
  assert (second['distanceMeters'], second['remainingMeters'], second['remainingSeconds']) == (250, 4000, 360)
  guidance.handle(g.NAV_STATE, state(7, 'Oak Street'))
  third = read_phone_navigation(time.monotonic_ns(), path)
  assert third['instruction'] == 'Oak Street' and 'distanceMeters' not in third  # old step's distance dropped


def test_road_without_name_gets_a_short_phrase(unit):
  guidance, path = unit
  guidance.handle(g.NAV_STATE, state(9))
  assert read_phone_navigation(time.monotonic_ns(), path)['instruction'] == 'Turn sharp left'


def test_inactive_status_and_disconnect_end_guidance(unit):
  guidance, path = unit
  guidance.handle(g.NAV_STATE, state(7, 'Oak Street'))
  guidance.handle(g.NAV_STATUS, field(1, g.STATUS_INACTIVE))
  assert read_phone_navigation(time.monotonic_ns(), path) is None
  guidance.handle(g.NAV_STATE, state(8, 'Pine Street'))
  assert read_phone_navigation(time.monotonic_ns(), path)['instruction'] == 'Pine Street'
  guidance.close()
  assert read_phone_navigation(time.monotonic_ns(), path) is None
  guidance.handle(g.NAV_STATE, state(7, 'After Close'))
  assert read_phone_navigation(time.monotonic_ns(), path) is None


def test_rerouting_holds_back_updates(unit):
  guidance, path = unit
  guidance.handle(g.NAV_STATUS, field(1, g.STATUS_REROUTING))
  guidance.handle(g.NAV_STATE, state(7, 'Oak Street'))
  assert read_phone_navigation(time.monotonic_ns(), path) is None


def test_a_live_companion_app_keeps_the_handoff(unit):
  guidance, path = unit
  PhoneNavigationPublisher(path=path).publish({'session': 'companion', 'sequence': 1, 'active': True, 'ageMs': 0,
                                               'instruction': 'Maple Ave', 'maneuverType': 'turn', 'maneuverModifier': 'left'})
  guidance.handle(g.NAV_STATE, state(8, 'Elm Street'))
  assert read_phone_navigation(time.monotonic_ns(), path)['session'] == 'companion'


def test_keep_alive_renews_the_lease_without_a_new_observation(unit):
  guidance, path = unit
  guidance.handle(g.NAV_STATE, state(8, 'Elm Street'))
  before = read_phone_navigation(time.monotonic_ns(), path)
  Timer.started[-1].fn()
  after = read_phone_navigation(time.monotonic_ns(), path)
  assert after['sequence'] == before['sequence'] and after['observed'] == before['observed']
  assert after['received'] >= before['received'] and len(Timer.started) == 2
