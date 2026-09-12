"""Exercise the shared pairing panel with detector-only data and real Vue templates."""
import shutil
import subprocess
from pathlib import Path

import pytest

UI_ROOT = Path(__file__).resolve().parents[4] / "starpilot/system/the_galaxy/assets/mobile"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js unavailable")
def test_uniden_pairing_controls_and_failure_states():
  script = r'''
import assert from "node:assert/strict"
import fs from "node:fs"
Error.stackTraceLimit = 0
const moduleURL = code => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
const source = fs.readFileSync("js/components/BluetoothPanel.js", "utf8").replace(/^import .*$/gm, "")
const calls = []
let status = { available: true, enabled: true, powered: true, offroad: true, devices: [] }
let statusFailure = false, operationFailure = false
const api = {
  async getBluetoothStatus() { if (statusFailure) throw Error("Offline"); return status },
  async bluetoothOp(op, body) { calls.push([op, body]); if (operationFailure) throw Error("Connection failed") },
}
globalThis.api = api
const { BluetoothPanel: panel } = await import(moduleURL("const GxNotice = {};\n" + source))
const view = Object.assign(panel.data(), panel.methods, { detectorOnly: true })
for (const [key, getter] of Object.entries(panel.computed)) Object.defineProperty(view, key, { get: () => getter.call(view) })
const saved = { address: "AA:00:00:00:00:01", name: "R4@1234", uniden: true, paired: true, trusted: true, connected: false }
const nearby = { address: "AA:00:00:00:00:02", name: "R8@1234", uniden: true, rssi: -58 }
const trusted = { address: "AA:00:00:00:00:03", name: "R9@1234", uniden: true, trusted: true }
const phone = { address: "AA:00:00:00:00:04", name: "Phone", paired: true }
status.devices = [saved, nearby, trusted, phone]
await view.refresh()
assert.deepEqual(view.known, [saved, trusted])
assert.deepEqual(view.availableDevices, [nearby])
view.detectorOnly = false
assert.equal(view.visibleDevices.length, 4)
view.detectorOnly = true
assert.match(view.statusOf({ ...saved, connected: true }), /Discovering services/)
assert.match(view.statusOf({ ...saved, connected: true, services_resolved: true }), /Services ready/)
view.pairingAddress = saved.address.toLowerCase()
assert.equal(view.statusOf(saved), "Pairing…")
view.pairingAddress = ""

// Compile and render the shipped template so controls, prompts, and gates are exercised.
const { compile } = await import(moduleURL(fs.readFileSync("../vendor/vue/vue.esm-browser.js", "utf8")))
const render = compile(panel.template, { decodeEntities: text => text })
const collect = (node, predicate) => {
  if (!node || typeof node !== "object") return []
  const children = Array.isArray(node) ? node : Array.isArray(node.children) ? node.children : []
  return [...(predicate(node) ? [node] : []), ...children.flatMap(child => collect(child, predicate))]
}
const text = node => typeof node === "string" ? node : Array.isArray(node) ? node.map(text).join("") :
  typeof node?.children === "string" ? node.children : text(node?.children || [])
const buttons = () => collect(render(view, []), node => node.type === "button")
const button = label => buttons().find(node => text(node).trim() === label)
assert.ok(button("Search for Detectors"))
assert.ok(button("Connect"))
assert.ok(button("Forget"))
assert.ok(!text(render(view, [])).includes("Phone"))
assert.ok(text(render(view, [])).includes(nearby.address))
assert.ok(text(render(view, [])).includes("-58 dBm"))
view.discovering = true
await button("Stop Search").props.onClick()
assert.equal(calls.at(-1)[0], "stop_scan")
await view.request("pair", { address: nearby.address })
assert.deepEqual(calls.at(-1), ["pair", { address: nearby.address }])
view.offroad = false
view.setupAllowed = false
assert.equal(button("Pair").props.disabled, true)
assert.equal(button("Forget").props.disabled, true)
assert.equal(button("Search for Detectors").props.disabled, true)
assert.equal(button("Connect").props.disabled, false)
view.setupAllowed = true
assert.equal(button("Search for Detectors").props.disabled, false, "setup works with ignition on in Park")
assert.equal(button("Pair").props.disabled, false)
view.offroad = true
view.pairingAddress = nearby.address
assert.equal(button("Pair").props.disabled, true)
assert.equal(button("Connect").props.disabled, true)
view.pairingAddress = ""
view.prompt = { id: "first", name: "R4", kind: "confirmation", value: "012345" }
assert.equal(collect(render(view, []), node => node.type === "input").length, 0)
assert.ok(text(render(view, [])).includes("012345"))
view.prompt.kind = "pin"
assert.equal(collect(render(view, []), node => node.type === "input").length, 1)
view.pairValue = "1234"
status.prompt = { id: "second", kind: "passkey" }
await view.refresh()
assert.equal(view.pairValue, "")
operationFailure = true
await view.request("connect", { address: saved.address })
assert.equal(view.operationError, "Connection failed")
await view.refresh()
assert.equal(view.operationError, "Connection failed", "polling must not erase an action error")
statusFailure = true
await view.refresh()
assert.equal(view.available, false)
assert.equal(view.offroadDisabled(), true)
assert.deepEqual(view.devices, [])
assert.equal(view.prompt, null)
assert.ok(text(render(view, [])).includes("No saved devices yet."))
const before = calls.length
await view.request("pair", { address: nearby.address })
assert.equal(calls.length, before)

const pageSource = fs.readFileSync("js/views/Bluetooth.js", "utf8").replace(/^import .*$/gm, "")
let routes
globalThis.captureRoutes = (base, tabs) => { routes = { base, tabs }; return { tab: "uniden", selectTab() {} } }
const stubs = "const UnidenPanel = {}, BluetoothPanel = {}, PhonePanel = {}, WheelControls = {}, GalaxySection = {}, GalaxyTabs = {};"
const { Bluetooth } = await import(moduleURL(stubs + "const useTabRouting = globalThis.captureRoutes;\n" + pageSource))
const route = Bluetooth.setup()
assert.equal(routes.base, "/bluetooth")
assert.equal(routes.tabs.uniden, "uniden")
const page = compile(Bluetooth.template, { decodeEntities: text => text })({ ...Bluetooth.data(), ...route }, [])
const sections = collect(page, node => node.type === "GalaxySection")
const detectorPanel = collect(sections[0], node => node.type === "BluetoothPanel")[0]
assert.ok(Object.hasOwn(detectorPanel.props, "detector-only"))
'''
  result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], cwd=UI_ROOT, capture_output=True, text=True)
  assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js unavailable")
