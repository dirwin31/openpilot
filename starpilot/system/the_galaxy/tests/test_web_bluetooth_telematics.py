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
  assert "Set up Bluetooth reconnect" in telematics
  assert 'v-else-if="bluetoothBannerVisible"' in telematics
  assert "getAvailability" in telematics
  # Setup may guide pairing, but must never hide Disconnect or Cancel.
  assert "bluetoothControlsHidden" not in telematics
  assert telematics.count('v-else-if="connectionPending"') == 2
  assert '@click="chooseDevice"' in telematics
  assert ':open="!bluetoothFlagsReady || !restoredAfterReload"' in telematics
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
  # both flags are required now, so both get a numbered step and a probed row
  assert "canWatchAdvertisements" in telematics
  assert "bluetoothFlagsReady" in telematics
  assert "Experimental Web Platform features" in telematics
  assert "Find device after reload" in telematics
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
const clientFor = (devices, savedID = null) => {
  const bluetooth = { getDevices: async () => devices }
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { bluetooth } })
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: { getItem: () => savedID, setItem(key, value) { savedID = value } } })
  const client = new LiveBLEClient({ onState: ({ state, message }) => states.push([state, message]) })
  client.advertisementTimeoutMs = 50
  return client
}

// A refresh: the remembered device reads as out of range until it advertises.
const device = makeDevice()
const client = clientFor([device], "remembered")
assert.equal(await client.reconnectRemembered(), true, "a rescan must recover the remembered device")
assert.equal(client.state, "connected")
assert.equal(client.restoredDeviceID, "remembered", "Only a saved connection restored after page load is verified")
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
assert.match(waiting.message, /device has not advertised/)
waiting.close()

// Without the experimental flag no rescan is possible, so name the missing flag.
const unwatchable = makeDevice({ watchable: false })
const blocked = clientFor([unwatchable])
assert.equal(await blocked.reconnectRemembered(), false)
assert.equal(unwatchable.connects, 1, "no rescan is possible, so no retry")
assert.match(blocked.message, /enable-experimental-web-platform-features/)
assert.equal(blocked.reconnectTimer, null, "A missing API needs setup, not endless retries")
blocked.close()

// A hung scan startup is bounded by the advertisement deadline, without an
// unhandled rejection. Abort must run even though startup never settles.
const hangs = makeDevice({ advertises: false })
let aborted = false
hangs.watchAdvertisements = ({ signal }) => {
  signal.addEventListener("abort", () => { aborted = true })
  return new Promise(() => {})
}
const bounded = clientFor([hangs])
assert.equal(await bounded.reconnectRemembered(), false)
assert.equal(aborted, true)
assert.equal(bounded.state, "error")
assert.match(bounded.message, /device has not advertised/)
assert.ok(bounded.reconnectTimer, "An absent advertisement is retryable")
bounded.close()

// Permission errors preserve the real cause and stop automatic retries.
const deniedDevice = makeDevice()
deniedDevice.watchAdvertisements = async () => { throw Object.assign(new Error("Nearby devices permission denied"), { name: "NotAllowedError" }) }
const denied = clientFor([deniedDevice])
assert.equal(await denied.reconnectRemembered(), false)
assert.equal(denied.state, "error")
assert.match(denied.message, /Nearby devices permission denied/)
assert.doesNotMatch(denied.message, /not advertised/)
assert.equal(denied.reconnectTimer, null)
denied.close()

// Cancel stops a scan immediately and its late completion cannot change state.
const cancelledDevice = makeDevice({ advertises: false })
const cancelled = clientFor([cancelledDevice])
const cancelAttempt = cancelled.reconnectRemembered()
while (!cancelledDevice.watchingAdvertisements) await new Promise(resolve => setTimeout(resolve, 1))
cancelled.disconnect()
assert.equal(await cancelAttempt, false)
assert.equal(cancelledDevice.watchingAdvertisements, false)
assert.equal(cancelled.state, "idle")
assert.equal(cancelled.reconnectTimer, null)
cancelled.close()

// Replacing a stale device opens the chooser synchronously and drops retries.
const stale = makeDevice({ watchable: false })
const replacement = makeDevice()
replacement.id = "replacement"
replacement.seen = true
const choose = clientFor([stale])
await choose.reconnectRemembered()
let chooserCalls = 0
navigator.bluetooth.requestDevice = async () => { chooserCalls++; return replacement }
const choosing = choose.connect()
assert.equal(chooserCalls, 1, "requestDevice must retain user activation")
await choosing
assert.equal(choose.device, replacement)
assert.equal(choose.state, "connected")
assert.equal(choose.reconnectTimer, null)
assert.equal(choose.restoredDeviceID, null, "A chooser grant is not proof of reload persistence")
choose.disconnect()
await choose.reconnect()
assert.equal(choose.restoredDeviceID, null, "Revisiting a route is not a page reload")
choose.close()

