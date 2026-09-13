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


def test_phone_tab_gates_platforms_and_owns_pairing():
  bluetooth = _read("js/views/Bluetooth.js")
  phone = _read("js/components/PhonePanel.js")
  browser = _read("js/browser.js")
  notice = _read("js/components/BluetoothSupportNotice.js")
  assert 'phone: "Phone"' in bluetooth and "<PhonePanel />" in bluetooth
  assert bluetooth.index('controllers: "Controllers"') < bluetooth.index('phone: "Phone"')
  # iOS has no Web Bluetooth in any browser; Firefox never exposes it either
  assert "platform: bluetoothPlatform()" in phone and "<BluetoothSupportNotice" in phone
  assert "isIOSDevice()" in browser and "/Firefox\\//" in browser
  assert "window.isSecureContext" in browser and 'window.location.protocol === "https:"' in browser
  assert "Bluetooth in the browser is not supported on iPhone at this time :(" in notice
  assert "Bluetooth Pairing in the Browser is not supported in Firefox" in notice
  assert "Chrome on Android required" in notice
  # outside the Phone tab the notice also says where pairing happens
  assert "Tools &rarr; Bluetooth &rarr; Phone" in notice and 'v-if="pairElsewhere"' in notice
  assert "pair-elsewhere" not in phone
  # the insecure gate must explain the jump instead of silently redirecting to :8443
  assert "window.location.replace(this.secureURL)" not in phone
  assert ':href="galaxyURL"' in phone
  assert "8443" not in phone
  assert "Set up Galaxy remote access" in phone
  assert "Use Bluetooth in Galaxy" in phone
  # the gate must lead with the notice and action, not bury them under prose
  assert "telematics-gate__open" in phone
  assert phone.index("telematics-gate__open") < phone.index("telematics-gate__steps")
  # the comma's pairing window opens from here instead of the device screen
  assert 'api.bluetoothOp("companion_pair")' in phone
  assert "companion_pairing_remaining" in phone
  assert "discoverable / 120s" in phone
  assert "Pair now" in phone
  assert "client.connect()" in phone
  assert ':open="!bluetoothFlagsReady || !restoredAfterReload"' in phone
  # pairing from Android's settings is the common wrong turn; warn against it
  assert "Do not pair from Android's Bluetooth settings" in phone
  assert "only creates a system bond" in phone
  # Disconnect cannot revoke the Chrome permission, so say how to do it by hand
  assert "Make Chrome forget this device" in phone
  assert "Bluetooth devices" in phone
  assert "Connected devices" in phone
  assert 'v-if="rememberedDevices > 0"' in phone
  assert "telematics-setup__more" in phone
  # both flags are required, so both get a numbered step and a probed row
  assert "bluetoothChecks" in phone
  assert "canRestoreBluetooth" in phone
  assert "canWatchAdvertisements" in phone
  assert "bluetoothFlagsReady" in phone
  assert "getAvailability" in phone
  assert "Experimental Web Platform features" in phone
  assert "Find device after reload" in phone
  assert "demo" not in phone.lower()


def test_telematics_has_security_gate_controls_and_complete_layouts():
  telematics = _read("js/views/Telematics.js")
  assert "window.isSecureContext" in telematics
  assert "isIOSDevice" in telematics
  assert "homeURL.hash = \"/\"" in telematics
  assert 'window.location.protocol === "https:"' in telematics
  assert "window.location.replace(this.secureURL)" not in telematics
  assert ':href="galaxyURL"' in telematics
  assert "8443" not in telematics
  assert "navigator.bluetooth" in telematics
  assert "reconnectRemembered" in _read("js/lan/connection.js")
  assert "Pair the device first" in telematics
  # pairing lives on the Bluetooth page's Phone tab now
  assert 'navigate("/bluetooth/phone")' in telematics
  assert "requestDevice" not in telematics and "chooseNew" not in telematics
  assert "chooseNew" not in _read("js/lan/connection.js")
  # The page origin selects the transport; no user-facing transport toggle.
  assert 'role="radiogroup" aria-label="Connection method"' not in telematics
  assert 'this.onGalaxyLink ? "bluetooth" : "lan"' in telematics
  assert "Use Bluetooth in Galaxy" in telematics
  assert "prepareOffline()" in telematics
  # both orientations share one connection bar: action on the status row, no Wi-Fi icon
  assert telematics.count('<TelematicsConnectBar ') == 2 and telematics.count('v-bind="connectBar"') == 2
  assert telematics.count('v-else-if="pending"') == 1
  # Wi-Fi settings belong to the Wi-Fi tab only
  assert """v-if="connectionMode === 'lan'" class="gx-btn gx-btn--outlined" type="button" aria-label="Local Wi-Fi settings\"""" in telematics
  # an unsupported browser on the Bluetooth tab gets the Phone tab's explanation
  assert "<BluetoothSupportNotice" in telematics and "bluetoothPlatform()" in telematics
  assert "Full screen" in telematics
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
  assert result.returncode == 0, "\n".join(line for line in result.stderr.splitlines() if len(line) < 1500)


