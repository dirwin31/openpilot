"""Wireless CarPlay bring-up over iAP2: identification, authentication, Wi-Fi handoff, StartSession."""
from __future__ import annotations

from dataclasses import dataclass, field
import time

from openpilot.starpilot.system.carplay.iap2.wire import Body, Frame, parse_params, read_string, read_uint

START_IDENTIFICATION = 0x1D00
IDENTIFICATION_INFORMATION = 0x1D01
IDENTIFICATION_ACCEPTED = 0x1D02
IDENTIFICATION_REJECTED = 0x1D03
REQUEST_CERTIFICATE = 0xAA00
CERTIFICATE = 0xAA01
REQUEST_CHALLENGE = 0xAA02
CHALLENGE_RESPONSE = 0xAA03
AUTH_FAILED = 0xAA04
AUTH_SUCCEEDED = 0xAA05
CARPLAY_AVAILABILITY = 0x4300
CARPLAY_START_SESSION = 0x4301
WIRELESS_CARPLAY_UPDATE = 0x4E0D
DEVICE_TRANSPORT_IDENTIFIER = 0x4E0E
REQUEST_WIFI_CONFIGURATION = 0x5702
WIFI_CONFIGURATION = 0x5703
POWER_SOURCE_UPDATE = 0xAE03

MESSAGES_SENT = [0xAA01, 0xAA03, 0x5000, 0x5002, 0x5200, 0x5203, 0xAE00, 0xAE02, 0x4157, 0x4159, 0x4154, 0x4156, 0x4301]
MESSAGES_RECEIVED = [0xAA00, 0xAA02, 0xAA04, 0xAA05, 0xEA00, 0xEA01, 0x5001, 0x5201, 0x5202, 0x5204, 0xAE01, 0x4158,
                     0x4155, 0x4300]
WIRELESS_RECEIVED = [WIRELESS_CARPLAY_UPDATE, DEVICE_TRANSPORT_IDENTIFIER, REQUEST_WIFI_CONFIGURATION]
AUTH_MESSAGES = {0xAA00, 0xAA01, 0xAA02, 0xAA03, 0xAA04, 0xAA05}
SECURITY_WPA2 = 2


@dataclass
class Identification:
  name: str
  model: str
  manufacturer: str
  serial: str
  bluetooth_mac: str
  ssid: str
  firmware: str = "0.1.0"
  hardware: str = "1.0"
  language: str = "en"
  ea_protocol: str = "com.shilapi.xcertplay"


@dataclass
class WirelessEndpoint:
  ssid: str
  passphrase: str
  channel: int
  addresses: list[str]
  port: int
  device_id: str
  public_key_hex: str
  source_version: str = "950.7.1"
  security: int = SECURITY_WPA2
  bssid: bytes | None = None


def mac_bytes(mac: str) -> bytes:
  return bytes(int(part, 16) for part in mac.split(":"))


def identification_information(ident: Identification, omitted: set[int] = frozenset()) -> Frame:
  sent = [m for m in MESSAGES_SENT if m != POWER_SOURCE_UPDATE] + [WIFI_CONFIGURATION]
  received = MESSAGES_RECEIVED + WIRELESS_RECEIVED
  body = (Body()
          .string(0, ident.name).string(1, ident.model).string(2, ident.manufacturer).string(3, ident.serial)
          .string(4, ident.firmware).string(5, ident.hardware)
          .u16_list(6, [m for m in sent if m not in omitted])
          .u16_list(7, [m for m in received if m not in omitted])
          .u8(8, 0)
          .u16(9, 20)
          .group(10, Body().u8(0, 1).string(1, ident.ea_protocol).u8(2, 0))
          .string(12, ident.language)
          .strings(13, [ident.language])
          .group(17, Body().u16(0, 0).string(1, "blue").void(2).bytes(3, mac_bytes(ident.bluetooth_mac))
                 .string(4, "blue").void(5))
          .group(24, Body().u16(0, 1).string(1, ident.ssid).void(2).u16(3, 1).void(4).void(5))
          .group(30, Body().u16(0, 42).string(1, "RouteGuidance").u16(2, 64).u16(4, 64).u16(6, 8).u16(7, 64)
                 .u16(8, 8)))
  return Frame.build(IDENTIFICATION_INFORMATION, body)


def rejected_messages(frame: Frame) -> set[int] | None:
  params = frame.params()
  if not params or any(pid not in (6, 7) for pid, _ in params):
    return None
  ids = set()
  for _, payload in params:
    if len(payload) % 2:
      return None
    ids.update(int.from_bytes(payload[i:i + 2], "big") for i in range(0, len(payload), 2))
  return ids if ids and not ids & AUTH_MESSAGES else None


def subscriptions() -> list[Frame]:
  return [
    Frame.build(0x5000, Body().group(0, _voids(1, 4, 6, 12, 26)).group(1, _voids(0, 1, 7))),
    Frame.build(0x5200, Body().u16(0, 42).void(1).void(2)),
    Frame.build(0xAE00, _voids(4, 5, 6)),
    Frame.build(0x4157, _voids(0, 4, 5)),
    Frame.build(0x4154, _voids(0, 1, 2, 3, 4, 11)),
  ]


