"""On-demand Google sign-in browser, operated by the user through Galaxy.

Galaxy receives only rendered frames and sends a fixed input vocabulary. CDP,
cookies and the RAM profile stay on the comma; there is no generic CDP proxy.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import secrets
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

from openpilot.starpilot.system.android_auto.browser_package import RUNTIME
LOGIN_URL = 'https://accounts.google.com/EmbeddedSetup'
WIDTH, HEIGHT = 480, 720
MAX_SECONDS = 600
DISCONNECT_SECONDS = 90  # Allow switching to a phone's Google approval app.
ACTIVE = ('starting', 'signing_in', 'finishing')


class BrowserError(RuntimeError):
  pass


def runtime_error(runtime=RUNTIME, namespace_root=Path('/proc/self/ns')):
  if not all((runtime / path).is_file() for path in ('root/usr/lib/chromium/chromium', 'root/usr/bin/bwrap',
                                                   'root/usr/bin/Xvfb', 'root/usr/local/bin/google-browser')):
    return 'The on-device browser is not installed. Use manual upload or install a build that includes it.'
  if not all((namespace_root / name).exists() for name in ('user', 'pid', 'ipc', 'uts', 'mnt', 'net')):
    return 'This device needs a browser-compatible AGNOS update. Manual upload and token import are still available.'
  return ''


def input_commands(value: dict) -> list[tuple[str, dict]]:
  """Validate before enqueueing. Never accept caller-supplied CDP methods/URLs."""
  if not isinstance(value, dict):
    raise BrowserError('Invalid browser input')
  kind = value.get('kind')
  if kind == 'text' and set(value) == {'kind', 'text'}:
    text = value['text']
    if isinstance(text, str) and 0 < len(text) <= 1024 and '\x00' not in text:
      return [('Input.insertText', {'text': text})]
  if kind == 'key' and set(value) == {'kind', 'key'}:
    key = value['key']
    codes = {'Enter': 13, 'Tab': 9, 'Backspace': 8, 'Delete': 46, 'Escape': 27,
             'ArrowLeft': 37, 'ArrowUp': 38, 'ArrowRight': 39, 'ArrowDown': 40, 'Home': 36, 'End': 35, 'SelectAll': 65}
    if isinstance(key, str) and key in codes:
      return [('Input.dispatchKeyEvent', {'type': event, 'key': 'a' if key == 'SelectAll' else key,
                                          'windowsVirtualKeyCode': codes[key], 'modifiers': 2 if key == 'SelectAll' else 0,
                                          **({'text': '\r'} if key == 'Enter' and event == 'keyDown' else {})})
              for event in ('keyDown', 'keyUp')]
  if kind in ('tap', 'scroll') and set(value) == ({'kind', 'x', 'y'} if kind == 'tap' else {'kind', 'x', 'y', 'delta'}):
    def number(key, low, high):
      v = value.get(key)
      return type(v) in (int, float) and math.isfinite(v) and low <= v <= high
    if number('x', 0, WIDTH - 1) and number('y', 0, HEIGHT - 1):
      point = {'x': value['x'], 'y': value['y']}
      if kind == 'tap':
        return [('Input.dispatchMouseEvent', {**point, 'type': event, 'button': 'left', 'clickCount': 1})
                for event in ('mousePressed', 'mouseReleased')]
      if number('delta', -720, 720):
        return [('Input.dispatchMouseEvent', {**point, 'type': 'mouseWheel', 'deltaX': 0, 'deltaY': value['delta']})]
  raise BrowserError('Invalid browser input')


class PipeCDP:
  """Serial CDP client over anonymous inherited pipes, with bounded I/O."""

  def __init__(self, read_fd, write_fd):
    self.read_fd, self.write_fd = read_fd, write_fd
    self.serial = 0
    self.buffer = bytearray()
    self.guard = lambda: None
    os.set_blocking(read_fd, False)
    os.set_blocking(write_fd, False)

  def call(self, method, params=None, session=None, timeout=3):
    self.serial += 1
    message = {'id': self.serial, 'method': method, 'params': params or {}}
    if session is not None:
      message['sessionId'] = session
    data = json.dumps(message).encode() + b'\0'
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as poll:
      poll.register(self.write_fd, selectors.EVENT_WRITE)
      while data:
        self.guard()
        if time.monotonic() >= deadline:
          raise BrowserError('Browser stopped responding')
        if not poll.select(min(0.25, max(0, deadline - time.monotonic()))):
          continue
        try:
          data = data[os.write(self.write_fd, data):]
        except BlockingIOError:
          continue
      poll.unregister(self.write_fd)
      poll.register(self.read_fd, selectors.EVENT_READ)
      while time.monotonic() < deadline:
        self.guard()
        while b'\0' in self.buffer:
          raw, _, remaining = self.buffer.partition(b'\0')
          self.buffer = bytearray(remaining)
          response = json.loads(raw)
          if response.get('id') == self.serial:
            if 'error' in response:
              raise BrowserError('Browser could not complete the request')
            return response.get('result', {})
        if len(self.buffer) > 4 * 1024 * 1024:
          raise BrowserError('Browser response exceeded the size limit')
        if not poll.select(min(0.25, max(0, deadline - time.monotonic()))):
          continue
        chunk = os.read(self.read_fd, 65536)
        if not chunk:
          break
        self.buffer.extend(chunk)
    raise BrowserError('Browser stopped responding')

  def close(self):
    for fd in (self.read_fd, self.write_fd):
      os.close(fd)
    self.buffer.clear()


class BrowserProcess:
  """Runs the packaged browser through the sandboxed launcher, with CDP over inherited pipes."""

  def __init__(self, runtime=RUNTIME):
    self.runtime = runtime
    self.process = self.cdp = None
    self.directory = None

  def start(self):
    # /dev/shm keeps the Google profile off persistent storage.
    self.directory = Path(tempfile.mkdtemp(prefix='starpilot-google-', dir='/dev/shm'))
    read_command, write_command = os.pipe()
    read_result, write_result = os.pipe()
    try:
      self.process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name('google_browser_launch.py')),
         str(self.runtime), str(self.directory), str(read_command), str(write_result), str(os.getpid())],
        pass_fds=(read_command, write_result), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
      self.cdp = PipeCDP(read_result, write_command)
    except BaseException:
      os.close(read_result)
      os.close(write_command)
      self.close()
      raise
    finally:
      os.close(read_command)
      os.close(write_result)
    # The pipe becomes usable after the private display and Chromium start.
    return self.cdp

  def close(self):
    if self.process is not None:
      # Kill the supervisor's process group even if its leader already exited.
      try:
        os.killpg(self.process.pid, signal.SIGTERM)
      except ProcessLookupError:
        pass
      try:
        self.process.wait(timeout=2)
      except subprocess.TimeoutExpired:
        os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait(timeout=2)
      self.process = None
    if self.cdp is not None:
      self.cdp.close()
      self.cdp = None
    if self.directory is not None:
      shutil.rmtree(self.directory, ignore_errors=True)
      self.directory = None


class GoogleBrowser:
  def __init__(self, *, permitted, imported, available=None, factory=BrowserProcess, clock=time.monotonic):
    self.permitted, self.imported, self.factory, self.clock = permitted, imported, factory, clock
    self.unavailable_reason = (lambda: runtime_error()) if available is None else (lambda: '')
    self.available = available or (lambda: not self.unavailable_reason())
    self.lock = threading.RLock()
    self.thread = None
    self.cancel = threading.Event()
    self.inputs = queue.Queue(maxsize=32)
    self.state, self.message, self.frame, self.origin = 'idle', '', '', ''
    self.owner = None
    self.session_id = ''
    self.last_seen = self.started = 0.0

  def busy(self):
    with self.lock:
      return self.thread is not None and self.thread.is_alive()

  def status(self, owner):
    with self.lock:
      if self.owner is not None and owner != self.owner:
        return {'available': self.available(), 'state': 'busy' if self.busy() else 'idle'}
      return {'available': self.available(), 'state': self.state, 'message': self.message or self.unavailable_reason(),
              'id': self.session_id, 'width': WIDTH, 'height': HEIGHT}

  def start(self, owner, email, remember):
    with self.lock:
      if self.busy():
        raise BrowserError('A Google sign-in is already running')
      if not self.available():
        raise BrowserError(self.unavailable_reason() or 'The on-device browser is not installed; use manual upload')
      if not self.permitted(owner):
        raise BrowserError('Sign in to Galaxy and put the car in Park before connecting Google')
      self.owner, self.session_id = owner, secrets.token_hex(16)
      self.state, self.message, self.frame, self.origin = 'starting', '', '', ''
      self.started = self.last_seen = self.clock()
      self.cancel = threading.Event()
      self.inputs = queue.Queue(maxsize=32)
      self.thread = threading.Thread(target=self._run, args=(owner, email, remember), name='google-setup-browser', daemon=True)
      self.thread.start()
      return self.status(owner)

  def action(self, owner, session_id, operation, value=None):
    with self.lock:
      if owner != self.owner or session_id != self.session_id or not self.permitted(owner):
        raise BrowserError('Google sign-in session expired')
      if operation == 'stop':
        self.cancel.set()
        self.frame = ''
      elif operation in ('frame', 'input'):
        self.last_seen = self.clock()
        if operation == 'input':
          if self.state != 'signing_in' or self.cancel.is_set():
            raise BrowserError('Google sign-in is not ready')
          commands = input_commands(value)
          try:
            self.inputs.put_nowait(commands)
          except queue.Full:
            raise BrowserError('Browser is catching up; try again') from None
      else:
        raise BrowserError('Invalid browser operation')
      return {**self.status(owner), 'frame': self.frame if operation == 'frame' else '', 'origin': self.origin}

  def _check(self, owner):
    if self.cancel.is_set() or not self.permitted(owner):
      raise BrowserError('Google sign-in closed')
    if self.clock() - self.started >= MAX_SECONDS:
      raise BrowserError('Google sign-in timed out; start again')
    if self.clock() - self.last_seen >= DISCONNECT_SECONDS:
      raise BrowserError('Google sign-in closed after disconnecting')

  def _run(self, owner, email, remember):
    process = self.factory()
    token = ''
    result = 'closed'
    try:
      cdp = process.start()
      cdp.guard = lambda: self._check(owner)
      cdp.call('Browser.getVersion', timeout=15)
      target = cdp.call('Target.createTarget', {'url': 'about:blank'})['targetId']
      session = cdp.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
      cdp.call('Browser.setDownloadBehavior', {'behavior': 'deny'})
      cdp.call('Emulation.setDeviceMetricsOverride', {'width': WIDTH, 'height': HEIGHT, 'deviceScaleFactor': 1, 'mobile': False}, session)
      navigation = cdp.call('Page.navigate', {'url': LOGIN_URL}, session, timeout=15)
      # Fresh profiles can initialize the certificate verifier during the first
      # request. Retry that configuration race once, with validation still on.
      if navigation.get('errorText') == 'net::ERR_CERT_VERIFIER_CHANGED':
        self._check(owner)
        navigation = cdp.call('Page.navigate', {'url': LOGIN_URL}, session, timeout=15)
      if navigation.get('errorText'):
        raise BrowserError('Could not open Google sign-in. Check your connection and try again.')
      with self.lock:
        self.state = 'signing_in'
      next_frame = 0.0
      while True:
        self._check(owner)
        cookies = cdp.call('Storage.getCookies').get('cookies', [])
        for cookie in cookies:
          if (cookie.get('name') == 'oauth_token' and cookie.get('domain', '').lstrip('.') == 'accounts.google.com'
              and cookie.get('secure') is True and isinstance(cookie.get('value'), str)):
            token = cookie['value']
            break
        cookies.clear()
        if token:
          self._check(owner)
          with self.lock:
            self.state, self.frame = 'finishing', ''
          # Tear down every browser process/profile before handing off the token.
          process.close()
          self._check(owner)
          self.imported(owner, email, token, remember)
          result = 'complete'
          break
        for _ in range(16):
          try:
            commands = self.inputs.get_nowait()
          except queue.Empty:
            break
          for method, params in commands:
            self._check(owner)
            cdp.call(method, params, session)
          commands.clear()
        if self.clock() >= next_frame:
          info = cdp.call('Target.getTargetInfo', {'targetId': target})['targetInfo']
          parsed = urlsplit(info.get('url', ''))
          origin = f'{parsed.scheme}://{parsed.hostname}' if parsed.scheme in ('https', 'http') else ''
          frame = cdp.call('Page.captureScreenshot', {'format': 'jpeg', 'quality': 65}, session).get('data', '')
          if not isinstance(frame, str) or len(frame) > 2 * 1024 * 1024:
            raise BrowserError('Browser frame exceeded the size limit')
          self._check(owner)
          with self.lock:
            self.frame, self.origin = frame, origin
          next_frame = self.clock() + 0.5
        self.cancel.wait(0.1)
    except BrowserError as error:
      with self.lock:
        self.message = str(error)
      result = 'closed' if self.cancel.is_set() else 'failed'
    except Exception:
      # CDP/OS errors can contain login URLs or credentials, so show a generic message.
      with self.lock:
        self.message = 'Could not complete Google sign-in. Try again or use manual upload.'
      result = 'failed'
    finally:
      token = ''
      process.close()
      with self.lock:
        self.frame, self.origin, self.state = '', '', result
        while not self.inputs.empty():
          self.inputs.get_nowait()

  def close(self):
    self.cancel.set()
    if self.thread is not None:
      self.thread.join(timeout=10)
