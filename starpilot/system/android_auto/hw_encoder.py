"""Hardware H.264 for Android Auto: ctypes wrapper around hw/libaa_encoder.so, and encoder selection.

The library owns a dedicated Qualcomm V4L2 encoder session (separate from
loggerd's), converts RGBA to NV12 with NEON straight into its ION buffer, and
returns one access unit per call. ``create_encoder`` validates it with a forced
keyframe before a session starts and falls back to libx264 on any failure. The
codec is never swapped mid-session.

Adapted from yummydirtx/openpilot ``tools/android_auto/{hardware_encode,live_encode}.py``
(MIT), pinned at 672a16f6183567c0ada53654f8527d97e1a483fa.
"""

from __future__ import annotations

import ctypes
import time
from pathlib import Path

from openpilot.starpilot.system.android_auto.encoder import MAX_ACCESS_UNIT, START_CODE, H264Encoder, nal_types

LIBRARY = Path(__file__).with_name("hw") / "libaa_encoder.so"
ABI = 2
MAX_WIDTH, MAX_HEIGHT, HW_FPS = 1280, 720, 30
AUD = b"\x00\x00\x00\x01\x09\xf0"


def normalize_access_unit(data: bytes, *, keyframe: bool) -> bytes:
  """Match the libx264 contract: exactly one leading AUD, one picture, SPS/PPS on keyframes."""
  if not data.startswith((b"\x00\x00\x01", b"\x00\x00\x00\x01")) or len(data) > MAX_ACCESS_UNIT:
    raise RuntimeError("Expected a bounded Annex B hardware frame")
  starts = list(START_CODE.finditer(data))
  units = []
  for index, match in enumerate(starts):
    end = starts[index + 1].start() if index + 1 < len(starts) else len(data)
    if match.end() >= end:
      raise RuntimeError("Empty hardware NAL unit")
    units.append((data[match.end()] & 31, data[match.start():end]))
  kinds = [kind for kind, _ in units]
  if kinds.count(1) + kinds.count(5) != 1:
    raise RuntimeError("Hardware encoder did not produce one picture")
  if keyframe and not {5, 7, 8}.issubset(kinds):
    raise RuntimeError("Hardware encoder did not produce an independently decodable keyframe")
  result = AUD + b"".join(unit for kind, unit in units if kind != 9)
  if len(result) > MAX_ACCESS_UNIT:
    raise RuntimeError("Hardware access unit too large")
  return result


class HardwareH264Encoder:
  backend = "qcom-v4l2"

  def __init__(self, width: int, height: int, fps: int = HW_FPS, bitrate_kbps: int = 6000, margin_height: int = 0,
               library: Path = LIBRARY):
    if not (0 < width <= MAX_WIDTH and 0 < height <= MAX_HEIGHT) or width % 2 or height % 2 or fps != HW_FPS:
      raise ValueError("Hardware encoder supports even sizes up to 1280x720 at 30 fps")
    if margin_height % 4:
      margin_height = 0  # only chroma-aligned black margins may be skipped
    self.width, self.height, self.fps = width, height, fps
    self.frame_index = 0
    self.last_encode_ms = 0.0
    self.handle = None
    self.lib = ctypes.CDLL(str(library))
    self.lib.aa_encoder_abi.restype = ctypes.c_int
    if self.lib.aa_encoder_abi() != ABI:
      raise RuntimeError("libaa_encoder.so ABI mismatch; rebuild")
    pointer = ctypes.c_void_p
    self.lib.aa_encoder_create.argtypes = [ctypes.c_int] * 5 + [pointer, ctypes.c_size_t]
    self.lib.aa_encoder_create.restype = pointer
    self.lib.aa_encoder_encode.argtypes = [pointer, pointer, ctypes.c_size_t, ctypes.c_int, pointer, ctypes.c_size_t,
                                           pointer, ctypes.c_size_t]
    self.lib.aa_encoder_encode.restype = ctypes.c_int
    self.lib.aa_encoder_destroy.argtypes = [pointer]
    self.lib.aa_encoder_destroy.restype = None
    self.error = ctypes.create_string_buffer(512)
    self.output = ctypes.create_string_buffer(MAX_ACCESS_UNIT)
    self.handle = self.lib.aa_encoder_create(width, height, fps, int(bitrate_kbps) * 1000, margin_height, self.error, len(self.error))
    if not self.handle:
      raise RuntimeError(self.error.value.decode("utf-8", "replace") or "hardware encoder unavailable")

  def encode_rgba(self, rgba, *, keyframe: bool = False) -> tuple[bytes, bool]:
    if not self.handle:
      raise RuntimeError("Encoder has been closed")
    if len(rgba) != self.width * self.height * 4:
      raise ValueError("Expected tightly packed RGBA at the negotiated size")
    started = time.monotonic()
    source = ctypes.c_char_p(bytes(rgba)) if not isinstance(rgba, bytes) else ctypes.c_char_p(rgba)
    force = keyframe or self.frame_index == 0
    size = self.lib.aa_encoder_encode(self.handle, source, len(rgba), int(force), self.output, len(self.output),
                                      self.error, len(self.error))
    if size < 0:
      raise RuntimeError(self.error.value.decode("utf-8", "replace"))
    data = normalize_access_unit(self.output.raw[:size], keyframe=force)
    self.frame_index += 1
    self.last_encode_ms = (time.monotonic() - started) * 1000
    return data, {5, 7, 8}.issubset(nal_types(data))

  def close(self) -> None:
    handle, self.handle = self.handle, None
    if handle:
      self.lib.aa_encoder_destroy(handle)


def create_encoder(width: int, height: int, *, preference: str, bitrate_kbps: int, margin_height: int,
                   software_fps: int, log) -> tuple[object, int]:
  """Return ``(encoder, fps)``: hardware at 30 fps when it works, else libx264."""
  if preference not in ("auto", "hardware", "software"):
    preference = "auto"
  if preference != "software":
    encoder = None
    try:
      if not LIBRARY.is_file():
        raise RuntimeError(f"{LIBRARY.name} not built")
      encoder = HardwareH264Encoder(width, height, bitrate_kbps=max(bitrate_kbps, 4000), margin_height=margin_height)
      # Exercise the driver and the keyframe contract before promising a session.
      encoder.encode_rgba(bytes(width * height * 4), keyframe=True)
      log("encoder", backend=encoder.backend, fps=HW_FPS)
      return encoder, HW_FPS
    except Exception as error:
      if encoder is not None:
        encoder.close()
      if preference == "hardware":
        raise
      log("encoder_fallback", reason=f"{type(error).__name__}: {str(error)[:160]}")
  encoder = H264Encoder(width, height, fps=software_fps, bitrate_kbps=bitrate_kbps)
  log("encoder", backend=encoder.backend, fps=software_fps)
  return encoder, software_fps
