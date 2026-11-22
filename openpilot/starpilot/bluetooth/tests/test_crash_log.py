from datetime import datetime
import os
import subprocess

from openpilot.starpilot.bluetooth.crash_log import CrashLog, KEEP, parse


class Clock:
  def __init__(self):
    self.now = 0.

  def __call__(self):
    return self.now


def fake_run(args, **_kwargs):
  return subprocess.CompletedProcess(args, 0, stdout=f'out of {args[0]}\n', stderr='')


def test_parse_kmsg_record():
  assert parse('6,9854,19439597231,-;Bluetooth: hci0 hardware error 0x85\n') == '[19439.597231] Bluetooth: hci0 hardware error 0x85'
  assert parse('6,1,5,-;first\n SUBSYSTEM=x\n') == '[    0.000005] first'


def test_crash_writes_context_after_window(tmp_path):
  clock = Clock()
  log = CrashLog(out=tmp_path, run=fake_run, clock=clock, now=lambda: datetime(2026, 11, 21))
  log.feed('[1.0] Bluetooth: hci0 ACL packet for unknown connection handle 3804')
  log.feed('[2.0] before line')
  assert log.feed('[3.0] Bluetooth: hci0 hardware error 0x85') is None
  assert log.feed('[3.5] after line') is None
  clock.now = 10.
  path = log.feed('[9.0] late line')
  text = path.read_text()
  assert '1 debug-handle lines skipped' in text
  assert text.index('before line') < text.index('hardware error') < text.index('--- after ---') < text.index('after line')
  assert 'out of hciconfig' in text


def test_idle_flush_and_rotation(tmp_path):
  clock = Clock()
  for i in range(KEEP + 2):
    (tmp_path / f'2000-01-{i:02d}.txt').write_text('old')
  log = CrashLog(out=tmp_path, run=fake_run, clock=clock, now=lambda: datetime(2026, 11, 21))
  log.feed('[3.0] Bluetooth: hci0 hardware error 0x85')
  assert log.flush() is not None
  assert len(list(tmp_path.glob('*.txt'))) == KEEP
  assert not (tmp_path / '2000-01-00.txt').exists()


def test_watch_reads_kmsg_records(tmp_path):
  clock = Clock()
  log = CrashLog(out=tmp_path / 'out', run=fake_run, clock=clock, now=lambda: datetime(2026, 11, 21))
  read, write = os.pipe()
  os.write(write, b'6,1,3000000,-;Bluetooth: hci0 hardware error 0x85\n')
  os.close(write)
  try:
    log.watch(f'/dev/fd/{read}')
  finally:
    os.close(read)
  assert log.pending is not None
