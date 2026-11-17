import os
import struct
import time

import pytest
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from openpilot.starpilot.system.carplay.airplay.session import open_audio_packet
from openpilot.starpilot.system.phone_projection import settings
from openpilot.starpilot.system.phone_projection.car_split import car_split
from openpilot.starpilot.system.phone_projection.decoder import FrameLayout, access_units
from openpilot.starpilot.system.phone_projection.ipc import StatusReader, Touch, TouchReceiver, TouchSender, write_status
from openpilot.starpilot.system.phone_projection.layout import Rect, android_crop, android_margins, fit, split
from openpilot.starpilot.system.phone_projection.runtime import TouchTracker
from openpilot.starpilot.system.phone_projection.video import VideoPipe, VideoPublisher


def test_split_puts_the_phone_on_the_chosen_side():
  comma, phone = split(Rect(0, 0, 2160, 1080), "right")
  assert (comma.x, comma.width, phone.x, phone.width) == (0, 1080, 1080, 1080)
  comma, phone = split(Rect(100, 0, 1000, 500), "left")
  assert (phone.x, comma.x) == (100, 600)


def test_fit_letterboxes():
  r = fit(1080, 1080, Rect(0, 0, 960, 1080))
  assert (r.x, r.width, r.height) == (0, 960, 960) and r.y == 60


def test_android_frame_leaves_a_panel_shaped_middle():
  assert android_margins((960, 1080)) == (960, 0)
  assert android_crop((960, 1080)) == (480, 0, 960, 1080)
  assert android_crop((1080, 1080)) == (420, 0, 1080, 1080)


def test_settings_round_trip_and_flag(tmp_path):
  assert settings.load(tmp_path) == settings.Settings()
  settings.update(tmp_path, enabled=True, side="left", audio="music")
  loaded = settings.load(tmp_path)
  assert (loaded.enabled, loaded.side, loaded.audio) == (True, "left", "music") and settings.enabled(tmp_path)
  (tmp_path / "settings.json").write_text('{"side": "top", "audio": 3}')
  assert (settings.load(tmp_path).side, settings.load(tmp_path).audio) == ("right", "phone")


def test_status_expires(tmp_path):
  path = tmp_path / "status.json"
  write_status({"state": "active"}, path)
  reader = StatusReader(path, max_age=5)
  assert reader.read()["state"] == "active"
  assert reader.read(now=time.time() + 10) == {}


def test_touches_cross_the_socket():
  import tempfile
  path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "t.sock")
  receiver, sender = TouchReceiver(path), TouchSender(path)
  try:
    sender.send(Touch("down", 1, 0.25, 0.75))
    sender.send(Touch("up", 1, 0.25, 0.75))
    deadline = time.monotonic() + 1
    got = []
    while len(got) < 2 and time.monotonic() < deadline:
      got += receiver.drain()
    assert got == [Touch("down", 1, 0.25, 0.75), Touch("up", 1, 0.25, 0.75)]
  finally:
    sender.close()
    receiver.close()


def test_touch_tracker_limits_slots():
  tracker = TouchTracker(slots=2)
  assert tracker.apply(Touch("down", 7, 0.1, 0.1)) == (0, True)
  assert tracker.apply(Touch("down", 8, 0.2, 0.2)) == (1, True)
  assert tracker.apply(Touch("down", 9, 0.3, 0.3)) is None
  assert tracker.apply(Touch("move", 9, 0.3, 0.3)) is None
  assert tracker.apply(Touch("up", 7, 0.5, 0.5)) == (0, False)
  assert tracker.active() == {1: (0.2, 0.2)}


class FakePhone:
  def __init__(self, claim):
    self.claim, self.seen = claim, []

  def touch(self, kind, pointer, x, y):
    self.seen.append((kind, x, y))
    return self.claim(x, y)


def test_car_split_routes_touches(tmp_path):
  settings.update(tmp_path, enabled=True, side="left")
  car = car_split(1920, 720, settings.load(tmp_path))
  assert car.viewport == (960, 720) and car.phone.x == 0 and car.comma.x == 960
  phone = FakePhone(lambda x, y: x < 960)
  from collections import namedtuple
  TouchEvent = namedtuple("TouchEvent", "kind x y")
  routed = car.route([TouchEvent("down", 0.25, 0.5), TouchEvent("down", 0.75, 0.5), TouchEvent("up", 1.0, 0.5)], phone)
  assert [e.kind for e in routed] == ["down", "up"]
  assert routed[0].x == pytest.approx(0.5) and routed[1].x == pytest.approx(1.0)
  assert phone.seen[0] == ("down", 480, 360)
  assert car_split(1920, 720, settings.Settings(enabled=False)) is None


