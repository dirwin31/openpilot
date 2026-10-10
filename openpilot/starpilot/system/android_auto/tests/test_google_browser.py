import json
import os
import threading
import time

import pytest

from openpilot.starpilot.system.android_auto.google_browser import BrowserError, GoogleBrowser, PipeCDP, input_commands, runtime_error


def test_runtime_admission_detects_missing_package_and_kernel_namespaces(tmp_path):
  assert 'not installed' in runtime_error(tmp_path, tmp_path / 'ns')
  for relative in ('root/usr/lib/chromium/chromium', 'root/usr/bin/bwrap', 'root/usr/bin/Xvfb', 'root/usr/local/bin/google-browser'):
    binary = tmp_path / relative
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.touch()
  assert 'AGNOS update' in runtime_error(tmp_path, tmp_path / 'ns')
  (tmp_path / 'ns').mkdir()
  for name in ('user', 'pid', 'ipc', 'uts', 'mnt', 'net'):
    (tmp_path / 'ns' / name).touch()
  assert runtime_error(tmp_path, tmp_path / 'ns') == ''


@pytest.mark.parametrize('value', [None, {}, {'kind': 'Runtime.evaluate', 'expression': 'secret'},
                                  {'kind': 'text', 'text': ''}, {'kind': 'text', 'text': 'x' * 1025},
                                  {'kind': 'tap', 'x': True, 'y': 0}, {'kind': 'tap', 'x': float('nan'), 'y': 0},
                                  {'kind': 'tap', 'x': 480, 'y': 0}, {'kind': 'key', 'key': 'F12'},
                                  {'kind': 'scroll', 'x': 2, 'y': 3, 'delta': 1000}])
def test_input_rejects_unbounded_or_arbitrary_commands(value):
  with pytest.raises(BrowserError):
    input_commands(value)


def test_pipe_handles_partial_response_and_events_without_exposing_errors():
  incoming, browser_out = os.pipe()
  browser_in, outgoing = os.pipe()
  client = PipeCDP(incoming, outgoing)
  def respond():
    message = json.loads(os.read(browser_in, 4096).rstrip(b'\0'))
    os.write(browser_out, b'{"method":"event"}\0{"id":')
    os.write(browser_out, str(message['id']).encode() + b',"result":{"ok":true}}\0')
  worker = threading.Thread(target=respond)
  worker.start()
  try:
    assert client.call('Browser.getVersion') == {'ok': True}
  finally:
    worker.join()
    client.close()
    os.close(browser_in)
    os.close(browser_out)


class FakeProcess:
  def __init__(self):
    self.closed = False
    self.cookies = []
    self.calls = []
    self.fail = False

  def start(self): return self
  def close(self): self.closed = True
  def call(self, method, params=None, session=None, **kwargs):
    self.calls.append((method, params))
    if self.fail:
      raise ValueError('secret-login-url?token=do-not-leak')
    if method == 'Target.createTarget':
      return {'targetId': 'target'}
    if method == 'Target.attachToTarget':
      return {'sessionId': 'private-cdp-session'}
    if method == 'Storage.getCookies':
      return {'cookies': list(self.cookies)}
    if method == 'Target.getTargetInfo':
      return {'targetInfo': {'url': 'https://accounts.google.com/signin?secret=not-public'}}
    if method == 'Page.captureScreenshot':
      return {'data': 'frame'}
    return {}


def wait_for(predicate):
  deadline = time.monotonic() + 2
  while not predicate() and time.monotonic() < deadline:
    time.sleep(0.01)
  assert predicate()


def owner(process=None, permitted=lambda _: True, clock=time.monotonic):
  process = process or FakeProcess()
  imports = []
  def imported(*args):
    assert process.closed  # All browser resources must be closed before import.
    imports.append(args)
  browser = GoogleBrowser(permitted=permitted, imported=imported, available=lambda: True, factory=lambda: process, clock=clock)
  return browser, process, imports


