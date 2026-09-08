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
  # the insecure gate must explain the jump instead of silently redirecting to :8443
  assert "window.location.replace(this.secureURL)" not in telematics
  assert ":href=\"secureURL\"" in telematics
  assert "NET::ERR_CERT_AUTHORITY_INVALID" in telematics
  assert "Proceed to {{ secureHost }} (unsafe)" in telematics
  # the gate must lead with the notice and action, not bury them under prose
  assert "telematics-gate__open" in telematics
  assert telematics.index("GxNotice") < telematics.index("telematics-gate__steps")
  assert "<details" in telematics
  assert "navigator.bluetooth" in telematics
  assert "Chrome on Android required" in telematics
  assert 'target.port = "8443"' in telematics
  assert "reconnectRemembered" in telematics
  assert "Pair the device first" in telematics
  # the Chrome flag instructions must stay reachable, not be a one-shot interstitial
  assert '@click="openBluetoothSetup"' in telematics
  assert "canRestoreBluetooth" in telematics
  # the banner must be driven by a real fault so it disappears when nothing is wrong
  assert "Chrome forgets this pairing on reload" in telematics
  assert 'v-else-if="bluetoothBannerVisible"' in telematics
  assert "getAvailability" in telematics
  # the reload banner replaces the bar's controls rather than sitting beside them
  assert "bluetoothControlsHidden" in telematics
  assert "bluetoothBannerVisible" in telematics
  assert 'v-if="!bluetoothControlsHidden"' in telematics
  # every banner carries its own way into the panel, so the lightbulb steps aside
  assert 'v-if="!bluetoothBannerVisible"' in telematics
  assert "takeover: true" in telematics
  # the device side of pairing is invisible from the phone, so the panel must spell it out
  assert "pair a phone" in telematics
  assert "discoverable / 120s" in telematics
  # the briefing ends at the real pair button
  assert '"Pair now"' in telematics
  assert "at the bottom of this panel" in telematics
  # pairing from Android's settings is the common wrong turn; warn against it
  assert "Do not pair from Android's Bluetooth settings" in telematics
  assert "only creates a system bond" in telematics
  assert 'v-if="showPairingSteps"' in telematics
  # Disconnect cannot revoke the Chrome permission, so say how to do it by hand
  assert "Make Chrome forget this device" in telematics
  assert "Bluetooth devices" in telematics
  assert "Connected devices" in telematics
  assert 'v-if="rememberedDevices > 0"' in telematics
  assert "telematics-setup__more" in telematics
  assert "bluetoothChecks" in telematics
  assert "bluetoothNeedsAttention" in telematics
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
  # a remembered device carries no scan result after a reload, so connecting to
  # it needs a fresh advertisement first
  assert "watchAdvertisements" in client
  assert "advertisementreceived" in client
  assert "ADVERTISEMENT_TIMEOUT_MS" in client
  assert "enable-experimental-web-platform-features" in client
  assert "demo" not in client.lower()


