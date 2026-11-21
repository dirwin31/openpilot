"""CarPlay AirPlay receiver: RTSP control channel, event channel, timing, and the main screen stream."""
from __future__ import annotations

import plistlib
import socket
from array import array
import struct
import threading
import time

from openpilot.starpilot.system.carplay.airplay.crypto import FrameCipher, hkdf, nonce64, open_
from openpilot.starpilot.system.carplay.airplay.info import (MAIN_UUID, STREAM_MAIN_SCREEN, TOUCH_HID_UID, ReceiverConfig,
                                                             info_plist, touch_report)
from openpilot.starpilot.system.carplay.airplay.pairing import PairSetup, PairVerify, PairingStore, auth_setup

PLIST = "application/x-apple-binary-plist"
TLV = "application/pairing+tlv8"
STATUS = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Internal Server Error"}
NTP_EPOCH = 2208988800
STREAM_DATA = 130
AUDIO_STREAMS = (100, 101, 102)
RTP_HEADER = 12
AUDIO_NONCE = 8
PCM_FORMATS = {0x10: (16000, 1), 0x20: (16000, 2), 0x400: (44100, 1), 0x800: (44100, 2), 0x4000: (48000, 1), 0x8000: (48000, 2)}
IAP_DATASTREAM_UUID = "E9459FD0-BCAD-4C45-820F-1E72447EF2F2"


def unsigned(value) -> int:
  return int(value) & 0xFFFFFFFFFFFFFFFF


def ntp_now() -> int:
  t = time.time() + NTP_EPOCH
  return (int(t) << 32) | int((t % 1) * (1 << 32))


class Request:
  def __init__(self, method: str, path: str, protocol: str, headers: dict, body: bytes):
    self.method, self.path, self.protocol, self.headers, self.body = method, path, protocol, headers, body


def parse_requests(buffer: bytearray) -> list[Request]:
  out = []
  while True:
    end = buffer.find(b"\r\n\r\n")
    if end < 0:
      return out
    lines = buffer[:end].decode(errors="replace").split("\r\n")
    headers = {}
    for line in lines[1:]:
      if ":" in line:
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", "0") or 0)
    if len(buffer) < end + 4 + length:
      return out
    body = bytes(buffer[end + 4:end + 4 + length])
    del buffer[:end + 4 + length]
    parts = lines[0].split(" ")
    if len(parts) >= 3:
      out.append(Request(parts[0], parts[1], parts[2], headers, body))


def build_response(req: Request, status: int = 200, body: bytes = b"", content_type: str | None = None) -> bytes:
  protocol = req.protocol if req.protocol.startswith(("RTSP", "HTTP")) else "RTSP/1.0"
  head = f"{protocol} {status} {STATUS.get(status, 'OK')}\r\n"
  if content_type:
    head += f"Content-Type: {content_type}\r\n"
  if "cseq" in req.headers:
    head += f"CSeq: {req.headers['cseq']}\r\n"
  head += "Server: AirTunes/950.7.1\r\n"
  head += f"Content-Length: {len(body)}\r\n\r\n"
  return head.encode() + body


def avcc_to_annexb(avcc: bytes) -> bytes:
  out = bytearray()
  sps_count = avcc[5] & 0x1F
  pos = 6
  for _ in range(sps_count):
    n = struct.unpack_from(">H", avcc, pos)[0]
    out += b"\0\0\0\1" + avcc[pos + 2:pos + 2 + n]
    pos += 2 + n
  pps_count = avcc[pos]
  pos += 1
  for _ in range(pps_count):
    n = struct.unpack_from(">H", avcc, pos)[0]
    out += b"\0\0\0\1" + avcc[pos + 2:pos + 2 + n]
    pos += 2 + n
  return bytes(out)


def length_prefixed_to_annexb(payload: bytes) -> bytes:
  out, pos = bytearray(), 0
  while pos + 4 <= len(payload):
    n = struct.unpack_from(">I", payload, pos)[0]
    if n == 0 or pos + 4 + n > len(payload):
      return payload
    out += b"\0\0\0\1" + payload[pos + 4:pos + 4 + n]
    pos += 4 + n
  return bytes(out) if pos == len(payload) else payload


