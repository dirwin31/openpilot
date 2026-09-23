"""Bounded shared-memory handoff of UI frames from the render thread to android_autod.

One fixed ``/dev/shm`` file holds a small header and a single RGBA frame slot:

* The consumer (android_autod) creates the file, writes the frame geometry it
  wants (the negotiated video size and the receiver's margins) and keeps
  refreshing a ``demand_until`` deadline while it streams. The producer knows
  the UI's own size and letterboxes it into the visible area (``fit_content``).
* The producer (the UI render thread) checks that deadline with one struct read
  per frame. Only while it is in the future, and at most at the requested rate,
  does it GPU-scale the UI into the requested geometry, read it back and publish
  it under a sequence lock (odd while writing).
* There is exactly one slot: an unconsumed frame is simply overwritten, so a
  slow encoder or head unit can never build a backlog or stall rendering.

Timestamps are ``time.monotonic_ns()`` (CLOCK_MONOTONIC), comparable across
processes on Linux. No locks are shared across processes; a torn read is
detected by the sequence number and retried by the consumer.
"""

from __future__ import annotations

import mmap
import os
import struct
import time
from dataclasses import dataclass

DEFAULT_PATH = "/dev/shm/starpilot_android_auto_frame"
MAGIC = 0x53464141  # "AAFS"
VERSION = 1
HEADER_SIZE = 4096
MAX_WIDTH, MAX_HEIGHT = 1920, 1080
FILE_SIZE = HEADER_SIZE + MAX_WIDTH * MAX_HEIGHT * 4

# magic, version, seq, width, height, captured_ns, frame_id, demand_until_ns,
# req_width, req_height, margin_w, margin_h, reserved, reserved, interval_us, reserved
_HEADER = struct.Struct("<IIQIIQQQIIIIIIII")
_SEQ_OFFSET = 8
_DEMAND_OFFSET = 40
_SEQ = struct.Struct("<Q")
_DEMAND = struct.Struct("<Q")


@dataclass(frozen=True)
class FrameRequest:
  width: int
  height: int
  margin_w: int
  margin_h: int
  interval_us: int

  def content(self, source_w: int, source_h: int) -> tuple[int, int, int, int]:
    return fit_content(source_w, source_h, self.width, self.height, self.margin_w, self.margin_h)


@dataclass(frozen=True)
class Frame:
  data: bytes
  width: int
  height: int
  captured_ns: int
  frame_id: int


