"""Runs the iAP2 link over a byte stream (RFCOMM or the AirPlay tunnel) and exchanges CSM frames."""
from __future__ import annotations

import queue
import select
import socket
import threading
import time

from openpilot.starpilot.system.carplay.iap2.link import CONTROL_SESSION_ID, LinkConfig, LinkEngine, WIRELESS_CONFIG
from openpilot.starpilot.system.carplay.iap2.wire import CsmFramer, Frame


def now_ms() -> int:
  return time.monotonic_ns() // 1_000_000


class Iap2Session:
  def __init__(self, sock: socket.socket, config: LinkConfig = WIRELESS_CONFIG, initiator: bool = False, log=print):
    self.sock = sock
    self.engine = LinkEngine(config)
    self.initiator = initiator
    self.log = log
    self.framer = CsmFramer()
    self.incoming: queue.Queue[Frame | None] = queue.Queue()
    self.outgoing: queue.Queue[bytes] = queue.Queue()
    self.ready = threading.Event()
    self.closed = threading.Event()
    self.dead_reason: str | None = None
    self.wake_r, self.wake_w = socket.socketpair()
    self.thread = threading.Thread(target=self._pump, name="carplay-iap2", daemon=True)

  def start(self) -> Iap2Session:
    self.thread.start()
    return self

  def send(self, frame: Frame) -> None:
    if self.closed.is_set():
      raise ConnectionError(f"iAP2 session closed: {self.dead_reason}")
    self.outgoing.put(frame.encode())
    self.wake_w.send(b"\0")

  def recv(self, timeout: float) -> Frame | None:
    try:
      frame = self.incoming.get(timeout=timeout)
    except queue.Empty:
      return None
    if frame is None:
      self.incoming.put(None)
    return frame

  def close(self) -> None:
    self.closed.set()
    try:
      self.wake_w.send(b"\0")
    except OSError:
      pass
    self.thread.join(timeout=2)
    for s in (self.sock, self.wake_r, self.wake_w):
      try:
        s.close()
      except OSError:
        pass

  def _flush(self) -> None:
    data = self.engine.take_output()
    if data:
      self.sock.sendall(data)

  def _drain_outgoing(self) -> None:
    while self.engine.writable and not self.outgoing.empty():
      data = self.outgoing.get_nowait()
      chunk = max(1, self.engine.peer_max_payload())
      for i in range(0, len(data), chunk):
        self.engine.send(CONTROL_SESSION_ID, data[i:i + chunk], now_ms())

  def _events(self) -> bool:
    while (event := self.engine.poll_event()) is not None:
      if event.kind == "control":
        for frame in self.framer.offer(event.data):
          self.incoming.put(frame)
      elif event.kind == "writable" and event.value:
        self.ready.set()
      elif event.kind == "session":
        self.log(f"iap2 session {event.session_id} data bytes={len(event.data)} (ignored)")
      elif event.kind == "dead":
        self.dead_reason = event.reason
        return False
    return True

  def _pump(self) -> None:
    try:
      self.engine.start(self.initiator, now_ms())
      while not self.closed.is_set():
        self._drain_outgoing()
        self.engine.advance(now_ms())
        self._flush()
        if not self._events():
          break
        deadline = self.engine.next_deadline()
        wait = 0.1 if deadline is None else min(0.1, max(0.001, (deadline - now_ms()) / 1000))
        readable, _, _ = select.select([self.sock, self.wake_r], [], [], wait)
        if self.wake_r in readable:
          self.wake_r.recv(4096)
        if self.sock in readable:
          data = self.sock.recv(8192)
          if not data:
            self.engine.feed_eof()
          else:
            self.engine.feed(data, now_ms())
      self._events()
    except OSError as e:
      self.dead_reason = f"{type(e).__name__}: {e}"
    finally:
      if self.dead_reason:
        self.log(f"iap2 link ended: {self.dead_reason}")
      self.closed.set()
      self.incoming.put(None)