def _listen_tcp(host: str) -> socket.socket:
  server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  server.bind((host, 0))
  server.listen(1)
  return server


def _shutdown(sock: socket.socket | None) -> None:
  """close() alone leaves a thread blocked in recv() on Linux; shutdown() wakes it so it can end."""
  if sock is None:
    return
  for step in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
    try:
      step()
    except OSError:
      pass


def keep_alive(sock: socket.socket) -> None:
  """End a silently gone phone in ~35 s: longer than the 30 s hotspot pause while the comma joins Wi-Fi."""
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
  for name, value in (("TCP_KEEPIDLE", 10), ("TCP_KEEPINTVL", 5), ("TCP_KEEPCNT", 5)):
    if hasattr(socket, name):
      sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
  buf = bytearray()
  while len(buf) < n:
    chunk = sock.recv(n - len(buf))
    if not chunk:
      return None
    buf += chunk
  return bytes(buf)


class ScreenStream:
  """Receives encrypted H.264 frames: 128-byte header (LE u32 size, opcode at 4) + ChaCha20-Poly1305 body."""

  def __init__(self, key: bytes, host: str, peer: str, on_config, on_frame, log):
    self.key, self.peer, self.on_config, self.on_frame, self.log = key, peer, on_config, on_frame, log
    self.server = _listen_tcp(host)
    self.port = self.server.getsockname()[1]
    self.closed = threading.Event()
    self.conn: socket.socket | None = None
    threading.Thread(target=self._run, name="carplay-screen", daemon=True).start()

  def close(self) -> None:
    self.closed.set()
    _shutdown(self.server)
    _shutdown(self.conn)

  def _run(self) -> None:
    try:
      conn, addr = self.server.accept()
    except OSError:
      return
    if addr[0] != self.peer or self.closed.is_set():
      conn.close()
      return
    self.conn = conn
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    keep_alive(conn)
    counter, frames = 0, 0
    try:
      while not self.closed.is_set():
        header = _recv_exact(conn, 128)
        if header is None:
          break
        size = struct.unpack_from("<I", header, 0)[0]
        body = _recv_exact(conn, size) if size else b""
        if body is None:
          break
        opcode = header[4]
        if opcode == 0 and len(body) >= 16:
          payload = open_(self.key, nonce64(counter), body, header)
          counter += 1
          frames += 1
          self.on_frame(length_prefixed_to_annexb(payload))
        elif opcode == 1:
          index = body.find(b"avcC")
          self.on_config("hevc" if b"hvcC" in body else "h264", body[index + 4:] if index >= 0 else body)
    except Exception as e:
      self.log(f"screen stream ended: {type(e).__name__}: {e}")
    finally:
      self.log(f"screen stream closed after {frames} frames")
      conn.close()


class IapTunnel:
  """iAP2 link bytes from the iPhone over Wi-Fi: encrypted frames of 32-byte-header packages ('comm' type)."""

  def __init__(self, key: bytes, host: str, peer: str, on_data, log, on_open=None):
    self.key, self.peer, self.on_data, self.log, self.on_open = key, peer, on_data, log, on_open
    self.server = _listen_tcp(host)
    self.port = self.server.getsockname()[1]
    self.closed = threading.Event()
    self.conn: socket.socket | None = None
    threading.Thread(target=self._run, name="carplay-iap-tunnel", daemon=True).start()

  def close(self) -> None:
    self.closed.set()
    _shutdown(self.server)
    _shutdown(self.conn)

  def _run(self) -> None:
    try:
      conn, addr = self.server.accept()
    except OSError:
      return
    if self.closed.is_set():
      conn.close()
      return
    self.conn = conn
    self.log("airplay iAP tunnel connected")
    if self.on_open is not None:
      self.on_open()
    cipher = FrameCipher(self.key, self.key)
    plain = bytearray()
    try:
      while not self.closed.is_set():
        data = conn.recv(16384)
        if not data:
          break
        plain += cipher.decrypt(data)
        while len(plain) >= 32:
          size = struct.unpack_from(">I", plain, 0)[0]
          if size < 32 or size > 4 << 20:
            raise ValueError(f"bad package size {size}")
          if len(plain) < size:
            break
          if struct.unpack_from(">I", plain, 16)[0] == 0x636F6D6D:
            self.on_data(bytes(plain[32:size]))
          del plain[:size]
    except Exception as e:
      self.log(f"airplay iAP tunnel ended: {type(e).__name__}: {e}")
    finally:
      self.log("airplay iAP tunnel closed")
      conn.close()