@pytest.mark.skipif(shutil.which("node") is None, reason="no node.js runtime available")
def test_telematics_javascript_modules_parse(tmp_path):
  for relative in ("js/ble/live_frames.js", "js/ble/live_ble.js", "js/views/Telematics.js", "js/components/GalaxyModal.js", "js/lan/live_lan.js", "js/lan/connection.js",
                   "js/offline.js", "js/telematics-app.js", "js/components/PhonePanel.js", "js/views/Bluetooth.js", "js/components/BluetoothSupportNotice.js", "js/browser.js"):
    source = UI_ROOT / relative
    target = tmp_path / (source.stem + ".mjs")
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run([shutil.which("node"), "--check", str(target)], capture_output=True, text=True)
    assert result.returncode == 0, f"{relative} failed to parse:\n{result.stderr}"


@pytest.mark.skipif(shutil.which("node") is None, reason="no node.js runtime available")
def test_telematics_defaults_to_paired_bluetooth_and_keeps_existing_connections():
  script = r'''
import assert from "node:assert/strict"
import fs from "node:fs"
globalThis.showSnackbar = () => {}
const navigations = []
globalThis.navigate = (target) => navigations.push(target)
const source = fs.readFileSync("js/views/Telematics.js", "utf8").replace(/^import .*$/gm, "")
const moduleURL = (code) => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
const { Telematics, TelematicsConnectBar } = await import(moduleURL("const GxNotice = {}, GalaxyModal = {}, BluetoothSupportNotice = {};\n" + source))
Object.assign(globalThis, await import(moduleURL(fs.readFileSync("js/browser.js", "utf8"))))
globalThis.offlineState = { state: "ready", message: "Saved" }
globalThis.prepareOffline = async () => {}
globalThis.window = { location: new URL("https://galaxy.firestar.link/1234567890abcdef/#/telematics") }
const bluetooth = {}
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { userAgent: "Android", bluetooth } })
let reconnects = 0, disconnects = 0
const makeView = (extra = {}) => {
  const view = Object.assign(Telematics.data(), Telematics.methods, {
    identity: "comma", connectionMode: "bluetooth", bluetoothSecure: true, bluetoothSupport: "ready",
    ble: { reconnect() { reconnects++ }, disconnect() { disconnects++ } },
  }, extra)
  for (const [name, getter] of Object.entries(Telematics.computed)) {
    Object.defineProperty(view, name, { get: () => getter.call(view) })
  }
  view.connection = {
    configure() {},
    connect() { return view.ble.reconnect() },
    disconnect() { view.ble?.disconnect() },
  }
  return view
}

// A phone Chrome already paired picks Bluetooth and connects without asking.
bluetooth.getDevices = async () => [{ id: "remembered" }]
const paired = makeView({ connectionMode: "", capability: "ready" })
let before = reconnects
await paired.refreshBluetoothStatus()
paired.defaultToBluetooth()
assert.equal(paired.connectionMode, "bluetooth")
assert.equal(reconnects, before + 1)

// Remote pages select Bluetooth even before pairing; Wi-Fi cannot be selected.
bluetooth.getDevices = async () => []
const unpaired = makeView({ capability: "ready" })
assert.equal(unpaired.connectionMode, "bluetooth")
unpaired.setConnectionMode("lan", false)
assert.equal(unpaired.connectionMode, "bluetooth")
unpaired.pairPhone()
assert.deepEqual(navigations, ["/bluetooth/phone"])

// Local origins ignore remembered Bluetooth permissions.
const chosen = makeView({ onGalaxyLink: false, connectionMode: "lan", rememberedDevices: 1 })
chosen.defaultToBluetooth()
assert.equal(chosen.connectionMode, "lan")

// Bluetooth on the HTTP page explains itself instead of opening anything.
const insecure = makeView({ ble: null, bluetoothSecure: false })
await insecure.connect()
assert.equal(insecure.bleState, "error")
assert.match(insecure.bleMessage, /Galaxy link/)

// The header says Syncing whenever the connect bar shows an attempt or retry.
for (const [state, extra, title] of [
  ["connecting", {}, "Syncing"], ["reconnecting", {}, "Syncing"], ["connected", {}, "Syncing"],
  ["idle", { connecting: true }, "Syncing"], ["error", {}, "No connection"], ["idle", {}, "No connection"],
]) {
  assert.equal(makeView({ bleState: state, ...extra }).heroStatus.title, title, `${state} ${JSON.stringify(extra)}`)
}
assert.equal(makeView({ bleState: "connected" }).heroStatus.detail, "Waiting for live data")
assert.equal(makeView({ bleState: "reconnecting", connectionMode: "lan" }).heroStatus.detail, "Reconnecting over Local Wi-Fi")

// A cancelled UI request cannot keep buttons busy or clear a newer request.
const returning = makeView({ rememberedDevices: 1, bleState: "error" })
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

// Render the actual Vue templates: both orientations retain their exit controls.
const { compile } = await import(moduleURL(fs.readFileSync("../vendor/vue/vue.esm-browser.js", "utf8")))
const render = compile(Telematics.template, { decodeEntities: text => text })
const renderBar = compile(TelematicsConnectBar.template, { decodeEntities: text => text })
const collect = (root, keep) => {
  const found = []
  const walk = node => {
    if (!node || typeof node !== "object") return
    if (keep(node)) found.push(node)
    if (Array.isArray(node.children)) node.children.forEach(walk)
  }
  walk(root)
  return found
}
const text = node => typeof node.children === "string" ? node.children
  : (node.children || []).map(child => typeof child.children === "string" ? child.children : "").join("")
// Unmounted, a child component renders as a vnode named after it; render its own
// template with the props the parent passed.
const barOf = view => {
  const bars = collect(render(view, []), node => node.type === "TelematicsConnectBar")
  assert.equal(bars.length, 1, `${view.isLandscape ? 'landscape' : 'portrait'} renders one connection bar`)
  return renderBar({ ...bars[0].props, $emit() {} }, [])
}
const originalWarn = console.warn
// Rendering without mounting emits component-resolution warnings; child
// components are intentionally opaque while we inspect the parent's buttons.
console.warn = () => {}
try {
  for (const [bleState, expected] of [["idle", "Connect"], ["connecting", "Cancel"], ["reconnecting", "Cancel"], ["connected", "Disconnect"], ["error", "Reconnect"]]) {
    const layouts = [false, true].map(isLandscape => {
      const bar = barOf(makeView({ capability: "ready", isLandscape, bleState }))
      const [statusRow, controlRow] = collect(bar, node => node.props?.class === "telematics-connect-bar__row")
      const action = collect(statusRow, node => node.type === "button").map(node => [text(node).trim(), node.props?.disabled])
      assert.ok(action.some(([label, disabled]) => label === expected && !disabled), `${isLandscape ? 'landscape' : 'portrait'} ${bleState} must expose ${expected} on the status row`)
      assert.ok(collect(controlRow, node => node.type === "button").some(node => text(node) === "Full screen"))
      return JSON.stringify(bar)
    })
    assert.equal(layouts[0], layouts[1], `${bleState}: portrait and landscape bars must match`)
  }
  for (const isLandscape of [false, true]) {
    // Device pages expose Wi-Fi settings and never expose a transport toggle.
    const local = makeView({ onGalaxyLink: false, capability: "ready", connectionMode: "lan", bluetoothSecure: false, ble: null, isLandscape })
    const controls = collect(barOf(local), node => !!node.props)
    assert.ok(!controls.some(node => node.props.role === "radiogroup"))
    assert.ok(controls.some(node => node.props["aria-label"] === "Local Wi-Fi settings"))
    assert.ok(!controls.some(node => node.props.role === "radio"))
    assert.equal(local.canConnect, true)
    local.bleState = "error"
    local.setConnectionMode("bluetooth")
    assert.equal(local.canConnect, true, "Switching from a LAN error must not leave a global gate")
    assert.ok(collect(barOf(local), node => !!node.props).some(node => node.props["aria-label"] === "Local Wi-Fi settings"))
    // Firefox and Safari on the Bluetooth tab get the Phone tab's explanation;
    // Chrome on the HTTP page gets the secure-page link instead.
    for (const platform of ["firefox", "ios", "unsupported", "insecure"]) {
      const unsupported = makeView({ capability: "ready", connectionMode: "bluetooth", bluetoothSupport: platform, ble: null, bluetoothSecure: false, isLandscape })
      const notices = collect(render(unsupported, []), node => node.type === "BluetoothSupportNotice")
      if (platform === "insecure") {
        assert.equal(notices.length, 0)
        assert.equal(unsupported.onGalaxyLink, true)
      } else {
        assert.equal(notices.length, 1)
        assert.equal(notices[0].props.platform, platform)
        assert.equal(notices[0].props["secure-url"], unsupported.secureURL)
        assert.ok("pair-elsewhere" in notices[0].props, "Telematics points to Tools → Bluetooth → Phone")
      }
      // Below it: only the connection bar, without the dashboard or a Reconnect action.
      unsupported.bleState = "error"
      const page = render(unsupported, [])
      assert.equal(collect(page, node => node.type === "TelematicsHeader").length, 0, `${platform} hides the dashboard`)
      const bar = barOf(unsupported)
      const [statusRow] = collect(bar, node => node.props?.class === "telematics-connect-bar__row")
      assert.equal(collect(statusRow, node => node.type === "button").length, 0, `${platform} hides Connect/Reconnect`)
      assert.ok(!collect(bar, node => !!node.props).some(node => node.props.role === "radiogroup"), "Remote pages never offer Wi-Fi")
    }
    // Back on Wi-Fi the dashboard and connect action return.
    const wifi = makeView({ capability: "ready", connectionMode: "lan", bluetoothSupport: "firefox", isLandscape })
    assert.equal(collect(render(wifi, []), node => node.type === "TelematicsHeader").length, 1)
    assert.ok(collect(barOf(wifi), node => node.type === "button").some(node => text(node).trim() === "Connect"))
  }
} finally { console.warn = originalWarn }

// A remote page cannot switch to LAN, including after a Bluetooth error.
const firefoxView = makeView({ connectionMode: "bluetooth", ble: null, bluetoothSecure: false, bluetoothSupport: "firefox" })
await firefoxView.connect()
let wifiStarts = 0
firefoxView.connection.connect = () => { wifiStarts++ }
firefoxView.setConnectionMode("lan")
assert.equal(firefoxView.connectionMode, "bluetooth")
assert.equal(wifiStarts, 0)

// Restored preferences use comma identity; a local page tries its own origin.
const frameModule = moduleURL(fs.readFileSync("js/ble/live_frames.js", "utf8"))
const lanModule = fs.readFileSync("js/lan/live_lan.js", "utf8").replace('"../ble/live_frames.js"', JSON.stringify(frameModule))
globalThis.localOrigin = (await import(moduleURL(lanModule))).localOrigin
const saved = new Map([["galaxy-telematics:serial", JSON.stringify({ mode: "lan", address: "http://old.local:8082" })]])
globalThis.localStorage = { getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value) }
window.location = new URL("http://192.168.1.5:8082/#/telematics")
globalThis.api = { getDeviceStatus: async () => ({ telematicsDeviceId: "serial", localHostname: "starpilot-comma.local" }) }
const restored = makeView({ identity: "", connectionMode: "" })
let starts = 0
restored.connection.connect = () => { starts++ }
await restored.loadDeviceStatus()
assert.equal(restored.identity, "serial")
assert.equal(restored.connectionMode, "lan")
assert.equal(restored.localAddress, "http://192.168.1.5:8082")
assert.equal(starts, 1, "Reload must restore the connection without a chooser")
await restored.loadDeviceStatus()
assert.equal(starts, 1, "Ordinary status polling must not reconnect a healthy link")
// Old saved "automatic" preferences fall back to the paired-phone default.
saved.set("galaxy-telematics:serial", JSON.stringify({ mode: "automatic" }))
const legacy = makeView({ identity: "", connectionMode: "" })
legacy.connection.connect = () => { starts++ }
await legacy.loadDeviceStatus()
assert.equal(legacy.connectionMode, "lan")
assert.equal(starts, 2, "Local pages choose Wi-Fi regardless of legacy preferences")
saved.set("galaxy-telematics:serial", JSON.stringify({ mode: "lan", address: "http://old.local:8082" }))
let finishIdentity
api.getDeviceStatus = () => new Promise(resolve => { finishIdentity = resolve })
const cancelledIdentity = makeView({ identity: "" })
cancelledIdentity.connection.connect = () => { starts++ }
const loading = cancelledIdentity.loadDeviceStatus()
cancelledIdentity.disconnect()
finishIdentity({ telematicsDeviceId: "serial" })
await loading
assert.equal(starts, 2, "Disconnect while identity loads must prevent automatic connection")

// A cached remote page can reconnect the last verified comma while offline.
window.location = new URL("https://galaxy.link/1234567890abcdef/mobile")
window.isSecureContext = true
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
window.addEventListener = () => {}
window.removeEventListener = () => {}
globalThis.document = { addEventListener() {}, removeEventListener() {}, visibilityState: "visible" }
globalThis.isIOSDevice = () => false
globalThis.bluetoothPlatform = () => "ready"
globalThis.usePolling = () => ({ start() {}, destroy() {} })
globalThis.getLiveBLEClient = () => ({})
let cachedStarts = 0
globalThis.TelematicsConnection = class {
  configure(value) { this.config = value }
  connect() { cachedStarts++; this.running = true }
  close() {}
}
saved.set("galaxy-telematics-page:/1234567890abcdef/mobile", "serial")
saved.set("galaxy-telematics:serial", JSON.stringify({ mode: "bluetooth", address: "http://starpilot-comma.local:8082" }))
const offline = makeView({ identity: "", connectionMode: "" })
Telematics.mounted.call(offline)
assert.equal(cachedStarts, 1)
assert.equal(offline.connection.config.identity, "serial")
assert.equal(offline.connection.config.mode, "bluetooth")
assert.equal(offline.localAddress, "http://starpilot-comma.local:8082")
Telematics.beforeUnmount.call(offline)

// Missing legacy mode preferences still restore Bluetooth on the remote page.
saved.set("galaxy-telematics:serial", JSON.stringify({ address: "http://starpilot-comma.local:8082" }))
bluetooth.getDevices = async () => [{ id: "remembered" }]
cachedStarts = 0
const fresh = makeView({ identity: "", connectionMode: "" })
Telematics.mounted.call(fresh)
assert.equal(cachedStarts, 1, "The remote page always restores Bluetooth")
await new Promise(resolve => setTimeout(resolve, 0))
assert.equal(fresh.connectionMode, "bluetooth")
assert.equal(cachedStarts, 1)
Telematics.beforeUnmount.call(fresh)
'''
  result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], cwd=UI_ROOT, capture_output=True, text=True)
  assert result.returncode == 0, "\n".join(line for line in result.stderr.splitlines() if len(line) < 1500)


