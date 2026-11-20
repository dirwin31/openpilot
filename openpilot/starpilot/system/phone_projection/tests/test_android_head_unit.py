import socket
import threading
import time

from openpilot.starpilot.system.phone_projection.android.session import (HeadUnitConfig, HeadUnitSession, TOUCH_DOWN,
                                                                         service_discovery_response)
from openpilot.starpilot.system.starport.session import ProjectionSession
from openpilot.starpilot.system.starport.tests.fake_head_unit import make_identity
from openpilot.starpilot.system.starport.wire import parse_fields

CFG = HeadUnitConfig(resolution=3, width=1920, height=1080, margin_width=960, margin_height=0)


class Sink:
  def __init__(self):
    self.configs, self.frames, self.closed, self.started = [], [], threading.Event(), threading.Event()
    self.navigation = []

  def on_video_start(self, session):
    self.started.set()

  def on_video_config(self, data):
    self.configs.append(data)

  def on_video_frame(self, data):
    self.frames.append(data)

  def on_audio(self, *args):
    pass

  def on_audio_stopped(self, name):
    pass

  def on_session_closed(self, session):
    self.closed.set()

  def on_navigation(self, session, kind, data):
    self.navigation.append(kind)


def test_service_discovery_describes_video_and_touch():
  fields = parse_fields(service_discovery_response(CFG))
  services = [parse_fields(s) for s in fields[1]]
  video = next(parse_fields(s[3][0]) for s in services if 3 in s and parse_fields(s[3][0]).get(1) == [3])
  config = parse_fields(video[4][0])
  assert config[1] == [3] and config[3] == [960]
  touch = next(parse_fields(parse_fields(s[4][0])[2][0]) for s in services if 4 in s)
  assert touch[1] == [1920] and touch[2] == [1080]


def test_starport_phone_projects_into_the_head_unit(tmp_path):
  identity = make_identity(tmp_path)
  phone_sock, hu_sock = socket.socketpair()
  sink = Sink()
  hu = HeadUnitSession(hu_sock, CFG, sink, log=lambda m: None, certificate=identity["hu_cert"], private_key=identity["hu_key"])
  hu_thread = threading.Thread(target=lambda: (hu.handshake(), hu.run()), daemon=True)
  hu_thread.start()

  phone = ProjectionSession(phone_sock, str(identity["phone_cert"]), str(identity["phone_key"]), ca=str(identity["root"]))
  phone.authenticate()
  mode = phone.start("Pixel", "Google")
  assert (mode.width, mode.height) == (1920, 1080)
  deadline = time.monotonic() + 5
  while not phone.focused and time.monotonic() < deadline:
    phone.pump(0.1)
  assert phone.focused
  assert sink.started.wait(2)

  keyframe = b"\x00\x00\x00\x01\x67sps\x00\x00\x00\x01\x68pps\x00\x00\x00\x01\x65idr"
  phone.send_frame(keyframe, 1000, keyframe=True)
  deadline = time.monotonic() + 3
  while not sink.frames and time.monotonic() < deadline:
    phone.pump(0.1)
  assert sink.frames and sink.frames[-1].endswith(b"idr")

  hu.send_touch(TOUCH_DOWN, [(960, 540, 0)])
  deadline = time.monotonic() + 3
  while not phone.touch_events and time.monotonic() < deadline:
    phone.pump(0.1)
  assert phone.touch_events

  phone_sock.close()
  assert sink.closed.wait(3)


class GuidingFeed:
  def snapshot(self):
    return {'state': 'guiding', 'metric': False, 'road': 'Main Street', 'type': 'turn', 'modifier': 'left',
            'distance': 400.0, 'remaining': 8000.0, 'duration': 800.0}


def test_phone_turn_by_turn_reaches_the_head_unit(tmp_path):
  from openpilot.starpilot.system.starport.nav_status import NAV_CURRENT_POSITION, NAV_NOTIFICATION, NavStatus
  identity = make_identity(tmp_path)
  phone_sock, hu_sock = socket.socketpair()
  sink = Sink()
  hu = HeadUnitSession(hu_sock, CFG, sink, log=lambda m: None, certificate=identity["hu_cert"], private_key=identity["hu_key"])
  threading.Thread(target=lambda: (hu.handshake(), hu.run()), daemon=True).start()
  phone = ProjectionSession(phone_sock, str(identity["phone_cert"]), str(identity["phone_key"]), ca=str(identity["root"]))
  phone.authenticate()
  phone.start("Pixel", "Google")
  phone.nav = NavStatus.create(phone, lambda *a, **k: None, feed=GuidingFeed())
  assert phone.nav is not None  # the head unit offers the navigation service
  deadline = time.monotonic() + 5
  while not {NAV_NOTIFICATION, NAV_CURRENT_POSITION} <= set(sink.navigation) and time.monotonic() < deadline:
    phone.nav.service()
    phone.pump(0.1)
  assert phone.nav.heard and phone.nav.started  # our start request, not StarPort's silent-car fallback
  assert {NAV_NOTIFICATION, NAV_CURRENT_POSITION} <= set(sink.navigation)
  phone_sock.close()
  assert sink.closed.wait(3)
