"""Wired Android Auto: the comma as an Android Open Accessory (AOA) USB device.

The car's USB port is the host. It enumerates the comma's normal gadget and
sends the AOA handshake (GET_PROTOCOL, the accessory strings, START), which the
kernel's f_accessory answers and reports as an ``ACCESSORY=START`` uevent on
``android0``. Like an Android phone, the comma then re-enumerates as Google's
accessory (18d1:2d01, keeping adb) and the car opens bulk endpoints that carry
the same Android Auto protocol as the Wi-Fi link.

``/dev/usb_accessory`` has no poll() and each read() consumes one whole USB
transfer, so ``AccessoryBridge`` copies between it and a socket pair; the
projection session uses the socket end unchanged. Gadget changes need root and
go through ``sudo -n``, like ``/usr/comma/set_adb.sh``; ``restore`` puts the
original gadget back.
"""

from __future__ import annotations

import errno
import os
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

GADGET = Path("/sys/kernel/config/usb_gadget/g1")
CONFIG = "configs/c.1"
UDC_NAME = "a600000.dwc3"
ACCESSORY_FUNCTION = "accessory.gs2"
ACCESSORY_DEVICE = "/dev/usb_accessory"
GOOGLE_VID = 0x18D1
ACCESSORY_ADB_PID = 0x2D01
READ_SIZE = 16384  # f_accessory's bulk buffer; a read must cover a whole transfer
MAX_EMPTY_READS = 64
NETLINK_KOBJECT_UEVENT = 15


def parse_uevent(data: bytes) -> dict[str, str]:
  """Parse one kernel uevent datagram (``action@path`` then ``KEY=value`` lines)."""
  parts = data.split(b"\0")
  event: dict[str, str] = {}
  if parts and b"@" in parts[0] and b"=" not in parts[0]:
    action, path = parts[0].decode("utf-8", "replace").split("@", 1)
    event.update(ACTION=action, DEVPATH=path)
    parts = parts[1:]
  for part in parts:
    if b"=" in part:
      key, value = part.decode("utf-8", "replace").split("=", 1)
      event[key] = value
  return event


class UeventListener:
  """Kernel uevents for the USB gadget: connection state and the accessory start request."""

  def __init__(self):
    self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_KOBJECT_UEVENT)
    self.sock.bind((0, 1))

  def next(self, timeout: float) -> dict[str, str] | None:
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
      self.sock.settimeout(remaining)
      try:
        data = self.sock.recv(8192)
      except TimeoutError:
        return None
      event = parse_uevent(data)
      # Connection state comes from android0; ACCESSORY=START from the usb_accessory misc device.
      if any(name in event.get("DEVPATH", "") for name in ("android_usb", "usb_accessory")):
        return event
    return None

  def close(self) -> None:
    self.sock.close()


