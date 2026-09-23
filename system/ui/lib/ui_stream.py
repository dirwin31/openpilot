"""MJPEG mirror of the StarPilot UI over the local network, with optional remote touch.

Opt-in via ``STREAM=1``. This module owns:

* validated configuration parsed from the environment,
* a bounded ``ThreadingHTTPServer`` (8 handler permits, 3 stream slots),
* demand tracking (image streams, snapshot leases, telemetry interest),
* a single reused raw capture slot with strict ownership handoff
  (``FREE -> CAPTURING -> ENCODING -> FREE``),
* one demand-driven JPEG encoder worker,
* a bounded queue of whole remote gestures (``POST /input``) the UI replays.

Design constraints:

* No sockets, threads or image allocations happen at import time.
* All GPU readback stays on the caller's render thread. The worker never
  touches pyray, freed CFFI pointers, cereal readers or GL.
* Nothing is captured or encoded unless a browser is actively requesting
  images, so an enabled but idle streamer costs ~one attribute check per frame.
* numpy/OpenCV are imported lazily by the worker only after the first demand.

Adapted from peterclampton/Comma4-UI-Streamer (MIT), pinned at commit
4cd44f05083df45eee18c39af0d895329b07758a, with native lifecycle integration,
bounded resources, per-client sequence tracking and in-memory telemetry.
"""

from __future__ import annotations

import ipaddress
import json
import math
import socket
import threading
import time
import urllib.parse
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import IntEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import NamedTuple

from openpilot.common.swaglog import cloudlog

DEFAULT_BIND = "0.0.0.0"
DEFAULT_PORT = 8091
DEFAULT_QUALITY = 60
DEFAULT_FPS = 20  # matches the tici/tizi UI render rate, so pacing never beats against it
DEFAULT_WIDTH = 1280

TOTAL_HANDLER_PERMITS = 8
MAX_STREAMS = 3
REQUEST_QUEUE_SIZE = 8
SOCKET_TIMEOUT = 2.0
# An MJPEG write may legitimately block for a while on congested Wi-Fi. The 2 s
# request timeout would drop the viewer ("Can't reach the live UI") on a brief
# stall, so streams get a longer write deadline once the headers are sent.
STREAM_WRITE_TIMEOUT = 10.0
# Cap the kernel send buffer so a slow link drops frames at the source instead
# of queueing seconds of stale video (Linux autotunes it up to megabytes).
# The kernel doubles this value, so ~2-3 JPEG frames can be in flight.
STREAM_SNDBUF = 128 * 1024
# Pacing tolerance as a fraction of the capture interval. Render frames jitter by
# a few ms; without slack a frame arriving just before the deadline is skipped
# and the effective rate collapses to every other frame.
PACING_SLACK = 0.25

STREAM_FIRST_FRAME_WAIT = 2.0
STREAM_IDLE_DEADLINE = 5.0
SNAPSHOT_MAX_AGE = 1.0
SNAPSHOT_WAIT = 2.0
SNAPSHOT_LEASE = 3.0
TELEMETRY_MAX_AGE = 2.0
TELEMETRY_INTEREST = 3.0
IDLE_RELEASE_GRACE = 5.0
IDLE_SELF_STOP = 60.0  # no image demand and no telemetry interest for this long -> self-stop

MIN_PORT = 1024
MAX_PORT = 65535
MIN_QUALITY = 1
MAX_QUALITY = 95
MIN_FPS = 1
MAX_FPS = 30
MIN_WIDTH = 160
MAX_WIDTH = 2160

# Remote control. The viewer sends each gesture whole, when the finger lifts,
# and the UI replays it with its original timing. The comma therefore never
# holds a partial press: a gesture lost with the connection never arrives, so
# there is nothing to cancel, and gestures from different viewers cannot mix.
# Coordinates are normalized to the image, so the viewer never needs the UI
# resolution or the downscaled stream size.
CONTROL_HEADER = "X-UI-Stream-Control"
CONTROL_MAX_EVENTS = 400  # per gesture; the viewer thins moves to ~60 Hz
CONTROL_MAX_BODY = 48 * 1024
# Longest gesture accepted. It also bounds how long a physical touch that
# starts mid-replay is held back (see GuiApplication._arbitrate_input).
CONTROL_MAX_GESTURE = 3.0
CONTROL_QUEUE_GESTURES = 4
# A queued gesture the UI has not started by now is dropped: the screen was
# busy with physical touches, and a tap replayed long after it was made would
# land on whatever the screen shows by then.
CONTROL_GESTURE_MAX_WAIT = 2.0

_SCHEMA_VERSION = 1


class StreamConfigError(ValueError):
  """Raised when enabled streamer configuration is invalid."""


@dataclass(frozen=True)
class StreamConfig:
  bind: str = DEFAULT_BIND
  port: int = DEFAULT_PORT
  quality: int = DEFAULT_QUALITY
  fps: int = DEFAULT_FPS
  width: int = DEFAULT_WIDTH
  control: bool = True

  def __str__(self) -> str:
    control = "on" if self.control else "off"
    return f"{self.bind}:{self.port} quality={self.quality} fps={self.fps} width={self.width or 'source'} control={control}"


def _env_int(env: Mapping[str, str], key: str, default: int, low: int, high: int) -> int:
  raw = env.get(key)
  if raw is None or raw == "":
    return default
  try:
    value = int(raw)
  except ValueError as exc:
    raise StreamConfigError(f"{key}={raw!r} is not an integer") from exc
  if not (low <= value <= high):
    raise StreamConfigError(f"{key}={value} is outside {low}..{high}")
  return value


