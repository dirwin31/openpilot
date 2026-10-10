"""Downloads and installs the pinned browser runtime in the background after Galaxy setup, while parked."""
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import shutil
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from openpilot.starpilot.system.android_auto.browser_package import PACKAGE, RUNTIME
from tools.google_browser.install import install


class Paused(RuntimeError):
  pass


def checked_url(url):
  parsed = urlsplit(url)
  if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
      or parsed.hostname not in ('github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com')):
    raise ValueError('Untrusted browser package URL')
  return url


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self, req, fp, code, msg, headers, newurl):
    return super().redirect_request(req, fp, code, msg, headers, checked_url(newurl))


def download(asset, destination, check, progress):
  digest = hashlib.sha256()
  received = 0
  opener = urllib.request.build_opener(ReleaseRedirect())
  request = urllib.request.Request(checked_url(asset['url']), headers={'User-Agent': 'StarPilot-Galaxy-Setup'})
  with opener.open(request, timeout=10) as response, destination.open('wb') as output:
    if response.status != 200:
      raise ValueError('Incomplete browser package response')
    while True:
      check()
      block = response.read(64 * 1024)
      if not block:
        break
      received += len(block)
      if received > asset['bytes']:
        raise ValueError('Browser package exceeds expected size')
      output.write(block)
      digest.update(block)
      progress(received)
  if received != asset['bytes'] or digest.hexdigest() != asset['sha256']:
    raise ValueError('Browser package integrity check failed')


class BrowserInstall:
  def __init__(self, *, ready, parked, destination=RUNTIME, package=PACKAGE, enabled=None, fetch=download):
    self.ready, self.parked, self.destination, self.package = ready, parked, destination, package
    self.enabled = (Path('/AGNOS').is_file() and platform.machine() == 'aarch64') if enabled is None else enabled
    self.fetch = fetch
    self.lock = threading.Lock()
    self.stop = threading.Event()
    self.thread = None
    self.retry_at = 0.0
    self.state, self.percent = 'pending', 0
    self.setup_seen = destination.parent / '.galaxy-setup-complete'

  def mark_setup_complete(self):
    if self.enabled and not self.setup_seen.exists():
      self.setup_seen.parent.mkdir(parents=True, exist_ok=True)
      self.setup_seen.touch(mode=0o600)

  def installed(self):
    try:
      return ((self.destination / 'package-id').read_text().strip() == self.package['id'] and
              all((self.destination / path).is_file() for path in ('playlink', 'root/usr/lib/chromium/chromium',
                   'root/usr/bin/bwrap', 'root/usr/bin/Xvfb', 'root/usr/local/bin/google-browser')))
    except OSError:
      return False

  def status(self):
    with self.lock:
      return {'state': self.state, 'percent': self.percent}

  def maintain(self):
    if not self.enabled or self.stop.is_set():
      return
    with self.lock:
      if self.state == 'ready' or self.thread is not None and self.thread.is_alive():
        return
      if self.installed():
        self.state, self.percent = 'ready', 100
        return
      if time.monotonic() < self.retry_at:
        return
      try:
        setup_ready = self.setup_seen.exists() or self.ready()
        parked = self.parked()
      except Exception:
        self.state, self.retry_at = 'retrying', time.monotonic() + 300
        return
      if not setup_ready:
        return
      if not parked:
        self.state = 'paused'
        return
      self.state, self.percent = 'downloading', 0
      self.thread = threading.Thread(target=self._run, name='galaxy-browser-install', daemon=True)
      self.thread.start()

  def _run(self):
    if platform.system() == 'Linux':
      try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
      except OSError:
        pass
    deadline = time.monotonic() + 1800
    def check():
      if self.stop.is_set() or not self.parked():
        raise Paused()
      if time.monotonic() > deadline:
        raise TimeoutError()
    total = sum(self.package[name]['bytes'] for name in ('browser', 'resolver'))
    def progress(offset, received):
      with self.lock:
        self.percent = min(99, (offset + received) * 100 // total)
    try:
      self.destination.parent.mkdir(parents=True, exist_ok=True)
      with (self.destination.parent / '.install.lock').open('a') as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not self.installed():
          check()
          # A power loss cannot run TemporaryDirectory cleanup. The exclusive
          # lock proves no other installer still owns these staging directories.
          for leftover in self.destination.parent.glob('.download-*'):
            if leftover.is_dir() and not leftover.is_symlink():
              shutil.rmtree(leftover)
          # Room for the downloads, the extracted runtime and a margin.
          if shutil.disk_usage(self.destination.parent).free < 3 * 1024**3:
            raise OSError('Insufficient storage')
          with tempfile.TemporaryDirectory(prefix='.download-', dir=self.destination.parent) as directory:
            work = Path(directory)
            offset = 0
            for name in ('browser', 'resolver'):
              self.fetch(self.package[name], work / name, check, lambda count, offset=offset: progress(offset, count))
              offset += self.package[name]['bytes']
            check()
            with self.lock:
              self.state = 'installing'
            install(work / 'browser', self.package['browser']['sha256'], work / 'runtime', check=check)
            helper = work / 'resolver'
            with helper.open('rb') as executable:
              header = executable.read(20)
            if header[:5] != b'\x7fELF\x02' or int.from_bytes(header[18:20], 'little') != 183:
              raise ValueError('Resolver is not ARM64')
            helper.chmod(0o755)
            helper.rename(work / 'runtime/playlink')
            (work / 'runtime/package-id').write_text(self.package['id'] + '\n')
            check()
            # Each package id installs to its own directory, so a running older version is never replaced.
            (work / 'runtime').rename(self.destination)
      with self.lock:
        self.state, self.percent = 'ready', 100
    except Paused:
      with self.lock:
        self.state, self.retry_at = 'paused', time.monotonic() + 10
    except Exception:
      with self.lock:
        self.state, self.retry_at = 'retrying', time.monotonic() + 300

  def close(self):
    self.stop.set()
    if self.thread is not None:
      self.thread.join(timeout=12)
