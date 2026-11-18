import json

import pytest

from openpilot.starpilot.galaxy_bluetooth.bridge.ancs import FOLLOWED_APPS
from openpilot.starpilot.navigation.notification_text import KEEP_ALIVE_S, NAVIGATION_APPS, NotificationNavigation, parse
from openpilot.starpilot.navigation.phone import (HOLD_NS, LEASE_NS, OBSERVATION_NS, GuidanceHold, PhoneNavigationPublisher,
                                                   read_phone_navigation)

NOW = 100_000_000_000


@pytest.mark.parametrize('sender, text, expected', [
  ('', 'In 500 ft, turn right onto N Tee Pee Ln', ('turn', 'right', 152.4, 'N Tee Pee Ln')),
  ('Turn left onto W Wyoming Ave', '0.3 mi', ('turn', 'left', 482.8, 'W Wyoming Ave')),
  ('In ¼ mi', 'Take exit 42A toward US-95 N / Reno', ('off ramp', '', 402.3, 'US-95 N / Reno')),
  ('', 'In 1/4 mi, keep left to stay on I-15 N', ('fork', 'left', 402.3, 'I-15 N')),
  ('', 'Slight right onto the ramp to I-15 N', ('turn', 'slightRight', None, 'I-15 N')),
  ('', 'Make a U-turn', ('turn', 'uturn', None, '')),
  ('', 'Continue on Western Ave for 2 mi', ('continue', '', 3218.7, 'Western Ave')),
  ('1.5 mi', 'I-11', ('', '', 2414.0, '')),
  ('', 'You have arrived', ('arrive', '', None, '')),
])
def test_prompt_wording(sender, text, expected):
  found = parse(sender, text)
  assert (found['maneuverType'], found['maneuverModifier'], found.get('distanceMeters'), found.get('roadName', '')) == expected
  assert found['instruction']


# Real Google Maps wording from an iPhone drive.
@pytest.mark.parametrize('text, expected', [
  ('Turn left onto N Tee Pee Ln', ('turn', 'left', 'N Tee Pee Ln', 'N Tee Pee Ln')),
  ('Turn right onto the US-95 S ramp', ('on ramp', '', 'US-95 S', 'US-95 S')),
  ('Use the right 3 lanes to take exit 76B for I-15 S',
   ('off ramp', '', 'I-15 S', 'Use the right 3 lanes to take exit 76B for I-15 S')),
  ('Take the Sahara Ave exit', ('off ramp', '', 'Sahara Ave', 'Sahara Ave')),
  ('Use the left lane to take the Sahara Ave East ramp to Convention Ctr',
   ('on ramp', '', 'Sahara Ave East', 'Use the left lane to take the Sahara Ave East ramp to Convention Ctr')),
  ('Merge onto W Sahara Ave', ('merge', '', 'W Sahara Ave', 'W Sahara Ave')),
  ('Use any lane to turn left onto S Sammy Davis Jr Dr', ('turn', 'left', 'S Sammy Davis Jr Dr', 'S Sammy Davis Jr Dr')),
])
def test_iphone_google_maps_wording(text, expected):
  found = parse('', text)
  assert (found['maneuverType'], found['maneuverModifier'], found.get('roadName', ''), found['instruction']) == expected
  assert 'distanceMeters' not in found


def test_exit_number_and_other_text():
  assert parse('', 'Take exit 42A toward Reno')['exitNumber'] == '42A'
  assert parse('Mom', 'Dinner at 6?') is None
  assert parse('Faster route available', 'Saves 4 min') is None
  assert parse('', '') is None


def test_bridge_follows_the_same_apps():
  assert set(NAVIGATION_APPS) == set(FOLLOWED_APPS)


class Timers:
  def __init__(self):
    self.due = []

  def __call__(self, delay, call, args=()):
    assert delay == KEEP_ALIVE_S
    self.due.append((call, args))
    return self

  def start(self):
    pass

  def fire(self):
    due, self.due = self.due, []
    for call, args in due:
      call(*args)


@pytest.fixture
def feed(tmp_path):
  clock, timers, enabled = [NOW], Timers(), [True]
  publisher = PhoneNavigationPublisher(tmp_path / 'navigation.json', lambda: clock[0])
  feeder = NotificationNavigation(publisher, enabled=lambda: enabled[0], mono_clock=lambda: clock[0], timer=timers,
                                  samples=tmp_path / 'samples.jsonl')
  return feeder, publisher, clock, timers, enabled


def prompt(text='In 500 ft, turn right onto Main St', app='com.google.Maps', age=0, sender='Google Maps'):
  return {'source': 'ios', 'app': app, 'appName': 'Google Maps', 'sender': sender, 'text': text, 'category': 'other', 'ageMs': age}


def test_prompt_becomes_phone_guidance(feed):
  feeder, publisher, clock, _timers, _enabled = feed
  assert feeder.offer(prompt(age=2000)) is True
  live = read_phone_navigation(NOW, publisher.path)
  assert (live['source'], live['maneuverType'], live['maneuverModifier'], live['roadName']) == ('iphoneGoogleMaps', 'turn', 'right', 'Main St')
  assert live['observed'] == NOW - 2_000_000_000
  first = live['session']
  clock[0] += 10_000_000_000
  assert feeder.offer(prompt('Turn left onto Oak Ave'))
  live = read_phone_navigation(clock[0], publisher.path)
  assert (live['session'], live['sequence'], live['roadName']) == (first, 2, 'Oak Ave')


