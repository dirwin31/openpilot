"""iAP2 control session messages (CSM): 0x4040 frames holding u16-length parameters."""
from __future__ import annotations

import struct

CSM_START = 0x4040
CSM_HEADER = 6
PARAM_HEADER = 4
MAX_FRAME = 0xFFFF


class Iap2ProtocolError(IOError):
  pass


def encode_param(param_id: int, payload: bytes) -> bytes:
  if len(payload) > MAX_FRAME - PARAM_HEADER:
    raise ValueError("CSM parameter too large")
  return struct.pack(">HH", PARAM_HEADER + len(payload), param_id) + payload


def parse_params(body: bytes) -> list[tuple[int, bytes]]:
  params, offset = [], 0
  while offset < len(body):
    if len(body) - offset < PARAM_HEADER:
      raise Iap2ProtocolError("Truncated CSM parameter header")
    length, param_id = struct.unpack_from(">HH", body, offset)
    if length < PARAM_HEADER or length > len(body) - offset:
      raise Iap2ProtocolError(f"Invalid CSM parameter length {length}")
    params.append((param_id, bytes(body[offset + PARAM_HEADER:offset + length])))
    offset += length
  return params


class Body:
  """Builds a CSM body; group() nests another Body as one parameter."""

  def __init__(self):
    self.parts: list[bytes] = []

  def add(self, param_id: int, payload: bytes) -> Body:
    self.parts.append(encode_param(param_id, payload))
    return self

  def void(self, param_id: int) -> Body:
    return self.add(param_id, b"")

  def u8(self, param_id: int, value: int) -> Body:
    return self.add(param_id, struct.pack(">B", value))

  def u16(self, param_id: int, value: int) -> Body:
    return self.add(param_id, struct.pack(">H", value))

  def u32(self, param_id: int, value: int) -> Body:
    return self.add(param_id, struct.pack(">I", value))

  def string(self, param_id: int, value: str) -> Body:
    if "\0" in value:
      raise ValueError("CSM strings must not contain NUL")
    return self.add(param_id, value.encode() + b"\0")

  def strings(self, param_id: int, values) -> Body:
    return self.add(param_id, b"".join(v.encode() + b"\0" for v in values))

  def bytes(self, param_id: int, value: bytes) -> Body:
    return self.add(param_id, bytes(value))

  def u16_list(self, param_id: int, values) -> Body:
    return self.add(param_id, b"".join(struct.pack(">H", v) for v in values))

  def group(self, param_id: int, child: Body) -> Body:
    return self.add(param_id, child.encode())

  def encode(self) -> bytes:
    return b"".join(self.parts)


class Frame:
  def __init__(self, message_id: int, payload: bytes = b""):
    self.message_id = message_id
    self.payload = bytes(payload)

  @classmethod
  def build(cls, message_id: int, body: Body | None = None) -> Frame:
    return cls(message_id, body.encode() if body else b"")

  def params(self) -> list[tuple[int, bytes]]:
    return parse_params(self.payload)

  def first(self, param_id: int) -> bytes | None:
    return next((p for i, p in self.params() if i == param_id), None)

  def encode(self) -> bytes:
    if CSM_HEADER + len(self.payload) > MAX_FRAME:
      raise ValueError("CSM frame too large")
    return struct.pack(">HHH", CSM_START, CSM_HEADER + len(self.payload), self.message_id) + self.payload

  def __repr__(self) -> str:
    return f"Frame(0x{self.message_id:04x}, {len(self.payload)} bytes)"


class CsmFramer:
  """Reassembles CSM frames that the link layer may split across packets."""

  def __init__(self):
    self.buffer = bytearray()

  def offer(self, chunk: bytes) -> list[Frame]:
    self.buffer += chunk
    frames = []
    while True:
      while len(self.buffer) >= 2 and self.buffer[:2] != b"\x40\x40":
        del self.buffer[0]
      if len(self.buffer) < CSM_HEADER:
        return frames
      length = struct.unpack_from(">H", self.buffer, 2)[0]
      if length < CSM_HEADER:
        del self.buffer[0]
        continue
      if len(self.buffer) < length:
        return frames
      message_id = struct.unpack_from(">H", self.buffer, 4)[0]
      frames.append(Frame(message_id, bytes(self.buffer[CSM_HEADER:length])))
      del self.buffer[:length]


def read_string(payload: bytes) -> str:
  if not payload or payload[-1] != 0:
    raise Iap2ProtocolError("CSM string is not NUL terminated")
  return payload[:-1].decode(errors="replace")


def read_uint(payload: bytes) -> int:
  return int.from_bytes(payload, "big")
