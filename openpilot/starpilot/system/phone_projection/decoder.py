"""Hardware H.264/H.265 decode of phone projection video into NV12 frames (ctypes over libprojection_decoder)."""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
from pathlib import Path

LIBRARY = Path(__file__).with_name("hw") / "libprojection_decoder.so"
START_CODE = b"\x00\x00\x00\x01"


@dataclass(frozen=True)
class FrameLayout:
  width: int
  height: int
  stride: int
  uv_offset: int
  size: int


class DecoderError(RuntimeError):
  pass


def _load(path: Path = LIBRARY) -> C.CDLL:
  lib = C.CDLL(str(path))
  lib.projection_decoder_abi.restype = C.c_int
  lib.projection_decoder_create.argtypes = [C.c_int, C.c_int, C.c_int, C.c_char_p, C.c_size_t]
  lib.projection_decoder_create.restype = C.c_void_p
  lib.projection_decoder_layout.argtypes = [C.c_void_p, C.POINTER(C.c_int)]
  lib.projection_decoder_push.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t, C.c_longlong, C.c_char_p, C.c_size_t]
  lib.projection_decoder_pull.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_size_t, C.POINTER(C.c_int),
                                          C.POINTER(C.c_longlong), C.c_char_p, C.c_size_t]
  lib.projection_decoder_destroy.argtypes = [C.c_void_p]
  lib.projection_decoder_nv12_layout.argtypes = [C.c_int, C.c_int, C.POINTER(C.c_int)]
  if lib.projection_decoder_abi() != 1:
    raise DecoderError("Unexpected projection decoder ABI")
  return lib


def nv12_layout(width: int, height: int, lib: C.CDLL | None = None) -> FrameLayout:
  """The Venus NV12 layout the decoder writes (and VisionIPC buffers must match)."""
  info = (C.c_int * 5)()
  (lib or _load()).projection_decoder_nv12_layout(width, height, info)
  return FrameLayout(*info)


class HardwareDecoder:
  """Single-threaded: push access units, pull the newest decoded frame."""

  def __init__(self, width: int, height: int, hevc: bool = False, lib: C.CDLL | None = None):
    self.visible = (width, height)
    self.lib = lib or _load()
    self.error = C.create_string_buffer(256)
    self.handle = self.lib.projection_decoder_create(int(hevc), width, height, self.error, len(self.error))
    if not self.handle:
      raise DecoderError(self.error.value.decode(errors="replace") or "decoder unavailable")
    self.info = (C.c_int * 5)()
    self.timestamp = C.c_longlong()
    self.layout = self._layout()
    self.frame = bytearray(self.layout.size)

  def _layout(self) -> FrameLayout:
    self.lib.projection_decoder_layout(self.handle, self.info)
    return self._visible(*self.info)

  def _visible(self, width: int, height: int, stride: int, uv_offset: int, size: int) -> FrameLayout:
    # The driver reports the coded size (16-pixel aligned); the picture is the size the stream was set up with.
    vw, vh = self.visible
    width = vw if 0 <= width - vw < 16 else width
    height = vh if 0 <= height - vh < 16 else height
    return FrameLayout(width, height, stride, uv_offset, size)

  def push(self, access_unit: bytes, timestamp_us: int = 0) -> bool:
    rc = self.lib.projection_decoder_push(self.handle, access_unit, len(access_unit), timestamp_us, self.error, len(self.error))
    if rc < 0:
      raise DecoderError(self.error.value.decode(errors="replace"))
    return rc == 1

  def pull(self, timeout_ms: int = 0) -> tuple[FrameLayout, bytearray, int, int] | None:
    """The newest decoded frame as (layout, NV12 bytes, timestamp, frames decoded since the last pull)."""
    layout = self._layout()
    if layout.size > len(self.frame):
      self.frame = bytearray(layout.size)
    buffer = (C.c_char * len(self.frame)).from_buffer(self.frame)
    rc = self.lib.projection_decoder_pull(self.handle, timeout_ms, buffer, len(self.frame), self.info,
                                          C.byref(self.timestamp), self.error, len(self.error))
    if rc < 0:
      raise DecoderError(self.error.value.decode(errors="replace"))
    if rc == 0:
      return None
    self.layout = self._visible(*self.info)
    return self.layout, self.frame, self.timestamp.value, rc

  def close(self) -> None:
    if self.handle:
      self.lib.projection_decoder_destroy(self.handle)
      self.handle = None

  def __del__(self):
    self.close()


def split_nals(stream: bytes) -> list[bytes]:
  """NAL units (without start codes) from an Annex-B byte stream."""
  nals, i, n = [], 0, len(stream)
  starts = []
  while i < n - 2:
    j = stream.find(b"\x00\x00\x01", i)
    if j < 0:
      break
    starts.append(j + 3)
    i = j + 3
  for k, start in enumerate(starts):
    end = starts[k + 1] - 3 if k + 1 < len(starts) else n
    nal = stream[start:end]
    if k + 1 < len(starts) and nal.endswith(b"\x00"):
      nal = nal[:-1]
    if nal:
      nals.append(nal)
  return nals


def access_units(stream: bytes, hevc: bool = False) -> list[bytes]:
  """Group an Annex-B stream into access units: parameter sets ride with the next picture."""
  units, pending = [], []
  for nal in split_nals(stream):
    kind = (nal[0] >> 1) & 0x3F if hevc else nal[0] & 0x1F
    vcl = kind < 32 if hevc else 1 <= kind <= 5
    first_slice = vcl and len(nal) > (2 if hevc else 1) and (nal[2] if hevc else nal[1]) & 0x80
    if vcl and first_slice and any(_is_vcl(p, hevc) for p in pending):
      units.append(b"".join(START_CODE + p for p in pending))
      pending = []
    if not vcl and any(_is_vcl(p, hevc) for p in pending):
      units.append(b"".join(START_CODE + p for p in pending))
      pending = []
    pending.append(nal)
  if pending:
    units.append(b"".join(START_CODE + p for p in pending))
  return units


def _is_vcl(nal: bytes, hevc: bool) -> bool:
  kind = (nal[0] >> 1) & 0x3F if hevc else nal[0] & 0x1F
  return kind < 32 if hevc else 1 <= kind <= 5
