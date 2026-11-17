"""What projectiond tells the UIs (a status file) and what they send back (touches on a datagram socket)."""
from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path

STATUS_PATH = Path("/tmp/starpilot-projection.json")
TOUCH_SOCKET = "/tmp/starpilot-projection-touch.sock"
VIPC_NAME = "projectiond"
STATUS_MAX_AGE = 5.0
TOUCH_KINDS = ("down", "move", "up", "cancel")


def write_status(status: dict, path: Path = STATUS_PATH) -> None:
  tmp = path.with_name(path.name + ".tmp")
  tmp.write_text(json.dumps({**status, "updated": time.time()}, separators=(",", ":")))
  os.chmod(tmp, 0o644)
  os.replace(tmp, path)


class StatusReader:
  """Cheap per-frame status: re-reads the file only when it changes, and ignores a stale one."""

  def __init__(self, path: Path = STATUS_PATH, max_age: float = STATUS_MAX_AGE):
    self.path, self.max_age = path, max_age
    self.mtime = 0.0
    self.value: dict = {}
    self.checked = 0.0

  def read(self, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    if now - self.checked >= 0.25:
      self.checked = now
      try:
        mtime = self.path.stat().st_mtime
        if mtime != self.mtime:
          self.mtime = mtime
          self.value = json.loads(self.path.read_text())
      except (OSError, ValueError):
        self.value = {}
    if not self.value or now - float(self.value.get("updated", 0)) > self.max_age:
      return {}
    return self.value


@dataclass(frozen=True)
class Touch:
  kind: str
  pointer: int
  x: float  # 0..1 across the phone picture
  y: float


class TouchSender:
  def __init__(self, path: str = TOUCH_SOCKET):
    self.path = path
    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    self.sock.setblocking(False)

  def send(self, touch: Touch) -> None:
    try:
      self.sock.sendto(json.dumps([touch.kind, touch.pointer, round(touch.x, 5), round(touch.y, 5)]).encode(), self.path)
    except OSError:
      pass

  def close(self) -> None:
    self.sock.close()


class TouchReceiver:
  def __init__(self, path: str = TOUCH_SOCKET):
    self.path = path
    try:
      os.unlink(path)
    except FileNotFoundError:
      pass
    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    self.sock.bind(path)
    os.chmod(path, 0o666)
    self.sock.setblocking(False)

  def fileno(self) -> int:
    return self.sock.fileno()

  def drain(self) -> list[Touch]:
    touches = []
    while True:
      try:
        data = self.sock.recv(256)
      except (BlockingIOError, OSError):
        return touches
      try:
        kind, pointer, x, y = json.loads(data)
        if kind in TOUCH_KINDS:
          touches.append(Touch(kind, int(pointer), min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0)))
      except (ValueError, TypeError):
        continue

  def close(self) -> None:
    self.sock.close()
    try:
      os.unlink(self.path)
    except OSError:
      pass
