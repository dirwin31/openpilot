"""Head-unit side of wireless Android Auto setup on the phone's Bluetooth channel.

The phone opens our Android Auto RFCOMM service; we tell it where to connect (WifiStartRequest),
answer its request for the network (WifiInfoResponse), and keep the channel open while the session
runs, answering its pings. Frames are a 16-bit length, a 16-bit type, then a protobuf.
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Callable

from openpilot.starpilot.system.starport.wire import field, one, parse_fields, signed

START_REQUEST, INFO_REQUEST, INFO_RESPONSE = 1, 2, 3
VERSION_REQUEST, VERSION_RESPONSE, CONNECT_STATUS, START_RESPONSE = 4, 5, 6, 7
PING_REQUEST, PING_RESPONSE = 8, 9
SECURITY_WPA2_PERSONAL = 8
ACCESS_POINT_DYNAMIC = 1
MAX_FRAME = 0xFFFF


class WirelessSetupError(RuntimeError):
  pass


def frame(kind: int, payload: bytes = b"") -> bytes:
  return struct.pack(">HH", len(payload), kind) + payload


def start_request(ip: str, port: int) -> bytes:
  return field(1, ip) + field(2, port) + field(3, 0)


def info_response(ssid: str, key: str, bssid: str) -> bytes:
  return field(1, ssid) + field(2, key) + field(3, bssid) + field(4, SECURITY_WPA2_PERSONAL) + field(5, ACCESS_POINT_DYNAMIC)


class FrameReader:
  def __init__(self):
    self.buffer = bytearray()

  def feed(self, data: bytes) -> list[tuple[int, bytes]]:
    self.buffer += data
    frames = []
    while len(self.buffer) >= 4:
      size, kind = struct.unpack(">HH", self.buffer[:4])
      if len(self.buffer) < 4 + size:
        break
      frames.append((kind, bytes(self.buffer[4:4 + size])))
      del self.buffer[:4 + size]
    return frames


class WirelessSetup:
  """One phone's setup channel. run() returns once the phone reports it joined, then hold() keeps it open."""

  def __init__(self, sock: socket.socket, ip: str, port: int, ssid: str, key: str, bssid: str,
               log: Callable[[str], None] = print, timeout: float = 30.0):
    self.sock, self.ip, self.port, self.ssid, self.key, self.bssid = sock, ip, port, ssid, key, bssid
    self.log, self.timeout = log, timeout
    self.reader = FrameReader()
    self.lock = threading.Lock()
    self.joined = False
    self.closed = threading.Event()

  def send(self, kind: int, payload: bytes = b"") -> None:
    with self.lock:
      self.sock.sendall(frame(kind, payload))

  def _read(self, deadline: float) -> list[tuple[int, bytes]]:
    self.sock.settimeout(max(0.05, deadline - time.monotonic()))
    try:
      data = self.sock.recv(4096)
    except TimeoutError:
      return []
    if not data:
      raise WirelessSetupError("the phone closed its setup channel")
    return self.reader.feed(data)

  def handle(self, kind: int, payload: bytes) -> None:
    if kind == INFO_REQUEST:
      self.send(INFO_RESPONSE, info_response(self.ssid, self.key, self.bssid))
      self.log("android wireless: sent network details")
    elif kind == START_REQUEST:
      self.send(START_REQUEST, start_request(self.ip, self.port))
    elif kind == PING_REQUEST:
      self.send(PING_RESPONSE, payload)
    elif kind in (START_RESPONSE, CONNECT_STATUS):
      fields = parse_fields(payload)
      status = signed(one(fields, 3 if kind == START_RESPONSE else 1, 0))
      if status == 0:
        self.joined = True
        self.log("android wireless: phone joined the hotspot")
      else:
        self.log(f"android wireless: phone reported status {status}")
    elif kind == VERSION_RESPONSE:
      pass
    else:
      self.log(f"android wireless: ignored message {kind}")

  def run(self) -> bool:
    self.send(START_REQUEST, start_request(self.ip, self.port))
    deadline = time.monotonic() + self.timeout
    while not self.joined and time.monotonic() < deadline:
      for kind, payload in self._read(deadline):
        self.handle(kind, payload)
    return self.joined

  def hold(self, done: Callable[[], bool]) -> None:
    """Answer pings until the session ends or the phone drops the channel."""
    try:
      while not done() and not self.closed.is_set():
        for kind, payload in self._read(time.monotonic() + 1.0):
          self.handle(kind, payload)
    except (OSError, WirelessSetupError):
      pass

  def close(self) -> None:
    self.closed.set()
    try:
      self.sock.close()
    except OSError:
      pass