// Cancelled chooser results and a slow remembered-device lookup cannot connect.
let finishChooser
const chooserCancelled = clientFor([])
navigator.bluetooth.requestDevice = () => new Promise(resolve => { finishChooser = resolve })
const chooserAttempt = chooserCancelled.connect()
chooserCancelled.disconnect()
const ignored = makeDevice()
ignored.seen = true
finishChooser(ignored)
await chooserAttempt
assert.equal(ignored.connects, 0)
assert.equal(chooserCancelled.state, "idle")
chooserCancelled.close()

let finishLookup
const lookupCancelled = clientFor([])
navigator.bluetooth.getDevices = () => new Promise(resolve => { finishLookup = resolve })
const lookupAttempt = lookupCancelled.reconnectRemembered()
lookupCancelled.disconnect()
finishLookup([ignored])
assert.equal(await lookupAttempt, false)
assert.equal(ignored.connects, 0)
assert.equal(lookupCancelled.state, "idle")
lookupCancelled.close()

// Cancelling while enabling notifications must not publish a late connection.
let finishNotifications
const notifying = makeDevice()
notifying.gatt.connect = async () => ({ getPrimaryService: async () => ({
  getCharacteristic: async uuid => uuid === COMPANION_UUIDS.live
    ? { ...characteristic(uuid), startNotifications: () => new Promise(resolve => { finishNotifications = resolve }) }
    : characteristic(uuid),
}) })
const notificationClient = clientFor([notifying])
const notificationAttempt = notificationClient.reconnectRemembered()
while (!finishNotifications) await new Promise(resolve => setTimeout(resolve, 1))
notificationClient.disconnect()
finishNotifications()
assert.equal(await notificationAttempt, false)
assert.equal(notificationClient.state, "idle")
notificationClient.close()

// Bound every post-connect stage, including authentication without a callback.
for (const stage of ["service", "characteristic", "authentication", "notifications"]) {
  const stalled = makeDevice()
  stalled.gatt.connect = async () => ({ getPrimaryService: () => stage === "service" ? new Promise(() => {}) : Promise.resolve({
    getCharacteristic: uuid => {
      if (stage === "characteristic") return new Promise(() => {})
      const value = characteristic(uuid)
      if (stage === "authentication" && uuid === COMPANION_UUIDS.status) value.readValue = () => new Promise(() => {})
      if (stage === "notifications" && uuid === COMPANION_UUIDS.live) value.startNotifications = () => new Promise(() => {})
      return Promise.resolve(value)
    },
  }) })
  const boundedStage = clientFor([stalled])
  const withTimeout = boundedStage._withTimeout.bind(boundedStage)
  boundedStage._withTimeout = (promise, ms, ...args) => withTimeout(promise, 10, ...args)
  assert.equal(await boundedStage.reconnectRemembered(), false)
  assert.equal(boundedStage.state, "error")
  assert.match(boundedStage.message, new RegExp(`${stage}.*timed out`))
  boundedStage.close()
}

// A stale GATT promise shares its server with a newer attempt on that device.
let finishGATT, drops = 0
const late = clientFor([])
const sameDevice = {
  gatt: { connect: () => new Promise(resolve => { finishGATT = resolve }), disconnect() { drops++ } },
}
late.device = sameDevice
late.connectAttempt = 1
const oldGATT = late._openGATT(sameDevice, 1)
late.disconnect()
late.connectAttempt++
late.manualDisconnect = false
finishGATT({})
await oldGATT
assert.equal(drops, 1, "Only the explicit cancellation may drop the link, not the old promise")
late.device = null
late.close()

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
let connects = 0, reconnects = 0, disconnects = 0
const makeView = (extra = {}) => {
  const view = Object.assign(Telematics.data(), Telematics.methods, {
    ble: { connect() { connects++ }, reconnect() { reconnects++ }, disconnect() { disconnects++ } },
  }, extra)
  for (const [name, getter] of Object.entries(Telematics.computed)) {
    Object.defineProperty(view, name, { get: () => getter.call(view) })
  }
  return view
}

// Both orientations brief users before pairing when either API is missing.
for (const isLandscape of [false, true]) {
  const initial = connects
  const view = makeView({ isLandscape })
  await view.connect()
  assert.equal(view.showBluetoothSetup, true)
  assert.equal(connects, initial)
  assert.equal(view.bluetoothSetupConfirmLabel, "Connect anyway")
  const pairing = view.continueBluetoothPairing()
  assert.equal(connects, initial + 1, "Chooser must open synchronously in the button gesture")
  await pairing
  assert.equal(view.connecting, false)
  assert.equal(view.showBluetoothSetup, false)
}
bluetooth.getDevices = async () => []
const halfFlagged = makeView()
assert.equal(halfFlagged.bluetoothFlagsReady, false)
assert.equal(halfFlagged.bluetoothBanner.action, "Show me how")
await halfFlagged.connect()
assert.equal(halfFlagged.showBluetoothSetup, true)
globalThis.BluetoothDevice = { prototype: { watchAdvertisements() {} } }
const firstPair = makeView()
await firstPair.connect()
assert.equal(firstPair.showBluetoothSetup, true, "First Android pairing still needs instructions")
assert.equal(firstPair.bluetoothSetupConfirmLabel, "Pair now")

