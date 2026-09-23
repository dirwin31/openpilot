"""Optional real-browser checks: run with pytest + Playwright and its browsers installed.

UI_STREAM_BROWSER selects chromium (default) or webkit. No comma is required:
two localhost origins serve the real Vue wrapper/viewer with synthetic frames.
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[4]
SVG = b'''<svg xmlns="http://www.w3.org/2000/svg" width="2160" height="1080" viewBox="0 0 2160 1080">
<rect width="2160" height="1080" fill="#121a23"/><path d="M650 1080L1000 200M1510 1080L1160 200" stroke="#60df9d" stroke-width="32"/>
<text x="1080" y="160" text-anchor="middle" fill="white" font-size="120">71 mph</text>
<text x="1080" y="930" text-anchor="middle" fill="white" font-size="70">Synthetic UI test frame</text></svg>'''


@pytest.fixture(scope="module")
def browser():
  playwright = pytest.importorskip("playwright.sync_api")
  with playwright.sync_playwright() as pw:
    engine = getattr(pw, os.getenv("UI_STREAM_BROWSER", "chromium"))
    options = {}
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if engine.name == "chromium" and chrome.exists():
      options["executable_path"] = str(chrome)
    instance = engine.launch(**options)
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def viewer_site():
  counts = {"stream": 0, "input": [], "control_allowed": True}
  stream_port = 0

  class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
      pass

    def do_POST(self):
      if urlsplit(self.path).path == "/input":
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        counts["input"].append({"header": self.headers.get("X-UI-Stream-Control"), "gesture": body["gesture"]})
        self.respond({"ok": True, "error": ""})
        return
      self.respond({"streamState": "running", "streamSequence": 1})

    def respond(self, body, mime="application/json"):
      if isinstance(body, dict):
        body = json.dumps(body).encode()
      self.send_response(200)
      self.send_header("Content-Type", mime)
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      self.wfile.write(body)

    def do_GET(self):
      path = urlsplit(self.path).path
      if path == "/fixture":
        self.respond(b'''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/starpilot/system/the_galaxy/assets/mobile/css/material.css">
<style>body{margin:0}.fixture-shell{padding:16px;contain:layout style;transform:translateZ(0)}</style>
<script type="importmap">{"imports":{"vue":"/starpilot/system/the_galaxy/assets/vendor/vue/vue.esm-browser.js"}}</script>
<div id="root" class="fixture-shell"></div><script type="module">
import {createApp} from '/starpilot/system/the_galaxy/assets/vendor/vue/vue.esm-browser.js';
import {UiStream} from '/starpilot/system/the_galaxy/assets/mobile/js/views/UiStream.js';
window.testApp=createApp(UiStream);window.testApp.mount('#root');
</script>''', "text/html")
      elif path == "/api/device/status":
        self.respond({"streamPort": stream_port, "streamState": "running", "streamSequence": 1})
      elif path == "/status":
        allowed = counts["control_allowed"]
        self.respond({"state": "ready", "frameSequence": 1, "frameAgeMs": 10, "outputWidth": 2160, "outputHeight": 1080,
                      "control": {"available": True, "allowed": allowed, "reason": "" if allowed else "the car is onroad",
                                  "maxGestureSeconds": 3.0, "maxEvents": 400}})
      elif path == "/telemetry":
        self.respond({"schemaVersion": 1, "isMetric": False, "vEgo": 31.7, "setSpeed": 72,
                      "leadDist": 30, "brake": 0, "engaged": True, "driveState": "enabled",
                      "cpuTempC": 50, "cpuUsagePercent": 27, "memoryUsagePercent": 44,
                      "modelExecMs": 19, "frameDropPerc": 0})
      elif path == "/stream":
        counts["stream"] += 1
        self.respond(SVG, "image/svg+xml")
      else:
        target = ROOT / ("system/ui/lib/ui_stream.html" if path == "/" else path.lstrip("/"))
        if not target.resolve().is_relative_to(ROOT) or not target.is_file():
          self.send_error(404)
          return
        mime = {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}.get(target.suffix, "application/octet-stream")
        self.respond(target.read_bytes(), mime)

  servers = [ThreadingHTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
  stream_port = servers[1].server_port
  threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
  for thread in threads:
    thread.start()
  yield f"http://127.0.0.1:{servers[0].server_port}/fixture", counts
  for server, thread in zip(servers, threads, strict=True):
    server.shutdown()
    server.server_close()
    thread.join(2)


def open_viewer(browser, viewer_site, width, height, fullscreen=None):
  page = browser.new_page(viewport={"width": width, "height": height})
  page.set_default_timeout(5000)
  page.on("pageerror", lambda error: print(error))
  if fullscreen:
    request = "undefined" if fullscreen == "missing" else "() => Promise.reject(new Error('denied'))"
    page.add_init_script(f'''Object.defineProperty(Element.prototype, 'requestFullscreen', {{configurable:true,value:{request}}});
Object.defineProperty(Element.prototype, 'webkitRequestFullscreen', {{configurable:true,value:undefined}});''')
  page.goto(viewer_site[0])
  page.locator("iframe").wait_for()
  frame = page.frame_locator("iframe")
  frame.locator("#cam").evaluate("img => img.decode()")
  return page, frame


@pytest.mark.parametrize("size", [(390, 844), (844, 390), (768, 1024), (568, 320), (1280, 800)])
def test_telemetry_preserves_portrait_image_and_uses_landscape_sides(browser, viewer_site, size):
  page, frame = open_viewer(browser, viewer_site, *size)
  try:
    visible_width = "img => Math.min(img.clientWidth, img.clientHeight * img.naturalWidth / img.naturalHeight)"
    before = frame.locator("#cam").evaluate(visible_width)
    frame.locator("#telemetry-toggle").click()
    frame.locator("#telemetry-driving").get_by_text("enabled", exact=True).wait_for()
    stage = frame.locator("#stage").bounding_box()
    left = frame.locator("#telemetry-device").bounding_box()
    right = frame.locator("#telemetry-driving").bounding_box()
    if frame.locator("body").evaluate("() => innerWidth <= innerHeight"):
      assert frame.locator("#cam").evaluate(visible_width) >= before * 0.90
      assert left["y"] >= stage["y"] + stage["height"] - 1
      assert right["y"] == left["y"]
    else:
      assert left["x"] + left["width"] <= stage["x"]
      assert stage["x"] + stage["width"] <= right["x"]
    assert frame.locator("body").evaluate("el => el.scrollWidth <= innerWidth")
  finally:
    page.close()


@pytest.mark.parametrize("fullscreen", ["missing", "rejected"])
def test_fullscreen_fallback_preserves_stream_and_exits(browser, viewer_site, fullscreen):
  page, frame = open_viewer(browser, viewer_site, 390, 844, fullscreen)
  try:
    frame.locator("body").evaluate("() => { window.streamIdentity = 123; }")
    frame.locator("#fs").click()
    page.locator("dialog:modal").wait_for()
    assert page.locator("iframe").bounding_box() == {"x": 0, "y": 0, "width": 390, "height": 844}
    assert frame.locator("body").evaluate("() => window.streamIdentity") == 123
    frame.get_by_role("button", name="Exit expanded view").click()
    page.locator("dialog:modal").wait_for(state="hidden")
    assert page.locator("body").evaluate("el => el.style.overflow") == ""
    frame.locator("#fs").click()
    page.locator("dialog:modal").wait_for()
    page.keyboard.press("Escape")
    page.locator("dialog:modal").wait_for(state="hidden")
    frame.locator("#fs").click()
    page.locator("dialog:modal").wait_for()
    page.evaluate("window.testApp.unmount()")
    assert page.locator("dialog").count() == 0
    assert page.locator("body").evaluate("el => el.style.overflow") == ""
  finally:
    page.close()


def test_fullscreen_uses_native_api_when_available_and_rejects_spoofed_messages(browser, viewer_site):
  page, frame = open_viewer(browser, viewer_site, 844, 390)
  try:
    # A source tag alone cannot expand Galaxy: require the actual iframe window.
    page.evaluate('''() => window.postMessage({source:'starpilot-ui-stream',type:'fullscreen',expanded:true}, location.origin)''')
    assert page.locator("dialog:modal").count() == 0
    frame.locator("#fs").click()
    frame.get_by_role("button", name="Exit expanded view").wait_for()
    assert frame.locator("body").evaluate('''() => !!(document.fullscreenElement || document.webkitFullscreenElement) ||
      document.documentElement.classList.contains('expanded')''')
    frame.get_by_role("button", name="Exit expanded view").click()
    frame.get_by_role("button", name="Fullscreen", exact=True).wait_for()
  finally:
    page.close()


def _image_content(page, frame):
  content = frame.locator("#cam").evaluate("""img => {
    const r = img.getBoundingClientRect(), s = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight);
    const w = img.naturalWidth * s, h = img.naturalHeight * s;
    return {left: r.left + (r.width - w) / 2, top: r.top + (r.height - h) / 2, width: w, height: h};
  }""")
  frame_box = page.locator("iframe").bounding_box()
  content["left"] += frame_box["x"]
  content["top"] += frame_box["y"]
  return content


def _wait_for_input(page, counts, n=1):
  deadline = time.monotonic() + 3.0
  while time.monotonic() < deadline and len(counts["input"]) < n:
    page.wait_for_timeout(20)


@pytest.mark.parametrize("size", [(390, 844), (1280, 800)])
def test_control_sends_one_whole_gesture_mapped_through_the_letterbox(browser, viewer_site, size):
  counts = viewer_site[1]
  counts["input"].clear()
  page, frame = open_viewer(browser, viewer_site, *size)
  try:
    cam = frame.locator("#cam")
    # Off by default: a tap on the image sends nothing.
    box = cam.bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    toggle = frame.locator("#control-toggle")
    toggle.wait_for()
    assert counts["input"] == []

    toggle.click()
    assert toggle.get_attribute("aria-pressed") == "true"
    content = _image_content(page, frame)
    x = content["left"] + content["width"] * 0.25
    y = content["top"] + content["height"] * 0.75
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + content["width"] * 0.25, y, steps=4)
    page.wait_for_timeout(100)
    assert counts["input"] == []  # nothing leaves the browser mid-gesture
    page.mouse.up()
    _wait_for_input(page, counts)

    assert len(counts["input"]) == 1 and counts["input"][0]["header"] == "1"
    gesture = counts["input"][0]["gesture"]
    kinds = [event["type"] for event in gesture]
    assert kinds[0] == "down" and kinds[-1] == "up" and set(kinds[1:-1]) <= {"move"}
    assert gesture[0]["t"] == 0 and all(a["t"] <= b["t"] for a, b in zip(gesture, gesture[1:], strict=False))
    assert gesture[-1]["t"] >= 100  # original timing is preserved for replay
    assert gesture[0]["x"] == pytest.approx(0.25, abs=0.01) and gesture[0]["y"] == pytest.approx(0.75, abs=0.01)
    assert gesture[-1]["x"] == pytest.approx(0.5, abs=0.01)
    assert frame.locator("body").evaluate("el => el.scrollWidth <= innerWidth")
  finally:
    page.close()


def test_control_interrupted_gesture_is_never_sent(browser, viewer_site):
  counts = viewer_site[1]
  counts["input"].clear()
  page, frame = open_viewer(browser, viewer_site, 844, 390)
  try:
    frame.locator("#control-toggle").click()
    content = _image_content(page, frame)
    page.mouse.move(content["left"] + content["width"] / 2, content["top"] + content["height"] / 2)
    page.mouse.down()
    # The browser takes the pointer away (scroll takeover, lost capture, ...).
    frame.locator("#cam").dispatch_event("pointercancel", {"pointerId": 1, "bubbles": True})
    page.mouse.up()
    page.wait_for_timeout(300)
    assert counts["input"] == []
  finally:
    page.close()


def test_control_disables_itself_when_the_comma_refuses(browser, viewer_site):
  counts = viewer_site[1]
  page, frame = open_viewer(browser, viewer_site, 844, 390)
  try:
    toggle = frame.locator("#control-toggle")
    toggle.click()
    counts["control_allowed"] = False
    frame.locator("#control-toggle[disabled]").wait_for()
    assert toggle.get_attribute("aria-pressed") == "false"
    assert "onroad" in toggle.get_attribute("title")
  finally:
    counts["control_allowed"] = True
    page.close()
