"""Phone video: hardware decode on a worker thread, newest picture published on VisionIPC for the UIs."""
from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

from openpilot.starpilot.system.phone_projection.decoder import DecoderError, FrameLayout, HardwareDecoder
from openpilot.starpilot.system.phone_projection.ipc import VIPC_NAME

# VisionIPC stream slots: the UIs read the one the status file names.
CARPLAY_STREAM = 0
ANDROID_STREAM = 2
QUEUE_LIMIT = 120  # about two seconds; a stalled decoder is reset rather than buffering further


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
    self.closed = threading.Event()
    self.frames = self.published = self.mismatched = 0
    self.last_frame = 0.0
    self.thread = threading.Thread(target=self._run, name="projection-video", daemon=True)
    self.thread.start()

  def config(self, annexb: bytes) -> None:
    """Parameter sets ride with the next picture, the way the decoder expects them."""
    self.pending_config = annexb

  def submit(self, access_unit: bytes) -> None:
    if self.pending_config:
      access_unit, self.pending_config = self.pending_config + access_unit, b""
    if self.queue.qsize() >= QUEUE_LIMIT:
      self._drop_backlog()
    self.queue.put(access_unit)

  def _drop_backlog(self) -> None:
    try:
      while True:
        self.queue.get_nowait()
    except queue.Empty:
      pass
    self.log("projection video: decoder fell behind, waiting for a keyframe")
    if self.request_keyframe:
      self.request_keyframe()

  def close(self) -> None:
    self.closed.set()
    self.queue.put(None)
    self.thread.join(timeout=2.0)

  def _run(self) -> None:
    try:
      decoder = self.decoder_factory(self.width, self.height, hevc=self.hevc)
    except DecoderError as error:
      self.log(f"projection video: decoder unavailable: {error}")
      return
    try:
      while not self.closed.is_set():
        try:
          unit = self.queue.get(timeout=0.02)
        except queue.Empty:
          unit = b""
        if unit is None:
          break
        if unit:
          while not decoder.push(unit) and not self.closed.is_set():
            self._publish(decoder.pull(5))
        self._publish(decoder.pull(0))
    except DecoderError as error:
      self.log(f"projection video: decode failed: {error}")
    finally:
      decoder.close()

  def _publish(self, result) -> None:
    if result is None:
      return
    layout, frame, _, count = result
    self.frames += count
    self.last_frame = time.monotonic()
    if self.publisher.send(self.stream, layout, frame):
      self.published += 1
    else:
      self.mismatched += 1
      if self.mismatched == 1:
        self.log(f"projection video: picture layout {layout} does not match the published buffers")