def parse_config(env: Mapping[str, str]) -> StreamConfig | None:
  """Return validated configuration, or ``None`` when killed by ``STREAM=0``.

  Raises :class:`StreamConfigError` for an invalid configuration. The streamer
  starts on demand (a browser opening the page posts ``UiStreamRequested``),
  so an unset ``STREAM`` means "available with defaults"; the literal ``0``
  is the kill switch. When killed, the remaining variables are ignored.
  """
  if env.get("STREAM", "1") == "0":
    return None

  bind = env.get("STREAM_BIND", DEFAULT_BIND).strip()
  try:
    ipaddress.IPv4Address(bind)
  except ValueError as exc:
    raise StreamConfigError(f"STREAM_BIND={bind!r} is not an IPv4 address") from exc

  port = _env_int(env, "STREAM_PORT", DEFAULT_PORT, MIN_PORT, MAX_PORT)
  quality = _env_int(env, "STREAM_QUALITY", DEFAULT_QUALITY, MIN_QUALITY, MAX_QUALITY)
  fps = _env_int(env, "STREAM_FPS", DEFAULT_FPS, MIN_FPS, MAX_FPS)
  width = _env_int(env, "STREAM_WIDTH", DEFAULT_WIDTH, 0, MAX_WIDTH)
  if width != 0 and width < MIN_WIDTH:
    raise StreamConfigError(f"STREAM_WIDTH={width} must be 0 or at least {MIN_WIDTH}")

  control = env.get("STREAM_CONTROL", "1") != "0"

  return StreamConfig(bind=bind, port=port, quality=quality, fps=fps, width=width, control=control)


class _SlotState(IntEnum):
  FREE = 0
  CAPTURING = 1
  ENCODING = 2


class _RawSlot:
  """The single reusable raw RGBA slot.

  The render thread owns it while ``CAPTURING``; the encoder worker owns it
  while ``ENCODING``. No other party may touch ``buffer`` in those states.
  """

  __slots__ = ("state", "buffer", "width", "height", "generation", "captured_at", "bottom_up")

  def __init__(self) -> None:
    self.state = _SlotState.FREE
    self.buffer: bytearray | None = None
    self.width = 0
    self.height = 0
    self.generation = 0
    self.captured_at = 0.0
    self.bottom_up = True


class ControlEvent(NamedTuple):
  """One remote pointer transition in normalized image coordinates (0..1)."""
  kind: str  # "down" | "move" | "up"
  x: float
  y: float
  t: float = 0.0  # seconds since the gesture's down


def _number(value: object) -> float | None:
  # bool is an int subclass; reject it so {"x": true} is not a coordinate.
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return None
  value = float(value)
  return value if math.isfinite(value) else None


def parse_gesture(raw: object) -> tuple[list[ControlEvent] | None, str]:
  """Validate one whole gesture: ``down``, any ``move``s, then ``up``.

  ``t`` is milliseconds from the viewer and must not go backwards. Returns the
  events rebased to seconds from the down, or ``(None, reason)``.
  """
  if not isinstance(raw, list) or not (2 <= len(raw) <= CONTROL_MAX_EVENTS):
    return None, f"a gesture has 2..{CONTROL_MAX_EVENTS} events"
  events: list[ControlEvent] = []
  for index, item in enumerate(raw):
    if not isinstance(item, dict):
      return None, "invalid event"
    expected = "down" if index == 0 else "up" if index == len(raw) - 1 else "move"
    if item.get("type") != expected:
      return None, "a gesture is down, moves, then up"
    x, y, t = _number(item.get("x")), _number(item.get("y")), _number(item.get("t"))
    if x is None or y is None or t is None:
      return None, "invalid event"
    events.append(ControlEvent(expected, min(1.0, max(0.0, x)), min(1.0, max(0.0, y)), t / 1000.0))
  start = events[0].t
  events = [event._replace(t=event.t - start) for event in events]
  if any(later.t < earlier.t for earlier, later in zip(events, events[1:], strict=False)):
    return None, "event times go backwards"
  if events[-1].t > CONTROL_MAX_GESTURE:
    return None, f"gesture longer than {CONTROL_MAX_GESTURE:g} s"
  return events, ""


def _is_local_host(host: str) -> bool:
  """True for an IP literal, localhost or an mDNS name.

  Rejecting other names defeats DNS rebinding: a page on attacker.example that
  re-resolves to the comma would otherwise be same-origin with the listener.
  """
  hostname = urllib.parse.urlsplit(f"//{host}").hostname or ""
  if hostname == "localhost" or hostname.endswith(".local"):
    return True
  try:
    ipaddress.ip_address(hostname)
  except ValueError:
    return False
  return True


class _Frame(NamedTuple):
  jpeg: bytes
  seq: int
  captured_at: float
  width: int
  height: int


def _load_viewer_html() -> bytes:
  return files(__package__).joinpath("ui_stream.html").read_bytes()


_viewer_html: bytes | None = None


def viewer_html() -> bytes:
  global _viewer_html
  if _viewer_html is None:
    _viewer_html = _load_viewer_html()
  return _viewer_html