class AccessoryGadget:
  """Switch the comma's USB gadget into accessory mode and back."""

  def __init__(self, log: Callable[..., None], root: Path = GADGET, udc: str = UDC_NAME,
               run: Callable[..., subprocess.CompletedProcess] = subprocess.run):
    self.log = log
    self.root = root
    self.udc = udc
    self.run = run
    self.original: dict[str, str] | None = None

  def _sudo(self, *args: str, data: str | None = None) -> None:
    result = self.run(["sudo", "-n", *args], input=data, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
      raise RuntimeError(f"{' '.join(args)}: {(result.stderr or '').strip() or result.returncode}")

  def _write(self, name: str, value: str) -> None:
    self._sudo("tee", str(self.root / name), data=value + "\n")

  def _read(self, name: str) -> str:
    try:
      return (self.root / name).read_text().strip()
    except OSError:
      return ""

  def prepare(self) -> None:
    """Create the accessory function so the kernel answers the car's AOA handshake."""
    if not self.root.is_dir():
      raise RuntimeError(f"USB gadget {self.root} not set up (enable ADB, which creates it)")
    self.original = {"idVendor": self._read("idVendor"), "idProduct": self._read("idProduct"), "UDC": self._read("UDC")}
    if not (self.root / "functions" / ACCESSORY_FUNCTION).is_dir():
      self._sudo("mkdir", str(self.root / "functions" / ACCESSORY_FUNCTION))
    if not self.original["UDC"]:
      self._write("UDC", self.udc)
    self.log("usb_gadget_prepared", original=self.original)

  def switch_to_accessory(self) -> None:
    """Re-enumerate as Google's accessory, as a phone does after the AOA START request."""
    self._write("UDC", "")
    self._write("idVendor", f"0x{GOOGLE_VID:04x}")
    self._write("idProduct", f"0x{ACCESSORY_ADB_PID:04x}")
    link = self.root / CONFIG / ACCESSORY_FUNCTION
    if not link.exists():
      self._sudo("ln", "-s", str(self.root / "functions" / ACCESSORY_FUNCTION), str(link))
    self._write("UDC", self.udc)
    for _ in range(50):
      if os.path.exists(ACCESSORY_DEVICE):
        break
      time.sleep(0.1)
    self._sudo("chown", f"{os.getuid()}:{os.getgid()}", ACCESSORY_DEVICE)
    self.log("usb_accessory_mode", vid=f"{GOOGLE_VID:04x}", pid=f"{ACCESSORY_ADB_PID:04x}")

  def restore(self) -> None:
    """Put the original gadget back; leaves the accessory function unlinked but created."""
    if self.original is None:
      return
    try:
      self._write("UDC", "")
      link = self.root / CONFIG / ACCESSORY_FUNCTION
      if link.is_symlink():
        self._sudo("rm", "-f", str(link))
      for key in ("idVendor", "idProduct"):
        if self.original[key]:
          self._write(key, self.original[key])
      self._write("UDC", self.original["UDC"] or self.udc)
      self.log("usb_gadget_restored")
    except Exception as error:
      self.log("usb_gadget_restore_failed", error=str(error))


class AccessoryBridge:
  """Expose /dev/usb_accessory as a connected socket for the projection session."""

  def __init__(self, device: str = ACCESSORY_DEVICE, log: Callable[..., None] = lambda *a, **k: None):
    self.log = log
    self.fd = os.open(device, os.O_RDWR)
    self.session_sock, self.bridge_sock = socket.socketpair()
    self.closed = threading.Event()
    self.error = ""
    self.threads = [threading.Thread(target=self._usb_to_socket, name="aa_usb_rx", daemon=True),
                    threading.Thread(target=self._socket_to_usb, name="aa_usb_tx", daemon=True)]
    for thread in self.threads:
      thread.start()

  @property
  def socket(self) -> socket.socket:
    return self.session_sock

  def _finish(self, error: str) -> None:
    if not self.closed.is_set():
      self.error = error
      self.closed.set()
      self.log("usb_bridge_closed", error=error)
      try:
        self.bridge_sock.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass

  def _usb_to_socket(self) -> None:
    empty = 0
    try:
      while not self.closed.is_set():
        data = os.read(self.fd, READ_SIZE)
        if not data:
          empty += 1  # a zero-length packet is normal; an endless run of them is a dead link
          if empty > MAX_EMPTY_READS:
            self._finish("car disconnected")
            return
          continue
        empty = 0
        self.bridge_sock.sendall(data)
    except OSError as error:
      self._finish("car disconnected" if error.errno in (errno.ENODEV, errno.EIO, errno.ESHUTDOWN) else str(error))

  def _socket_to_usb(self) -> None:
    try:
      while not self.closed.is_set():
        data = self.bridge_sock.recv(READ_SIZE)
        if not data:
          break
        view = memoryview(data)
        while view:
          view = view[os.write(self.fd, view):]
    except OSError as error:
      self._finish(str(error))
    self._finish(self.error or "session closed")

  def close(self) -> None:
    self._finish(self.error or "closed")
    for sock in (self.session_sock, self.bridge_sock):
      try:
        sock.close()
      except OSError:
        pass
    try:
      os.close(self.fd)  # unblocks a pending read with an error on disconnect
    except OSError:
      pass
