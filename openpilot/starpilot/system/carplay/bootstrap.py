"""Development entry: wireless CarPlay bring-up over Bluetooth, then log the iPhone's AirPlay connection.

sudo PYTHONPATH=<repo> python3 -m openpilot.starpilot.system.carplay.bootstrap --phone C0:2C:5C:34:CB:DD
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from openpilot.starpilot.system.carplay.airplay.info import ReceiverConfig
from openpilot.starpilot.system.carplay.airplay.pairing import PairingStore
from openpilot.starpilot.system.carplay.airplay.session import AirPlayServer
from openpilot.starpilot.system.carplay.iap2.control import Identification, WirelessControl, WirelessEndpoint
from openpilot.starpilot.system.carplay.iap2.session import Iap2Session
from openpilot.starpilot.system.carplay.mfi import MfiIdentity
from openpilot.starpilot.system.starport.sdp import SDP_PSM, build_request, parse_response, rfcomm_channel

IAP2_IPHONE_UUID = uuid.UUID("00000000-deca-fade-deca-deafdecacafe")
STATE_DIR = Path("/data/carplay")
HOSTAPD_CONF = Path("/run/starpilot-galaxy-hotspot/hostapd.conf")
HOTSPOT_STATUS = Path("/tmp/starpilot-galaxy-hotspot-status.json")
HOTSPOT_INTERFACE = "galaxy0"
HOTSPOT_ADDRESS = "172.31.254.1"
AIRPLAY_PORT = 7000


def log(message: str) -> None:
  print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)


class AirPlayIdentity:
  """Our own Ed25519 receiver identity (not the Apple accessory key), kept across runs."""

  def __init__(self, path: Path = STATE_DIR / "airplay_identity.json"):
    try:
      saved = json.loads(path.read_text())
      self.private = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(saved["private"]))
      self.pairing_id = saved["pairing_id"]
    except (OSError, ValueError, KeyError):
      self.private = ed25519.Ed25519PrivateKey.generate()
      self.pairing_id = str(uuid.uuid4()).upper()
      raw = self.private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                       serialization.NoEncryption())
      path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
      fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
      with os.fdopen(fd, "w") as f:
        json.dump({"private": raw.hex(), "pairing_id": self.pairing_id}, f)
    self.public = self.private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

  @property
  def device_id(self) -> str:
    digest = bytearray(hashlib.sha256(self.public).digest()[:6])
    digest[0] = (digest[0] & 0xFC) | 0x02
    return ":".join(f"{b:02X}" for b in digest)


def hotspot() -> dict:
  conf = dict(line.split("=", 1) for line in HOSTAPD_CONF.read_text().splitlines() if "=" in line)
  status = json.loads(HOTSPOT_STATUS.read_text())
  if not status.get("active"):
    raise RuntimeError("Galaxy hotspot is not active")
  bssid = Path(f"/sys/class/net/{HOTSPOT_INTERFACE}/address").read_text().strip()
  return {"ssid": conf["ssid"], "passphrase": conf.get("wpa_passphrase", ""), "channel": int(conf["channel"]),
          "bssid": bytes.fromhex(bssid.replace(":", ""))}


def local_bluetooth_mac() -> str:
  out = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=5).stdout
  match = re.search(r"Controller ([0-9A-F:]{17})", out)
  if not match:
    raise RuntimeError("No Bluetooth controller")
  return match.group(1)


def iap2_channel(address: str, timeout: float = 10.0) -> int:
  sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
  sock.settimeout(timeout)
  try:
    sock.connect((address, SDP_PSM))
    collected, continuation = bytearray(), b""
    for _ in range(8):
      sock.send(build_request(continuation, service=IAP2_IPHONE_UUID))
      attributes, continuation = parse_response(sock.recv(4096))
      collected += attributes
      if not continuation:
        break
  finally:
    sock.close()
  channel = rfcomm_channel(bytes(collected))
  if channel is None:
    raise RuntimeError("iPhone does not advertise the iAP2 RFCOMM service")
  return channel


def connect_iap2(address: str) -> socket.socket:
  channel = iap2_channel(address)
  log(f"iPhone iAP2 RFCOMM channel {channel}")
  sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
  sock.settimeout(15)
  sock.connect((address, channel))
  sock.settimeout(None)
  return sock


class FileSink:
  """Debug sink: writes the H.264 stream to a file and logs session events."""

  def __init__(self, path: Path):
    self.path = path
    self.file = None
    self.frames = 0
    self.last_log = 0.0

  def on_video_config(self, codec: str, data: bytes) -> None:
    from openpilot.starpilot.system.carplay.airplay.session import avcc_to_annexb
    log(f"video config codec={codec} bytes={len(data)}")
    if self.file is None:
      self.file = open(self.path, "wb")
    if codec == "h264":
      self.file.write(avcc_to_annexb(data))

  def on_video_frame(self, data: bytes) -> None:
    if self.file is None:
      self.file = open(self.path, "wb")
    self.file.write(data)
    self.frames += 1
    now = time.monotonic()
    if now - self.last_log > 5:
      self.last_log = now
      log(f"video frames={self.frames}")

  def on_iap_tunnel_data(self, data: bytes) -> None:
    log(f"iAP tunnel data bytes={len(data)} head={data[:12].hex()}")

  def on_session_active(self, session) -> None:
    pass

  def on_event_ready(self, session) -> None:
    pass

  def on_session_closed(self, session=None) -> None:
    log(f"session closed frames={self.frames}")
    if self.file:
      self.file.flush()


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--phone", required=True, help="iPhone Bluetooth address (already paired)")
  parser.add_argument("--name", default="StarPilot")
  parser.add_argument("--timeout", type=float, default=120)
  parser.add_argument("--width", type=int, default=1280)
  parser.add_argument("--height", type=int, default=720)
  args = parser.parse_args()

  mfi = MfiIdentity.load()
  log("accessory identity loaded and matches its certificate")
  identity = AirPlayIdentity()
  spot = hotspot()
  bt_mac = local_bluetooth_mac()
  log(f"hotspot ssid={spot['ssid']} channel={spot['channel']} bt={bt_mac} deviceid={identity.device_id}")

  cfg = ReceiverConfig(name=args.name, device_id=identity.device_id, bluetooth_mac=bt_mac, width=args.width,
                       height=args.height)
  sink = FileSink(STATE_DIR / "screen.h264")
  server = AirPlayServer(HOTSPOT_ADDRESS, AIRPLAY_PORT, cfg, identity, PairingStore(STATE_DIR / "pairings.json"), mfi,
                         sink, log)

  sock = connect_iap2(args.phone)
  log("RFCOMM connected")
  session = Iap2Session(sock, log=log).start()
  ident = Identification(name=args.name, model="comma", manufacturer="StarPilot",
                         serial="SP-" + identity.device_id.replace(":", ""), bluetooth_mac=bt_mac, ssid=spot["ssid"])
  endpoint = WirelessEndpoint(ssid=spot["ssid"], passphrase=spot["passphrase"], channel=spot["channel"],
                              addresses=[HOTSPOT_ADDRESS], port=AIRPLAY_PORT, device_id=identity.device_id,
                              public_key_hex=identity.public.hex(), bssid=spot["bssid"])
  try:
    result = WirelessControl(session, mfi, ident, endpoint, log=log).run(args.timeout)
    log(f"iAP2 bootstrap result {result}")
    end = time.monotonic() + args.timeout
    while time.monotonic() < end and server.session and not server.session.closed.is_set():
      time.sleep(1)
  finally:
    session.close()
    server.close()


if __name__ == "__main__":
  main()