def _voids(*ids: int) -> Body:
  body = Body()
  for i in ids:
    body.void(i)
  return body


def wifi_configuration(ep: WirelessEndpoint) -> Frame:
  body = Body()
  if ep.bssid:
    body.bytes(0, ep.bssid)
  body.string(1, ep.ssid).string(2, ep.passphrase).u8(3, ep.security).u8(4, ep.channel)
  return Frame.build(WIFI_CONFIGURATION, body)


def start_session(ep: WirelessEndpoint) -> Frame:
  wireless = Body().string(0, ep.ssid).string(1, ep.passphrase).u8(2, ep.channel)
  for address in ep.addresses:
    wireless.string(3, address)
  wireless.u8(4, ep.security)
  body = (Body().group(1, wireless).u32(2, ep.port).string(3, ep.device_id).string(4, ep.public_key_hex)
          .string(5, ep.source_version))
  return Frame.build(CARPLAY_START_SESSION, body)


def describe(frame: Frame) -> str:
  try:
    parts = []
    for pid, payload in frame.params():
      if payload and payload[-1] == 0 and all(32 <= b < 127 for b in payload[:-1]):
        parts.append(f"{pid}={read_string(payload)!r}")
      elif len(payload) <= 4:
        parts.append(f"{pid}={read_uint(payload) if payload else 'void'}")
      else:
        parts.append(f"{pid}=<{len(payload)}b>")
    return " ".join(parts)
  except Exception:
    return f"<{len(frame.payload)}b>"


@dataclass
class WirelessControl:
  session: object
  mfi: object
  ident: Identification
  endpoint: WirelessEndpoint
  log: object = print
  on_frame: object = None
  state: dict = field(default_factory=lambda: {"identified": False, "authenticated": False, "wifi_sent": 0,
                                               "start_sent": 0, "transport_seen": False, "available": None})

  def run(self, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    omitted: set[int] | None = None
    subscribed = False
    while time.monotonic() < deadline:
      frame = self.session.recv(min(1.0, max(0.05, deadline - time.monotonic())))
      if frame is None:
        if self.session.closed.is_set():
          self.log(f"iap2 channel closed: {self.session.dead_reason}")
          break
        continue
      mid = frame.message_id
      self.log(f"iap2 rx 0x{mid:04x} {describe(frame) if mid not in (REQUEST_CHALLENGE,) else ''}")
      if mid == START_IDENTIFICATION:
        self._send(identification_information(self.ident, omitted or set()))
      elif mid == IDENTIFICATION_ACCEPTED:
        self.state["identified"] = True
        self.log("iap2 identification accepted")
      elif mid == IDENTIFICATION_REJECTED:
        retry = rejected_messages(frame)
        if omitted is not None or retry is None:
          self.log(f"iap2 identification rejected: {describe(frame)}")
          break
        omitted = retry
        self.log(f"iap2 identification rejected; retrying without {sorted(hex(m) for m in retry)}")
        self._send(identification_information(self.ident, omitted))
      elif mid == REQUEST_CERTIFICATE:
        self._send(Frame.build(CERTIFICATE, Body().bytes(0, self.mfi.certificate)))
      elif mid == REQUEST_CHALLENGE:
        challenge = frame.first(0) or b""
        self._send(Frame.build(CHALLENGE_RESPONSE, Body().bytes(0, self.mfi.sign(challenge))))
      elif mid == AUTH_FAILED:
        self.log("iap2 authentication FAILED")
        break
      elif mid == AUTH_SUCCEEDED:
        self.state["authenticated"] = True
        self.log("iap2 authentication succeeded")
        if not subscribed:
          subscribed = True
          for sub in subscriptions():
            self._send(sub)
      elif mid == REQUEST_WIFI_CONFIGURATION:
        if self.state["wifi_sent"] < 7:
          self.state["wifi_sent"] += 1
          self._send(wifi_configuration(self.endpoint))
      elif mid == CARPLAY_AVAILABILITY:
        if self.state["start_sent"] < 3:
          self.state["start_sent"] += 1
          self._send(start_session(self.endpoint))
      elif mid == WIRELESS_CARPLAY_UPDATE:
        self.state["available"] = bool(frame.first(0) and frame.first(0)[0])
      elif mid == DEVICE_TRANSPORT_IDENTIFIER:
        self.state["transport_seen"] = True
        if self.state["wifi_sent"] < 7:
          self.state["wifi_sent"] += 1
          self._send(wifi_configuration(self.endpoint))
      elif self.on_frame:
        self.on_frame(frame)
    return self.state

  def _send(self, frame: Frame) -> None:
    self.log(f"iap2 tx 0x{frame.message_id:04x} bytes={len(frame.payload)}")
    self.session.send(frame)


def params_of(payload: bytes) -> list[tuple[int, bytes]]:
  return parse_params(payload)