class UiStream:
  """Bounded MJPEG server, demand tracker and encoder lifecycle."""

  def __init__(self, config: StreamConfig):
    self.config = config
    self._interval = 1.0 / config.fps

    self._lock = threading.RLock()
    self._frame_cv = threading.Condition(self._lock)
    self._encode_signal = threading.Event()
    self._stop_signal = threading.Event()

    self._slot = _RawSlot()
    self._generation = 0
    self._paused = False
    self._stopping = False
    self._bound = False
    self._serving = False

    self._latest_jpeg: bytes | None = None
    self._latest_seq = 0
    self._latest_at = 0.0
    self._latest_width = 0
    self._latest_height = 0
    self._source_width = 0
    self._source_height = 0

    self._active_streams = 0
    self._snapshot_waiters = 0
    self._snapshot_deadline = 0.0
    self._telemetry_deadline = 0.0
    self._telemetry_payload: bytes | None = None
    self._telemetry_at = 0.0

    self._next_capture_at = 0.0
    self._idle_release_at = 0.0
    self._idle_since = 0.0
    self._capture_failed = False
    self._capture_error = ""

    self._captures = 0
    self._encoded = 0
    self._published = 0
    self._dropped_busy = 0
    self._skipped_pacing = 0
    self._capture_errors = 0
    self._encode_errors = 0

    # Remote control: whole gestures waiting for the UI, with submit times.
    self._control_allowed = False
    self._control_reason = "not ready"
    self._control_gestures: deque[tuple[float, list[ControlEvent]]] = deque()
    self._control_accepted = 0
    self._control_rejected = 0
    self._control_dropped = 0

    self._worker: threading.Thread | None = None
    self._serve_thread: threading.Thread | None = None
    self._bgr = None
    self._flipped = None
    self._resized = None

    self._server = _BoundedHTTPServer(self, config)
    self._bound = True
    cloudlog.warning(f"UI streamer bound to {config}")

  @classmethod
  def from_env(cls, env: Mapping[str, str] | None = None) -> UiStream | None:
    import os
    try:
      config = parse_config(os.environ if env is None else env)
    except StreamConfigError as exc:
      cloudlog.error(f"UI streamer disabled: {exc}")
      return None
    if config is None:
      return None
    try:
      return cls(config)
    except OSError as exc:
      cloudlog.error(f"UI streamer disabled: cannot bind {config.bind}:{config.port}: {exc}")
      return None

  @property
  def port(self) -> int:
    return int(self._server.server_address[1])

  # ------------------------------------------------------------------ lifecycle

  def serve(self) -> None:
    """Start accepting connections. Idempotent."""
    with self._lock:
      if self._serving or self._stopping:
        return
      self._serving = True
      # Arm the idle clock so a start request that never gets a viewer still
      # self-stops (see IDLE_SELF_STOP).
      self._schedule_idle_release_locked(time.monotonic())
    # Only retain threads whose start succeeded. shutdown() would wait forever
    # if the HTTP thread existed but never entered serve_forever().
    worker = threading.Thread(target=self._encode_worker, name="ui_stream_encode", daemon=True)
    worker.start()
    self._worker = worker
    server_thread = threading.Thread(target=self._serve_forever, name="ui_stream_http", daemon=True)
    server_thread.start()
    self._serve_thread = server_thread

  def _serve_forever(self) -> None:
    try:
      self._server.serve_forever(poll_interval=0.5)
    except Exception as exc:  # pragma: no cover - defensive
      cloudlog.error(f"UI streamer server stopped: {exc}")

  def stop(self) -> None:
    """Idempotent shutdown. Safe even if the window is already gone."""
    with self._lock:
      if self._stopping:
        return
      self._stopping = True
      self._serving = False
      self._generation += 1
      self._active_streams = 0
      self._snapshot_waiters = 0
      self._snapshot_deadline = 0.0
      self._telemetry_deadline = 0.0
      self._idle_since = 0.0
      self._latest_jpeg = None
      if self._slot.state == _SlotState.FREE:
        self._slot.buffer = None
      self._frame_cv.notify_all()

    self._encode_signal.set()
    self._stop_signal.set()

    server = self._server
    server.close_connections()
    if self._serve_thread is not None:
      try:
        server.shutdown()
      except Exception:  # pragma: no cover - defensive
        pass
    try:
      server.server_close()
    except Exception:  # pragma: no cover - defensive
      pass

    for thread in (self._serve_thread, self._worker):
      if thread is not None:
        thread.join(timeout=3.0)

    worker_alive = self._worker is not None and self._worker.is_alive()
    with self._lock:
      if not worker_alive:
        self._bgr = self._flipped = self._resized = None
      if self._slot.state == _SlotState.FREE:
        self._slot.buffer = None
    self._serve_thread = None
    self._worker = None

  def pause(self) -> None:
    """Screen is off: invalidate the latest frame and stop capturing."""
    with self._frame_cv:
      if self._paused or self._stopping:
        return
      self._paused = True
      self._generation += 1
      self._latest_jpeg = None
      # Arm the grace period now: while the screen is off the render loop no
      # longer calls maybe_capture, but the worker still evaluates this deadline.
      self._schedule_idle_release_locked(time.monotonic())
      self._frame_cv.notify_all()

  def resume(self) -> None:
    with self._frame_cv:
      if not self._paused or self._stopping:
        return
      self._paused = False
      self._generation += 1
      # Wake starts a fresh idle window: a page that reconnects on wake must not
      # be killed by idle time accumulated while the screen was off.
      self._idle_since = 0.0
      self._frame_cv.notify_all()

  def is_paused(self) -> bool:
    with self._lock:
      return self._paused

  def is_stopping(self) -> bool:
    with self._lock:
      return self._stopping

  # ------------------------------------------------------------------- demand

  def image_demand_active(self, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    with self._lock:
      return self._image_demand_locked(now)

  def _image_demand_locked(self, now: float) -> bool:
    return not self._capture_failed and (self._active_streams > 0 or now < self._snapshot_deadline)

  def telemetry_due(self, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    with self._lock:
      return not self._stopping and now < self._telemetry_deadline

  def extend_telemetry_interest(self, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    with self._lock:
      self._telemetry_deadline = max(self._telemetry_deadline, now + TELEMETRY_INTEREST)

  def set_telemetry(self, payload: bytes, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    with self._lock:
      self._telemetry_payload = payload
      self._telemetry_at = now

  def begin_stream(self) -> bool:
    with self._lock:
      if self._stopping or self._active_streams >= MAX_STREAMS:
        return False
      self._active_streams += 1
      self._cancel_idle_release_locked()
      return True

  def end_stream(self) -> None:
    with self._lock:
      self._active_streams = max(0, self._active_streams - 1)
      self._schedule_idle_release_locked(time.monotonic())

  # ------------------------------------------------------------------ capture

  def output_size(self, width: int, height: int) -> tuple[int, int]:
    """Encoded dimensions for a ``width`` x ``height`` source (never upscaled).

    The render thread uses this to downscale on the GPU before readback, which
    is far cheaper than reading the full texture and resizing on the CPU.
    """
    target = self.config.width
    if not target or target >= width:
      return width, height
    new_w = max(2, target - (target % 2))
    new_h = max(2, int(round(height * new_w / width)))
    new_h -= new_h % 2
    return new_w, new_h

  def maybe_capture(self, now: float, width: int, height: int,
                    read: Callable[[bytearray], bool], bottom_up: bool = True,
                    source_size: tuple[int, int] | None = None) -> bool:
    """Reserve the slot and perform a render-thread readback if due.

    ``read`` receives the owned buffer and must fill ``width * height * 4``
    bytes of RGBA, returning ``True`` on success. It is only called on
    demand, on the caller's thread, and never while the slot is busy.
    ``bottom_up`` says the rows arrive in GL order (a plain render-texture
    readback) and need a vertical flip; a GPU blit can deliver them top-down.
    ``source_size`` is the UI resolution when the capture is already scaled.
    """
    with self._lock:
      if self._stopping or self._paused or self._capture_failed:
        return False
      if not self._image_demand_locked(now):
        self._schedule_idle_release_locked(now)
        return False
      slack = self._interval * PACING_SLACK
      if now + slack < self._next_capture_at:
        self._skipped_pacing += 1
        return False
      slot = self._slot
      if slot.state != _SlotState.FREE:
        self._dropped_busy += 1
        self._next_capture_at = now + self._interval - slack
        return False
      self._cancel_idle_release_locked()

      slot.state = _SlotState.CAPTURING
      slot.width = width
      slot.height = height
      slot.generation = self._generation
      slot.captured_at = now
      slot.bottom_up = bottom_up
      self._captures += 1
      # Advance on the schedule, not from "now", so render jitter does not
      # accumulate into drift; after a long gap, restart from now.
      self._next_capture_at += self._interval
      if self._next_capture_at <= now:
        self._next_capture_at = now + self._interval
      self._source_width, self._source_height = source_size or (width, height)
      buffer = slot.buffer
      if buffer is None or len(buffer) != width * height * 4:
        buffer = None

    if buffer is None:
      try:
        buffer = bytearray(width * height * 4)
      except (MemoryError, OverflowError):
        self._abort_capture(slot, error=True)
        cloudlog.error("UI streamer: unable to allocate capture buffer")
        return False
      with self._lock:
        if slot.state != _SlotState.CAPTURING:
          return False
        slot.buffer = buffer

    ok = False
    try:
      ok = bool(read(buffer))
    except Exception as exc:
      self._abort_capture(slot, error=True)
      cloudlog.error(f"UI streamer readback failed: {exc}")
      return False

    if not ok:
      self._abort_capture(slot, error=True)
      return False

    with self._lock:
      if slot.state != _SlotState.CAPTURING:
        return False
      slot.state = _SlotState.ENCODING
    self._encode_signal.set()
    return True

  def _abort_capture(self, slot: _RawSlot, error: bool) -> None:
    with self._lock:
      if slot.state == _SlotState.CAPTURING:
        slot.state = _SlotState.FREE
      if error:
        self._capture_errors += 1

  def fail_capture(self, reason: str) -> None:
    """Permanently disable capture (e.g. unsupported texture format).

    The server keeps serving status so clients can show the error, but no
    further readback is attempted.
    """
    with self._frame_cv:
      if self._capture_failed:
        return
      self._capture_failed = True
      self._capture_error = reason
      self._latest_jpeg = None
      self._frame_cv.notify_all()
    cloudlog.error(f"UI streamer capture disabled: {reason}")

  # ------------------------------------------------------------------- worker

  def _encode_worker(self) -> None:
    # numpy/OpenCV are intentionally NOT imported here: they are imported by
    # _encode() on the first real frame, so serve() stays cheap and a streamer
    # nobody watches never pays for the encoder dependencies.
    while not self._stop_signal.is_set():
      self._encode_signal.wait(0.5)
      if self._stop_signal.is_set():
        return
      self._encode_signal.clear()

      # The worker owns _bgr/_flipped/_resized, so idle release happens here and
      # not on the render thread. It also runs while the screen is off, because
      # this loop keeps waking even when rendering is paused.
      self._maybe_release_idle(time.monotonic())

      with self._lock:
        slot = self._slot
        if slot.state != _SlotState.ENCODING:
          continue
        buffer, width, height = slot.buffer, slot.width, slot.height
        captured_at, generation, bottom_up = slot.captured_at, slot.generation, slot.bottom_up

      jpeg = None
      encoded_width, encoded_height = width, height
      try:
        if buffer is not None:
          result = self._encode(buffer, width, height, bottom_up)
          if result is not None:
            # Downscaling changes the published dimensions; /status and the
            # viewer report what was actually encoded, not the capture size.
            jpeg, encoded_width, encoded_height = result
      except ImportError as exc:
        # Missing encoder dependencies are permanent; report through /status
        # instead of retrying every frame.
        self.fail_capture(f"encoder dependencies missing: {exc}")
      except Exception as exc:
        with self._lock:
          self._encode_errors += 1
        cloudlog.error(f"UI streamer encode failed: {exc}")

      if jpeg is not None:
        self._publish(jpeg, captured_at, generation, encoded_width, encoded_height)

      with self._lock:
        if self._slot is slot and slot.state == _SlotState.ENCODING:
          slot.state = _SlotState.FREE

  def _encode(self, buffer: bytearray, width: int, height: int,
              bottom_up: bool = True) -> tuple[bytes, int, int] | None:
    """Return the JPEG with the dimensions it was actually encoded at."""
    import cv2
    import numpy as np

    expected = width * height * 4
    if len(buffer) < expected:
      return None
    src = np.frombuffer(buffer, dtype=np.uint8, count=expected).reshape(height, width, 4)

    if self._bgr is None or self._bgr.shape[0] != height or self._bgr.shape[1] != width:
      self._bgr = np.empty((height, width, 3), np.uint8)
      self._flipped = np.empty((height, width, 3), np.uint8)
      self._resized = None

    cv2.cvtColor(src, cv2.COLOR_RGBA2BGR, self._bgr)
    if bottom_up:
      cv2.flip(self._bgr, 0, self._flipped)
      upright = self._flipped
    else:
      upright = self._bgr

    new_w, new_h = self.output_size(width, height)
    if new_w != width:
      if self._resized is None or self._resized.shape[0] != new_h or self._resized.shape[1] != new_w:
        self._resized = np.empty((new_h, new_w, 3), np.uint8)
      cv2.resize(upright, (new_w, new_h), self._resized, interpolation=cv2.INTER_AREA)
      frame = self._resized
    else:
      frame = upright

    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.config.quality])
    if not ok:
      return None
    return encoded.tobytes(), frame.shape[1], frame.shape[0]

  def _publish(self, jpeg: bytes, captured_at: float, generation: int, width: int, height: int) -> None:
    with self._frame_cv:
      if self._stopping or self._paused or generation != self._generation:
        return
      self._latest_jpeg = jpeg
      self._latest_seq += 1
      self._latest_at = captured_at
      self._latest_width = width
      self._latest_height = height
      self._published += 1
      self._encoded += 1
      self._frame_cv.notify_all()

  def publish_encoded_frame(self, jpeg: bytes, captured_at: float | None = None) -> None:
    """Publish an already-encoded JPEG.

    Used by tests and synthetic sources. Real captures flow through
    :meth:`maybe_capture` and the encoder worker.
    """
    now = time.monotonic() if captured_at is None else captured_at
    with self._lock:
      generation = self._generation
      width, height = self._source_width, self._source_height
    self._publish(jpeg, now, generation, width, height)

  # -------------------------------------------------------------- frame access

  def latest_frame(self, max_age: float) -> _Frame | None:
    now = time.monotonic()
    with self._lock:
      if self._paused or self._stopping or self._latest_jpeg is None:
        return None
      if now - self._latest_at > max_age:
        return None
      return self._frame_locked()

  def current_sequence(self) -> int:
    with self._lock:
      return self._latest_seq

  def acquire_snapshot_frame(self, timeout: float) -> _Frame | None:
    """Take a one-frame lease and wait for the next published frame.

    The lease ends when the wait does -- on the frame, on the timeout, or on a
    dropped connection -- so a single snapshot costs a single capture.
    ``SNAPSHOT_LEASE`` is only the upper bound for a waiter that never returns.
    Concurrent snapshots share one lease and one frame.
    """
    with self._lock:
      if self._stopping or self._capture_failed:
        return None
      now = time.monotonic()
      self._snapshot_waiters += 1
      self._snapshot_deadline = max(self._snapshot_deadline, now + SNAPSHOT_LEASE)
      self._cancel_idle_release_locked()
      after_seq = self._latest_seq
    try:
      return self.wait_for_frame(after_seq, timeout)
    finally:
      with self._lock:
        self._snapshot_waiters = max(0, self._snapshot_waiters - 1)
        if self._snapshot_waiters == 0:
          self._snapshot_deadline = 0.0
          self._schedule_idle_release_locked(time.monotonic())

  def wait_for_frame(self, after_seq: int, timeout: float) -> _Frame | None:
    """Wait for a frame newer than ``after_seq``, or ``None`` on timeout.

    A paused streamer is waited through rather than refused: holding a stream
    slot is the image demand that makes the screen policy resume rendering with
    the panel still off (``_stream_holds_render``). Refusing immediately would
    mean a viewer connecting while the display sleeps could never generate the
    demand that produces its first frame.
    """
    deadline = time.monotonic() + timeout
    with self._frame_cv:
      while True:
        if self._stopping or self._capture_failed:
          return None
        if self._latest_jpeg is not None and self._latest_seq > after_seq:
          return self._frame_locked()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          return None
        self._frame_cv.wait(remaining)

  def _frame_locked(self) -> _Frame:
    return _Frame(self._latest_jpeg, self._latest_seq, self._latest_at,
                  self._latest_width, self._latest_height)

  # ------------------------------------------------------------------ telemetry

  def telemetry_snapshot(self, max_age: float) -> bytes | None:
    now = time.monotonic()
    with self._lock:
      if self._stopping or self._telemetry_payload is None:
        return None
      if now - self._telemetry_at > max_age:
        return None
      return self._telemetry_payload

  # --------------------------------------------------------------------- idle

  def _cancel_idle_release_locked(self) -> None:
    self._idle_release_at = 0.0

  def _schedule_idle_release_locked(self, now: float) -> None:
    if self._idle_release_at == 0.0:
      self._idle_release_at = now + IDLE_RELEASE_GRACE

  def _maybe_release_idle(self, now: float) -> None:
    with self._lock:
      if self._idle_release_at == 0.0 or now < self._idle_release_at:
        return
      if self._image_demand_locked(now) or now < self._telemetry_deadline:
        self._cancel_idle_release_locked()
        return
      self._idle_release_at = 0.0
      self._latest_jpeg = None
      if self._slot.state == _SlotState.FREE:
        self._slot.buffer = None
        self._bgr = self._flipped = self._resized = None

  def self_stop_due(self, now: float | None = None) -> bool:
    """True once nothing has wanted the streamer for ``IDLE_SELF_STOP`` seconds.

    Polled by the render loop, which owns teardown. Any image demand or
    telemetry interest resets the clock, so a viewer sitting between frames
    never trips it; a start request that never gets a viewer does.
    """
    now = time.monotonic() if now is None else now
    with self._lock:
      if self._stopping or not self._serving:
        return False
      if self._image_demand_locked(now) or now < self._telemetry_deadline:
        self._idle_since = 0.0
        return False
      if self._idle_since == 0.0:
        self._idle_since = now
        return False
      return (now - self._idle_since) >= IDLE_SELF_STOP

  def _idle_seconds_locked(self, now: float) -> float:
    return 0.0 if self._idle_since == 0.0 else max(0.0, now - self._idle_since)

  # ------------------------------------------------------------------ control

  def set_control_allowed(self, allowed: bool, reason: str = "") -> None:
    """Render-thread policy gate (e.g. never while driving)."""
    with self._lock:
      self._control_allowed = bool(allowed)
      self._control_reason = "" if allowed else (reason or "unavailable")

  def _control_refusal_locked(self) -> str:
    if not self.config.control:
      return "remote control is disabled on this device (STREAM_CONTROL=0)"
    if self._stopping:
      return "streamer is stopping"
    if not self._control_allowed:
      return f"remote control unavailable: {self._control_reason}"
    return ""

  def submit_gesture(self, raw: object, now: float | None = None) -> tuple[int, str]:
    """Validate and queue one whole gesture. HTTP handler threads.

    Returns ``(http_status, error)``; ``error`` is empty on success.
    """
    now = time.monotonic() if now is None else now
    events, error = parse_gesture(raw)
    if events is None:
      return 400, error
    with self._lock:
      refusal = self._control_refusal_locked()
      if refusal:
        self._control_rejected += 1
        return 403, refusal
      if len(self._control_gestures) >= CONTROL_QUEUE_GESTURES:
        self._control_rejected += 1
        return 429, "the comma is still replaying earlier input"
      self._control_gestures.append((now, events))
      self._control_accepted += 1
    return 202, ""

  def take_gesture(self, now: float | None = None) -> list[ControlEvent] | None:
    """Next gesture for the UI to replay, or None. Render thread only.

    Refused control (policy gate, kill switch, shutdown) discards everything
    queued: nothing has reached the UI yet, so there is nothing to undo.
    Gestures that waited past ``CONTROL_GESTURE_MAX_WAIT`` are dropped.
    """
    now = time.monotonic() if now is None else now
    with self._lock:
      if self._control_refusal_locked():
        self._control_dropped += len(self._control_gestures)
        self._control_gestures.clear()
        return None
      while self._control_gestures:
        submitted_at, events = self._control_gestures.popleft()
        if now - submitted_at <= CONTROL_GESTURE_MAX_WAIT:
          return events
        self._control_dropped += 1
      return None

  # ------------------------------------------------------------------- status

  def status(self) -> dict[str, object]:
    now = time.monotonic()
    with self._lock:
      if self._stopping:
        state = "stopped"
      elif self._capture_failed:
        state = "error"
      elif self._paused:
        state = "paused"
      elif self._latest_jpeg is not None:
        state = "ready"
      else:
        state = "starting"
      frame_age = None if self._latest_jpeg is None else round((now - self._latest_at) * 1000)
      telemetry_age = None if self._telemetry_payload is None else round((now - self._telemetry_at) * 1000)
      return {
        "schemaVersion": _SCHEMA_VERSION,
        "state": state,
        "paused": self._paused,
        "stopping": self._stopping,
        "captureFailed": self._capture_failed,
        "captureError": self._capture_error,
        "frameSequence": self._latest_seq,
        "frameAgeMs": frame_age,
        "sourceWidth": self._source_width,
        "sourceHeight": self._source_height,
        "outputWidth": self._latest_width,
        "outputHeight": self._latest_height,
        "activeStreams": self._active_streams,
        "maxStreams": MAX_STREAMS,
        "telemetryAgeMs": telemetry_age,
        "telemetryInterested": now < self._telemetry_deadline,
        "idleSeconds": round(self._idle_seconds_locked(now), 1),
        "idleSelfStopSeconds": IDLE_SELF_STOP,
        "quality": self.config.quality,
        "fps": self.config.fps,
        "control": {
          "available": self.config.control,
          "allowed": not self._control_refusal_locked(),
          "reason": self._control_refusal_locked(),
          "queued": len(self._control_gestures),
          "maxGestureSeconds": CONTROL_MAX_GESTURE,
          "maxEvents": CONTROL_MAX_EVENTS,
          "accepted": self._control_accepted,
          "rejected": self._control_rejected,
          "dropped": self._control_dropped,
        },
        "counters": {
          "captured": self._captures,
          "encoded": self._encoded,
          "published": self._published,
          "droppedBusy": self._dropped_busy,
          "skippedPacing": self._skipped_pacing,
          "captureErrors": self._capture_errors,
          "encodeErrors": self._encode_errors,
        },
      }


class _BoundedHTTPServer(ThreadingHTTPServer):
  """ThreadingHTTPServer with a hard cap on concurrent handlers.

  The permit is acquired before a worker thread is spawned, so a flood of
  connections cannot create unbounded threads. When saturated the accepted
  socket is closed immediately.
  """

  daemon_threads = True
  allow_reuse_address = True
  request_queue_size = REQUEST_QUEUE_SIZE

  def __init__(self, stream: UiStream, config: StreamConfig):
    self.stream = stream
    self._permits = threading.BoundedSemaphore(TOTAL_HANDLER_PERMITS)
    self._conn_lock = threading.Lock()
    self._connections: set[socket.socket] = set()
    super().__init__((config.bind, config.port), _StreamRequestHandler)

  def process_request(self, request, client_address) -> None:
    if not self._permits.acquire(blocking=False):
      try:
        request.close()
      except OSError:
        pass
      return
    try:
      super().process_request(request, client_address)
    except Exception:
      self._permits.release()
      raise

  def process_request_thread(self, request, client_address) -> None:
    try:
      super().process_request_thread(request, client_address)
    finally:
      self._permits.release()

  def register_connection(self, connection: socket.socket) -> None:
    with self._conn_lock:
      self._connections.add(connection)

  def unregister_connection(self, connection: socket.socket) -> None:
    with self._conn_lock:
      self._connections.discard(connection)

  def close_connections(self) -> None:
    with self._conn_lock:
      connections = list(self._connections)
    for connection in connections:
      try:
        connection.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass


class _StreamRequestHandler(BaseHTTPRequestHandler):
  protocol_version = "HTTP/1.1"
  timeout = SOCKET_TIMEOUT

  def setup(self) -> None:
    super().setup()
    self.server.register_connection(self.connection)

  def finish(self) -> None:
    try:
      super().finish()
    except Exception:  # pragma: no cover - already-broken connections
      pass
    finally:
      self.server.unregister_connection(self.connection)

  def log_message(self, *args) -> None:
    pass

  @property
  def stream(self) -> UiStream:
    return self.server.stream

  # ------------------------------------------------------------------ routing

  def do_GET(self) -> None:
    self._route(head_only=False)

  def do_HEAD(self) -> None:
    # HEAD is deliberately supported only for finite, no-demand responses.
    # Stream/snapshot/telemetry are rejected so a HEAD can never create demand.
    self._route(head_only=True)

  def _route(self, head_only: bool) -> None:
    path = urllib.parse.urlsplit(self.path).path
    try:
      if path == "/":
        self._send_bytes(200, "text/html; charset=utf-8", viewer_html())
      elif path == "/status":
        self._handle_status()
      elif path == "/stream":
        if head_only:
          self._method_not_allowed()  # HEAD must not reserve demand
        else:
          self._handle_stream()
      elif path == "/snapshot":
        if head_only:
          self._method_not_allowed()
        else:
          self._handle_snapshot()
      elif path == "/telemetry":
        if head_only:
          self._method_not_allowed()
        else:
          self._handle_telemetry()
      elif path == "/input":
        self._method_not_allowed(allow="POST")
      else:
        self._send_plain(404, "not found")
    except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
      self.close_connection = True

  def do_POST(self) -> None:
    path = urllib.parse.urlsplit(self.path).path
    if path != "/input":
      self._method_not_allowed()
      return
    try:
      self._handle_control()
    except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
      self.close_connection = True

  def do_PUT(self) -> None:
    self._method_not_allowed()

  def do_DELETE(self) -> None:
    self._method_not_allowed()

  def do_PATCH(self) -> None:
    self._method_not_allowed()

  def do_OPTIONS(self) -> None:
    self._method_not_allowed()

  def _method_not_allowed(self, allow: str = "GET") -> None:
    try:
      self._send_plain(405, "method not allowed", headers={"Allow": allow})
    except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
      self.close_connection = True

  # ----------------------------------------------------------------- handlers

  def _handle_stream(self) -> None:
    stream = self.stream
    if not stream.begin_stream():
      self._send_plain(503, "stream limit reached", headers={"Retry-After": "1"})
      return
    try:
      # Serve an already-fresh frame immediately; otherwise wait for a genuinely
      # new one so a stale buffered frame is never sent as the opening part.
      frame = stream.latest_frame(SNAPSHOT_MAX_AGE)
      if frame is None:
        frame = stream.wait_for_frame(stream.current_sequence(), STREAM_FIRST_FRAME_WAIT)
      if frame is None:
        self._send_plain(503, "no frame available", headers={"Retry-After": "1"})
        return

      self.send_response(200)
      self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
      self.send_header("Cache-Control", "no-store")
      self.send_header("Connection", "close")
      self.end_headers()
      self.close_connection = True
      self._tune_stream_socket()

      last_seq = -1
      while True:
        if stream.is_stopping() or stream.is_paused():
          break
        self._write_frame(frame)
        last_seq = frame.seq
        frame = stream.wait_for_frame(last_seq, STREAM_IDLE_DEADLINE)
        if frame is None:
          break
    finally:
      stream.end_stream()

  def _tune_stream_socket(self) -> None:
    """Low-latency settings for a long-lived MJPEG response. Best effort."""
    connection = self.connection
    for level, option, value in ((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
                                 (socket.SOL_SOCKET, socket.SO_SNDBUF, STREAM_SNDBUF)):
      try:
        connection.setsockopt(level, option, value)
      except OSError:
        pass
    try:
      connection.settimeout(STREAM_WRITE_TIMEOUT)
    except OSError:
      pass

  def _write_frame(self, frame: _Frame) -> None:
    # One write per part: the unbuffered socket writer would otherwise issue
    # three sends, and Nagle + delayed ACK can hold the small trailer back.
    header = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame.jpeg)).encode() + b"\r\n\r\n"
    self.wfile.write(b"".join((header, frame.jpeg, b"\r\n")))
    self.wfile.flush()

  def _handle_snapshot(self) -> None:
    stream = self.stream
    frame = stream.latest_frame(SNAPSHOT_MAX_AGE)
    if frame is None:
      frame = stream.acquire_snapshot_frame(SNAPSHOT_WAIT)
    if frame is None:
      self._send_plain(503, "no frame available", headers={"Retry-After": "1"})
      return
    self._send_bytes(200, "image/jpeg", frame.jpeg)

  def _handle_telemetry(self) -> None:
    stream = self.stream
    stream.extend_telemetry_interest()
    payload = stream.telemetry_snapshot(TELEMETRY_MAX_AGE)
    if payload is None:
      self._send_plain(503, "no telemetry available", headers={"Retry-After": "1"})
      return
    self._send_bytes(200, "application/json; charset=utf-8", payload)

  def _control_request_error(self) -> tuple[int, str]:
    """Reject anything a hostile web page could send. Empty error = OK.

    A cross-site page can POST ``text/plain`` without a preflight, but not JSON
    with a custom header: those force a CORS preflight, and this server answers
    OPTIONS with 405, so the browser never sends the real request. The Origin
    and Host checks cover same-origin tricks (DNS rebinding) on top of that.
    """
    host = self.headers.get("Host") or ""
    if not _is_local_host(host):
      return 403, "host not allowed"
    origin = self.headers.get("Origin")
    if origin is not None and urllib.parse.urlsplit(origin).netloc != host:
      return 403, "cross-origin input refused"
    if self.headers.get(CONTROL_HEADER) != "1":
      return 403, f"missing {CONTROL_HEADER} header"
    content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
      return 415, "expected application/json"
    return 200, ""

  def _handle_control(self) -> None:
    status, error = self._control_request_error()
    if not error:
      try:
        length = int(self.headers.get("Content-Length") or "")
      except ValueError:
        status, error = 411, "Content-Length required"
      else:
        if not (0 < length <= CONTROL_MAX_BODY):
          status, error = 413, "body too large"
        else:
          try:
            payload = json.loads(self.rfile.read(length))
          except (ValueError, UnicodeDecodeError):
            payload = None
          if not isinstance(payload, dict):
            status, error = 400, "invalid JSON"
          else:
            status, error = self.stream.submit_gesture(payload.get("gesture"))
    body = json.dumps({"ok": not error, "error": error}).encode()
    self._send_bytes(status, "application/json; charset=utf-8", body)

  def _handle_status(self) -> None:
    payload = json.dumps(self.stream.status(), allow_nan=False).encode()
    self._send_bytes(200, "application/json; charset=utf-8", payload)

  # ----------------------------------------------------------------- response

  def _send_bytes(self, status: int, content_type: str, body: bytes,
                  headers: Mapping[str, str] | None = None) -> None:
    self.send_response(status)
    self.send_header("Content-Type", content_type)
    self.send_header("Content-Length", str(len(body)))
    self.send_header("Cache-Control", "no-store")
    self.send_header("Connection", "close")
    for key, value in (headers or {}).items():
      self.send_header(key, value)
    self.end_headers()
    self.close_connection = True
    if self.command != "HEAD":
      self.wfile.write(body)

  def _send_plain(self, status: int, message: str,
                  headers: Mapping[str, str] | None = None) -> None:
    self._send_bytes(status, "text/plain; charset=utf-8", message.encode(), headers)
