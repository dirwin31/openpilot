"""Local-network live telemetry transport for Galaxy clients."""

from __future__ import annotations

import base64
import json
import queue
import threading
import time
from collections.abc import Iterator
from typing import Any

from openpilot.starpilot.system.bluetooth.identity import telemetry_device_id
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.system.bluetooth.live import (
  LIVE_FRAME_SIZE,
  LIVE_PROTOCOL_VERSION,
  LiveTelemetryPublisher,
  live_metadata,
)


LAN_STREAM_HEARTBEAT_SECONDS = 15.0
LAN_STREAM_QUEUE_SIZE = 4


def _json(value: Any) -> str:
  return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sse(event: str, data: Any) -> bytes:
  return f"event: {event}\ndata: {_json(data)}\n\n".encode()


class LanTelemetryManager:
  """Share one live publisher between all local HTTP stream clients."""

  def __init__(self, params: Params | None = None, params_memory: Params | None = None,
               publisher_factory=LiveTelemetryPublisher, monotonic=time.monotonic):
    self.params = params or Params()
    self.params_memory = params_memory or Params(memory=True)
    self._publisher_factory = publisher_factory
    self._monotonic = monotonic
    self._lock = threading.RLock()
    self._publisher = None
    self._subscribers: set[queue.Queue[tuple[str, Any]]] = set()
    self._latest_at: float | None = None
    self._closed = False

  def start(self) -> None:
    with self._lock:
      if self._closed:
        raise RuntimeError("LAN telemetry manager is closed")
      if self._publisher is not None:
        self._publisher.start()  # Idempotent; restart if its worker failed.
        return
      publisher = self._publisher_factory(self._publish, self.params, self.params_memory)
      try:
        publisher.start()
      except Exception:
        try:
          publisher.close()
        except Exception:
          cloudlog.exception("Unable to clean up LAN live telemetry after startup failure")
        raise
      self._publisher = publisher

  def close(self) -> None:
    with self._lock:
      self._closed = True
      publisher = self._publisher
      self._publisher = None
      subscribers = tuple(self._subscribers)
      self._subscribers.clear()
    for subscriber in subscribers:
      self._put(subscriber, ("close", {}))
    if publisher is not None:
      publisher.close()

  def status(self) -> dict[str, Any]:
    with self._lock:
      latest_at = self._latest_at
      age = None if latest_at is None else max(0.0, self._monotonic() - latest_at)
      publisher = self._publisher
      running = publisher is not None and publisher.is_running()
      freshness = publisher.source_status() if publisher is not None else {"fresh": False, "source_age_sec": None}
    return {
      "available": True,
      "transport": "lan",
      "device": telemetry_device_id(self.params),
      "device_id": telemetry_device_id(self.params),
      "monotonic_ms": round(self._monotonic() * 1000) & 0xFFFFFFFF,
      **freshness,
      "protocol_version": LIVE_PROTOCOL_VERSION,
      "frame_size": LIVE_FRAME_SIZE,
      "frame_types": [1, 2],
      "rate_hz": 10,
      "health_rate_hz": 2,
      "stream": "/api/telematics/stream",
      "running": running,
      "latest_frame_age_sec": age,
    }

  def _put(self, subscriber: queue.Queue[tuple[str, Any]], item: tuple[str, Any]) -> None:
    try:
      subscriber.put_nowait(item)
    except queue.Full:
      try:
        subscriber.get_nowait()
      except queue.Empty:
        pass
      try:
        subscriber.put_nowait(item)
      except queue.Full:
        pass

  def _publish(self, frame: bytes) -> None:
    if len(frame) != LIVE_FRAME_SIZE:
      cloudlog.warning("Ignoring invalid LAN live frame (%d bytes)", len(frame))
      return
    encoded = base64.b64encode(frame).decode("ascii")
    with self._lock:
      if self._closed:
        return
      self._latest_at = self._monotonic()
      publisher = self._publisher
      if publisher is None:
        return
      metadata = live_metadata(self.params, publisher.details())
      # Each queued sample carries its metadata. Dropping superseded frames can
      # never discard the only copy of an alert/model/source update.
      payload = {"encoding": "base64", "data": encoded, **publisher.source_status()}
      for subscriber in tuple(self._subscribers):
        self._put(subscriber, ("sample", (payload, metadata)))

  def stream(self) -> Iterator[bytes]:
    subscriber: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=LAN_STREAM_QUEUE_SIZE)
    self.start()
    with self._lock:
      if self._closed:
        raise RuntimeError("LAN telemetry manager is closed")
      self._subscribers.add(subscriber)
      publisher = self._publisher
      details = publisher.details() if publisher is not None and hasattr(publisher, "details") else None
      initial_metadata = live_metadata(self.params, details)
      self._put(subscriber, ("metadata", initial_metadata))
    last_metadata = None
    try:
      while True:
        try:
          event, data = subscriber.get(timeout=LAN_STREAM_HEARTBEAT_SECONDS)
          if event == "close":
            return
          if event == "sample":
            payload, metadata = data
            if metadata != last_metadata:
              yield _sse("metadata", metadata)
              last_metadata = metadata
            yield _sse("frame", payload)
          else:
            last_metadata = data
            yield _sse(event, data)
        except queue.Empty:
          yield b": keepalive\n\n"
    finally:
      with self._lock:
        self._subscribers.discard(subscriber)