def pcm_format(bits: int) -> tuple[int, int] | None:
  for mask, fmt in PCM_FORMATS.items():
    if bits & mask:
      return fmt
  return None


def open_audio_packet(key: bytes, packet: bytes) -> bytes | None:
  """Decrypt one realtime audio packet and return its PCM as little-endian 16-bit samples."""
  if len(packet) < RTP_HEADER + 16 + AUDIO_NONCE:
    return None
  nonce = b"\0" * 4 + packet[-AUDIO_NONCE:]
  try:
    payload = open_(key, nonce, packet[RTP_HEADER:-AUDIO_NONCE], packet[4:RTP_HEADER])
  except Exception:
    return None
  samples = array("h", payload[:len(payload) & ~1])
  samples.byteswap()  # the phone sends big-endian 16-bit samples
  return samples.tobytes()


class AudioStream:
  """Realtime audio (RTP over UDP, ChaCha20-Poly1305 per packet) with a control port that is drained and ignored."""

  def __init__(self, key: bytes, host: str, audio_type: str, rate: int, channels: int, on_audio, log):
    self.key, self.audio_type, self.rate, self.channels, self.on_audio, self.log = key, audio_type, rate, channels, on_audio, log
    self.data = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.data.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    self.data.bind((host, 0))
    self.control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.control.bind((host, 0))
    self.port, self.control_port = self.data.getsockname()[1], self.control.getsockname()[1]
    self.closed = threading.Event()
    self.packets = self.failures = 0
    threading.Thread(target=self._run, name="carplay-audio", daemon=True).start()
    threading.Thread(target=self._drain, name="carplay-rtcp", daemon=True).start()

  def close(self) -> None:
    self.closed.set()
    for sock in (self.data, self.control):
      try:
        sock.close()
      except OSError:
        pass

  def _drain(self) -> None:
    while not self.closed.is_set():
      try:
        self.control.recv(2048)
      except OSError:
        return

  def _run(self) -> None:
    while not self.closed.is_set():
      try:
        packet = self.data.recv(4096)
      except OSError:
        return
      pcm = open_audio_packet(self.key, packet)
      if pcm is None:
        self.failures += 1
        if self.failures == 1:
          self.log(f"carplay audio {self.audio_type}: packet failed to decrypt")
        continue
      self.packets += 1
      if self.packets == 1:
        self.log(f"carplay audio {self.audio_type}: first packet {len(pcm)} bytes at {self.rate} Hz x{self.channels}")
      self.on_audio(self.audio_type, self.rate, self.channels, pcm)


class Timing:
  def __init__(self, host: str):
    self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.sock.bind((host, 0))
    self.port = self.sock.getsockname()[1]
    self.peer: tuple[str, int] | None = None
    self.closed = threading.Event()
    threading.Thread(target=self._rx, daemon=True).start()
    threading.Thread(target=self._tx, daemon=True).start()

  def _rx(self) -> None:
    while not self.closed.is_set():
      try:
        data, addr = self.sock.recvfrom(256)
      except OSError:
        return
      if len(data) >= 32 and data[1] == 210:
        reply = bytearray(32)
        reply[0], reply[1] = 0x80, 211
        reply[2:4] = struct.pack(">H", 7)
        reply[8:16] = data[24:32]
        reply[16:24] = struct.pack(">Q", ntp_now())
        reply[24:32] = struct.pack(">Q", ntp_now())
        self.sock.sendto(bytes(reply), addr)

  def _tx(self) -> None:
    while not self.closed.wait(3.0):
      if self.peer:
        req = bytearray(32)
        req[0], req[1] = 0x80, 210
        req[2:4] = struct.pack(">H", 7)
        req[24:32] = struct.pack(">Q", ntp_now())
        try:
          self.sock.sendto(bytes(req), self.peer)
        except OSError:
          pass

  def close(self) -> None:
    self.closed.set()
    self.sock.close()