def test_carplay_audio_packet_is_decrypted_and_swapped():
  key = os.urandom(32)
  header = bytes([0x80, 0x60]) + struct.pack(">HII", 1, 480, 0x1234)
  pcm_big_endian = struct.pack(">4h", 1, -2, 300, -400)
  nonce8 = os.urandom(8)
  sealed = ChaCha20Poly1305(key).encrypt(b"\0" * 4 + nonce8, pcm_big_endian, header[4:12])
  pcm = open_audio_packet(key, header + sealed + nonce8)
  assert struct.unpack("<4h", pcm) == (1, -2, 300, -400)
  assert open_audio_packet(key, header + sealed[:-1] + b"\0" + nonce8) is None


def test_access_units_split_on_pictures():
  sps, pps = b"\0\0\0\x01\x67a", b"\0\0\0\x01\x68b"
  idr, p = b"\0\0\0\x01\x65\x88", b"\0\0\0\x01\x41\x9a"
  units = access_units(sps + pps + idr + p)
  assert len(units) == 2 and units[0].startswith(sps) and units[1] == p


class FakeServer:
  def __init__(self, name):
    self.sent = []

  def create_buffers_with_sizes(self, *args):
    pass

  def start_listener(self):
    pass

  def send(self, stream, data, frame_id, *_):
    self.sent.append((stream, len(data), frame_id))


class FakeDecoder:
  layout = FrameLayout(4, 2, 128, 128 * 32, 128 * 48)

  def __init__(self, width, height, hevc=False):
    self.units = []

  def push(self, unit, timestamp_us=0):
    self.units.append(unit)
    return True

  def pull(self, timeout_ms=0):
    if not self.units:
      return None
    self.units.pop()
    return self.layout, bytearray(self.layout.size), 0, 1

  def close(self):
    pass


def test_video_pipe_decodes_and_publishes_with_config_first():
  publisher = VideoPublisher({0: FakeDecoder.layout}, server_factory=FakeServer)
  decoders = []

  def factory(*args, **kwargs):
    decoders.append(FakeDecoder(*args, **kwargs))
    return decoders[-1]

  seen = []
  pipe = VideoPipe(publisher, 0, 4, 2, decoder_factory=factory, log=lambda m: None)
  original = FakeDecoder.push
  FakeDecoder.push = lambda self, unit, ts=0: (seen.append(unit), original(self, unit, ts))[1]
  try:
    pipe.config(b"CFG")
    pipe.submit(b"PIC")
    deadline = time.monotonic() + 2
    while pipe.published < 1 and time.monotonic() < deadline:
      time.sleep(0.01)
  finally:
    FakeDecoder.push = original
    pipe.close()
  assert pipe.published == 1 and publisher.server.sent[0][:2] == (0, FakeDecoder.layout.size)
  assert seen[0] == b"CFGPIC"


def test_carplay_audio_stream_receives_udp_packets():
  import socket
  from openpilot.starpilot.system.carplay.airplay.session import AudioStream
  key = os.urandom(32)
  got = []
  stream = AudioStream(key, "127.0.0.1", "media", 48000, 2, lambda *args: got.append(args), lambda m: None)
  tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    for seq in range(3):
      header = bytes([0x80, 0x60]) + struct.pack(">HII", seq, seq * 2, 1)
      nonce8 = struct.pack(">Q", seq)
      sealed = ChaCha20Poly1305(key).encrypt(b"\0" * 4 + nonce8, struct.pack(">2h", seq, -seq), header[4:12])
      tx.sendto(header + sealed + nonce8, ("127.0.0.1", stream.port))
    deadline = time.monotonic() + 2
    while len(got) < 3 and time.monotonic() < deadline:
      time.sleep(0.01)
  finally:
    tx.close()
    stream.close()
  assert [g[:3] for g in got] == [("media", 48000, 2)] * 3
  assert struct.unpack("<2h", got[2][3]) == (2, -2)
