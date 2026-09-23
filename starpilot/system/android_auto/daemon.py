"""android_autod: local control socket for wireless Android Auto projection.

Runs whenever Bluetooth is enabled and stays idle (no radio, network, encoder
or capture work) until the user presses Start. Commands are one JSON line per
connection on ``ANDROID_AUTO_SOCKET_PATH``, mirroring bluetooth_managerd.

  python -m openpilot.starpilot.system.android_auto.daemon            # service
  python -m openpilot.starpilot.system.android_auto.daemon --once     # foreground diagnostic run
  python -m openpilot.starpilot.system.android_auto.daemon --once --synthetic   # test pattern, no UI needed
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socketserver
import sys
import threading
import time
from typing import Any

from openpilot.starpilot.system.android_auto.protocol import ANDROID_AUTO_SOCKET_PATH
from openpilot.starpilot.system.android_auto.supervisor import Supervisor

PAIRING_COMMANDS = {"prepare_pairing"}


def _offroad() -> bool:
  try:
    from openpilot.common.params import Params
    return Params().get_bool("IsOffroad")
  except Exception:
    return True


def handle(supervisor: Supervisor, request: dict[str, Any]) -> dict[str, Any]:
  command = str(request.get("command", ""))
  if command in PAIRING_COMMANDS and not _offroad():
    raise RuntimeError("Pair the car while parked (offroad)")
  if command == "status":
    return {"status": supervisor.status()}
  if command == "start":
    supervisor.start()
  elif command == "stop":
    supervisor.stop()
  elif command == "select_receiver":
    supervisor.select_receiver(str(request.get("address", "")), str(request.get("name", "")))
  elif command == "set_view":
    supervisor.set_view(str(request.get("view", "")))
  elif command == "prepare_pairing":
    supervisor.prepare_pairing()
  elif command == "devices":
    return {"devices": supervisor.devices()}
  else:
    raise RuntimeError(f"Unknown Android Auto command: {command}")
  return {}


class RequestHandler(socketserver.StreamRequestHandler):
  def handle(self) -> None:
    try:
      request = json.loads(self.rfile.readline(64 * 1024))
      response = {"ok": True, **handle(self.server.supervisor, request)}
    except Exception as error:
      response = {"ok": False, "error": str(error)}
    self.wfile.write(json.dumps(response, separators=(",", ":"), default=str).encode() + b"\n")


class Server(socketserver.ThreadingUnixStreamServer):
  daemon_threads = True

  def __init__(self, path: str, supervisor: Supervisor):
    self.supervisor = supervisor
    super().__init__(path, RequestHandler)


def run_once(duration: float, synthetic: bool) -> int:
  """Foreground diagnostic: start, print state changes, stop after ``duration`` seconds or Ctrl-C."""
  supervisor = Supervisor(synthetic=synthetic)
  stop = threading.Event()
  signal.signal(signal.SIGINT, lambda *_: stop.set())
  signal.signal(signal.SIGTERM, lambda *_: stop.set())
  supervisor.start()
  deadline = time.monotonic() + duration
  last = None
  try:
    while not stop.is_set() and time.monotonic() < deadline:
      status = supervisor.status()
      summary = (status["state"], status["detail"], status["error"])
      if summary != last:
        print(json.dumps({key: status[key] for key in ("state", "label", "detail", "error", "mode", "head_unit")}, default=str), flush=True)
        last = summary
      if status["state"] == "streaming" and int(time.monotonic()) % 5 == 0:
        print(json.dumps({"stats": status["stats"]}), flush=True)
      stop.wait(1.0)
  finally:
    supervisor.close()
  return 0 if supervisor.status()["error"] == "" else 1


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--once", action="store_true", help="run one foreground session for diagnostics")
  parser.add_argument("--duration", type=float, default=900.0)
  parser.add_argument("--synthetic", action="store_true", help="with --once: send a moving test pattern instead of the UI")
  args = parser.parse_args()
  if args.once:
    return run_once(args.duration, args.synthetic)

  try:
    os.unlink(ANDROID_AUTO_SOCKET_PATH)
  except FileNotFoundError:
    pass
  supervisor = Supervisor()
  exit_event = threading.Event()

  def housekeeping():
    while not exit_event.wait(2.0):
      try:
        supervisor.maintain()
      except Exception:
        pass

  threading.Thread(target=housekeeping, daemon=True).start()
  server = Server(ANDROID_AUTO_SOCKET_PATH, supervisor)

  def shutdown(*_):
    exit_event.set()
    threading.Thread(target=server.shutdown, daemon=True).start()

  signal.signal(signal.SIGTERM, shutdown)
  signal.signal(signal.SIGINT, shutdown)
  try:
    os.chmod(ANDROID_AUTO_SOCKET_PATH, 0o660)
    server.serve_forever(poll_interval=0.5)
  finally:
    exit_event.set()
    server.server_close()
    supervisor.close()
    try:
      os.unlink(ANDROID_AUTO_SOCKET_PATH)
    except FileNotFoundError:
      pass
  return 0


if __name__ == "__main__":
  sys.exit(main())
