import os
import subprocess
import tty

from openpilot.starpilot.system.android_auto import usb_accessory as usb


def test_parse_uevent_accessory_start_and_state():
  start = usb.parse_uevent(b"change@/devices/virtual/misc/usb_accessory\0ACTION=change\0ACCESSORY=START\0SEQNUM=9\0")
  assert start["ACTION"] == "change" and start["DEVPATH"] == "/devices/virtual/misc/usb_accessory" and start["ACCESSORY"] == "START"
  state = usb.parse_uevent(b"change@/devices/virtual/android_usb/android0\0USB_STATE=CONFIGURED\0")
  assert state["USB_STATE"] == "CONFIGURED"
  assert usb.parse_uevent(b"libudev\0junk") == {}


def fake_gadget(tmp_path):
  root = tmp_path / "g1"
  (root / "functions" / "ffs.adb").mkdir(parents=True)
  (root / usb.CONFIG).mkdir(parents=True)
  for name, value in (("idVendor", "0x04d8"), ("idProduct", "0x1234"), ("UDC", "a600000.dwc3")):
    (root / name).write_text(value + "\n")
  calls = []

  def run(args, input=None, **kwargs):
    assert args[:2] == ["sudo", "-n"]
    command, rest = args[2], args[3:]
    calls.append((command, *rest, (input or "").strip()))
    if command == "tee":
      open(rest[0], "w").write(input)
    elif command == "mkdir":
      os.mkdir(rest[0])
    elif command == "ln":
      os.symlink(rest[1], rest[2])
    elif command == "rm":
      os.unlink(rest[1])
    return subprocess.CompletedProcess(args, 0, "", "")
  return root, calls, run


def test_gadget_switches_to_accessory_and_restores(tmp_path, monkeypatch):
  root, calls, run = fake_gadget(tmp_path)
  device = tmp_path / "usb_accessory"
  device.write_text("")
  monkeypatch.setattr(usb, "ACCESSORY_DEVICE", str(device))
  gadget = usb.AccessoryGadget(lambda *a, **k: None, root=root, run=run)
  gadget.prepare()
  assert (root / "functions" / usb.ACCESSORY_FUNCTION).is_dir()
  gadget.switch_to_accessory()
  assert (root / "idVendor").read_text().strip() == "0x18d1" and (root / "idProduct").read_text().strip() == "0x2d01"
  assert (root / usb.CONFIG / usb.ACCESSORY_FUNCTION).is_symlink()
  assert (root / "UDC").read_text().strip() == "a600000.dwc3"
  udc_writes = [call[-1] for call in calls if call[0] == "tee" and call[1].endswith("UDC")]
  assert udc_writes == ["", "a600000.dwc3"]  # unbind before changing identity, then rebind
  gadget.restore()
  assert (root / "idVendor").read_text().strip() == "0x04d8" and (root / "idProduct").read_text().strip() == "0x1234"
  assert not (root / usb.CONFIG / usb.ACCESSORY_FUNCTION).exists()
  assert (root / "UDC").read_text().strip() == "a600000.dwc3"


def test_bridge_copies_both_ways_and_reports_disconnect():
  master, slave = os.openpty()
  tty.setraw(slave)
  bridge = usb.AccessoryBridge(os.ttyname(slave))
  os.close(slave)
  try:
    sock = bridge.socket
    sock.settimeout(5)
    os.write(master, b"\x00\x03\x00\x06\x00\x01\x00\x01\x00\x04")  # the car's version request
    assert sock.recv(64) == b"\x00\x03\x00\x06\x00\x01\x00\x01\x00\x04"
    sock.sendall(b"reply")
    received = b""
    while len(received) < 5:
      received += os.read(master, 64)
    assert received == b"reply"
    os.close(master)  # the car unplugs
    assert bridge.closed.wait(5)
    assert sock.recv(64) == b""
  finally:
    bridge.close()


def test_supervisor_wired_session_end_to_end(tmp_path, monkeypatch):
  import socket
  import threading
  import time
  from pathlib import Path
  from openpilot.starpilot.system.android_auto import hw_encoder, identity as identity_store, supervisor as supervisor_module
  from openpilot.starpilot.system.android_auto.tests.fake_head_unit import FakeHeadUnit, make_identity

  identity = make_identity(tmp_path / "ident")
  data = tmp_path / "aa"
  (data / "identity").mkdir(parents=True)
  for src, name in ((identity["phone_cert"], "phone-cert.pem"), (identity["phone_key"], "phone-key.pem"), (identity["root"], "root-cert.pem")):
    (data / "identity" / name).write_bytes(Path(src).read_bytes())
  (data / "identity" / "phone-key.pem").chmod(0o600)
  monkeypatch.setattr(identity_store, "IDENTITY_DIR", data / "identity")
  monkeypatch.setattr(identity_store, "CONFIG_PATH", data / "config.json")
  monkeypatch.setattr(identity_store, "LOG_DIR", data / "logs")
  monkeypatch.setattr(hw_encoder, "LIBRARY", tmp_path / "missing.so")
  hu = FakeHeadUnit(identity)
  calls = []

  class FakeGadget:
    def __init__(self, log):
      pass

    def prepare(self):
      calls.append("prepare")

    def switch_to_accessory(self):
      calls.append("switch")

    def restore(self):
      calls.append("restore")

  class FakeListener:
    events = [{"DEVPATH": "/devices/virtual/android_usb/android0", "USB_STATE": "CONFIGURED"},
              {"DEVPATH": "/devices/virtual/misc/usb_accessory", "ACCESSORY": "START"}]

    def next(self, timeout):
      return self.events.pop(0) if self.events else None

    def close(self):
      pass

  class FakeBridge:  # the "cable": a TCP connection to the fake head unit
    def __init__(self, log=None):
      self.socket = socket.create_connection(("127.0.0.1", hu.port))
      self.closed = threading.Event()

    def close(self):
      self.closed.set()
      self.socket.close()

  monkeypatch.setattr(usb, "AccessoryGadget", FakeGadget)
  monkeypatch.setattr(usb, "UeventListener", FakeListener)
  monkeypatch.setattr(usb, "AccessoryBridge", FakeBridge)
  sup = supervisor_module.Supervisor(synthetic=True)
  sup.set_connection("wired")
  try:
    sup.start()  # no car chosen: wired needs none
    deadline = time.monotonic() + 20
    while len(hu.frames) < 3:
      assert time.monotonic() < deadline, sup.status()
      time.sleep(0.05)
    status = sup.status()
    assert status["connection"] == "wired" and status["state"] == "streaming"
    try:
      sup.set_connection("wireless")
      raise AssertionError("connection changed while running")
    except RuntimeError:
      pass
  finally:
    sup.stop()
    hu.close()
  assert calls[:2] == ["prepare", "switch"] and "restore" in calls
  assert identity_store.load_config()["connection"] == "wired"
