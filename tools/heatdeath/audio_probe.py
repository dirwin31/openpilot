#!/usr/bin/env python3
"""Music-chain probe: the shipped audio server and pipe reader under a replayed driving load.

run     (root) starts stand-in road loads, the audio chain (as comma) and one heatdeath replay, then scores the result
audio   (comma) private PulseAudio server -> pipe -> StarPort's PipeSource, fed a constant tone
hog     one stand-in load: bounded CPU pulses with a real process's scheduling class, cores and duty
"""
import argparse
import array
import ctypes as C
import json
import os
from pathlib import Path
import resource
import signal
import struct
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
TOOL = HERE / 'heatdeath.py'
PYTHON = '/usr/local/venv/bin/python3'
TONE = 8000            # constant sample value; silence from an underrun reads as ~0
LOW = TONE // 2
RATE_OUT, FRAME = 48000, 4
BYTES_PER_MS = RATE_OUT * FRAME / 1000

# Averages from route 000000ab segments 11-19 (procLog), with each process's real class and cores from the code.
# The replay's own feeder and collector (46 % on cores 0-3) stand in for spot monitor, galaxy, proclogd and the GPS daemons.
ROAD = [  # name, policy, nice-or-rtprio, cores, cpu ms, period ms
  ('locationd', 'fifo', 5, '0-3', 3.6, 10),
  ('lagd', 'fifo', 5, '0-3', 3.75, 50),
  ('paramsd', 'fifo', 5, '0-3', 4.35, 50),
  ('dmonitoringd', 'fifo', 5, '0-3', 2.3, 50),
  ('sensord', 'fifo', 1, '1', 0.94, 10),
  ('loggerd', 'other', 0, '0-3', 1.85, 10),
  ('ui', 'other', 5, '0-3', 9, 50),
  ('vision', 'other', 10, '0-3', 52, 167),
  ('renderer', 'idle', 0, '0-2', 100, 100),
  ('athenad', 'other', 0, '0-3', 2.25, 50),
  ('uploader', 'other', 10, '0-3', 8.6, 100),
  ('hardwared', 'other', 0, '0-7', 2, 50),
  ('encoderd', 'fifo', 52, '3', 3.75, 50),
  ('camerad', 'other', 0, '6', 3.5, 50),
  ('modeld', 'fifo', 54, '7', 8.5, 50),
  ('dmonmodeld', 'fifo', 5, '7', 7, 50),
]
CONFIGS = {  # audio server nice, music chain nice (Soloist/feeder), starportd nice
  'new': {'server': -5, 'chain': 5, 'starportd': 0},
  'old': {'server': 0, 'chain': 10, 'starportd': 5},
}


def cores(text):
  if '-' in text:
    a, b = text.split('-')
    return set(range(int(a), int(b) + 1))
  return {int(c) for c in text.split(',')}


def hog(args):
  os.sched_setaffinity(0, cores(args.cores))
  if args.policy == 'fifo':
    os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(args.prio))
  elif args.policy == 'idle':
    os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
  else:
    os.setpriority(os.PRIO_PROCESS, 0, args.prio)
  period, dose = round(args.period * 1e6), round(args.ms * 1e6)
  end = time.monotonic_ns() + round(args.seconds * 1e9)
  due = time.monotonic_ns() + (os.getpid() * 7919 % 1000) * period // 1000  # spread phases
  while due < end:
    delay = (due - time.monotonic_ns()) / 1e9
    if delay > 0:
      time.sleep(delay)
    cpu = time.thread_time_ns()
    while time.thread_time_ns() - cpu < dose and time.monotonic_ns() < due + period:
      pass
    due += period


class SampleSpec(C.Structure):
  _fields_ = [('format', C.c_int), ('rate', C.c_uint32), ('channels', C.c_uint8)]


class BufferAttr(C.Structure):
  _fields_ = [(name, C.c_uint32) for name in ('maxlength', 'tlength', 'prebuf', 'minreq', 'fragsize')]