// API exposure and a current permission do not prove persistence.
bluetooth.getAvailability = async () => true
bluetooth.getDevices = async () => [{ id: "remembered" }]
const healthy = makeView()
await healthy.refreshBluetoothStatus()
assert.equal(healthy.bluetoothFlagsReady, true)
assert.equal(healthy.bluetoothSetupReady, false)
assert.equal(healthy.bluetoothChecks.at(-1).value, "Not verified")
healthy.restoredAfterReload = true
assert.equal(healthy.bluetoothSetupReady, true)
assert.equal(healthy.bluetoothChecks.at(-1).value, "Verified")
assert.equal(healthy.bluetoothBanner, null)

// Returning users can retry, and Choose device always takes the chooser path.
const returning = makeView({ rememberedDevices: 1, bleState: "error" })
returning.ble.device = { id: "stale-permission" }
const retriesBefore = reconnects
await returning.connect()
assert.equal(reconnects, retriesBefore + 1)
assert.equal(returning.showBluetoothSetup, false)
await returning.openBluetoothSetup()
const chooserBefore = connects
assert.equal(returning.bluetoothSetupConfirmLabel, "Done")
await returning.confirmBluetoothSetup()
assert.equal(connects, chooserBefore, "Done must not invoke the chooser")
await returning.chooseDevice()
assert.equal(connects, chooserBefore + 1)
assert.ok(disconnects > 0, "Choosing a replacement stops retries")
assert.equal(returning.showBluetoothSetup, false)

// A cancelled UI request cannot keep buttons busy or clear a newer request.
let finishOld, finishNew
returning.ble.reconnect = () => new Promise(resolve => { finishOld = resolve })
const old = returning.connect()
assert.equal(returning.connectionPending, true)
returning.disconnect()
returning.bleState = "idle"
assert.equal(returning.connectionPending, false)
returning.ble.reconnect = () => new Promise(resolve => { finishNew = resolve })
const newer = returning.connect()
finishOld()
await old
assert.equal(returning.connecting, true)
finishNew()
await newer
assert.equal(returning.connecting, false)

// Radio faults keep actionable setup access, including while a link exists.
bluetooth.getAvailability = async () => false
delete bluetooth.getDevices
delete globalThis.BluetoothDevice
for (const isLandscape of [false, true]) {
  const connected = makeView({ isLandscape, bleState: "connected" })
  await connected.refreshBluetoothStatus()
  assert.equal(connected.connected, true)
  assert.equal(connected.bluetoothBanner.title, "Bluetooth is off")
  assert.equal(connected.bluetoothNeedsAttention, true)
  connected.bleState = "reconnecting"
  assert.equal(connected.connectionPending, true)
  await connected.openBluetoothSetup()
  assert.equal(connected.showBluetoothSetup, true)
}
delete bluetooth.getAvailability
const opaque = makeView()
await opaque.refreshBluetoothStatus()
assert.equal(opaque.bluetoothRadio, "unknown")
assert.equal(opaque.bluetoothChecks[0].ok, undefined)
const view = makeView()
// Render the actual Vue template: both orientations retain their exit controls.
const { compile } = await import(moduleURL(fs.readFileSync("../vendor/vue/vue.esm-browser.js", "utf8")))
const render = compile(Telematics.template, { decodeEntities: text => text })
const originalWarn = console.warn
// Rendering without mounting emits component-resolution warnings; child
// components are intentionally opaque while we inspect the parent's buttons.
console.warn = () => {}
try {
  for (const isLandscape of [false, true]) {
    for (const [bleState, expected] of [["idle", "Connect"], ["connecting", "Cancel"], ["reconnecting", "Cancel"], ["connected", "Disconnect"], ["error", "Reconnect"]]) {
      const rendered = makeView({ capability: "ready", isLandscape, bleState })
      const buttons = []
      const walk = node => {
        if (!node || typeof node !== "object") return
        if (node.type === "button") {
          const text = typeof node.children === "string" ? node.children
            : (node.children || []).map(child => typeof child.children === "string" ? child.children : "").join("")
          buttons.push([text.trim(), node.props?.disabled])
        }
        if (Array.isArray(node.children)) node.children.forEach(walk)
      }
      walk(render(rendered, []))
      assert.ok(buttons.some(([label, disabled]) => label === expected && !disabled), `${isLandscape ? 'landscape' : 'portrait'} ${bleState} must expose ${expected}`)
    }
  }
} finally { console.warn = originalWarn }

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
