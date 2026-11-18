"""Phone video: hardware decode on a worker thread, newest picture published on VisionIPC for the UIs."""
from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

from openpilot.starpilot.system.phone_projection.decoder import DecoderError, FrameLayout, HardwareDecoder, split_nals
from openpilot.starpilot.system.phone_projection.ipc import VIPC_NAME

# VisionIPC stream slots: the UIs read the one the status file names.
CARPLAY_STREAM = 0
ANDROID_STREAM = 2
QUEUE_LIMIT = 120  # about two seconds; a stalled decoder is reset rather than buffering further
RECOVER_SECONDS = 1.0
KEYFRAME_RETRY_SECONDS = 1.0
OUTPUT_WAIT_MS = 10  # after a picture goes in, how long to wait for it to come out
IDLE_WAIT = 0.5


def is_keyframe(access_unit: bytes, hevc: bool = False) -> bool:
  for nal in split_nals(access_unit):
    kind = (nal[0] >> 1) & 0x3F if hevc else nal[0] & 0x1F
    if (16 <= kind <= 21) if hevc else kind == 5:
      return True
  return False


class VideoPublisher:
  def __init__(self, layouts: dict[int, FrameLayout], server_factory=None):
    if server_factory is None:
      from msgq.visionipc import VisionIpcServer
      server_factory = VisionIpcServer
    self.layouts = layouts
    self.server = server_factory(VIPC_NAME)
    for stream, layout in layouts.items():
      self.server.create_buffers_with_sizes(stream, 4, layout.width, layout.height, layout.size, layout.stride, layout.uv_offset)
    self.server.start_listener()
    self.lock = threading.Lock()
    self.frame_ids = dict.fromkeys(layouts, 0)

  def send(self, stream: int, layout: FrameLayout, frame: bytearray) -> bool:
    expected = self.layouts[stream]
    if (layout.stride, layout.uv_offset, layout.size) != (expected.stride, expected.uv_offset, expected.size):
      return False
    now = time.monotonic_ns()
    with self.lock:
      self.frame_ids[stream] += 1
      self.server.send(stream, memoryview(frame)[:expected.size], self.frame_ids[stream], now, now)
    return True


class VideoPipe:
  """Access units in (Annex-B), pictures out. One pipe per phone session."""

  def __init__(self, publisher: VideoPublisher, stream: int, width: int, height: int, *, hevc: bool = False,
               decoder_factory: Callable[..., HardwareDecoder] = HardwareDecoder, request_keyframe: Callable[[], None] | None = None,
               log: Callable[[str], None] = print):
    self.publisher, self.stream, self.width, self.height, self.hevc = publisher, stream, width, height, hevc
    self.decoder_factory, self.request_keyframe, self.log = decoder_factory, request_keyframe, log
    self.queue: queue.Queue[bytes | None] = queue.Queue()
    self.pending_config = b""
    self.last_config = b""
    self.awaiting_keyframe = False
    self.keyframe_asked = 0.0
    self.closed = threading.Event()
    self.frames = self.published = self.mismatched = self.pushed = 0
    self.last_frame = 0.0
    self.error = ""
    self.picture_size: tuple[int, int] | None = None
    self.thread = threading.Thread(target=self._run, name="projection-video", daemon=True)
    self.thread.start()

  def config(self, annexb: bytes) -> None:
    """Parameter sets ride with the next picture, the way the decoder expects them."""
    self.pending_config = self.last_config = annexb

  def submit(self, access_unit: bytes) -> None:
    if self.pending_config:
      access_unit, self.pending_config = self.pending_config + access_unit, b""
    if self.queue.qsize() >= QUEUE_LIMIT:
      self._wait_for_keyframe("decoder fell behind")
    if self.awaiting_keyframe:
      if not is_keyframe(access_unit, self.hevc):
        if time.monotonic() - self.keyframe_asked > KEYFRAME_RETRY_SECONDS:
          self._ask_keyframe()
        return
      self.awaiting_keyframe = False
      if self.last_config and not access_unit.startswith(self.last_config):
        access_unit = self.last_config + access_unit
    self.queue.put(access_unit)

  def _wait_for_keyframe(self, reason: str) -> None:
    """Drop everything queued; pictures that refer to dropped ones would show as corrupt."""
    try:
      while True:
        if self.queue.get_nowait() is None:
          self.queue.put(None)
          break
    except queue.Empty:
      pass
    if not self.awaiting_keyframe:
      self.log(f"projection video: {reason}, waiting for a keyframe")
    self.awaiting_keyframe = True
    self._ask_keyframe()

  def _ask_keyframe(self) -> None:
    self.keyframe_asked = time.monotonic()
    if self.request_keyframe:
      self.request_keyframe()

  def close(self) -> None:
    self.closed.set()
    self.queue.put(None)
    self.thread.join(timeout=2.0)

  def _run(self) -> None:
    while not self.closed.is_set():
      try:
        decoder = self.decoder_factory(self.width, self.height, hevc=self.hevc)
      except DecoderError as error:
        self._fail(f"decoder unavailable: {error}")
        self.closed.wait(RECOVER_SECONDS)
        continue
      try:
        self._decode(decoder)
      except DecoderError as error:
        self._fail(f"decode failed: {error}")
      finally:
        decoder.close()
      self.closed.wait(RECOVER_SECONDS)

  def _fail(self, message: str) -> None:
    if message != self.error:
      self.log(f"projection video: {message}")
    self.error = message
    self._wait_for_keyframe("restarting the decoder")

  def _decode(self, decoder) -> None:
    while not self.closed.is_set():
      waiting = self.pushed > self.frames
      try:
        unit = self.queue.get(timeout=0.02 if waiting else IDLE_WAIT)
      except queue.Empty:
        unit = b""
      if unit is None:
        return
      if unit:
        while not decoder.push(unit) and not self.closed.is_set():
          self._publish(decoder.pull(5))
        self.pushed += 1
        self._publish(decoder.pull(OUTPUT_WAIT_MS))
      else:
        self._publish(decoder.pull(0))

  def _publish(self, result) -> None:
    if result is None:
      return
    layout, frame, _, count = result
    self.frames += count
    self.last_frame = time.monotonic()
    size = (layout.width, layout.height)
    if size != self.picture_size:
      self.picture_size = size
      self.log(f"projection video: phone sends {size[0]}x{size[1]} (asked for {self.width}x{self.height})")
    if self.publisher.send(self.stream, layout, frame):
      self.published += 1
      self.mismatched = 0
      self.error = ""
    else:
      self.mismatched += 1
      if self.mismatched == 1:
        self.log(f"projection video: picture layout {layout} does not match the published buffers")
      self.error = f"The phone sent {size[0]}x{size[1]} instead of {self.width}x{self.height}."
