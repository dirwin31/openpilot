import assert from "node:assert/strict"
import { JetlinkPage, addonState } from "../web/js/jetlink.js"

assert.equal(addonState(null).label, "Unavailable")
assert.equal(addonState({ enabled: true, present: false }).label, "Waiting for JetLink")
assert.equal(addonState({ enabled: false, present: false }).label, "Off")
assert.equal(addonState({ enabled: true, present: true, running: false }).label, "Idle")
assert.equal(addonState({ enabled: true, present: true, running: true, connected: true, modelMismatch: true }).label, "Jetson has a different model")
assert.equal(addonState({ enabled: true, present: true, running: true, connected: true, yielding: true }).label, "Yielding to driving model")
assert.equal(addonState({ enabled: true, present: true, running: true, connected: true }).label, "On Jetson")
assert.equal(addonState({ enabled: true, present: true, running: true, connected: false }).label, "Waiting for Jetson")
assert.equal(addonState({ enabled: true, present: false }, false).label, "Needs USB computer")

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
console.log("jetlink page ok")
