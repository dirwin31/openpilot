"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

Keep the Jetson awake from the comma.

Borrows the gadget from its owner (jetlinkd) and holds the loan. The owner
keeps a lent gadget presented, so the Jetson enumerates it, wakes if it was
asleep, and stays up until the loan goes back.

    python -m jetlink.comma.hold                 until Ctrl-C or SIGTERM
    python -m jetlink.comma.hold -t 600          for ten minutes
    python -m jetlink.comma.hold -t 900 --parked for fifteen, or until a drive starts
    python -m jetlink.comma.hold -- CMD [ARGS]   while CMD runs; exits with its status

scripts/comma/jetlink_hold.py is the same, for a checkout. The owner lends to
one borrower at a time, so this loan keeps modeld and a bench out: --parked
gives it back the moment the owner sees the car onroad. A timed hold keeps
HOLD, so another process can tell how long the Jetson stays up (awake_for).

The standard library and jetlink.comma only.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time

from jetlink.comma import gadget, lending

BORROW_TIMEOUT = 60.0
POLL = 0.5
HOLD = gadget.STATUS.parent / 'hold.json'


def say(msg: str) -> None:
  print(f'jetlink_hold: {msg}', flush=True)


def status(returncode: int) -> int:
  """A child's exit status as a shell reports it: 128 + N for signal N."""
  return returncode if returncode >= 0 else 128 - returncode


def onroad() -> bool:
  """Has the owner seen the car onroad? Its own status record, as it last read the param."""
  return (gadget.owner_status() or {}).get('offroad') is False


def awake_for() -> float | None:
  """Seconds left on a timed hold that still has the loan, or None."""
  record = gadget.read_record(HOLD) or {}
  until = record.get('until')
  if type(until) not in (int, float) or until <= time.monotonic() or not gadget.pid_alive(record.get('pid')):
    return None
  return float(until) - time.monotonic()


def hold(loan: lending.Loan, seconds: float | None, command: list[str], parked: bool = False) -> int:
  child = subprocess.Popen(command) if command else None
  end = None if seconds is None else time.monotonic() + seconds
  lost = False
  try:
    while True:
      wait = POLL if end is None else max(min(POLL, end - time.monotonic()), 0)
      if child is not None:
        try:
          return status(child.wait(timeout=wait))
        except subprocess.TimeoutExpired:
          pass
      # the owner closing the loan's socket: it exited or restarted, and the
      # loan went with it. Without a child, this is the wait
      gone = not lost and lending.hung_up(loan.conn, 0 if child is not None else wait)
      if end is not None and time.monotonic() >= end:
        return 0
      if parked and onroad():
        say('a drive started, giving the gadget back')
        return 0
      if gone:
        lost = True
        say('the owner went away, and the loan with it')
        if child is None:
          return 1
  except KeyboardInterrupt:
    if child is None:
      return 0   # the usual way to end an open-ended hold
    if child.poll() is None:
      child.terminate()
      try:
        child.wait(5.0)
      except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
    return status(child.returncode)


def main(argv: list[str] | None = None, path=lending.SOCKET) -> int:
  argv = sys.argv[1:] if argv is None else list(argv)
  command: list[str] = []
  dashed = '--' in argv
  if dashed:
    split = argv.index('--')
    argv, command = argv[:split], argv[split + 1:]
  parser = argparse.ArgumentParser(prog='jetlink_hold', description='Borrow the gadget from the owner and hold it, so the Jetson stays awake.',
                                   usage='%(prog)s [-t SECONDS] [--parked] [--timeout SECONDS] [-- CMD [ARGS]]')
  parser.add_argument('-t', '--seconds', type=float, help='hold this long, then give the loan back')
  parser.add_argument('--parked', action='store_true', help='give the loan back when a drive starts')
  parser.add_argument('--timeout', type=float, default=BORROW_TIMEOUT, help='how long to wait for the loan (default %(default)g s)')
  args = parser.parse_args(argv)
  if args.seconds is not None and command:
    parser.error('give -t or a command, not both')
  if dashed and not command:
    parser.error('-- needs a command')
  if args.parked and onroad():
    say('the car is onroad, not holding')
    return 1

  # Not a probe connect first: the owner takes one connection at a time, and
  # a probe queued beside the borrow can get the borrow refused.
  started = time.monotonic()
  loan = lending.borrow('hold', timeout=args.timeout, path=path) if path.exists() else None
  if loan is None:
    if time.monotonic() - started < min(1.0, args.timeout / 2):   # refused at once, not timed out
      why = gadget.gadget_error() or 'is Jetlink on and jetlinkd running?'
      say(f'no gadget owner is listening on {path}: {why}')
    else:
      say(f'no loan from the owner within {args.timeout:g} s; is modeld or a bench holding it?')
    return 1
  timed = args.seconds is not None
  try:
    link = 'the cable link' if loan.cable else 'the gadget'
    say(f'holding {link}: udc {loan.udc}, mount {loan.mount}')
    if timed:
      try:
        gadget.write_record(HOLD, {'pid': os.getpid(), 'until': time.monotonic() + args.seconds})
      except OSError as e:
        say(f'could not keep {HOLD} ({e})')
    return hold(loan, args.seconds, command, parked=args.parked)
  finally:
    if timed:
      HOLD.unlink(missing_ok=True)
    loan.close()
    say('released')


def _interrupt(signum, frame):
  raise KeyboardInterrupt


def run() -> None:
  # the lending layer's routine lines ("borrowed the gadget ...") repeat ours;
  # its errors still show
  gadget.log.setLevel(logging.ERROR)
  signal.signal(signal.SIGTERM, _interrupt)
  try:
    sys.exit(main())
  except KeyboardInterrupt:   # before there was a loan to hold
    sys.exit(130)


if __name__ == '__main__':
  run()
