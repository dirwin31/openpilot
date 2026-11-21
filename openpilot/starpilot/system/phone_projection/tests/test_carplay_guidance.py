"""CarPlay route guidance carried in the Wi-Fi session's iAP2 tunnel becomes phone guidance for navigationd."""
import socket
import threading
import time

import pytest

from openpilot.starpilot.navigation.phone import PhoneNavigationPublisher, read_phone_navigation, select_navigation
from openpilot.starpilot.system.carplay.iap2.link import TUNNEL_CONFIG
from openpilot.starpilot.system.carplay.iap2.session import Iap2Session
from openpilot.starpilot.system.carplay.iap2.tunnel import TunnelLink
from openpilot.starpilot.system.carplay.iap2.wire import Body, Frame
from openpilot.starpilot.system.phone_projection import guidance as g


class Timer:
  def __init__(self, delay, fn, args=()):
    self.daemon = False

  def start(self):
    pass


@pytest.fixture
def unit(tmp_path):
  publisher = PhoneNavigationPublisher(path=tmp_path / 'phone.json')
  return g.CarPlayGuidance(publisher=publisher, log=lambda m: None, timer=Timer), publisher.path


def maneuver(index, kind, road='', side=0):
  body = Body().u16(1, index).u8(3, kind).u8(8, side)
  if road:
    body.string(4, road)
  return g.ROUTE_GUIDANCE_MANEUVER_UPDATE, Frame.build(g.ROUTE_GUIDANCE_MANEUVER_UPDATE, body).payload


def route(state=1, current=None, distance=None, remaining=None, seconds=None):
  body = Body().u8(1, state)
  if current is not None:
    body.u16_list(13, current)
  if distance is not None:
    body.u32(10, distance)
  if remaining is not None:
    body.u32(7, remaining)
  if seconds is not None:
    body.add(6, seconds.to_bytes(8, 'big'))
  return g.ROUTE_GUIDANCE_UPDATE, Frame.build(g.ROUTE_GUIDANCE_UPDATE, body).payload


def test_maneuver_and_route_updates_publish_the_next_turn(unit):
  guidance, path = unit
  guidance.handle_frame(*maneuver(0, 2, 'Main St'))
  assert read_phone_navigation(time.monotonic_ns(), path) is None  # no current maneuver yet
  guidance.handle_frame(*route(current=[0, 1], distance=180, remaining=5200, seconds=600))
  phone = read_phone_navigation(time.monotonic_ns(), path)
  assert phone['source'] == 'carPlay' and phone['maneuverType'] == 'turn' and phone['maneuverModifier'] == 'right'
  assert phone['roadName'] == 'Main St' and phone['distanceMeters'] == 180
  assert phone['remainingMeters'] == 5200 and phone['remainingSeconds'] == 600


def test_next_maneuver_drops_the_old_distance_and_exits_keep_their_side(unit):
  guidance, path = unit
  guidance.handle_frame(*maneuver(0, 1, 'Elm St'))
  guidance.handle_frame(*maneuver(1, 23, 'I-15 N'))
  guidance.handle_frame(*route(current=[0], distance=40))
  guidance.handle_frame(*route(current=[1]))
  phone = read_phone_navigation(time.monotonic_ns(), path)
  assert (phone['maneuverType'], phone['maneuverModifier'], phone['roadName']) == ('off ramp', 'right', 'I-15 N')
  assert 'distanceMeters' not in phone


@pytest.mark.parametrize("state", [0, 2])
def test_no_route_or_arrival_ends_guidance(unit, state):
  guidance, path = unit
  guidance.handle_frame(*maneuver(0, 1))
  guidance.handle_frame(*route(current=[0], distance=90))
  assert read_phone_navigation(time.monotonic_ns(), path)['active']
  guidance.handle_frame(*route(state=state))
  assert read_phone_navigation(time.monotonic_ns(), path) is None and guidance.step == {}


def test_rerouting_holds_the_last_step(unit):
  guidance, path = unit
  guidance.handle_frame(*maneuver(0, 1))
  guidance.handle_frame(*route(current=[0], distance=90))
  before = read_phone_navigation(time.monotonic_ns(), path)
  guidance.handle_frame(*route(state=5, current=[0], distance=60))
  assert read_phone_navigation(time.monotonic_ns(), path)['distanceMeters'] == before['distanceMeters']