def test_uniden_settings_controls_keep_edits_and_report_transport_status():
  script = r'''
import assert from "node:assert/strict"
import fs from "node:fs"
Error.stackTraceLimit = 0
const moduleURL = code => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
const source = fs.readFileSync("js/components/UnidenPanel.js", "utf8").replace(/^import .*$/gm, "")
const data = { config: { enabled: false, address: "A", bands: ["KA"], min_strength: 2, auto_slowdown: false, ignore_muted: true },
  state: { connected: true, can_write: true, alerts: [] }, offroad: true, bands: ["KA"],
  settings: [{ key: "volume", label: "Volume", choices: [0, 1, 2, 3, 4, 5] }] }
const calls = []
let fail = false
globalThis.api = {
  getUniden: async () => { if (fail) throw Error("Offline"); return data },
  getBluetoothStatus: async () => ({ devices: [{ address: "A", name: "R8W", uniden: true, paired: true }] }),
  unidenOp: async body => { calls.push(body); return { config: body.config } },
}
const { UnidenPanel: panel } = await import(moduleURL("const GxNotice = {};\n" + source))
const view = Object.assign(panel.data(), panel.methods)
for (const [key, get] of Object.entries(panel.computed)) Object.defineProperty(view, key, { get: () => get.call(view) })
await view.refresh()
assert.equal(view.disabled, false)
view.config.auto_slowdown = true
view.dirty = true
await view.refresh()
assert.equal(view.config.auto_slowdown, true, "poll must not discard pending edits")
assert.equal(view.writeDisabled, true)
await view.save()
assert.equal(calls[0].config.auto_slowdown, true)
assert.equal(view.dirty, false)
view.values.volume = 5
await view.send("volume")
assert.deepEqual(calls.at(-1), { operation: "setting", setting: "volume", value: 5 })
assert.match(view.message, /confirmation is unavailable/)
const { compile } = await import(moduleURL(fs.readFileSync("../vendor/vue/vue.esm-browser.js", "utf8")))
const render = compile(panel.template, { decodeEntities: text => text })
assert.ok(render(view, []))
const collect = node => {
  if (!node || typeof node !== "object") return []
  const children = Array.isArray(node) ? node : Array.isArray(node.children) ? node.children : []
  return [node, ...children.flatMap(collect)]
}
const volumeSelect = () => collect(render(view, [])).find(node => node.type === "select" && node.props.id === "uniden-volume")
view.state = { connected: false, can_write: false }
view.dirty = true
assert.equal(volumeSelect().props.disabled, false, "value selection must not require a writable detector or saved config")
volumeSelect().props["onUpdate:modelValue"](0)
await view.refresh()
assert.equal(view.values.volume, 0, "polling must preserve selected values, including zero")
assert.equal(view.writeDisabled, true)
const beforeBlockedSend = calls.length
await view.send("volume")
assert.equal(calls.length, beforeBlockedSend)
view.offroad = false
assert.equal(volumeSelect().props.disabled, true)
view.offroad = false
const before = calls.length
await view.send("volume")
await view.save()
assert.equal(calls.length, before)
fail = true
await view.refresh()
assert.equal(view.writeDisabled, true)
assert.equal(view.state.connected, undefined)
assert.match(view.error, /Offline/)
'''
  result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], cwd=UI_ROOT, capture_output=True, text=True)
  assert result.returncode == 0, result.stdout + result.stderr
