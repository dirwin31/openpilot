"""Head-unit side of an Android Auto session: the comma is the car, the phone projects into it.

The mirror of StarPort's phone-side session (starport/session.py): we send the version request, act as the
TLS client with the head-unit certificate, report authentication, answer service discovery, and then accept
whatever channels the phone opens. Video is acknowledged one frame at a time and handed to the decoder.
"""
from __future__ import annotations

import ssl
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from openpilot.starpilot.system.starport.wire import field, one, parse_fields, signed, text

HERE = Path(__file__).parent
CERTIFICATE, PRIVATE_KEY = HERE / "headunit.crt", HERE / "headunit.key"
VERSION = (1, 6)
FRAGMENT_SIZE = 16000
MAX_MESSAGE = 4 * 1024 * 1024
FLAG_FIRST, FLAG_LAST, FLAG_CONTROL, FLAG_ENCRYPTED = 1, 2, 4, 8

# Control channel messages.
VERSION_REQUEST, VERSION_RESPONSE, SSL_HANDSHAKE, AUTH_COMPLETE = 0x01, 0x02, 0x03, 0x04
SERVICE_DISCOVERY_REQUEST, SERVICE_DISCOVERY_RESPONSE = 0x05, 0x06
CHANNEL_OPEN_REQUEST, CHANNEL_OPEN_RESPONSE = 0x07, 0x08
PING_REQUEST, PING_RESPONSE = 0x0B, 0x0C
NAV_FOCUS_REQUEST, NAV_FOCUS_NOTIFICATION = 0x0D, 0x0E
BYEBYE_REQUEST, BYEBYE_RESPONSE = 0x0F, 0x10
AUDIO_FOCUS_REQUEST, AUDIO_FOCUS_NOTIFICATION = 0x12, 0x13

# Media, input and sensor channel messages.
MEDIA_DATA, MEDIA_CODEC_CONFIG = 0x0000, 0x0001
MEDIA_SETUP, MEDIA_START, MEDIA_STOP, MEDIA_CONFIG, MEDIA_ACK = 0x8000, 0x8001, 0x8002, 0x8003, 0x8004
VIDEO_FOCUS_REQUEST, VIDEO_FOCUS_NOTIFICATION = 0x8007, 0x8008
INPUT_EVENT, INPUT_BINDING_REQUEST, INPUT_BINDING_RESPONSE = 0x8001, 0x8002, 0x8003
SENSOR_START_REQUEST, SENSOR_START_RESPONSE, SENSOR_EVENT = 0x8001, 0x8002, 0x8003

CODEC_PCM, CODEC_H264 = 1, 3
AUDIO_SPEECH, AUDIO_SYSTEM, AUDIO_MEDIA = 1, 2, 3
SENSOR_NIGHT, SENSOR_DRIVING_STATUS = 10, 13
FRAME_RATE_30 = 2
FOCUS_STATES = {1: 1, 2: 2, 3: 2, 4: 3}  # gain, transient, transient-may-duck → gain/transient; release → loss
TOUCH_DOWN, TOUCH_UP, TOUCH_MOVE, TOUCH_CANCEL, POINTER_DOWN, POINTER_UP = 0, 1, 2, 3, 5, 6


@dataclass(frozen=True)
class Channels:
  sensors: int = 1
  video: int = 2
  input: int = 3
  media_audio: int = 4
  speech_audio: int = 5
  system_audio: int = 6
  bluetooth: int = 7


@dataclass(frozen=True)
class HeadUnitConfig:
  resolution: int            # VideoCodecResolutionType (3 = 1920x1080)
  width: int
  height: int
  margin_width: int
  margin_height: int
  density: int = 200
  audio: bool = False        # offer media and speech audio sinks (the phone otherwise keeps its own audio)
  bluetooth_address: str = ""
  name: str = "StarPilot"


class SessionClosed(EOFError):
  pass


def _message(*fields: bytes) -> bytes:
  return b"".join(fields)


def audio_config(rate: int, channels: int) -> bytes:
  return field(1, rate) + field(2, 16) + field(3, channels)