@pytest.mark.skipif(shutil.which("node") is None, reason="no node.js runtime available")
def test_remembered_reconnect_rescans_when_chrome_reports_out_of_range(tmp_path):
  for relative in ("js/ble/live_frames.js", "js/ble/live_ble.js"):
    source = UI_ROOT / relative
    body = source.read_text(encoding="utf-8").replace('"./live_frames.js"', '"./live_frames.mjs"')
    (tmp_path / (source.stem + ".mjs")).write_text(body, encoding="utf-8")

  script = r'''
import assert from "node:assert/strict"
import { LiveBLEClient, COMPANION_UUIDS } from "./live_ble.mjs"

const outOfRange = () => Object.assign(new Error("Bluetooth Device is no longer in range."), { name: "NetworkError" })
const characteristic = (uuid) => ({
  readValue: async () => new TextEncoder().encode(JSON.stringify(uuid === COMPANION_UUIDS.status ? { ok: true } : {})),
  addEventListener() {}, removeEventListener() {}, startNotifications: async () => {},
})

const makeDevice = ({ advertises = true, watchable = true } = {}) => {
  const listeners = {}
  const device = {
    id: "remembered", name: "Galaxy device", seen: false, watchingAdvertisements: false, watches: 0, connects: 0,
    addEventListener(type, handler) { (listeners[type] ||= []).push(handler) },
    removeEventListener(type, handler) { listeners[type] = (listeners[type] || []).filter((entry) => entry !== handler) },
    gatt: {
      connected: false,
      disconnect() { this.connected = false },
      async connect() {
        device.connects += 1
        // Chrome only connects to a peripheral it has seen advertise since load.
        if (!device.seen) throw outOfRange()
        this.connected = true
        return {
          getPrimaryService: async () => ({ getCharacteristic: async (uuid) => characteristic(uuid) }),
        }
      },
    },
  }
  if (watchable) {
    device.watchAdvertisements = async ({ signal }) => {
      device.watches += 1
      device.watchingAdvertisements = true
      signal?.addEventListener?.("abort", () => { device.watchingAdvertisements = false })
      if (!advertises) return
      setTimeout(() => {
        device.seen = true
        for (const handler of listeners.advertisementreceived || []) handler({})
      }, 5)
    }
  }
  return device
}

const states = []
const clientFor = (devices) => {
  const bluetooth = { getDevices: async () => devices }
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { bluetooth } })
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: { getItem: () => null, setItem() {} } })
  const client = new LiveBLEClient({ onState: ({ state, message }) => states.push([state, message]) })
  client.advertisementTimeoutMs = 50
  return client
}

// A refresh: the remembered device reads as out of range until it advertises.
const device = makeDevice()
const client = clientFor([device])
assert.equal(await client.reconnectRemembered(), true, "a rescan must recover the remembered device")
assert.equal(client.state, "connected")
assert.equal(device.connects, 2, "the connect must be retried after the advertisement")
assert.equal(device.watches, 1)
assert.equal(device.watchingAdvertisements, false, "the scan must stop once the device is seen")
assert.ok(states.some(([, message]) => message === "Looking for the device…"))
client.close()

// A device that never advertises must surface guidance, not Chrome raw text.
const silent = makeDevice({ advertises: false })
const waiting = clientFor([silent])
assert.equal(await waiting.reconnectRemembered(), false)
assert.equal(waiting.state, "error")
assert.match(waiting.message, /Waiting for the device to advertise/)
waiting.close()

// Without the experimental flag no rescan is possible, so name the missing flag.
const unwatchable = makeDevice({ watchable: false })
const blocked = clientFor([unwatchable])
assert.equal(await blocked.reconnectRemembered(), false)
assert.equal(unwatchable.connects, 1, "no rescan is possible, so no retry")
assert.match(blocked.message, /enable-experimental-web-platform-features/)
blocked.close()
'''
  result = subprocess.run(
    [shutil.which("node"), "--input-type=module", "-e", script], cwd=tmp_path, capture_output=True, text=True)
  assert result.returncode == 0, result.stderr


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

// The reconnect flag being on is no longer enough to skip the briefing: a first
// pair always sees the flags and the "pair a phone" steps before the chooser.
bluetooth.getDevices = async () => []
const supported = makeView()
await supported.connect()
assert.equal(supported.showBluetoothSetup, true, "a first pair must see the setup panel")
assert.equal(connects, 2, "the briefing must not open the chooser yet")

// Once Chrome remembers a device, Connect goes straight to pairing.
const returning = makeView()
returning.rememberedDevices = 1
await returning.connect()
assert.equal(returning.showBluetoothSetup, false, "a remembered device must not be briefed again")
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

const manual = makeView()
await manual.openBluetoothSetup()
assert.equal(manual.showBluetoothSetup, true, "Setup must be reachable without tapping Connect")
assert.equal(Telematics.computed.bluetoothSetupConfirmLabel.call(manual), "Done")
assert.equal(Telematics.computed.bluetoothSetupConfirmLabel.call(
  Object.assign(makeView(), { bluetoothSetupMode: "gate" })), "Pair now", "the gate confirms by pairing")
await manual.confirmBluetoothSetup()
assert.equal(connects, 4, "Dismissing the status panel must not open the chooser")

const checks = (view) => Telematics.computed.bluetoothChecks.call(
  Object.assign(view, { canRestoreBluetooth: Telematics.computed.canRestoreBluetooth.call(view) }))

// Radio off and no permissions backend: both rows fail and the gear must flag it.
bluetooth.getAvailability = async () => false
const unhealthy = makeView()
await unhealthy.refreshBluetoothStatus()
assert.equal(unhealthy.bluetoothRadio, "unavailable")
assert.equal(checks(unhealthy)[0].ok, false)
assert.equal(checks(unhealthy)[1].value, "Not enabled")
assert.equal(checks(unhealthy)[2].value, "Needs the setting above", "Remembered device is a consequence, not its own fix")
assert.equal(Telematics.computed.bluetoothNeedsAttention.call(unhealthy), true)
assert.equal(Telematics.computed.bluetoothBanner.call(
  Object.assign(unhealthy, { canRestoreBluetooth: false })).title, "Bluetooth is off",
  "A dead radio must outrank the reconnect advice")

