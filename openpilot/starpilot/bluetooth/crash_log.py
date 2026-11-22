"""Save the kernel log around a Bluetooth controller firmware crash; the journal does not survive a reboot."""
from collections import deque
from datetime import datetime
import logging
import os
from pathlib import Path
import subprocess
import threading
import time

KMSG = '/dev/kmsg'
OUT = Path('/data/bluetooth_crashes')
TRIGGER = 'hci0 hardware error'
NOISE = 'unknown connection handle 3804'  # Qualcomm firmware debug packets on handle 0x2EDC
BEFORE = 300
AFTER_S = 5.
KEEP = 10
LOG = logging.getLogger('bluetooth.crash_log')


def parse(record: str) -> str:
  head, _, text = record.partition(';')
  try:
    stamp = int(head.split(',')[2]) / 1e6
  except (IndexError, ValueError):
    return record.rstrip('\n')
  return f'[{stamp:12.6f}] ' + text.split('\n', 1)[0]


def snapshot(run=subprocess.run) -> str:
  out = []
  for args in (['hciconfig', '-a', 'hci0'], ['bluetoothctl', 'devices', 'Connected'], ['bluetoothctl', 'show']):
    try:
      result = run(args, capture_output=True, text=True, timeout=5, check=False)
      out.append(f'$ {" ".join(args)}\n{result.stdout}{result.stderr}')
    except (OSError, subprocess.SubprocessError) as error:
      out.append(f'$ {" ".join(args)}\n{error}\n')
  return '\n'.join(out)


class CrashLog:
  def __init__(self, out=OUT, run=subprocess.run, clock=time.monotonic, now=datetime.now):
    self.out, self.run, self.clock, self.now = out, run, clock, now
    self.before = deque(maxlen=BEFORE)
    self.noise = 0
    self.pending = None  # (deadline, header lines, after lines)

  def feed(self, line: str) -> Path | None:
    """Take one parsed kernel line; returns the report path once one is written."""
    if NOISE in line:
      self.noise += 1
      return None
    if self.pending is not None:
      self.pending[2].append(line)
      return self.flush() if self.clock() >= self.pending[0] else None
    self.before.append(line)
    if TRIGGER in line:
      header = [f'{self.noise} debug-handle lines skipped', '--- before (oldest first) ---', *self.before]
      self.pending = (self.clock() + AFTER_S, header, [])
      LOG.error('Bluetooth controller crashed: %s', line)
    return None

  def flush(self) -> Path | None:
    if self.pending is None:
      return None
    _, header, after = self.pending
    self.pending = None
    self.before.clear()
    text = '\n'.join([*header, '--- after ---', *after, '--- adapter ---', snapshot(self.run)])
    try:
      self.out.mkdir(parents=True, exist_ok=True)
      path = self.out / self.now().strftime('%Y-%m-%d_%H-%M-%S.txt')
      path.write_text(text)
      for old in sorted(self.out.glob('*.txt'))[:-KEEP]:
        old.unlink(missing_ok=True)
    except OSError:
      LOG.exception('Could not save Bluetooth crash log')
      return None
    LOG.error('Bluetooth crash log saved to %s', path)
    return path

  def watch(self, kmsg=KMSG) -> None:
    fd = os.open(kmsg, os.O_RDONLY | os.O_NONBLOCK)
    try:
      while True:
        try:
          record = os.read(fd, 8192).decode(errors='replace')
        except BlockingIOError:
          time.sleep(0.5)
          if self.pending is not None and self.clock() >= self.pending[0]:
            self.flush()
          continue
        except BrokenPipeError:
          continue  # records overwritten before we read them
        if not record:
          break
        self.feed(parse(record))
    finally:
      os.close(fd)


def start() -> threading.Thread | None:
  if not os.path.exists(KMSG):
    return None

  def run():
    try:
      CrashLog().watch()
    except OSError:
      LOG.exception('Bluetooth crash log watcher stopped')

  thread = threading.Thread(target=run, name='bluetooth-crash-log', daemon=True)
  thread.start()
  return thread