def service_discovery_response(cfg: HeadUnitConfig, ch: Channels = Channels()) -> bytes:
  services = []
  sensors = field(1, field(1, SENSOR_DRIVING_STATUS)) + field(1, field(1, SENSOR_NIGHT))
  services.append(field(1, ch.sensors) + field(2, sensors))
  video_config = (field(1, cfg.resolution) + field(2, FRAME_RATE_30) + field(3, cfg.margin_width) + field(4, cfg.margin_height) +
                  field(5, cfg.density) + field(10, CODEC_H264))
  video = field(1, CODEC_H264) + field(2, 0) + field(4, video_config) + field(5, 1)
  services.append(field(1, ch.video) + field(3, video))
  touch = field(1, cfg.width) + field(2, cfg.height)
  services.append(field(1, ch.input) + field(4, field(2, touch)))
  audio_sinks = [(ch.system_audio, AUDIO_SYSTEM, 16000, 1)]
  if cfg.audio:
    audio_sinks += [(ch.media_audio, AUDIO_MEDIA, 48000, 2), (ch.speech_audio, AUDIO_SPEECH, 16000, 1)]
  for channel, kind, rate, count in audio_sinks:
    sink = field(1, CODEC_PCM) + field(2, kind) + field(3, audio_config(rate, count))
    services.append(field(1, channel) + field(3, sink))
  if cfg.bluetooth_address:
    bluetooth = field(1, cfg.bluetooth_address) + field(2, 2) + field(2, 4)
    services.append(field(1, ch.bluetooth) + field(6, bluetooth))
  body = b"".join(field(1, s) for s in services)
  body += (field(2, "comma") + field(3, cfg.name) + field(4, "2026") + field(5, "starpilot") + field(6, 0) +
           field(7, "comma") + field(8, cfg.name) + field(9, "1") + field(10, "1.0") + field(11, 0) + field(12, 0) +
           field(14, cfg.name))
  return body


