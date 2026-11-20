import assert from "node:assert/strict"
import { JetlinkPage, addonState } from "../web/js/jetlink.js"

assert.equal(addonState(null), "Unavailable")
assert.equal(addonState({ enabled: true, present: false }), "Waiting for JetLink")
assert.equal(addonState({ enabled: false, present: false }), "Off")
assert.equal(addonState({ enabled: true, present: true, running: false }), "Idle")
assert.equal(addonState({ enabled: true, present: true, running: true, connected: true, modelMismatch: true }), "Jetson has a different model")
assert.equal(addonState({ enabled: true, present: true, running: true, connected: true, yielding: true }), "Yielding to driving model")
assert.equal(addonState({ enabled: true, present: true, running: true, connected: true }), "On Jetson")
assert.equal(addonState({ enabled: true, present: true, running: true, connected: false }), "Waiting for Jetson")
assert.equal(addonState({ enabled: true, present: false }, false), "Needs USB computer")

const jetlink = { mode: "usb", chargePhone: false, addons: { vasm: { enabled: false }, signVision: { enabled: true } } }
const calls = []
const page = { jetlink, addon: (field) => jetlink.addons[field], configure: (change) => calls.push(change) }
const charge = { target: { checked: true } }
JetlinkPage.methods.toggle.call(page, "chargePhone", charge)
assert.equal(charge.target.checked, false)
const vasm = { target: { checked: true } }
JetlinkPage.methods.toggle.call(page, "vasm", vasm)
assert.equal(vasm.target.checked, false)
assert.deepEqual(calls, [{ chargePhone: true }, { vasm: true }])

const sent = []
const configuring = { ...JetlinkPage.data(), canChange: true, jetlink: { ...jetlink, chargePhone: true },
  feed: { action: async (action, model, payload) => { sent.push([action, payload]); return { message: "saved" } } } }
await JetlinkPage.methods.configure.call(configuring, { mode: "ios" })
await JetlinkPage.methods.configure.call(configuring, { signVision: false })
assert.deepEqual(sent, [["jetlink", { mode: "ios", chargePhone: true }], ["jetlink", { signVision: false }]])
assert.equal(configuring.busy, "")
assert.equal(configuring.message, "saved")

assert.equal(JetlinkPage.methods.timing({ connected: true, answered: 12, roundTripMs: 4.1, jetsonMs: 2.2 }), "12 passes · 4.1 ms round trip · 2.2 ms on Jetson")
assert.equal(JetlinkPage.methods.timing({ connected: false }), "")
assert.match(JetlinkPage.template, /href="#\/manage_models"/)
assert.match(JetlinkPage.template, /Prepared means the remote model is built, not active/)
assert.match(JetlinkPage.template, /Small and Chestnut catalog selections are unchanged/)
assert.match(JetlinkPage.template, /tools\/jetson_addons\/install\.sh again/)
assert.ok(JetlinkPage.template.includes('href="https://github.com/StarPilotHQ/StarPilot/blob/testing/openpilot/starpilot/models/JETLINK.md"'), "setup guide points at testing")
const rowsPage = { jetlink: { mode: "usb", addons: { vasm: { enabled: true, present: true, running: false } } }, usb: true,
  addon: JetlinkPage.methods.addon, timing: JetlinkPage.methods.timing }
const rows = JetlinkPage.computed.addonRows.call(rowsPage)
assert.deepEqual(rows.map(row => [row.field, row.label, row.idle, row.enabled]), [["vasm", "Idle", true, true], ["signVision", "Unavailable", false, false]])
assert.ok(!JetlinkPage.template.includes("addon(item.field)"), "each row reads its add-on once")
console.log("jetlink page ok")