def test_no_browser_until_explicit_start_and_exact_owner_only():
  browser, process, _ = owner()
  assert browser.status(('owner',))['state'] == 'idle'
  assert not process.calls
  status = browser.start(('owner',), 'user@example.com', False)
  try:
    wait_for(lambda: browser.state == 'signing_in')
    with pytest.raises(BrowserError):
      browser.start(('other',), 'other@example.com', False)
    assert browser.status(('other',)) == {'available': True, 'state': 'busy'}
    with pytest.raises(BrowserError):
      browser.action(('other',), status['id'], 'frame')
    with pytest.raises(BrowserError):
      browser.action(('owner',), 'old-session', 'input', {'kind': 'text', 'text': 'private'})
    frame = browser.action(('owner',), status['id'], 'frame')
    assert frame['origin'] == 'https://accounts.google.com'
    assert 'secret=' not in json.dumps(frame)
  finally:
    browser.close()
  assert process.closed and not browser.busy() and browser.frame == ''


def test_google_cookie_handoff_is_private_and_browser_closes_first():
  browser, process, imports = owner()
  process.cookies = [{'name': 'oauth_token', 'domain': '.accounts.google.com', 'secure': True, 'value': 'oauth2_4/private'}]
  browser.start(('owner',), 'user@example.com', True)
  wait_for(lambda: not browser.busy())
  assert imports == [(('owner',), 'user@example.com', 'oauth2_4/private', True)]
  assert browser.status(('owner',))['state'] == 'complete'
  assert 'private' not in json.dumps(browser.status(('owner',)))
  assert browser.frame == '' and process.closed


@pytest.mark.parametrize('error,retried', [('net::ERR_CERT_VERIFIER_CHANGED', True), ('net::ERR_CERT_AUTHORITY_INVALID', False)])
def test_startup_retries_only_verifier_configuration_race(error, retried):
  process = FakeProcess()
  original = process.call
  navigations = []
  def call(method, *args, **kwargs):
    if method == 'Page.navigate':
      navigations.append(method)
      return {'errorText': error} if len(navigations) == 1 else {}
    return original(method, *args, **kwargs)
  process.call = call
  browser, _, imports = owner(process)
  try:
    browser.start(('owner',), 'user@example.com', False)
    wait_for(lambda: browser.state in ('signing_in', 'failed'))
    assert len(navigations) == (2 if retried else 1)
    assert browser.state == ('signing_in' if retried else 'failed')
    assert not imports
  finally:
    browser.close()


@pytest.mark.parametrize('domain,secure', [('evil.google.com', True), ('accounts.google.com.evil.com', True), ('accounts.google.com', False)])
def test_does_not_capture_other_cookie_domains(domain, secure):
  browser, process, imports = owner()
  process.cookies = [{'name': 'oauth_token', 'domain': domain, 'secure': secure, 'value': 'oauth2_4/private'}]
  browser.start(('owner',), 'user@example.com', False)
  wait_for(lambda: browser.state == 'signing_in')
  browser.close()
  assert imports == []


@pytest.mark.parametrize('reason', ['permission', 'disconnect', 'deadline'])
def test_lifecycle_stops_without_frontend_polling(reason):
  state = {'permit': True, 'now': 0}
  browser, process, imports = owner(permitted=lambda _: state['permit'], clock=lambda: state['now'])
  browser.start(('owner',), 'user@example.com', False)
  wait_for(lambda: browser.state == 'signing_in')
  if reason == 'permission':
    state['permit'] = False
  if reason == 'disconnect':
    state['now'] = 91
  if reason == 'deadline':
    state['now'] = 601
    browser.last_seen = 601
  wait_for(lambda: not browser.busy())
  assert process.closed and not imports and browser.frame == ''


def test_crashes_do_not_disclose_secrets_and_are_retryable():
  browser, process, imports = owner()
  process.fail = True
  browser.start(('owner',), 'user@example.com', False)
  wait_for(lambda: not browser.busy())
  assert 'do-not-leak' not in json.dumps(browser.status(('owner',)))
  assert browser.state == 'failed' and process.closed and not imports
  process.fail = False
  browser.start(('owner',), 'user@example.com', False)
  wait_for(lambda: browser.state == 'signing_in')
  browser.close()
