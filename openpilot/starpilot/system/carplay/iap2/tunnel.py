"""The iAP2 link the iPhone carries inside CarPlay's Wi-Fi session, so route guidance keeps coming once Bluetooth is gone.

Bytes from the iPhone arrive over the AirPlay iAP data stream; replies go back as iAPSendMessage event commands.
"""
from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable

from openpilot.starpilot.system.carplay.iap2.link import TUNNEL_CONFIG
from openpilot.starpilot.system.carplay.iap2.session import Iap2Session

SEND_WAIT_S = 10.0


class TunnelLink:
  def __init__(self, send: Callable[[bytes], bool], log: Callable[[str], None] = print):
    self.send, self.log = send, log
    self.closed = threading.Event()
    self.ours, theirs = socket.socketpair()
    self.session = Iap2Session(theirs, TUNNEL_CONFIG, initiator=True, log=log)
    threading.Thread(target=self._outbound, name="carplay-iap-tunnel-out", daemon=True).start()
    self.session.start()

  def feed(self, data: bytes) -> None:
    if not self.closed.is_set():
      try:
        self.ours.sendall(data)
      except OSError:
        self.close()

  def _outbound(self) -> None:
    try:
      while not self.closed.is_set():
        data = self.ours.recv(65536)
        if not data:
          break
        deadline = time.monotonic() + SEND_WAIT_S
        while not self.send(data):  # the event channel may not be up yet
          if self.closed.is_set() or time.monotonic() > deadline:
            self.log("iap2 tunnel: could not send to the iPhone")
            return
          time.sleep(0.1)
    except OSError:
      pass
    finally:
      self.close()

  def close(self) -> None:
    if self.closed.is_set():
      return
    self.closed.set()
    try:
      self.ours.shutdown(socket.SHUT_RDWR)
    except OSError:
      pass
    threading.Thread(target=self.session.close, daemon=True).start()
