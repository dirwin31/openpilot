import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
UI_ROOT = REPO_ROOT / "starpilot/system/the_galaxy/assets/mobile"


def _read(relative: str) -> str:
  return (UI_ROOT / relative).read_text(encoding="utf-8")


def test_telematics_route_navigation_and_styles_are_wired():
  app = _read("js/app.js")
  store = _read("js/store.js")
  shell = _read("js/components/AppShell.js")
  tools = _read("js/views/Tools.js")
  index = _read("index.html")

  assert 'import { Telematics } from "./views/Telematics.js"' in app
  assert '"/telematics": Telematics' in app
  assert '"/telematics"' in store
  assert '"/dashboard"' not in app
  assert '"/dashboard"' not in store
  assert '"/dashboard"' not in shell
  assert 'name: "Telematics", link: "/telematics"' in shell
  assert 'name: "Telematics", link: "/telematics"' in tools
  assert "isIOSDevice" in shell and "isIOSDevice" in tools
  assert "visibleTools" in tools
  assert ':class="{ active: isActive(link.link) }"' in shell
  assert "fullScreenTelematics" in shell
  assert 'matchMedia("(orientation: landscape)")' in shell
  assert 'href="/assets/mobile/css/telematics.css"' in index
  assert not (UI_ROOT / "js/views/Dashboard.js").exists()
  assert not (UI_ROOT / "css/dashboard.css").exists()


def test_telematics_has_security_gate_controls_and_complete_layouts():
  telematics = _read("js/views/Telematics.js")
  assert "window.isSecureContext" in telematics
  assert "isIOSDevice" in telematics
  assert "homeURL.hash = \"/\"" in telematics
  assert 'window.location.protocol !== "https:"' in telematics
  assert "window.location.replace(this.secureURL)" in telematics
  assert "navigator.bluetooth" in telematics
  assert "Chrome on Android required" in telematics
  assert 'target.port = "8443"' in telematics
  assert "reconnectRemembered" in telematics
  assert "Pair the device first" in telematics
  assert "telematics-landscape" in telematics
  assert "telematics-portrait" in telematics
  assert "STEER DELAY" in telematics
  assert "LONGITUDINAL %" in telematics
  assert "LEAD VEHICLE" in telematics
  assert "CURVE TARGET" in telematics
  assert "STOP SIGNAL" in telematics
  assert "requestFullscreen" in telematics
  assert 'navigationUI: "hide"' in telematics
  assert "fullscreenchange" in telematics
  assert "bi-fullscreen-exit" in telematics
  assert "demo" not in telematics.lower()


def test_ble_client_uses_companion_service_and_coalesces_updates():
  client = _read("js/ble/live_ble.js")
  assert "9b6d1000-6f7a-4a5b-8c3d-2e1f0a9b8c7d" in client
  assert "navigator.bluetooth.requestDevice" in client
  assert "navigator.bluetooth.getDevices" in client
  assert "startNotifications" in client
  assert "characteristicvaluechanged" in client
  assert 'command("get_live_metadata")' in client
  assert "metadataRevision" in client and "alertID" in client
  assert "200 - elapsed" in client
  assert "gattserverdisconnected" in client
  assert "PAIRING_MESSAGE" in client
  assert "SECURITY_READ_RETRIES" in client
  assert "_readAuthenticatedStatus" in client
  assert "_startNotifications" in client
  assert "CONNECT_TIMEOUT_MS" in client
  assert "device.gatt.disconnect()" in client
  assert "this.device?.gatt?.connected === true" in client
  assert "allowGenericGattError" in client
  assert "demo" not in client.lower()


@pytest.mark.skipif(shutil.which("node") is None, reason="no node.js runtime available")
def test_telematics_javascript_modules_parse(tmp_path):
  for relative in ("js/ble/live_frames.js", "js/ble/live_ble.js", "js/views/Telematics.js", "js/components/GalaxyModal.js"):
    source = UI_ROOT / relative
    target = tmp_path / (source.stem + ".mjs")
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run([shutil.which("node"), "--check", str(target)], capture_output=True, text=True)
    assert result.returncode == 0, f"{relative} failed to parse:\n{result.stderr}"


@pytest.mark.skipif(shutil.which("node") is None, reason="no node.js runtime available")
def test_android_setup_preserves_pairing_and_existing_connections():
  script = r'''
import assert from "node:assert/strict"
import fs from "node:fs"
const notices = []
globalThis.showSnackbar = (...args) => notices.push(args)
const source = fs.readFileSync("js/views/Telematics.js", "utf8").replace(/^import .*$/gm, "")
const moduleURL = (code) => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
const { Telematics } = await import(moduleURL("const GxNotice = {}, GalaxyModal = {};\n" + source))
const bluetooth = {}
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { userAgent: "Android", bluetooth } })
let connects = 0
let reconnects = 0
const makeView = () => Object.assign(Telematics.data(), Telematics.methods, {
  ble: { connect() { connects++ }, reconnect() { reconnects++ } },
})
const view = makeView()
await view.connect()
assert.equal(view.showBluetoothSetup, true)
assert.equal(connects, 0, "Opening setup must not open the chooser")
assert.equal(view.connecting, false)
const pairing = view.continueBluetoothPairing()
assert.equal(connects, 1, "Continue must invoke pairing before yielding user activation")
await pairing
assert.equal(view.showBluetoothSetup, false)
await view.connect()
assert.equal(connects, 2, "Skipping setup must allow subsequent pairing attempts")

bluetooth.getDevices = async () => []
const supported = makeView()
await supported.connect()
assert.equal(supported.showBluetoothSetup, false)
assert.equal(connects, 3)
delete bluetooth.getDevices
const existing = makeView()
existing.ble.device = { id: "remembered-in-this-page" }
existing.bleState = "error"
await existing.connect()
assert.equal(existing.showBluetoothSetup, false)
assert.equal(reconnects, 1, "Existing device reconnect must not require setup")
navigator.userAgent = "Macintosh"
const desktop = makeView()
await desktop.connect()
assert.equal(desktop.showBluetoothSetup, false)
assert.equal(connects, 4)

let copied
navigator.clipboard = { async writeText(value) { copied = value } }
await view.copyBluetoothSetting("enable-web-bluetooth-new-permissions-backend")
assert.equal(copied, "chrome://flags/#enable-web-bluetooth-new-permissions-backend")
delete navigator.clipboard
await view.copyBluetoothSetting("enable-experimental-web-platform-features")
assert.equal(notices.at(-1)[1], "error", "Clipboard failure must offer manual copying")
'''
  result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], cwd=UI_ROOT, capture_output=True, text=True)
  assert result.returncode == 0, result.stderr
