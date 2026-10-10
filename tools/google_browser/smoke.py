"""Offline smoke test of the installed, sandboxed browser. Does not sign in to Google."""
import base64
import os
from pathlib import Path
import time
from urllib.parse import quote

from openpilot.starpilot.system.android_auto.google_browser import BrowserProcess, RUNTIME


def smoke(runtime=RUNTIME, screenshot=None):
  start = time.monotonic()
  browser = BrowserProcess(runtime)
  profile = None
  try:
    cdp = browser.start()
    profile = browser.directory
    version = cdp.call('Browser.getVersion', timeout=15)
    target = cdp.call('Target.createTarget', {'url': 'about:blank'})['targetId']
    session = cdp.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
    cdp.call('Emulation.setDeviceMetricsOverride', {'width': 480, 'height': 720, 'deviceScaleFactor': 1, 'mobile': False}, session)
    markup = '<html><body><h1>On-device browser</h1><input id="field" autofocus><p>Offline rendering and input check</p></body></html>'
    cdp.call('Page.navigate', {'url': 'data:text/html,' + quote(markup)}, session)
    cdp.call('Runtime.evaluate', {'expression': "document.querySelector('input').focus()"}, session)
    cdp.call('Input.insertText', {'text': 'human input'}, session)
    result = cdp.call('Runtime.evaluate', {'expression': "document.querySelector('input').value"}, session)
    assert result['result']['value'] == 'human input'
    image = cdp.call('Page.captureScreenshot', {'format': 'jpeg', 'quality': 65}, session)['data']
    assert base64.b64decode(image).startswith(b'\xff\xd8')
    if screenshot is not None:
      Path(screenshot).write_bytes(base64.b64decode(image))
    # Ask the browser itself whether it is sandboxed rather than trusting launch flags.
    cdp.call('Page.navigate', {'url': 'chrome://sandbox'}, session)
    deadline = time.monotonic() + 5
    status = ''
    while 'adequately sandboxed' not in status.lower() and time.monotonic() < deadline:
      status = cdp.call('Runtime.evaluate', {'expression': 'document.body.innerText'}, session)['result'].get('value', '')
      time.sleep(0.05)
    assert 'adequately sandboxed' in status.lower(), status
    print({'browser': version['product'], 'seconds': round(time.monotonic() - start, 2), 'jpeg_bytes': len(base64.b64decode(image)),
           'sandbox': 'enabled', 'uid': os.getuid()})
  finally:
    browser.close()
  assert profile is not None and not profile.exists()
  print('Browser and RAM profile closed successfully')


if __name__ == '__main__':
  smoke()