// Everything on, one device already granted: no attention needed anywhere.
bluetooth.getAvailability = async () => true
bluetooth.getDevices = async () => [{ id: "remembered" }]
const healthy = makeView()
await healthy.refreshBluetoothStatus()
assert.equal(healthy.rememberedDevices, 1)
assert.deepEqual(checks(healthy).map((check) => check.ok), [true, true, true])
assert.equal(Telematics.computed.bluetoothSetupReady.call(
  Object.assign(healthy, { bluetoothChecks: checks(healthy) })), true)
assert.equal(Telematics.computed.bluetoothNeedsAttention.call(healthy), false)
assert.equal(Telematics.computed.bluetoothBanner.call(
  Object.assign(healthy, { canRestoreBluetooth: true })), null, "No banner when everything is fine")

// Radio fine, backend missing: the reconnect banner, not the radio one.
const reconnectOnly = Object.assign(makeView(), { bluetoothRadio: "available", canRestoreBluetooth: false })
assert.equal(Telematics.computed.bluetoothBanner.call(reconnectOnly).action, "Show me how")

// The reload banner replaces the bar's lightbulb and Connect; nothing else does.
const controlsHidden = (view) => {
  const banner = Telematics.computed.bluetoothBanner.call(view)
  const withBanner = Object.assign(view, { bluetoothBanner: banner })
  const visible = Telematics.computed.bluetoothBannerVisible.call(withBanner)
  return Telematics.computed.bluetoothControlsHidden.call(Object.assign(withBanner, { bluetoothBannerVisible: visible }))
}
const bar = (extra) => Object.assign(makeView(), { bluetoothRadio: "available", canRestoreBluetooth: false }, extra)
assert.equal(controlsHidden(bar()), true, "the reload banner owns the bar")
assert.equal(controlsHidden(bar({ bluetoothRadio: "unavailable" })), false, "a dead radio must still leave Connect reachable")
assert.equal(controlsHidden(bar({ canRestoreBluetooth: true })), false, "nothing is hidden once the setting is on")
assert.equal(controlsHidden(bar({ isLandscape: true })), false, "landscape shows no banner, so it keeps its controls")
assert.equal(controlsHidden(bar({ bleState: "error" })), false, "an error notice outranks the banner")

// The lightbulb answers to the banner alone: any banner replaces it, and it
// comes back the moment none is showing.
const bannerUp = (view) => Telematics.computed.bluetoothBannerVisible.call(
  Object.assign(view, { bluetoothBanner: Telematics.computed.bluetoothBanner.call(view) }))
assert.equal(bannerUp(bar()), true, "the reload banner replaces the lightbulb")
assert.equal(bannerUp(bar({ bluetoothRadio: "unavailable" })), true, "so does the radio banner")
assert.equal(bannerUp(bar({ canRestoreBluetooth: true })), false, "no banner, so the lightbulb stays")
assert.equal(bannerUp(bar({ bleState: "needs-pairing" })), false, "a pairing notice has no button of its own")

// The steps must not point at a Connect button that is currently hidden.
const steps = (view) => Telematics.computed.showPairingSteps.call(view)
assert.equal(steps(Object.assign(makeView(), { connected: false, canRestoreBluetooth: false, bluetoothSetupMode: "info" })), false)
assert.equal(steps(Object.assign(makeView(), { connected: false, canRestoreBluetooth: false, bluetoothSetupMode: "gate" })), true)
assert.equal(steps(Object.assign(makeView(), { connected: false, canRestoreBluetooth: true, bluetoothSetupMode: "info" })), true)

// Not yet paired is normal, not a fault: nothing may appear for it.
const unpaired = Object.assign(makeView(), { bluetoothRadio: "available", canRestoreBluetooth: true, rememberedDevices: 0 })
assert.equal(Telematics.computed.bluetoothBanner.call(unpaired), null, "An unpaired device must not raise a banner")

// A browser that cannot report radio state must read as unknown, never as broken.
delete bluetooth.getAvailability
delete bluetooth.getDevices
const opaque = makeView()
await opaque.refreshBluetoothStatus()
assert.equal(opaque.bluetoothRadio, "unknown")
assert.equal(checks(opaque)[0].ok, undefined, "Unknown radio state must not render as a failure")

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
