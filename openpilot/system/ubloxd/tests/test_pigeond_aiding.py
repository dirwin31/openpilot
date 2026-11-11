from datetime import datetime, UTC
from types import SimpleNamespace

import pytest

from openpilot.cereal import messaging
from openpilot.system.ubloxd import pigeond

DAY = 24 * 3600
TIME_UTC, POS_LLH = 0x10, 0x01


class FakePigeon:
  def __init__(self, clock, data=None):
    self.clock, self.data, self.sent = clock, data or {}, []

  def send(self, dat):
    pass

  def send_with_ack(self, dat, ack=pigeond.UBLOX_ACK, nack=pigeond.UBLOX_NACK):
    self.sent.append(dat)

  def receive(self):
    self.clock[0] += 0.1
    return self.data.pop(round(self.clock[0], 1), b'')

  def aiding(self, kind):
    return sum(1 for msg in self.sent if msg[2:4] == b"\x13\x40" and msg[6] == kind)


def last_position(age_s):
  msg = messaging.new_message('gpsLocationExternal')
  msg.gpsLocationExternal.latitude = 40.
  msg.gpsLocationExternal.longitude = -110.
  msg.gpsLocationExternal.unixTimestampMillis = int((datetime.now(UTC).timestamp() - age_s) * 1000)
  return msg.to_bytes()


@pytest.fixture
def env(monkeypatch):
  state = SimpleNamespace(clock=[0.], valid_after=0., checks=0, position=last_position(60))

  def system_time_valid():
    state.checks += 1
    return state.clock[0] >= state.valid_after

  monkeypatch.setattr(pigeond, 'system_time_valid', system_time_valid)
  monkeypatch.setattr(pigeond, 'Params', lambda: SimpleNamespace(get=lambda key: state.position if key == "LastGPSPosition" else None))
  monkeypatch.setattr(pigeond, 'time', SimpleNamespace(monotonic=lambda: state.clock[0], sleep=lambda _s: None))
  monkeypatch.setattr(pigeond, 'signal', SimpleNamespace(signal=lambda *_a: None, SIGINT=2))
  monkeypatch.setattr(pigeond, 'threading', SimpleNamespace(Thread=lambda **_kw: SimpleNamespace(start=lambda: None)))
  monkeypatch.setattr(pigeond, 'messaging', SimpleNamespace(PubMaster=lambda _services: None))
  for name in ('set_power', 'init_baudrate', 'init_pigeon'):
    monkeypatch.setattr(pigeond, name, lambda *_a: None)
  return state


def run(env, monkeypatch, duration, data=None):
  pigeon = FakePigeon(env.clock, data)
  monkeypatch.setattr(pigeond, 'TTYPigeon', lambda: pigeon)
  pigeond.run_receiving(duration)
  return pigeon


def test_position_sent_at_init_without_valid_clock(env):
  env.valid_after, env.position = 1e9, last_position(30 * DAY)
  pigeon = FakePigeon(env.clock)
  assert not pigeond.init(pigeon)
  assert pigeon.aiding(POS_LLH) == 1
  assert pigeon.aiding(TIME_UTC) == 0


@pytest.mark.parametrize('age_days, sent', [(1, 1), (8, 0)])
def test_stale_position_guard_applies_with_valid_clock(env, age_days, sent):
  env.position = last_position(age_days * DAY)
  pigeon = FakePigeon(env.clock)
  assert pigeond.init(pigeon)
  assert pigeon.aiding(POS_LLH) == sent
  assert pigeon.aiding(TIME_UTC) == 1


def test_time_sent_once_when_clock_becomes_valid(env, monkeypatch):
  env.valid_after = 5.
  pigeon = run(env, monkeypatch, 20)
  assert pigeon.aiding(TIME_UTC) == 1
  assert pigeon.aiding(POS_LLH) == 1
  assert env.checks < 15, 'clock is checked about once a second, not every loop'


def test_time_sent_at_init_is_not_repeated(env, monkeypatch):
  pigeon = run(env, monkeypatch, 20)
  assert pigeon.aiding(TIME_UTC) == 1
  assert env.checks == 3


def test_reinit_repeats_aiding_and_pending_time(env, monkeypatch):
  env.valid_after = 8.
  pigeon = run(env, monkeypatch, 20, {3.0: b"\x00"})
  assert pigeon.aiding(POS_LLH) == 2
  assert pigeon.aiding(TIME_UTC) == 1