def feeder(args):
  """Same client call and buffer shape as the phone relay's PulseWriter; 44100 Hz makes the server resample as for Spotify."""
  os.setpriority(os.PRIO_PROCESS, 0, args.nice)
  lib = C.CDLL('libpulse-simple.so.0')
  lib.pa_simple_new.argtypes = [C.c_char_p, C.c_char_p, C.c_int, C.c_char_p, C.c_char_p, C.POINTER(SampleSpec), C.c_void_p, C.POINTER(BufferAttr),
                                C.POINTER(C.c_int)]
  lib.pa_simple_new.restype = C.c_void_p
  lib.pa_simple_write.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_int)]
  scale = args.rate / 48000
  spec = SampleSpec(3, args.rate, 2)
  attr = BufferAttr(round(96000 * scale) & ~3, round(38400 * scale) & ~3, 0xffffffff, round(3840 * scale) & ~3, 0xffffffff)
  error = C.c_int()
  stream = lib.pa_simple_new(args.server.encode(), b'probe', 1, b'spotify', b'probe tone', C.byref(spec), None, C.byref(attr), C.byref(error))
  assert stream, f'no stream ({error.value})'
  chunk = array.array('h', [TONE]) * (args.rate // 50 * 2)  # 20 ms
  data = chunk.tobytes()
  while True:
    assert lib.pa_simple_write(stream, data, len(data), C.byref(error)) >= 0, error.value


def audio(args):
  for part in ('', 'opendbc_repo', 'msgq_repo'):
    sys.path.insert(0, str(args.repo / part))
  from openpilot.starpilot.soloist import backend
  from openpilot.starpilot.soloist.paths import Layout
  from openpilot.starpilot.soloist.preferences import Preferences
  from openpilot.starpilot.system.starport.media_audio import BYTES_PER_SECOND, PipeSource

  config = CONFIGS[args.config]
  layout = Layout(args.root)
  launch = backend.server_launch(layout, Preferences(output='starport'))
  paths = backend.runtime_paths(layout)
  paths['daemon'].write_text(f'nice-level = {config["server"]}\n')
  children = []
  stop = []
  signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
  log = (args.root / 'server.log').open('wb')
  children.append(subprocess.Popen(launch.argv, env=launch.env, stdout=log, stderr=subprocess.STDOUT))
  deadline = time.monotonic() + 10
  while not (paths['socket'].exists() and paths['starport'].exists()):
    assert time.monotonic() < deadline and children[0].poll() is None, 'audio server did not start'
    time.sleep(0.05)
  env = {**os.environ, 'LD_LIBRARY_PATH': backend.library_path()}
  children.append(subprocess.Popen([sys.executable, '-B', __file__, 'feeder', '--server', f'unix:{paths["socket"]}', '--rate', str(args.rate),
                                    '--nice', str(config['chain'])], env=env))
  # The reader sits where starportd does: core 6, at starportd's nice level.
  os.sched_setaffinity(0, {6})
  os.setpriority(os.PRIO_PROCESS, 0, config['starportd'])
  source = PipeSource(paths['starport'], lambda: True)
  rows = array.array('q')  # mono ns, bytes, low samples, low runs, queued bytes before the read
  tail_low = False
  try:
    while not stop:
      assert all(child.poll() is None for child in children), 'audio child exited'
      source.active(time.monotonic())
      queued = source._queued(source.fd) if source.fd is not None else 0
      data = source.read(BYTES_PER_SECOND)
      now = time.monotonic_ns()
      low = runs = 0
      if data:
        samples = array.array('h', data[:len(data) - len(data) % 2])
        for value in samples:
          if value < LOW:
            low += 1
            runs += not tail_low
            tail_low = True
          else:
            tail_low = False
      rows.extend((now, len(data), low, runs, queued))
      time.sleep(0.025)
  finally:
    for child in children:
      child.terminate()
    for child in children:
      child.wait(timeout=5)
    (args.root / 'reader.bin').write_bytes(rows.tobytes())
    (args.root / 'reader.json').write_text(json.dumps({'overruns': source.overruns, 'config': config, 'rate': args.rate}))


def cpu_times():
  result = {}
  for line in Path('/proc/stat').read_text().splitlines():
    if line.startswith('cpu') and line[3].isdigit():
      f = [int(x) for x in line.split()[1:8]]
      result[int(line.split()[0][3:])] = (sum(f) - f[3] - f[4], sum(f))
  return result


def quantile(values, fraction):
  return sorted(values)[round((len(values) - 1) * fraction)]


def run(args):
  out = args.output
  out.mkdir(mode=0o755, parents=True)
  audio_root = out / 'audio'
  audio_root.mkdir()
  os.chown(audio_root, 1000, 1000)
  config = CONFIGS[args.config]
  load = list(ROAD) if args.road else []
  if args.road:  # the real Soloist decoder and StarPort stream thread, at their nice level for this configuration
    load += [('soloist', 'other', config['chain'], '0-7', 5.2, 20), ('starportd', 'other', config['starportd'], '6', 6, 33)]

  def as_comma():
    resource.setrlimit(resource.RLIMIT_NICE, (30, 30))
    os.setgid(1000)
    os.setuid(1000)

  env = {'PATH': os.environ['PATH'], 'HOME': '/home/comma'}
  children = [subprocess.Popen([PYTHON, '-B', __file__, 'audio', '--repo', str(args.repo), '--root', str(audio_root), '--config', args.config,
                                '--rate', str(args.rate)],
                               env=env, preexec_fn=as_comma)]
  try:
    time.sleep(4)
    assert children[0].poll() is None, 'audio chain failed to start'
    server_nice = subprocess.check_output(['ps', '-o', 'ni=', '-C', 'pulseaudio']).decode().split()
    for _name, policy, prio, where, ms, period in load:
      children.append(subprocess.Popen([PYTHON, '-B', __file__, 'hog', '--policy', policy, '--prio', str(prio), '--cores', where, '--ms', str(ms),
                                        '--period', str(period), '--seconds', '110']))
    bench = out / 'bench'
    git = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'safe.directory', 'GIT_CONFIG_VALUE_0': str(args.repo)}
    replay = subprocess.Popen([PYTHON, '-B', str(TOOL), 'run', '--bundle', str(args.bundle), '--output', str(bench)],
                              env={'PATH': os.environ['PATH'], 'HOME': '/root', **git}, cwd=args.repo, stdout=(out / 'bench.out').open('w'),
                              stderr=subprocess.STDOUT)
    release = bench / 'release.bin'
    while not release.exists():
      assert replay.poll() is None, 'replay failed before release'
      time.sleep(0.05)
    (start,) = struct.unpack('<Q', release.read_bytes())
    begin, end = start + 20_000_000_000, start + 57_000_000_000
    time.sleep(max(0, (begin - time.monotonic_ns()) / 1e9))
    before = cpu_times()
    if args.control:  # positive control: starve the music source for 0.6 s; the probe must report the silence
      time.sleep(10)
      pid = int(subprocess.check_output(['pgrep', '-f', 'audio_probe.py feeder']).split()[0])
      os.kill(pid, signal.SIGSTOP)
      time.sleep(0.6)
      os.kill(pid, signal.SIGCONT)
    time.sleep(max(0, (end - time.monotonic_ns()) / 1e9))
    after = cpu_times()
    code = replay.wait(timeout=120)
  finally:
    children[0].terminate()
    for child in children[1:]:
      child.kill()
    for child in children:
      child.wait(timeout=10)
  raw = array.array('q')
  raw.frombytes((audio_root / 'reader.bin').read_bytes())
  rows = [tuple(raw[i:i + 5]) for i in range(0, len(raw), 5)]
  window = [row for row in rows if begin <= row[0] < end]
  first = window[0]
  received, lateness, gaps = 0, [], []
  for previous, row in zip(window, window[1:], strict=False):
    received += row[1]
    lateness.append((row[0] - first[0]) / 1e6 - received / BYTES_PER_MS)
    gaps.append((row[0] - previous[0]) / 1e6)
  floor = min(lateness)
  lateness = [value - floor for value in lateness]
  report = json.loads(subprocess.check_output([PYTHON, '-B', str(TOOL), 'report', '--output', str(bench), '--skip-seconds', '20']))
  controls = {}
  for actor, label in (('controlsd', 'Controls.update'), ('card', 'Car.step'), ('selfdrived', 'SelfdriveD.step')):
    v = report['actors'][actor][label]
    controls[actor] = {'p99': round(v['wallMsP99'], 2), 'max': round(v['wallMsMax'], 2), 'gapMax': round(v['startGapMsMax'], 1)}
  lag = {name: sum(v.get('rollingLagFlags') or 0 for v in phases.values()) for name, phases in report['actors'].items()}
  result = {
    'name': out.name, 'config': args.config, 'road': args.road, 'rate': args.rate, 'replayExit': code, 'replayFailure': report['failure'],
    'active': report['activeObserverSamples'], 'serverNice': server_nice,
    'silenceMs': round(sum(row[2] for row in window) / 2 / 48, 1), 'silenceEvents': sum(row[3] for row in window),
    'latenessMs': {'p50': round(quantile(lateness, .5), 1), 'p99': round(quantile(lateness, .99), 1), 'max': round(max(lateness), 1)},
    'readerPassMs': {'p99': round(quantile(gaps, .99), 1), 'max': round(max(gaps), 1)},
    'pipeQueuedMaxMs': round(max(row[4] for row in window) / BYTES_PER_MS, 1),
    **json.loads((audio_root / 'reader.json').read_text()),
    'coreBusyPct': [round(100 * (after[c][0] - before[c][0]) / max(1, after[c][1] - before[c][1])) for c in sorted(after)],
    'controls': controls, 'lagFlags': lag,
  }
  (out / 'probe.json').write_text(json.dumps(result, indent=2) + '\n')
  print(json.dumps(result))


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest='command', required=True)
  r = commands.add_parser('run')
  r.add_argument('--repo', type=Path, default=HERE.parents[1])
  r.add_argument('--bundle', type=Path, required=True)
  r.add_argument('--output', type=Path, required=True)
  r.add_argument('--config', choices=sorted(CONFIGS), default='new')
  r.add_argument('--rate', type=int, default=44100)
  r.add_argument('--road', action='store_true')
  r.add_argument('--control', action='store_true')
  a = commands.add_parser('audio')
  a.add_argument('--repo', type=Path, required=True)
  a.add_argument('--root', type=Path, required=True)
  a.add_argument('--config', choices=sorted(CONFIGS), required=True)
  a.add_argument('--rate', type=int, required=True)
  f = commands.add_parser('feeder')
  f.add_argument('--server', required=True)
  f.add_argument('--rate', type=int, required=True)
  f.add_argument('--nice', type=int, required=True)
  h = commands.add_parser('hog')
  h.add_argument('--policy', choices=('other', 'idle', 'fifo'), required=True)
  h.add_argument('--prio', type=int, required=True)
  h.add_argument('--cores', required=True)
  h.add_argument('--ms', type=float, required=True)
  h.add_argument('--period', type=float, required=True)
  h.add_argument('--seconds', type=float, required=True)
  args = parser.parse_args()
  {'run': run, 'audio': audio, 'hog': hog, 'feeder': feeder}[args.command](args)


if __name__ == '__main__':
  main()