def test_google_maps_fixed_title_is_not_instruction_text(feed):
  feeder, publisher, _clock, _timers, _enabled = feed
  assert feeder.offer(prompt('Use the right 3 lanes to take exit 76B for I-15 S', sender='Navigation Guidance'))
  live = read_phone_navigation(NOW, publisher.path)
  assert (live['maneuverType'], live['instruction']) == ('off ramp', 'Use the right 3 lanes to take exit 76B for I-15 S')


def test_other_apps_plain_text_and_the_switch_fall_through(feed):
  feeder, publisher, _clock, _timers, enabled = feed
  assert feeder.offer(prompt(app='com.apple.MobileSMS')) is False
  assert feeder.offer(prompt('Faster route available')) is True
  enabled[0] = False
  assert feeder.offer(prompt()) is False
  assert not publisher.path.exists()


def test_keep_alive_renews_the_lease_not_the_observation(feed):
  feeder, publisher, clock, timers, _enabled = feed
  feeder.offer(prompt())
  for _ in range(5):
    clock[0] += int(KEEP_ALIVE_S * 1e9)
    timers.fire()
  assert clock[0] - NOW > LEASE_NS
  live = read_phone_navigation(clock[0], publisher.path)
  assert live['observed'] == NOW and live['sequence'] == 1
  clock[0] = NOW + OBSERVATION_NS + 1
  timers.fire()
  assert timers.due == [] and read_phone_navigation(clock[0], publisher.path) is None


def test_companion_app_guidance_keeps_priority(feed):
  feeder, publisher, clock, timers, _enabled = feed
  feeder.offer(prompt())
  ours = read_phone_navigation(NOW, publisher.path)['session']
  companion = {'session': 'android', 'sequence': 1, 'active': True, 'ageMs': 0, 'instruction': 'Oak Ave', 'distanceMeters': 100}
  assert publisher.publish(companion)['accepted']
  clock[0] += 1_000_000_000
  assert feeder.offer(prompt('Turn left onto Elm St')) is True
  timers.fire()
  live = read_phone_navigation(clock[0], publisher.path)
  assert live['session'] == 'android' and timers.due == []
  # Once the companion goes quiet, prompts start a fresh session instead of reviving the retired one.
  clock[0] += LEASE_NS + 1
  assert feeder.offer(prompt('Turn left onto Elm St')) is True
  assert json.loads(publisher.path.read_text())['session'] == 'android'
  clock[0] += OBSERVATION_NS
  assert feeder.offer(prompt('Turn left onto Elm St')) is True
  live = read_phone_navigation(clock[0], publisher.path)
  assert live['session'] not in ('android', ours) and live['roadName'] == 'Elm St'
  assert json.loads(publisher.path.read_text())['retiredSessions'] == [ours, 'android']


def test_companion_app_between_prompts_is_not_retired(feed):
  feeder, publisher, clock, _timers, _enabled = feed
  companion = {'session': 'android', 'sequence': 1, 'active': True, 'ageMs': 0, 'instruction': 'Oak Ave', 'distanceMeters': 100}
  publisher.publish(companion)
  publisher.publish({'session': 'android', 'sequence': 2, 'active': False, 'ageMs': 0})
  clock[0] += 1_000_000_000
  assert feeder.offer(prompt()) is True
  assert publisher.publish({**companion, 'sequence': 3})['accepted']
  assert read_phone_navigation(clock[0], publisher.path)['session'] == 'android'


def test_arrival_ends_guidance(feed):
  feeder, publisher, clock, timers, _enabled = feed
  feeder.offer(prompt())
  assert feeder.offer(prompt('You have arrived')) is True
  assert read_phone_navigation(NOW, publisher.path) is None
  timers.fire()
  assert timers.due == []


def test_samples_are_kept_for_tuning(feed, tmp_path):
  feeder = feed[0]
  feeder.offer(prompt())
  feeder.offer(prompt('Faster route available'))
  rows = [json.loads(line) for line in (tmp_path / 'samples.jsonl').read_text().splitlines()]
  assert [row['text'] for row in rows] == ['In 500 ft, turn right onto Main St', 'Faster route available']
  assert rows[0]['parsed']['roadName'] == 'Main St' and rows[1]['parsed'] is None


def test_hold_bridges_a_short_gap_only():
  hold, phone = GuidanceHold(), {'observed': NOW}
  assert hold.update(phone, NOW) is phone
  assert hold.update(None, NOW + HOLD_NS) is phone
  assert hold.update(None, NOW + HOLD_NS + 1) is None
  assert hold.update(None, NOW) is None


def test_iphone_steps_hold_until_passed_and_android_is_untouched(feed):
  feeder, publisher, _clock, _timers, _enabled = feed
  feeder.offer(prompt('Turn left onto Oak Ave', sender='Navigation Guidance'))
  assert read_phone_navigation(NOW, publisher.path)['untilPassed'] is True
  feeder.offer(prompt())
  assert read_phone_navigation(NOW, publisher.path)['untilPassed'] is False
  assert feeder.offer({**prompt(), 'source': 'android'}) is False


def test_next_prompt_after_a_step_expires_is_not_dropped(feed):
  feeder, publisher, clock, timers, _enabled = feed
  feeder.offer(prompt('In 500 ft, turn right onto Main St'))
  first = read_phone_navigation(NOW, publisher.path)['session']
  while timers.due:
    clock[0] += int(KEEP_ALIVE_S * 1e9)
    timers.fire()
  # The handoff still holds our own session with a recent receipt: it must not count as another app guiding.
  clock[0] += 30_000_000_000
  assert feeder.offer(prompt('Turn right onto the US-95 S ramp'))
  live = read_phone_navigation(clock[0], publisher.path)
  assert (live['session'], live['roadName']) == (first, 'US-95 S')