@pytest.mark.skipif(shutil.which("node") is None, reason="no node.js runtime available")
def test_phone_panel_gates_browsers_and_pairs_in_the_click():
  script = r'''
import assert from "node:assert/strict"
import fs from "node:fs"
const notices = []
globalThis.showSnackbar = (...args) => notices.push(args)
const source = fs.readFileSync("js/components/PhonePanel.js", "utf8").replace(/^import .*$/gm, "")
const moduleURL = (code) => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
const { PhonePanel } = await import(moduleURL("const GxNotice = {}, BluetoothSupportNotice = {};\n" + source))
Object.assign(globalThis, await import(moduleURL(fs.readFileSync("js/browser.js", "utf8"))))
const CHROME = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36"
const FIREFOX = "Mozilla/5.0 (Android 14; Mobile; rv:130.0) Gecko/130.0 Firefox/130.0"
const bluetooth = {}
const setBrowser = (userAgent, url, { ios = false, withBluetooth = true } = {}) => {
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { userAgent, platform: ios ? "iPhone" : "", maxTouchPoints: 0, bluetooth: withBluetooth ? bluetooth : undefined } })
  const location = new URL(url)
  globalThis.window = { location, isSecureContext: location.protocol === "https:" }
}
const makeView = () => {
  const view = Object.assign(PhonePanel.data(), PhonePanel.methods)
  for (const [name, getter] of Object.entries(PhonePanel.computed)) {
    Object.defineProperty(view, name, { get: () => getter.call(view) })
  }
  return view
}

setBrowser("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)", "https://comma.local:8443/#/bluetooth/phone", { ios: true, withBluetooth: false })
assert.equal(makeView().platform, "ios")
// "Request Desktop Website" on iPhone reports a Mac, but still has a touch screen.
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15", platform: "MacIntel", maxTouchPoints: 5 } })
assert.equal(makeView().platform, "ios")
setBrowser(FIREFOX, "http://192.168.1.5:8082/#/bluetooth/phone", { withBluetooth: false })
const firefox = makeView()
assert.equal(firefox.platform, "firefox", "Firefox receives the browser support notice")
assert.equal(firefox.secureURL, "", "No invented local HTTPS address")
setBrowser("Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0", "https://comma.local:8443/#/bluetooth/phone")
assert.equal(makeView().platform, "unsupported")
setBrowser(CHROME, "http://192.168.1.5:8082/#/bluetooth/phone", { withBluetooth: false })
const insecure = makeView()
assert.equal(insecure.platform, "insecure")
assert.equal(insecure.secureURL, "", "The Galaxy link is loaded from device status")
let polls = 0
globalThis.usePolling = () => { polls++; return { start() {}, destroy() {} } }
PhonePanel.created.call(insecure)
assert.equal(polls, 0, "Only a page that can pair polls the pairing window")
setBrowser(CHROME, "https://galaxy.firestar.link/1234567890abcdef/#/bluetooth/phone")
assert.equal(makeView().platform, "ready")

// The comma's pairing window opens from the phone and reports its countdown.
const ops = []
globalThis.api = {
  bluetoothOp: async (operation) => { ops.push(operation) },
  getBluetoothStatus: async () => ({ offroad: true, enabled: true, companion_pairing_remaining: 118 }),
}
const view = makeView()
await view.openPairingWindow()
assert.deepEqual(ops, ["companion_pair"])
assert.equal(view.pairingRemaining, 118)
assert.equal(view.busy, "")

// Chrome's chooser opens inside the click, and a proven bond is released for Telematics.
let choosers = 0
const client = {
  state: "idle", message: "", device: null, disconnects: 0,
  connect() { choosers++; this.state = "connected"; this.device = { name: "comma-1234" }; return Promise.resolve() },
  disconnect() { this.disconnects++; this.state = "idle" },
}
globalThis.getLiveBLEClient = () => client
bluetooth.getDevices = async () => [{ id: "remembered" }]
const pairing = view.pair()
assert.equal(choosers, 1, "Chooser must open synchronously in the button gesture")
await pairing
assert.match(view.pairMessage, /comma-1234/)
assert.equal(client.disconnects, 1)
assert.equal(view.rememberedDevices, 1)
assert.equal(view.busy, "")

client.connect = function () { this.state = "needs-pairing"; this.message = "Pair the device first"; return Promise.reject(new Error("bond")) }
await view.pair()
assert.equal(view.error, "Pair the device first")
client.connect = function () { this.state = "idle"; this.message = "No device selected"; return Promise.resolve() }
view.error = ""
await view.pair()
assert.equal(view.error, "", "Closing Chrome's chooser is not an error")

let copied
navigator.clipboard = { async writeText(value) { copied = value } }
await view.copyBluetoothSetting("enable-web-bluetooth-new-permissions-backend")
assert.equal(copied, "chrome://flags/#enable-web-bluetooth-new-permissions-backend")
delete navigator.clipboard
await view.copyBluetoothSetting("enable-experimental-web-platform-features")
assert.equal(notices.at(-1)[1], "error", "Clipboard failure must offer manual copying")
'''
  result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], cwd=UI_ROOT, capture_output=True, text=True)
  assert result.returncode == 0, "\n".join(line for line in result.stderr.splitlines() if len(line) < 1500)


@pytest.mark.skipif(shutil.which("node") is None, reason="no node.js runtime available")
@pytest.mark.parametrize("script", ["telematics_offline_test.mjs", "telematics_pwa_test.mjs", "telematics_setup_test.mjs"])
def test_telematics_offline_pwa(script):
  result = subprocess.run([shutil.which("node"), str(Path(__file__).with_name(script))], capture_output=True, text=True, timeout=20)
  assert result.returncode == 0, result.stderr
