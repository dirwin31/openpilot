"""Starts the browser in bubblewrap on CDP pipes 3/4 and cleans up when Galaxy exits."""
import ctypes
import fcntl
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys


def launch(runtime, profile, read_fd, write_fd, expected_parent):
  root = Path(runtime) / 'root'
  process = None

  def stop(*_):
    raise SystemExit(0)

  signal.signal(signal.SIGTERM, stop)
  signal.signal(signal.SIGINT, stop)
  parent = os.getppid()
  # PR_SET_PDEATHSIG: if Galaxy dies we get SIGTERM and remove the browser and RAM profile.
  if ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
    raise RuntimeError('Parent death supervision unavailable')
  if os.getppid() != parent or parent != expected_parent:
    raise SystemExit(1)
  resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
  os.nice(10)
  try:
    # Duplicate first: source descriptors might themselves be 3 or 4.
    incoming = fcntl.fcntl(read_fd, fcntl.F_DUPFD_CLOEXEC, 5)
    outgoing = fcntl.fcntl(write_fd, fcntl.F_DUPFD_CLOEXEC, 5)
    os.dup2(incoming, 3, inheritable=True)
    os.dup2(outgoing, 4, inheritable=True)
    os.close(incoming)
    os.close(outgoing)
    command = [str(root / 'lib/ld-linux-aarch64.so.1'), '--library-path', str(root / 'usr/lib/aarch64-linux-gnu'),
               str(root / 'usr/bin/bwrap'), '--unshare-user', '--unshare-pid', '--unshare-ipc', '--unshare-uts',
               '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
               '--ro-bind', str(root), '/', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
               '--bind', str(profile), '/session', '--ro-bind', '/etc/resolv.conf', '/etc/resolv.conf',
               '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'HOME', '/session',
               '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'DISPLAY', ':99',
               '--setenv', 'XDG_RUNTIME_DIR', '/session', '--chdir', '/session', '/usr/local/bin/google-browser']
    process = subprocess.Popen(command, pass_fds=(3, 4), stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process.wait()
  finally:
    if process is not None:
      process.terminate()
      try:
        process.wait(timeout=2)
      except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    shutil.rmtree(profile, ignore_errors=True)


if __name__ == '__main__':
  launch(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))
