"""Wakes the Jetson from Galaxy by borrowing JetLink's gadget, which its owner keeps presented while lent."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

SECONDS = 15 * 60
RECORD = Path("/dev/shm/jetlink/galaxy-wake.json")
POLL = 1.0


def awake_for() -> float | None:
  """Seconds left on a wake that is still holding the gadget, if any. Monotonic: the clock is shared across processes."""
  from jetlink.comma import gadget
  record = gadget.read_record(RECORD) or {}
  until = record.get("until")
  if type(until) not in (int, float) or until <= time.monotonic() or not gadget.pid_alive(record.get("pid")):
    return None
  return float(until) - time.monotonic()


def start() -> bool:
  """Hold the gadget in a process of its own, so a Galaxy restart does not end it. False when one already holds it."""
  if awake_for() is not None:
    return False
  subprocess.Popen([sys.executable, "-m", "openpilot.starpilot.models.jetlink_wake"], start_new_session=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
  return True


def hold(loan, onroad, seconds: float = SECONDS) -> None:
  from jetlink.comma import gadget, lending
  until = time.monotonic() + seconds
  try:
    gadget.write_record(RECORD, {"pid": os.getpid(), "until": until})
    # modeld needs the gadget once driving starts
    while time.monotonic() < until and not onroad():
      if lending.hung_up(loan.conn, POLL):
        break
  finally:
    loan.close()
    RECORD.unlink(missing_ok=True)


def main() -> int:
  from jetlink.comma import lending
  from openpilot.common.params import Params
  params = Params()
  loan = lending.borrow("galaxy-wake", timeout=30.0) if lending.SOCKET.exists() else None
  if loan is None:
    return 1
  hold(loan, lambda: params.get_bool("IsOnroad"))
  return 0


if __name__ == "__main__":
  sys.exit(main())