class AirPlaySession:
  def __init__(self, conn: socket.socket, peer: tuple, host: str, cfg: ReceiverConfig, identity, pairings: PairingStore,
               mfi, sink, log):
    self.conn, self.peer, self.host, self.cfg = conn, peer[0], host, cfg
    self.log, self.mfi, self.sink = log, mfi, sink
    self.pair_setup = PairSetup(identity, pairings)
    self.pair_verify = PairVerify(identity, pairings)
    self.cipher: FrameCipher | None = None
    self.event_sock: socket.socket | None = None
    self.event_cipher: FrameCipher | None = None
    self.event_lock = threading.Lock()
    self.event_cseq = 0
    self.timing: Timing | None = None
    self.screen: ScreenStream | None = None
    self.iap_tunnel: IapTunnel | None = None
    self.audio: dict[tuple[int, str], AudioStream] = {}
    self.closed = threading.Event()

  def run(self) -> None:
    buffer = bytearray()
    try:
      while not self.closed.is_set():
        data = self.conn.recv(16384)
        if not data:
          self.log("airplay control: peer closed")
          break
        buffer += self.cipher.decrypt(data) if self.cipher else data
        for req in parse_requests(buffer):
          status, body, ctype = self._handle(req)
          wire = build_response(req, status, body, ctype)
          self.conn.sendall(self.cipher.encrypt(wire) if self.cipher else wire)
          if self.cipher is None and self.pair_verify.control_keys:
            self.cipher = FrameCipher(*self.pair_verify.control_keys)
            self.log("airplay control channel encrypted")
    except Exception as e:
      self.log(f"airplay control ended: {type(e).__name__}: {e}")
    finally:
      self.close()

  def close(self) -> None:
    if self.closed.is_set():
      return
    self.closed.set()
    for item in (self.screen, self.timing, self.iap_tunnel, *self.audio.values()):
      if item:
        item.close()
    self.audio.clear()
    _shutdown(self.event_sock)
    _shutdown(self.conn)
    self.sink.on_session_closed(self)

  def _handle(self, req: Request) -> tuple[int, bytes, str | None]:
    path = req.path.lower()
    if not path.endswith(("/feedback", "/command")):
      self.log(f"airplay rx {req.method} {req.path} body={len(req.body)}")
    if req.method == "SETUP":
      return self._setup(req)
    if req.method in ("SET_PARAMETER", "GET_PARAMETER", "FLUSH", "OPTIONS"):
      return 200, b"", None
    if req.method == "RECORD":
      self.log("airplay RECORD: session active")
      self.sink.on_session_active(self)
      return 200, b"", None
    if req.method == "TEARDOWN":
      self._teardown(req)
      return 200, b"", None
    if path.endswith("/pair-setup"):
      return 200, self.pair_setup.handle(req.body), TLV
    if path.endswith("/pair-verify"):
      return 200, self.pair_verify.handle(req.body), TLV
    if path.endswith("/auth-setup"):
      body = auth_setup(req.body, self.mfi)
      return (200, body, "application/octet-stream") if body else (400, b"", None)
    if path.endswith("/info"):
      if req.body:
        try:
          self.log(f"airplay /info request {plistlib.loads(req.body)}")
        except Exception:
          pass
      return 200, plistlib.dumps(info_plist(self.cfg), fmt=plistlib.FMT_BINARY), PLIST
    if path.endswith("/command"):
      self._command(req)
      return 200, b"", None
    return 200, b"", None

  def _setup(self, req: Request) -> tuple[int, bytes, str | None]:
    body = plistlib.loads(req.body)
    if "streams" in body:
      result = []
      for stream in body["streams"]:
        kind = int(stream.get("type", -1))
        self.log(f"airplay SETUP stream type={kind} keys={sorted(stream.keys())}")
        if kind == STREAM_MAIN_SCREEN and self.pair_verify.shared:
          connection_id = unsigned(stream.get("streamConnectionID", 0))
          key = hkdf(self.pair_verify.shared, f"DataStream-Salt{connection_id}".encode(),
                     b"DataStream-Output-Encryption-Key")
          if self.screen:
            self.screen.close()
          self.screen = ScreenStream(key, self.host, self.peer, self.sink.on_video_config, self.sink.on_video_frame,
                                     self.log)
          result.append({"type": kind, "dataPort": self.screen.port})
        elif kind == STREAM_DATA and str(stream.get("clientTypeUUID", "")).upper() == IAP_DATASTREAM_UUID \
            and self.pair_verify.shared:
          seed = unsigned(stream.get("seed", 0))
          key = hkdf(self.pair_verify.shared, f"DataStream-Salt{seed}".encode(), b"DataStream-Output-Encryption-Key")
          if self.iap_tunnel:
            self.iap_tunnel.close()
          opened = getattr(self.sink, "on_iap_tunnel_open", None)
          self.iap_tunnel = IapTunnel(key, self.host, self.peer, self.sink.on_iap_tunnel_data, self.log,
                                      on_open=(lambda opened=opened: opened(self)) if opened else None)
          entry = {"type": kind, "streamID": 1, "dataPort": self.iap_tunnel.port}
          if "streamConnectionID" in stream:
            entry["streamConnectionID"] = unsigned(stream["streamConnectionID"])
          result.append(entry)
        elif kind in AUDIO_STREAMS and self.pair_verify.shared and not self.cfg.disable_audio:
          entry = self._setup_audio(kind, stream)
          if entry:
            result.append(entry)
      return 200, plistlib.dumps({"streams": result}, fmt=plistlib.FMT_BINARY), PLIST
    self.log(f"airplay SETUP session name={body.get('name')!r} model={body.get('model')!r} "
             f"features={body.get('features')}")
    self.timing = Timing(self.host)
    peer_timing = int(body.get("timingPort", 0) or 0)
    if peer_timing:
      self.timing.peer = (self.peer, peer_timing)
    event_server = _listen_tcp(self.host)
    threading.Thread(target=self._event, args=(event_server,), name="carplay-event", daemon=True).start()
    response = {"timingPort": self.timing.port, "eventPort": event_server.getsockname()[1],
                "enabledFeatures": ["iAPChannel", "viewAreas"]}
    return 200, plistlib.dumps(response, fmt=plistlib.FMT_BINARY), PLIST

  def _setup_audio(self, kind: int, stream: dict) -> dict | None:
    audio_type = str(stream.get("audioType", "default"))
    fmt = pcm_format(int(stream.get("audioFormat", 0) or 0))
    self.log(f"carplay audio {audio_type}: type={kind} format={stream.get('audioFormat')} keys={sorted(stream.keys())}")
    if fmt is None:
      self.log(f"carplay audio {audio_type}: unsupported format {stream.get('audioFormat')}")
      return None
    connection_id = unsigned(stream.get("streamConnectionID", 0))
    key = hkdf(self.pair_verify.shared, f"DataStream-Salt{connection_id}".encode(), b"DataStream-Output-Encryption-Key")
    previous = self.audio.pop((kind, audio_type), None)
    if previous:
      previous.close()
    audio = AudioStream(key, self.host, audio_type, *fmt, self._on_audio, self.log)
    self.audio[(kind, audio_type)] = audio
    self.log(f"carplay audio {audio_type}: setup type={kind} {fmt[0]} Hz x{fmt[1]}")
    return {"type": kind, "dataPort": audio.port, "controlPort": audio.control_port, "streamConnectionID": connection_id}

  def _on_audio(self, audio_type: str, rate: int, channels: int, pcm: bytes) -> None:
    handler = getattr(self.sink, "on_audio", None)
    if handler:
      handler(audio_type, rate, channels, pcm)

  def _close_audio(self, types: list[int]) -> None:
    for key in [k for k in self.audio if not types or k[0] in types]:
      stream = self.audio.pop(key)
      stream.close()
      handler = getattr(self.sink, "on_audio_stopped", None)
      if handler:
        handler(key[1])

  def _teardown(self, req: Request) -> None:
    try:
      body = plistlib.loads(req.body) if req.body else {}
    except Exception:
      body = {}
    types = [int(s.get("type", -1)) for s in body.get("streams", [])] if body else []
    self.log(f"airplay TEARDOWN types={types or 'all'}")
    if (not types or STREAM_MAIN_SCREEN in types) and self.screen:
      self.screen.close()
      self.screen = None
    self._close_audio(types)

  def _command(self, req: Request) -> None:
    try:
      body = plistlib.loads(req.body) if req.body else {}
    except Exception:
      return
    self.log(f"airplay command {body.get('type')} {body.get('params', {})}")
    handler = getattr(self.sink, "on_command", None)
    if handler:
      handler(str(body.get("type", "")), body.get("params", {}))

  def _event(self, server: socket.socket) -> None:
    server.settimeout(30)
    try:
      sock, addr = server.accept()
    except OSError:
      self.log("airplay event channel: no connection")
      return
    finally:
      server.close()
    shared = self.pair_verify.shared
    write_key = hkdf(shared, b"Events-Salt", b"Events-Write-Encryption-Key")
    read_key = hkdf(shared, b"Events-Salt", b"Events-Read-Encryption-Key")
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    with self.event_lock:
      self.event_sock, self.event_cipher = sock, FrameCipher(read_key, write_key)
    self.log("airplay event channel connected")
    self.sink.on_event_ready(self)
    buffer = bytearray()
    try:
      while not self.closed.is_set():
        data = sock.recv(16384)
        if not data:
          break
        buffer += self.event_cipher.decrypt(data)
        for req in parse_requests(buffer):
          if req.method.startswith(("RTSP/", "HTTP/")):
            continue
          self.log(f"airplay event rx {req.method} {req.path}")
          with self.event_lock:
            sock.sendall(self.event_cipher.encrypt(build_response(req)))
    except OSError:
      pass
    self.log("airplay event channel closed")

  def send_command(self, command: dict) -> bool:
    with self.event_lock:
      if not self.event_sock or not self.event_cipher:
        return False
      self.event_cseq += 1
      body = plistlib.dumps(command, fmt=plistlib.FMT_BINARY)
      head = (f"POST /command RTSP/1.0\r\nContent-Type: {PLIST}\r\nContent-Length: {len(body)}\r\n"
              f"CSeq: {self.event_cseq}\r\n\r\n").encode()
      try:
        self.event_sock.sendall(self.event_cipher.encrypt(head + body))
        return True
      except OSError:
        return False

  def send_touch(self, contacts: list[tuple[int, float, float, bool]]) -> bool:
    """contacts: (slot, x, y, down) in CarPlay pixels."""
    return self.send_command({"type": "hidSendReport", "uuid": format(TOUCH_HID_UID, "x"),
                              "hidReport": touch_report(contacts)})

  def send_iap(self, data: bytes) -> bool:
    return self.send_command({"type": "iAPSendMessage", "params": {"data": data}})

  def request_keyframe(self) -> bool:
    return self.send_command({"type": "forceKeyFrame"})

  def set_night_mode(self, night: bool) -> bool:
    return self.send_command({"type": "setNightMode", "params": {"nightMode": night}})

  def show_view_area(self, index: int, count: int) -> bool:
    return self.send_command({"type": "updateViewArea", "params": {"uuid": MAIN_UUID, "viewAreaIndex": index,
                              "animationDurationMillis": 300,
                              "adjacentViewAreas": [i for i in range(count) if i != index]}})


class AirPlayServer:
  def __init__(self, host: str, port: int, cfg: ReceiverConfig, identity, pairings: PairingStore, mfi, sink, log):
    self.host, self.cfg, self.identity, self.pairings, self.mfi, self.sink, self.log = host, cfg, identity, pairings, mfi, sink, log
    self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self.server.bind((host, port))
    self.server.listen(4)
    self.port = self.server.getsockname()[1]
    self.session: AirPlaySession | None = None
    self.closed = threading.Event()
    threading.Thread(target=self._accept, name="carplay-airplay", daemon=True).start()

  def _accept(self) -> None:
    while not self.closed.is_set():
      try:
        conn, peer = self.server.accept()
      except OSError:
        return
      conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
      keep_alive(conn)
      self.log(f"airplay connection from {peer[0]}")
      session = AirPlaySession(conn, peer, self.host, self.cfg, self.identity, self.pairings, self.mfi, self.sink, self.log)
      self.session = session
      threading.Thread(target=session.run, name="carplay-control", daemon=True).start()

  def close(self) -> None:
    self.closed.set()
    self.server.close()
    if self.session:
      self.session.close()