def fit_content(source_w: int, source_h: int, width: int, height: int, margin_w: int, margin_h: int) -> tuple[int, int, int, int]:
  """Largest undistorted rectangle for the source inside the receiver's visible area.

  Android Auto margins are split evenly on both sides of the encoded frame; the
  visible area is centred. Returns even-aligned ``(x, y, w, h)``.
  """
  visible_w, visible_h = width - margin_w, height - margin_h
  scale = min(visible_w / source_w, visible_h / source_h)
  w = max(2, int(source_w * scale) & ~1)
  h = max(2, int(source_h * scale) & ~1)
  x = ((width - w) // 2) & ~1
  y = ((height - h) // 2) & ~1
  return x, y, w, h


class FrameConsumer:
  """android_autod side: owns the file, publishes demand, reads the latest frame."""

  def __init__(self, path: str = DEFAULT_PATH):
    self.path = path
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
      os.ftruncate(fd, FILE_SIZE)
      self.mm = mmap.mmap(fd, FILE_SIZE)
    finally:
      os.close(fd)
    self.request: FrameRequest | None = None
    self.last_key = (0, 0)
    self._write_header(0)

  def _write_header(self, demand_until_ns: int) -> None:
    r = self.request or FrameRequest(0, 0, 0, 0, 0)
    seq = _SEQ.unpack_from(self.mm, _SEQ_OFFSET)[0] if self.mm[:4] == struct.pack("<I", MAGIC) else 0
    _HEADER.pack_into(self.mm, 0, MAGIC, VERSION, seq & ~1, 0, 0, 0, 0, demand_until_ns,
                      r.width, r.height, r.margin_w, r.margin_h, 0, 0, r.interval_us, 0)

  def configure(self, request: FrameRequest) -> None:
    if not (0 < request.width <= MAX_WIDTH and 0 < request.height <= MAX_HEIGHT) or request.width % 2 or request.height % 2:
      raise ValueError("Unsupported frame size")
    if not (0 <= request.margin_w < request.width and 0 <= request.margin_h < request.height) or request.interval_us <= 0:
      raise ValueError("Unsupported frame geometry")
    self.request = request
    self._write_header(0)

  def demand(self, seconds: float = 1.0) -> None:
    _DEMAND.pack_into(self.mm, _DEMAND_OFFSET, time.monotonic_ns() + int(seconds * 1e9))

  def release_demand(self) -> None:
    _DEMAND.pack_into(self.mm, _DEMAND_OFFSET, 0)

  def latest(self) -> Frame | None:
    """Return a frame newer than the last one returned, or None."""
    for _ in range(3):
      seq1 = _SEQ.unpack_from(self.mm, _SEQ_OFFSET)[0]
      if seq1 & 1:
        time.sleep(0.001)
        continue
      fields = _HEADER.unpack_from(self.mm, 0)
      width, height, captured_ns, frame_id = fields[3], fields[4], fields[5], fields[6]
      if fields[0] != MAGIC or frame_id == 0 or (frame_id, captured_ns) == self.last_key:
        return None
      request = self.request
      if request is None or (width, height) != (request.width, request.height):
        return None
      size = width * height * 4
      data = self.mm[HEADER_SIZE:HEADER_SIZE + size]
      if _SEQ.unpack_from(self.mm, _SEQ_OFFSET)[0] != seq1:
        continue
      self.last_key = (frame_id, captured_ns)
      return Frame(data, width, height, captured_ns, frame_id)
    return None

  def close(self) -> None:
    try:
      self.release_demand()
      self.mm.close()
    finally:
      try:
        os.unlink(self.path)
      except FileNotFoundError:
        pass


class FrameProducer:
  """Render-thread side. Every method is cheap when nobody is projecting."""

  REOPEN_INTERVAL = 1.0

  def __init__(self, path: str = DEFAULT_PATH):
    self.path = path
    self.mm: mmap.mmap | None = None
    self._inode = 0
    self._next_open_check = 0.0
    self._next_capture_ns = 0
    self.frame_id = 0
    self.captures = 0

  def _ensure_open(self, now: float) -> bool:
    if now < self._next_open_check:
      return self.mm is not None
    self._next_open_check = now + self.REOPEN_INTERVAL
    try:
      st = os.stat(self.path)
    except OSError:
      self._close()
      return False
    if self.mm is not None and st.st_ino == self._inode:
      return True
    self._close()
    if st.st_size < FILE_SIZE:
      return False
    try:
      fd = os.open(self.path, os.O_RDWR)
      try:
        self.mm = mmap.mmap(fd, FILE_SIZE)
      finally:
        os.close(fd)
      self._inode = st.st_ino
    except OSError:
      self.mm = None
      return False
    return True

  def _close(self) -> None:
    if self.mm is not None:
      try:
        self.mm.close()
      except (BufferError, ValueError):
        pass
    self.mm = None

  def pending_request(self, now: float | None = None) -> FrameRequest | None:
    """The requested geometry when demand is live, else None."""
    now = time.monotonic() if now is None else now
    if not self._ensure_open(now):
      return None
    mm = self.mm
    assert mm is not None
    fields = _HEADER.unpack_from(mm, 0)
    if fields[0] != MAGIC or fields[1] != VERSION or fields[7] <= int(now * 1e9):
      return None
    request = FrameRequest(fields[8], fields[9], fields[10], fields[11], fields[14])
    if not (0 < request.width <= MAX_WIDTH and 0 < request.height <= MAX_HEIGHT) or request.interval_us <= 0 or \
       request.margin_w >= request.width or request.margin_h >= request.height:
      return None
    return request

  def demand_active(self, now: float | None = None) -> bool:
    return self.pending_request(now) is not None

  def due(self, request: FrameRequest, now_ns: int) -> bool:
    return now_ns >= self._next_capture_ns - request.interval_us * 250  # 25% pacing slack, in ns

  def publish(self, request: FrameRequest, pixels, captured_ns: int) -> None:
    """Copy one tightly packed top-down RGBA frame of the requested size."""
    mm = self.mm
    size = request.width * request.height * 4
    if mm is None or len(pixels) < size:
      return
    seq = _SEQ.unpack_from(mm, _SEQ_OFFSET)[0]
    _SEQ.pack_into(mm, _SEQ_OFFSET, seq | 1)
    mm[HEADER_SIZE:HEADER_SIZE + size] = memoryview(pixels)[:size]
    self.frame_id += 1
    struct.pack_into("<IIQQ", mm, 16, request.width, request.height, captured_ns, self.frame_id)
    _SEQ.pack_into(mm, _SEQ_OFFSET, (seq | 1) + 1)
    self.captures += 1
    interval_ns = request.interval_us * 1000
    # Advance on the schedule so render jitter does not drift; after a gap, restart from now.
    self._next_capture_ns += interval_ns
    if self._next_capture_ns <= captured_ns:
      self._next_capture_ns = captured_ns + interval_ns


class SyntheticFrames:
  """Consumer-compatible moving test pattern, to prove the car link without the UI.

  Colour bars that slide one step per frame plus a frame counter make frozen,
  torn or stale video obvious on the car's screen.
  """

  def __init__(self):
    self.request: FrameRequest | None = None
    self.index = 0
    self.next_ns = 0

  def configure(self, request: FrameRequest) -> None:
    self.request = request

  def demand(self, seconds: float = 1.0) -> None:
    pass

  def release_demand(self) -> None:
    pass

  def latest(self) -> Frame | None:
    request = self.request
    now_ns = time.monotonic_ns()
    if request is None or now_ns < self.next_ns:
      return None
    self.next_ns = now_ns + request.interval_us * 1000
    import numpy as np
    width, height = request.width, request.height
    colors = np.array([[255, 255, 255, 255], [255, 255, 0, 255], [0, 255, 255, 255], [0, 255, 0, 255],
                       [255, 0, 255, 255], [255, 0, 0, 255], [0, 0, 255, 255], [40, 40, 40, 255]], np.uint8)
    columns = ((np.arange(width) + self.index * 8) * len(colors) // width) % len(colors)
    image = np.ascontiguousarray(np.broadcast_to(colors[columns], (height, width, 4)))
    x, y, w, h = request.content(width, height)
    image[:y], image[y + h:], image[:, :x], image[:, x + w:] = 0, 0, 0, 0
    try:
      import cv2
      scale = height / 240
      cv2.putText(image, f"StarPilot AA {self.index:06d}", (x + int(20 * scale), y + int(60 * scale)), cv2.FONT_HERSHEY_SIMPLEX,
                  scale, (0, 0, 0, 255), max(2, int(3 * scale)), cv2.LINE_AA)
    except ImportError:
      pass
    self.index += 1
    return Frame(image.tobytes(), width, height, now_ns, self.index)

  def close(self) -> None:
    pass