def test_unreadable_frames_are_ignored(unit):
  guidance, path = unit
  guidance.handle_frame(g.ROUTE_GUIDANCE_UPDATE, b'\x00\x09\x00')
  assert read_phone_navigation(time.monotonic_ns(), path) is None


def test_tunnel_link_carries_iap2_both_ways():
  phone_sock, bridge = socket.socketpair()
  phone = Iap2Session(phone_sock, TUNNEL_CONFIG, initiator=False, log=lambda m: None).start()

  def send(data):
    bridge.sendall(data)
    return True
  tunnel = TunnelLink(send, log=lambda m: None)

  def pump():
    try:
      while (data := bridge.recv(65536)):
        tunnel.feed(data)
    except OSError:
      pass
  threading.Thread(target=pump, daemon=True).start()
  try:
    assert phone.ready.wait(5) and tunnel.session.ready.wait(5)
    phone.send(Frame.build(0x5201, Body().u8(1, 1)))
    received = tunnel.session.recv(5)
    assert received is not None and received.message_id == 0x5201
    tunnel.session.send(Frame.build(0x5200, Body().u16(0, 42)))
    deadline = time.monotonic() + 5
    while (frame := phone.recv(0.2)) is None and time.monotonic() < deadline:
      pass
    assert frame is not None and frame.message_id == 0x5200
  finally:
    tunnel.close()
    phone.close()
    bridge.close()


def test_runner_carries_tunnel_guidance_to_navigation_and_ends_it_with_the_session(tmp_path):
  from openpilot.starpilot.system.phone_projection.carplay_runner import CarPlayRunner
  from openpilot.starpilot.system.phone_projection.runtime import TouchTracker

  class Audio:
    def request(self, wanted):
      pass

  publisher = PhoneNavigationPublisher(path=tmp_path / 'phone.json')
  runner = CarPlayRunner.__new__(CarPlayRunner)
  runner.lock, runner.touches, runner.state, runner.pipe = threading.Lock(), TouchTracker(), "active", None
  runner.message = ""
  runner.ctx = type("Ctx", (), {"audio": Audio(), "log": staticmethod(lambda m: None)})()
  runner.tunnel = runner.guidance = None
  runner.guidance_factory = lambda: g.CarPlayGuidance(publisher=publisher, log=lambda m: None, timer=Timer)
  runner.control = (None, None, None)  # identification never starts: the phone below sends only route updates

  phone_sock, bridge = socket.socketpair()
  phone = Iap2Session(phone_sock, TUNNEL_CONFIG, initiator=False, log=lambda m: None).start()

  class AirPlay:
    def send_iap(self, data):
      bridge.sendall(data)
      return True
  session = AirPlay()
  runner.session = session

  def pump():
    try:
      while (data := bridge.recv(65536)):
        runner.on_iap_tunnel_data(data)
    except OSError:
      pass
  runner.on_iap_tunnel_open(session)
  threading.Thread(target=pump, daemon=True).start()
  try:
    assert phone.ready.wait(5)
    phone.send(Frame.build(g.ROUTE_GUIDANCE_MANEUVER_UPDATE, Body().u16(1, 0).u8(3, 1).string(4, 'Elm St')))
    phone.send(Frame.build(g.ROUTE_GUIDANCE_UPDATE, Body().u8(1, 1).u16_list(13, [0]).u32(10, 120)))
    deadline = time.monotonic() + 5
    while (value := read_phone_navigation(time.monotonic_ns(), publisher.path)) is None and time.monotonic() < deadline:
      time.sleep(0.02)
    assert value is not None and value['roadName'] == 'Elm St'
    now = time.monotonic_ns()
    selected = select_navigation({'status': 'noDestination'}, value, now_ns=now, drive_id=now - 1)
    assert selected['source'] == 'phone:carPlay' and selected['status'] == 'guiding'
    runner.on_session_closed(session)
    assert runner.tunnel is None and read_phone_navigation(time.monotonic_ns(), publisher.path) is None
  finally:
    phone.close()
    bridge.close()
