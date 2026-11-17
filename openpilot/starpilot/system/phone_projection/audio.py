"""Phone audio into the comma's music output: the private Pulse server Spotify plays through.

Wherever music is set to play (a Bluetooth speaker or the car through StarPort), phone audio follows.
While a phone wants this, projectiond holds a request file so the music supervisor keeps the server up
even when Spotify itself is not running. Each audio type (media, alerts, navigation…) is its own Pulse
stream, so the server mixes them.
"""
from __future__ import annotations

import ctypes as C
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

REQUEST_PATH = Path("/tmp/starpilot-projection-audio")
RETRY_SECONDS = 3.0
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


class MusicOutput:
  def __init__(self, log: Callable[[str], None] = print, socket_path: Callable[[], Path] = music_socket,
               stream_factory=PulseStream, request_path: Path = REQUEST_PATH, sink: str = "spotify"):
    self.log, self.socket_path, self.stream_factory, self.request_path, self.sink = log, socket_path, stream_factory, request_path, sink
    self.streams: dict[str, PulseStream] = {}
    self.retry_at: dict[str, float] = {}
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
      stream = self.streams.get(name)
      if stream is not None and stream.format != (rate, channels):
        self.streams.pop(name).close()
        stream = None
      if stream is None:
        if time.monotonic() < self.retry_at.get(name, 0.0):
          return False
        server = self.socket_path()
        if not server.exists():
          self.retry_at[name] = time.monotonic() + RETRY_SECONDS
          return False
        try:
          stream = self.streams[name] = self.stream_factory(str(server), name, rate, channels, self.sink)
          self.log(f"projection audio: {name} playing through the music output")
        except OSError as error:
          self.retry_at[name] = time.monotonic() + RETRY_SECONDS
          self.log(f"projection audio: {name} unavailable: {error}")
          return False
      try:
        stream.write(pcm)
        return True
      except OSError as error:
        self.log(f"projection audio: {name} stopped: {error}")
        stream.close()
        self.streams.pop(name, None)
        self.retry_at[name] = time.monotonic() + RETRY_SECONDS
        return False

  def stop(self, name: str) -> None:
    with self.lock:
      stream = self.streams.pop(name, None)
      if stream is not None:
        stream.close()

  def stop_all(self) -> None:
    with self.lock:
      for stream in self.streams.values():
        stream.close()
      self.streams.clear()

  def close(self) -> None:
    self.request(False)
    self.stop_all()