def touch_event(action: int, pointers: list[tuple[int, int, int]], action_index: int = 0) -> bytes:
  """InputReport with one touch event; pointers are (x, y, id) in touchscreen pixels."""
  touch = b"".join(field(1, field(1, x) + field(2, y) + field(3, pid)) for x, y, pid in pointers)
  touch += field(2, action_index) + field(3, action)
  return field(1, time.monotonic_ns() // 1000) + field(3, touch)


class HeadUnitSession:
  def __init__(self, sock, cfg: HeadUnitConfig, sink, log: Callable[[str], None] = print,
               certificate: Path = CERTIFICATE, private_key: Path = PRIVATE_KEY, channels: Channels = Channels(),
               handshake_timeout: float = 15.0):
    self.sock, self.cfg, self.sink, self.log, self.ch = sock, cfg, sink, log, channels
    self.handshake_timeout = handshake_timeout
    self.incoming, self.outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(certificate), str(private_key))
    self.tls = context.wrap_bio(self.incoming, self.outgoing, server_side=False)
    self.authenticated = False
    self.send_lock = threading.Lock()
    self.fragments: dict[int, bytearray] = {}
    self.buffer = bytearray()
    self.open_channels: set[int] = set()
    self.media_sessions: dict[int, int] = {}
    self.closed = threading.Event()
    self.close_lock = threading.Lock()
    self.notified = False
    self.phone_name = ""
    self.video_started = False

  # ---- framing ---------------------------------------------------------------------------------------------
  def send(self, channel: int, kind: int, body: bytes = b"", *, encrypted: bool = True, control: bool = False) -> None:
    data = struct.pack(">H", kind) + body
    with self.send_lock:
      for offset in range(0, len(data), FRAGMENT_SIZE):
        chunk = data[offset:offset + FRAGMENT_SIZE]
        flags = (FLAG_FIRST if offset == 0 else 0) | (FLAG_LAST if offset + len(chunk) == len(data) else 0)
        flags |= (FLAG_ENCRYPTED if encrypted else 0) | (FLAG_CONTROL if control else 0)
        if encrypted:
          self.tls.write(chunk)
          chunk = self.outgoing.read()
        header = struct.pack(">BBH", channel, flags, len(chunk))
        if flags & 3 == FLAG_FIRST:
          header += struct.pack(">I", len(data))
        self.sock.sendall(header + chunk)

  def _read_exact(self, size: int, deadline: float | None) -> bytes:
    while len(self.buffer) < size:
      if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise TimeoutError("Android Auto phone did not answer in time")
        self.sock.settimeout(remaining)
      else:
        self.sock.settimeout(None)
      chunk = self.sock.recv(65536)
      if not chunk:
        raise SessionClosed("the phone closed the connection")
      self.buffer += chunk
    data = bytes(self.buffer[:size])
    del self.buffer[:size]
    return data

  def receive(self, deadline: float | None = None) -> tuple[int, int, bytes]:
    while True:
      channel, flags, size = struct.unpack(">BBH", self._read_exact(4, deadline))
      segment = flags & 3
      total = struct.unpack(">I", self._read_exact(4, deadline))[0] if segment == FLAG_FIRST else None
      if total is not None and total > MAX_MESSAGE:
        raise ValueError("Android Auto message too large")
      payload = self._read_exact(size, deadline)
      if flags & FLAG_ENCRYPTED:
        self.incoming.write(payload)
        plain = bytearray()
        while True:
          try:
            part = self.tls.read(65536)
          except ssl.SSLWantReadError:
            break
          if not part:
            raise SessionClosed("TLS closed")
          plain += part
        payload = bytes(plain)
      if segment == FLAG_FIRST:
        self.fragments[channel] = bytearray(payload)
        continue
      if segment == 0:
        self.fragments.setdefault(channel, bytearray()).extend(payload)
        continue
      if segment == FLAG_LAST:
        payload = bytes(self.fragments.pop(channel, bytearray()) + payload)
      if len(payload) < 2:
        raise ValueError("Android Auto message without a type")
      return channel, struct.unpack(">H", payload[:2])[0], payload[2:]

  # ---- setup -----------------------------------------------------------------------------------------------
  def handshake(self) -> None:
    deadline = time.monotonic() + self.handshake_timeout
    self.send(0, VERSION_REQUEST, struct.pack(">HH", *VERSION), encrypted=False)
    channel, kind, data = self.receive(deadline)
    while channel == 0 and kind == PING_REQUEST:
      channel, kind, data = self.receive(deadline)
    if channel != 0 or kind != VERSION_RESPONSE or len(data) < 6:
      raise ValueError(f"expected the phone's version, got {channel}/{kind:#x}")
    major, minor, status = struct.unpack(">HHH", data[:6])
    self.log(f"android auto: phone protocol {major}.{minor} status {status}")
    while True:
      try:
        self.tls.do_handshake()
        done = True
      except ssl.SSLWantReadError:
        done = False
      out = self.outgoing.read()
      if out:
        self.send(0, SSL_HANDSHAKE, out, encrypted=False)
      if done:
        break
      channel, kind, data = self.receive(deadline)
      if channel != 0 or kind != SSL_HANDSHAKE:
        raise ValueError(f"expected TLS from the phone, got {channel}/{kind:#x}")
      self.incoming.write(data)
    self.send(0, AUTH_COMPLETE, field(1, 0), encrypted=False)
    self.authenticated = True
    self.log(f"android auto: authenticated ({self.tls.version()})")

  # ---- running ---------------------------------------------------------------------------------------------
  def run(self) -> None:
    try:
      while not self.closed.is_set():
        channel, kind, data = self.receive()
        self.dispatch(channel, kind, data)
    except (OSError, SessionClosed, ValueError, ssl.SSLError) as error:
      if not self.closed.is_set():
        self.log(f"android auto: session ended: {type(error).__name__}: {error}")
    finally:
      self.close()

  def dispatch(self, channel: int, kind: int, data: bytes) -> None:
    if kind == CHANNEL_OPEN_REQUEST and channel != 0:
      self.open_channels.add(channel)
      self.send(channel, CHANNEL_OPEN_RESPONSE, field(1, 0), control=True)
      return
    if channel == 0:
      self._control(kind, data)
    elif channel == self.ch.video:
      self._video(kind, data)
    elif channel in (self.ch.media_audio, self.ch.speech_audio, self.ch.system_audio):
      self._audio(channel, kind, data)
    elif channel == self.ch.input:
      if kind == INPUT_BINDING_REQUEST:
        self.send(channel, INPUT_BINDING_RESPONSE, field(1, 0))
    elif channel == self.ch.sensors:
      if kind == SENSOR_START_REQUEST:
        self.send(channel, SENSOR_START_RESPONSE, field(1, 0))
        self.send_sensors(night=False)

  def _control(self, kind: int, data: bytes) -> None:
    if kind == SERVICE_DISCOVERY_REQUEST:
      fields = parse_fields(data)
      self.phone_name = text(fields, 4) or text(fields, 5) or "Android phone"
      self.send(0, SERVICE_DISCOVERY_RESPONSE, service_discovery_response(self.cfg, self.ch))
      self.log(f"android auto: {self.phone_name} discovered our services")
    elif kind == PING_REQUEST:
      self.send(0, PING_RESPONSE, field(1, one(parse_fields(data), 1, 0) or 0))
    elif kind == AUDIO_FOCUS_REQUEST:
      request = one(parse_fields(data), 1, 1) or 1
      self.send(0, AUDIO_FOCUS_NOTIFICATION, field(1, FOCUS_STATES.get(request, 1)) + field(2, 0))
    elif kind == NAV_FOCUS_REQUEST:
      self.send(0, NAV_FOCUS_NOTIFICATION, field(1, 2))
    elif kind == BYEBYE_REQUEST:
      self.send(0, BYEBYE_RESPONSE)
      self.closed.set()

  def _ack(self, channel: int) -> None:
    self.send(channel, MEDIA_ACK, field(1, self.media_sessions.get(channel, 0)) + field(2, 1))

  def _media_setup(self, channel: int) -> None:
    self.send(channel, MEDIA_CONFIG, field(1, 2) + field(2, 1) + field(3, 0))

  def _video(self, kind: int, data: bytes) -> None:
    if kind == MEDIA_SETUP:
      self._media_setup(self.ch.video)
      self.send(self.ch.video, VIDEO_FOCUS_NOTIFICATION, field(1, 1) + field(2, 0))
    elif kind == MEDIA_START:
      self.media_sessions[self.ch.video] = signed(one(parse_fields(data), 1, 0)) or 0
      self.video_started = True
      self.sink.on_video_start(self)
    elif kind == MEDIA_CODEC_CONFIG:
      self.sink.on_video_config(data)
      self._ack(self.ch.video)
    elif kind == MEDIA_DATA:
      self.sink.on_video_frame(data[8:] if len(data) > 8 else b"")
      self._ack(self.ch.video)
    elif kind == VIDEO_FOCUS_REQUEST:
      self.send(self.ch.video, VIDEO_FOCUS_NOTIFICATION, field(1, 1) + field(2, 0))
    elif kind == MEDIA_STOP:
      self.video_started = False

  def _audio(self, channel: int, kind: int, data: bytes) -> None:
    name, rate, count = {self.ch.media_audio: ("media", 48000, 2), self.ch.speech_audio: ("speech", 16000, 1),
                         self.ch.system_audio: ("system", 16000, 1)}[channel]
    if kind == MEDIA_SETUP:
      self._media_setup(channel)
    elif kind == MEDIA_START:
      self.media_sessions[channel] = signed(one(parse_fields(data), 1, 0)) or 0
    elif kind == MEDIA_DATA:
      if self.cfg.audio and len(data) > 8:
        self.sink.on_audio(name, rate, count, data[8:])
      self._ack(channel)
    elif kind == MEDIA_STOP:
      self.sink.on_audio_stopped(name)

  # ---- to the phone ---------------------------------------------------------------------------------------
  def send_touch(self, action: int, pointers: list[tuple[int, int, int]], action_index: int = 0) -> None:
    if self.ch.input in self.open_channels:
      self.send(self.ch.input, INPUT_EVENT, touch_event(action, pointers, action_index))

  def send_sensors(self, night: bool) -> None:
    if self.ch.sensors not in self.open_channels:
      return
    batch = field(SENSOR_DRIVING_STATUS, field(1, 0)) + field(SENSOR_NIGHT, field(1, int(night)))
    self.send(self.ch.sensors, SENSOR_EVENT, batch)

  def request_keyframe(self) -> None:
    if self.ch.video in self.open_channels:
      self.send(self.ch.video, VIDEO_FOCUS_NOTIFICATION, field(1, 1) + field(2, 1))

  def close(self) -> None:
    with self.close_lock:
      first, self.notified = not self.notified, True
      self.closed.set()
    try:
      self.sock.close()
    except OSError:
      pass
    if first:
      self.sink.on_session_closed(self)
