#!/usr/bin/env python3
"""Wired Android Auto test: project to the car over USB instead of Wi-Fi.

Runs on the comma with the car's USB port connected to the comma's USB-C
device port. It adds the Android accessory function to the comma's USB gadget,
waits for the car's accessory handshake, re-enumerates as Google's accessory
and runs the real supervisor streaming path over the USB link. The original
gadget (adb, USB networking) is restored on exit.

  cd /data/openpilot
  PYTHONPATH=/data/openpilot /usr/local/venv/bin/python tools/android_auto/usb_device.py

Stop wireless Android Auto in settings first. Start this, then plug the cable
into the car (or unplug and replug it: the car sends the handshake on connect).
"""

import argparse
import json
import signal
import threading
import time
from types import SimpleNamespace

from openpilot.starpilot.system.android_auto import identity as identity_store
from openpilot.starpilot.system.android_auto.supervisor import Cancelled, Supervisor
from openpilot.starpilot.system.android_auto.usb_accessory import AccessoryBridge, AccessoryGadget, UeventListener


class UsbLease:
  """Stands in for the car's Wi-Fi network: the link is the USB cable."""
  local_ip = None

  def __init__(self, bridge: AccessoryBridge):
    self.bridge = bridge

  def still_connected(self) -> bool:
    return not self.bridge.closed.is_set()


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--view", choices=("car", "mirror"), help="default: the configured view")
  parser.add_argument("--encoder", choices=("auto", "hardware", "software"), help="default: the configured encoder")
  parser.add_argument("--wait", type=float, default=600.0, help="seconds to wait for the car's handshake")
  args = parser.parse_args()

  supervisor = Supervisor()
  for key in ("view", "encoder"):
    if getattr(args, key) is not None:
      supervisor.config[key] = getattr(args, key)
  ident = identity_store.load_identity()
  print(f"identity ok, expires {ident.expires} ({ident.days_left} days)", flush=True)

  def log(name, **values):
    supervisor.log(name, **values)
    print(json.dumps({"event": name, **values}, default=str), flush=True)

  def stop(*_):
    supervisor._stop.set()
    supervisor._close_sockets()
  signal.signal(signal.SIGINT, stop)
  signal.signal(signal.SIGTERM, stop)

  supervisor.log.open()
  supervisor.log("session_start", receiver="usb", generation=0)
  gadget = AccessoryGadget(log)
  listener = UeventListener()
  bridge = None
  result = 0
  try:
    gadget.prepare()
    print("waiting for the car's accessory handshake; plug in (or replug) the USB cable", flush=True)
    deadline = time.monotonic() + args.wait
    started = False
    while not started and not supervisor._stop.is_set() and time.monotonic() < deadline:
      event = listener.next(1.0)
      if event is None:
        continue
      log("usb_uevent", **{key: event[key] for key in ("DEVPATH", "USB_STATE", "ACCESSORY") if key in event})
      started = event.get("ACCESSORY") == "START"
    if not started:
      print("no accessory handshake from the car", flush=True)
      return 1

    gadget.switch_to_accessory()
    bridge = AccessoryBridge(log=log)
    peer = bridge.socket
    supervisor._connect_tcp = lambda result, lease: supervisor._track(peer)

    def report():
      while not supervisor._stop.wait(5.0):
        status = supervisor.status()
        print(json.dumps({key: status[key] for key in ("state", "view", "encoder", "target_fps", "mode", "stats")}), flush=True)
    threading.Thread(target=report, daemon=True).start()

    supervisor._project(SimpleNamespace(endpoint=None), UsbLease(bridge), ident)
    print("session ended", flush=True)
  except Cancelled:
    print("stopped", flush=True)
  except Exception as error:
    if not supervisor._stop.is_set():
      print(json.dumps({"result": "fail", "error": f"{type(error).__name__}: {error}",
                        "bridge": bridge.error if bridge else "", "recent": list(supervisor.log.recent)[-8:]}, default=str))
      result = 1
  finally:
    supervisor._stop.set()
    supervisor._close_sockets()
    if bridge is not None:
      bridge.close()
    listener.close()
    gadget.restore()
    supervisor.log("session_stop")
    supervisor.log.close()
    print(f"log: {identity_store.LOG_DIR}", flush=True)
  return result


if __name__ == "__main__":
  raise SystemExit(main())
