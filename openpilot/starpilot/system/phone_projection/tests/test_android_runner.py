import socket
import threading
import time
from functools import partial

from openpilot.starpilot.system.phone_projection.android import runner as runner_module
from openpilot.starpilot.system.phone_projection.android.session import HeadUnitSession
from openpilot.starpilot.system.phone_projection.android.wireless import (FrameReader, INFO_REQUEST, INFO_RESPONSE,
                                                                          START_REQUEST, START_RESPONSE, frame)
from openpilot.starpilot.system.phone_projection.bluez import HFP_AG_UUID
from openpilot.starpilot.system.phone_projection.ipc import Touch
from openpilot.starpilot.system.phone_projection.runtime import Context, Hotspot
from openpilot.starpilot.system.phone_projection.settings import Settings
from openpilot.starpilot.system.starport.session import ProjectionSession
from openpilot.starpilot.system.starport.tests.fake_head_unit import make_identity
from openpilot.starpilot.system.starport.wire import field, one, parse_fields, text

SPOT = Hotspot("StarPilot-Test", "secret-pass", 36, bytes.fromhex("021122334455"))
PHONE = {"path": "/org/bluez/hci0/dev_AA", "address": "AA:BB:CC:DD:EE:FF", "name": "Pixel", "paired": True,
         "connected": True, "uuids": [HFP_AG_UUID]}


class FakeBluez:
  def __init__(self):
    self.on_connection = None
    self.registered = threading.Event()

  def devices(self):
    return [PHONE]

  def adapter_address(self):
    return "00:11:22:33:44:55"

  def register_android_auto(self, on_connection):
    self.on_connection = on_connection
    self.registered.set()

  def unregister_android_auto(self):
    pass

  def connect(self, path):
    pass


class FakeAudio:
  def __init__(self):
    self.requests = []

  def request(self, wanted):
    self.requests.append(wanted)

  def write(self, *args):
    return True

  def stop(self, name):
    pass

  def stop_all(self):
    pass


class FakePipe:
  instances = []

  def __init__(self, publisher, stream, width, height, **kwargs):
    self.stream, self.units, self.published = stream, [], 0
    FakePipe.instances.append(self)

  def config(self, data):
    self.units.append(data)

  def submit(self, data):
    self.units.append(data)
    self.published += 1

  def close(self):
    pass


def phone_bluetooth(sock, joined):
  reader = FrameReader()
  endpoint, network = None, None
  while network is None or endpoint is None:
    for kind, payload in reader.feed(sock.recv(4096)):
      fields = parse_fields(payload)
      if kind == START_REQUEST:
        endpoint = (text(fields, 1), one(fields, 2))
        sock.sendall(frame(INFO_REQUEST))
      elif kind == INFO_RESPONSE:
        network = (text(fields, 1), text(fields, 2), text(fields, 3))
  joined.append((endpoint, network))
  sock.sendall(frame(START_RESPONSE, field(3, 0)))
  return endpoint


def test_android_phone_connects_over_bluetooth_and_wifi(tmp_path, monkeypatch):
  identity = make_identity(tmp_path)
  monkeypatch.setattr(runner_module, "VideoPipe", FakePipe)
  bluez, audio = FakeBluez(), FakeAudio()
  ctx = Context(publisher=None, panel=(960, 1080), settings=lambda: Settings(enabled=True, audio="music"), audio=audio,
                bluez=bluez, log=lambda m: None)
  factory = partial(HeadUnitSession, certificate=identity["hu_cert"], private_key=identity["hu_key"])
  probe = socket.socket()
  probe.bind(("127.0.0.1", 0))
  port = probe.getsockname()[1]
  probe.close()
  runner = runner_module.AndroidAutoRunner(ctx, hotspot_source=lambda: SPOT, listen_address="127.0.0.1", port=port,
                                           session_factory=factory).start()
  try:
    assert bluez.registered.wait(3)
    phone_bt, comma_bt = socket.socketpair()
    threading.Thread(target=bluez.on_connection, args=(PHONE["address"], comma_bt), daemon=True).start()
    joined = []
    ip, tcp_port = phone_bluetooth(phone_bt, joined)
    assert joined[0][1] == ("StarPilot-Test", "secret-pass", "02:11:22:33:44:55")
    assert tcp_port == port

    conn = socket.create_connection(("127.0.0.1", tcp_port), timeout=5)
    phone = ProjectionSession(conn, str(identity["phone_cert"]), str(identity["phone_key"]), ca=str(identity["root"]))
    phone.authenticate()
    mode = phone.start("Pixel", "Google")
    assert (mode.margin_width, mode.margin_height) == (960, 0)
    deadline = time.monotonic() + 5
    while not runner.active and time.monotonic() < deadline:
      phone.pump(0.1)
    assert runner.active and runner.status()["phone"] == "Pixel"
    assert audio.requests[-1] is True
    phone.send_frame(b"\x00\x00\x00\x01\x65frame", 1000, keyframe=True)
    deadline = time.monotonic() + 3
    while runner.frames() == 0 and time.monotonic() < deadline:
      phone.pump(0.1)
    assert runner.frames() == 1 and FakePipe.instances[-1].stream == 2

    runner.touch([Touch("down", 0, 0.5, 0.5), Touch("up", 0, 0.5, 0.5)])
    deadline = time.monotonic() + 3
    while len(phone.touch_events) < 2 and time.monotonic() < deadline:
      phone.pump(0.1)
    kinds = [e.kind for e in phone.touch_events]
    assert kinds == ["down", "up"]
    assert abs(phone.touch_events[0].x - 0.5) < 0.01 and abs(phone.touch_events[0].y - 0.5) < 0.01

    conn.close()
    deadline = time.monotonic() + 3
    while runner.active and time.monotonic() < deadline:
      time.sleep(0.05)
    assert not runner.active
  finally:
    ctx.stop.set()
    runner.close()
