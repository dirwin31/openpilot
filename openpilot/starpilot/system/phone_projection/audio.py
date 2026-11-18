"""Phone audio into the comma's music output: the private Pulse server Spotify plays through.

Wherever music is set to play (a Bluetooth speaker or the car through StarPort), phone audio follows.
While a phone wants this, projectiond holds a request file so the music supervisor keeps the server up
even when Spotify itself is not running. Each audio type (media, alerts, navigation…) is its own Pulse
stream, so the server mixes them.
"""
from __future__ import annotations

import ctypes as C
import os
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path

REQUEST_PATH = Path("/tmp/starpilot-projection-audio")
RETRY_SECONDS = 3.0
QUEUE_PACKETS = 50  # about half a second; when the output stalls the oldest audio is dropped
STOP = object()
SAMPLE_S16LE = 3


class SampleSpec(C.Structure):
  _fields_ = [("format", C.c_int), ("rate", C.c_uint32), ("channels", C.c_uint8)]


class BufferAttr(C.Structure):
  _fields_ = [(name, C.c_uint32) for name in ("maxlength", "tlength", "prebuf", "minreq", "fragsize")]


def music_socket() -> Path:
  from openpilot.starpilot.soloist import backend
  return backend.runtime_paths(backend.Layout.default())["socket"]


def music_library_dir() -> Path:
  from openpilot.starpilot.soloist import backend
  return backend.SERVER_ROOT / "lib"


class PulseStream:
  def __init__(self, server: str, name: str, rate: int, channels: int, sink: str):
    self.lib = C.CDLL("libpulse-simple.so.0")
    self.lib.pa_simple_new.argtypes = [C.c_char_p, C.c_char_p, C.c_int, C.c_char_p, C.c_char_p,
                                       C.POINTER(SampleSpec), C.c_void_p, C.POINTER(BufferAttr), C.POINTER(C.c_int)]
    self.lib.pa_simple_new.restype = C.c_void_p
    self.lib.pa_simple_write.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_int)]
    self.lib.pa_simple_write.restype = C.c_int
    self.lib.pa_simple_free.argtypes = [C.c_void_p]
    frame = 2 * channels
    attr = BufferAttr(rate * frame // 2, rate * frame // 10, 0xFFFFFFFF, rate * frame // 50, 0xFFFFFFFF)
    error = C.c_int()
    self.stream = self.lib.pa_simple_new(f"unix:{server}".encode(), b"StarPilot phone projection", 1, sink.encode(),
                                         name.encode(), C.byref(SampleSpec(SAMPLE_S16LE, rate, channels)), None,
                                         C.byref(attr), C.byref(error))
    if not self.stream:
      raise OSError(f"music output refused the stream ({error.value})")
    self.format = (rate, channels)

  def write(self, pcm: bytes) -> None:
    error = C.c_int()
    if self.lib.pa_simple_write(self.stream, pcm, len(pcm), C.byref(error)) < 0:
      raise OSError(f"music output closed ({error.value})")

  def close(self) -> None:
    if self.stream:
      self.lib.pa_simple_free(self.stream)
      self.stream = None


class StreamWriter:
  """One audio type on its own thread, so a slow output never holds up packet receive or the other types."""

  def __init__(self, output: MusicOutput, name: str):
    self.output, self.name = output, name
    self.queue: queue.Queue = queue.Queue(QUEUE_PACKETS)
    self.stream = None
    self.retry_at = 0.0
    self.thread = threading.Thread(target=self._run, name=f"projection-audio-{name}", daemon=True)
    self.thread.start()

  def put(self, item) -> None:
    while True:
      try:
        self.queue.put_nowait(item)
        return
      except queue.Full:
        try:
          self.queue.get_nowait()
        except queue.Empty:
          pass

  def _run(self) -> None:
    while True:
      item = self.queue.get()
      if item is None or item is STOP:
        self._close()
        if item is None:
          return
        continue
      self._write(*item)

  def _close(self) -> None:
    if self.stream is not None:
      self.stream.close()
      self.stream = None

  def _write(self, rate: int, channels: int, pcm: bytes) -> None:
    out = self.output
    if self.stream is not None and self.stream.format != (rate, channels):
      self._close()
    if self.stream is None:
      if time.monotonic() < self.retry_at:
        return
      server = out.socket_path()
      if not server.exists():
        self.retry_at = time.monotonic() + RETRY_SECONDS
        return
      try:
        self.stream = out.stream_factory(str(server), self.name, rate, channels, out.sink)
        out.log(f"projection audio: {self.name} playing through the music output")
      except OSError as error:
        self.retry_at = time.monotonic() + RETRY_SECONDS
        out.log(f"projection audio: {self.name} unavailable: {error}")
        return
    try:
      self.stream.write(pcm)
    except OSError as error:
      out.log(f"projection audio: {self.name} stopped: {error}")
      self._close()
      self.retry_at = time.monotonic() + RETRY_SECONDS


class MusicOutput:
  def __init__(self, log: Callable[[str], None] = print, socket_path: Callable[[], Path] = music_socket,
               stream_factory=PulseStream, request_path: Path = REQUEST_PATH, sink: str = "spotify"):
    self.log, self.socket_path, self.stream_factory, self.request_path, self.sink = log, socket_path, stream_factory, request_path, sink
    self.writers: dict[str, StreamWriter] = {}
    self.lock = threading.Lock()
    self.wanted = False

  def request(self, wanted: bool) -> None:
    """Ask the music supervisor to keep its audio server running for the phone."""
    if wanted == self.wanted:
      return
    self.wanted = wanted
    try:
      if wanted:
        self.request_path.write_text(str(os.getpid()))
      else:
        self.request_path.unlink(missing_ok=True)
    except OSError:
      pass
    if not wanted:
      self.stop_all()

  def write(self, name: str, rate: int, channels: int, pcm: bytes) -> bool:
    if not self.wanted:
      return False
    with self.lock:
      writer = self.writers.get(name)
      if writer is None:
        writer = self.writers[name] = StreamWriter(self, name)
    writer.put((rate, channels, pcm))
    return True

  def stop(self, name: str) -> None:
    with self.lock:
      writer = self.writers.get(name)
    if writer is not None:
      writer.put(STOP)

  def stop_all(self) -> None:
    with self.lock:
      writers = list(self.writers.values())
    for writer in writers:
      writer.put(STOP)

  def close(self) -> None:
    self.request(False)
    with self.lock:
      writers, self.writers = list(self.writers.values()), {}
    for writer in writers:
      writer.put(None)
    for writer in writers:
      writer.thread.join(timeout=1.0)
