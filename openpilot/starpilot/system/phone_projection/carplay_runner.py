"""Wireless CarPlay for projectiond: dial a paired iPhone over Bluetooth, hand it the hotspot, show its screen."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import threading
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from openpilot.starpilot.system.carplay.airplay.info import ReceiverConfig
from openpilot.starpilot.system.carplay.airplay.pairing import PairingStore
from openpilot.starpilot.system.carplay.airplay.session import AirPlayServer, avcc_to_annexb
from openpilot.starpilot.system.carplay.iap2.control import Identification, WirelessControl, WirelessEndpoint
from openpilot.starpilot.system.carplay.iap2.session import Iap2Session
from openpilot.starpilot.system.carplay.mfi import MfiIdentity
from openpilot.starpilot.system.phone_projection.bluez import IAP2_UUID, pick_iphone
from openpilot.starpilot.system.phone_projection.ipc import Touch
from openpilot.starpilot.system.phone_projection.layout import carplay_size, carplay_width_mm
from openpilot.starpilot.system.phone_projection.runtime import HOTSPOT_ADDRESS, STATE_DIR, Context, TouchTracker, hotspot
from openpilot.starpilot.system.phone_projection.video import CARPLAY_STREAM, VideoPipe

AIRPLAY_PORT = 7000
LEGACY_STATE = Path("/data/carplay")
RETRY_SECONDS = 10.0
IDLE_SECONDS = 2.0
BOOTSTRAP_TIMEOUT = 45.0
NAME = "StarPilot"


class AirPlayIdentity:
  """Our own Ed25519 receiver identity (not the Apple accessory key), kept across runs."""

  def __init__(self, path: Path):
    try:
      saved = json.loads(path.read_text())
      self.private = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(saved["private"]))
      self.pairing_id = saved["pairing_id"]
    except (OSError, ValueError, KeyError):
      self.private = ed25519.Ed25519PrivateKey.generate()
      self.pairing_id = str(uuid.uuid4()).upper()
      raw = self.private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
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


def adopt_legacy_state(state: Path, legacy: Path = LEGACY_STATE) -> None:
  """Keep the identity the first test runs created, so the iPhone does not see a new car."""
  for name in ("airplay_identity.json", "pairings.json"):
    target = state / name
    if not target.exists() and os.access(legacy / name, os.R_OK):
      state.mkdir(mode=0o700, parents=True, exist_ok=True)
      shutil.copyfile(legacy / name, target)
      os.chmod(target, 0o600)


def iap2_socket(address: str, timeout: float = 15.0) -> socket.socket:
  from openpilot.starpilot.system.starport.sdp import SDP_PSM, build_request, parse_response, rfcomm_channel
  sdp = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
  sdp.settimeout(timeout)
  try:
    sdp.connect((address, SDP_PSM))
    collected, continuation = bytearray(), b""
    for _ in range(8):
      sdp.send(build_request(continuation, service=uuid.UUID(IAP2_UUID)))
      attributes, continuation = parse_response(sdp.recv(4096))
      collected += attributes
      if not continuation:
        break
  finally:
    sdp.close()
  channel = rfcomm_channel(bytes(collected))
  if channel is None:
    raise OSError("The iPhone does not offer CarPlay over Bluetooth")
  sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
  sock.settimeout(timeout)
  sock.connect((address, channel))
  sock.settimeout(None)
  return sock


class CarPlayRunner:
  kind = "carplay"

  def __init__(self, ctx: Context, *, mfi_loader=MfiIdentity.load, connect=iap2_socket, hotspot_source=hotspot,
               server_factory=AirPlayServer, local_address=None):
    self.ctx, self.mfi_loader, self.connect, self.hotspot_source = ctx, mfi_loader, connect, hotspot_source
    self.server_factory = server_factory
    self.local_address = local_address or ctx.bluez.adapter_address
    adopt_legacy_state(STATE_DIR)
    self.identity = AirPlayIdentity(STATE_DIR / "airplay_identity.json")
    self.pairings = PairingStore(STATE_DIR / "pairings.json")
    self.state, self.message, self.phone = "starting", "", ""
    self.server = None
    self.server_key = None
    self.session = None
    self.link = None
    self.pipe: VideoPipe | None = None
    self.touches = TouchTracker()
    self.lock = threading.Lock()
    self.thread = threading.Thread(target=self._run, name="projection-carplay", daemon=True)

  def start(self) -> CarPlayRunner:
    self.thread.start()
    return self

  @property
  def active(self) -> bool:
    return self.state == "active"

  def status(self) -> dict:
    return {"state": self.state, "message": self.message, "phone": self.phone}

  def _set(self, state: str, message: str = "") -> None:
    if (state, message) != (self.state, self.message):
      self.ctx.log(f"carplay: {state} {message}".rstrip())
    self.state, self.message = state, message

  # ---- connection loop ---------------------------------------------------------------------------------
  def _run(self) -> None:
    stop = self.ctx.stop
    while not stop.is_set():
      try:
        wait = self._attempt()
      except Exception as error:  # one failed attempt never ends CarPlay
        self._set("retrying", f"{type(error).__name__}: {error}")
        wait = RETRY_SECONDS
      stop.wait(wait)
    self.close()

  def _attempt(self) -> float:
    try:
      mfi = self.mfi_loader()
    except (OSError, ValueError) as error:
      self._set("unavailable", f"CarPlay accessory key missing: {error}")
      return 60.0
    spot = self.hotspot_source()
    if spot is None:
      self._set("waiting_hotspot", "Turn on the Galaxy hotspot so the iPhone can connect.")
      return RETRY_SECONDS
    settings = self.ctx.settings()
    iphone = pick_iphone(self.ctx.bluez.devices(), settings.iphone)
    if iphone is None:
      self._set("waiting_phone", "Pair your iPhone with the comma over Bluetooth.")
      return RETRY_SECONDS
    self.phone = iphone["name"]
    self._ensure_server(spot, settings.audio == "music", mfi)
    if self.session is not None and not self.session.closed.is_set():
      return IDLE_SECONDS
    self._set("connecting", f"Connecting to {iphone['name']}…")
    sock = self.connect(iphone["address"])
    link = Iap2Session(sock, log=lambda m: None).start()
    self.link = link
    bt_mac = self.local_address()
    ident = Identification(name=NAME, model="comma", manufacturer="StarPilot", serial="SP-" + self.identity.device_id.replace(":", ""),
                           bluetooth_mac=bt_mac, ssid=spot.ssid)
    endpoint = WirelessEndpoint(ssid=spot.ssid, passphrase=spot.passphrase, channel=spot.channel, addresses=[HOTSPOT_ADDRESS],
                                port=AIRPLAY_PORT, device_id=self.identity.device_id, public_key_hex=self.identity.public.hex(),
                                bssid=spot.bssid)
    try:
      WirelessControl(link, mfi, ident, endpoint, log=lambda m: None).run(BOOTSTRAP_TIMEOUT)
      deadline = time.monotonic() + BOOTSTRAP_TIMEOUT
      while time.monotonic() < deadline and not self.ctx.stop.is_set():
        if self.session is not None and not self.session.closed.is_set():
          break
        time.sleep(0.25)
      if self.session is None or self.session.closed.is_set():
        self._set("retrying", f"{iphone['name']} did not join the hotspot.")
        return RETRY_SECONDS
      while self.session is not None and not self.session.closed.is_set() and not self.ctx.stop.is_set():
        time.sleep(0.5)
    finally:
      self.link = None
      link.close()
    self._set("retrying", "CarPlay disconnected.")
    return 3.0

  def _ensure_server(self, spot, audio: bool, mfi) -> None:
    key = (spot.ssid, audio, self.ctx.panel)
    if self.server is not None and self.server_key == key:
      return
    if self.server is not None:
      self.server.close()
    width, height = carplay_size(self.ctx.panel)
    cfg = ReceiverConfig(name=NAME, device_id=self.identity.device_id, bluetooth_mac=self.local_address(), width=width,
                         height=height, width_mm=carplay_width_mm(self.ctx.panel), fps=self.ctx.fps, disable_audio=not audio)
    self.ctx.log(f"carplay: asking for {width}x{height} at {cfg.fps} fps, {cfg.width_mm} mm wide")
    self.server = self.server_factory(HOTSPOT_ADDRESS, AIRPLAY_PORT, cfg, self.identity, self.pairings, mfi, self, self._log)
    self.server_key = key

  def _log(self, message: str) -> None:
    if "frames" not in message:
      self.ctx.log(f"carplay: {message}")

  # ---- AirPlay sink --------------------------------------------------------------------------------------
  def on_session_active(self, session) -> None:
    with self.lock:
      self.session = session
    self.ctx.audio.request(self.ctx.settings().audio == "music")
    self._set("active", f"{self.phone or 'iPhone'} connected")

  def on_event_ready(self, session) -> None:
    with self.lock:
      self.session = session
    session.request_keyframe()

  def on_video_config(self, codec: str, data: bytes) -> None:
    if codec != "h264":
      self.ctx.log(f"carplay: unsupported video codec {codec}")
      return
    with self.lock:
      if self.pipe is None:
        width, height = carplay_size(self.ctx.panel)
        self.pipe = VideoPipe(self.ctx.publisher, CARPLAY_STREAM, width, height, request_keyframe=self._keyframe, log=self.ctx.log)
      self.pipe.config(avcc_to_annexb(data))

  def on_video_frame(self, data: bytes) -> None:
    pipe = self.pipe
    if pipe is not None:
      pipe.submit(data)

  def on_iap_tunnel_data(self, data: bytes) -> None:
    pass

  def on_command(self, kind: str, params) -> None:
    if kind.lower() in ("disablebluetooth", "disable-bluetooth"):
      link = self.link
      if link is not None:
        self.ctx.log("carplay: Wi-Fi took over; closing the Bluetooth link")
        threading.Thread(target=link.close, daemon=True).start()

  def on_audio(self, name: str, rate: int, channels: int, pcm: bytes) -> None:
    self.ctx.audio.write(f"carplay-{name}", rate, channels, pcm)

  def on_audio_stopped(self, name: str) -> None:
    self.ctx.audio.stop(f"carplay-{name}")

  def on_session_closed(self) -> None:
    with self.lock:
      pipe, self.pipe, self.session = self.pipe, None, None
    if pipe is not None:
      threading.Thread(target=pipe.close, daemon=True).start()
    self.ctx.audio.request(False)
    self.touches.reset()
    if self.state == "active":
      self._set("retrying", "CarPlay disconnected.")

  def _keyframe(self) -> None:
    session = self.session
    if session is not None:
      session.request_keyframe()

  # ---- input -----------------------------------------------------------------------------------------------
  def touch(self, touches: list[Touch]) -> None:
    session = self.session
    if session is None:
      return
    width, height = carplay_size(self.ctx.panel)
    for touch in touches:
      if touch.kind == "cancel":
        contacts = [(slot, x * width, y * height, False) for slot, (x, y) in self.touches.active().items()]
        self.touches.reset()
        if contacts:
          session.send_touch(contacts)
        continue
      result = self.touches.apply(touch)
      if result is None:
        continue
      slot, down = result
      contacts = [(s, x * width, y * height, True) for s, (x, y) in self.touches.active().items() if s != slot]
      session.send_touch(contacts + [(slot, touch.x * width, touch.y * height, down)])

  def frames(self) -> int:
    pipe = self.pipe
    return pipe.published if pipe is not None else 0

  def problem(self) -> str:
    pipe = self.pipe
    return pipe.error if pipe is not None else ""

  def close(self) -> None:
    if self.server is not None:
      self.server.close()
      self.server = None
    pipe, self.pipe = self.pipe, None
    if pipe is not None:
      pipe.close()
