import assert from "node:assert/strict"
import { LiveLANClient, localOrigin } from "./live_lan.mjs"
import { TelematicsConnection } from "./connection.mjs"
import { LIVE_FLAGS } from "./live_frames.mjs"
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
const until = async condition => {
  for (let n = 0; n < 100 && !condition(); n++) await wait(2)
  assert.ok(condition(), "condition timed out")
}
const status = { available: true, device_id: "comma", protocol_version: 1, frame_size: 64, frame_types: [1, 2], monotonic_ms: 10000 }
const config = { address: "http://starpilot-comma.local:8082", expectedIdentity: "comma" }
const event = (name, data) => new TextEncoder().encode(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`)
const sample = (time = 10000, fresh = true) => {
  const bytes = new Uint8Array(64)
  const view = new DataView(bytes.buffer)
  bytes.set([83, 80, 1, 1]); view.setUint16(4, 64, true); view.setUint32(8, time, true)
  return event("frame", { encoding: "base64", data: Buffer.from(bytes).toString("base64"), source_age_sec: fresh ? 0 : 500, fresh })
}
const clientFor = callbacks => {
  const client = new LiveLANClient(callbacks)
  client.connectTimeoutMs = 35
  client.staleTimeoutMs = 60
  return client
}
assert.equal(localOrigin("172.20.10.1"), "http://172.20.10.1:8082")
assert.equal(localOrigin("https://starpilot-comma.local:8443/"), "https://starpilot-comma.local:8443")
assert.throws(() => localOrigin("https://galaxy.link"), /local/)
assert.throws(() => localOrigin("http://user:pass@192.168.1.2"), /local/)

// Every connection stage is bounded, including JSON and stream headers.
for (const stage of ["status", "json", "stream", "first-frame"]) {
  globalThis.fetch = async url => {
    if (url.endsWith("/status")) return stage === "status" ? new Promise(() => {}) : { ok: true, json: () => stage === "json" ? new Promise(() => {}) : Promise.resolve(status) }
    return stage === "stream" ? new Promise(() => {}) : { ok: true, body: new ReadableStream() }
  }
  const client = clientFor()
  await assert.rejects(client.connect(config), /timed out/)
  assert.equal(client.state, "error")
  client.close()
}
let streams = 0
fetch = async url => url.endsWith("/status") ? { ok: true, json: async () => ({ ...status, device_id: "other" }) } : (streams++, {})
await assert.rejects(clientFor().connect(config), /different comma/)
assert.equal(streams, 0)
for (const altered of [{ frame_types: [1] }, { protocol_version: 2 }, { frame_size: 32 }]) {
  fetch = async () => ({ ok: true, json: async () => ({ ...status, ...altered }) })
  await assert.rejects(clientFor().connect(config), /compatible/)
}

// Cancel during status body parsing must never open a subsequent stream.
let finishStatus, signal
fetch = async (_url, options) => { signal = options.signal; return { ok: true, json: () => new Promise(resolve => { finishStatus = resolve }) } }
const cancelled = clientFor()
const pending = cancelled.connect(config)
await until(() => finishStatus)
cancelled.disconnect()
finishStatus(status)
assert.equal(await pending, false)
assert.equal(signal.aborted, true)
assert.equal(cancelled.state, "idle")

// Reject frozen source data even when the packet clock is new.
let wire
fetch = async url => url.endsWith("/status") ? { ok: true, json: async () => status } : {
  ok: true, body: new ReadableStream({ start(controller) { wire = controller; controller.enqueue(sample(10000, false)) } }),
}
await assert.rejects(clientFor().connect(config), /timed out/)

// Fresh frames connect. Replaying a timestamp must not keep the link alive.
let lives = 0, metadata = 0
fetch = async url => url.endsWith("/status") ? { ok: true, json: async () => status } : {
  ok: true, body: new ReadableStream({ start(controller) { wire = controller; controller.enqueue(sample()) } }),
}
const live = clientFor({ onLive: () => lives++, onMetadata: () => metadata++ })
assert.equal(await live.connect(config), true)
wire.enqueue(sample())
wire.enqueue(event("metadata", { model: "x" }))
await wait(10)
assert.equal(lives, 1)
assert.equal(metadata, 1)
await wait(65)
assert.equal(live.state, "error")
live.close()

// A read resolving after cancellation cannot publish anything.
let finishRead, reads = 0
fetch = async url => url.endsWith("/status") ? { ok: true, json: async () => status } : {
  ok: true, body: { getReader: () => ({
    read: () => ++reads === 1 ? Promise.resolve({ value: sample(), done: false }) : new Promise(resolve => { finishRead = resolve }),
    cancel: async () => {}, releaseLock() {},
  }) },
}
const late = clientFor({ onMetadata: () => metadata++ })
await late.connect(config)
await until(() => finishRead)
late.disconnect()
finishRead({ value: event("metadata", { late: true }), done: false })
await wait(5)
assert.equal(metadata, 1)

// Controller transport doubles model real callbacks, including reconnecting
// synchronously at the beginning of a remembered BLE restore.
class Transport {
  constructor() { this.state = "idle"; this.calls = 0; this.drops = 0; this.callbacks = {}; this.fail = false }
  setCallbacks(callbacks) { this.callbacks = callbacks }
  stateTo(state, message = "") { this.state = state; this.callbacks.onState?.({ state, message }) }
  async connect() {
    this.calls++
    this.stateTo("connecting")
    if (this.fail) { this.stateTo("error", "Wi-Fi unavailable"); throw new Error("Wi-Fi unavailable") }
    this.stateTo("connected")
    return true
  }
  isActive() { return this.state === "connected" }
  disconnect() { this.drops++; this.stateTo("idle") }
  close() { this.disconnect() }
  detach() { this.callbacks = {} }
}
class BLE extends Transport {
  constructor() { super(); this.choosers = 0; this.restores = 0; this.retry = 0; this.device = null }
  connect() { this.choosers++; return super.connect() }
  async reconnectRemembered() { this.restores++; this.stateTo("reconnecting"); return super.connect() }
  async reconnect() { this.retry++; this.stateTo("reconnecting"); return super.connect() }
}
const lan = new Transport(), ble = new BLE(), observed = []
const controller = new TelematicsConnection({ lan, ble, callbacks: { onState: value => observed.push(value), onLive: (frame, stats) => {} } })
controller.configure({ mode: "automatic", identity: "comma", address: config.address })
lan.fail = true
await controller.connect()
assert.equal(controller.source, "bluetooth")
assert.equal(ble.restores, 1)
assert.equal(ble.choosers, 0, "Automatic must never invoke a chooser")
assert.equal(lan.calls, 1, "BLE reconnecting must not restart LAN selection")
assert.equal(ble.state, "connected")

// Explicit pairing reaches the chooser before yielding for any LAN request.
const pairing = controller.connect({ chooseNew: true })
assert.equal(ble.choosers, 1)
assert.equal(lan.calls, 1)
await pairing

// One session survives source changes, excludes gaps, and rejects overlap.
const frame = time => ({ monotonicMilliseconds: time, flags: LIVE_FLAGS.started | LIVE_FLAGS.telemetryValid | LIVE_FLAGS.lateralActive })
ble.callbacks.onLive(frame(1000), null, Date.now())
ble.callbacks.onLive(frame(2000), null, Date.now())
assert.equal(controller.session.observedDrivingSeconds, 1)
controller.disconnect()
assert.equal(controller.session.observedDrivingSeconds, 1)
lan.fail = false
await controller.connect()
assert.equal(controller.source, "lan")
lan.callbacks.onLive(frame(1900), null, Date.now())
assert.equal(controller.session.observedDrivingSeconds, 1)
lan.callbacks.onLive(frame(10000), null, Date.now())
assert.equal(controller.session.observedDrivingSeconds, 1, "Disconnected time must not count")
lan.callbacks.onLive(frame(11000), null, Date.now())
lan.callbacks.onLive(frame(11000), null, Date.now())
ble.callbacks.onLive(frame(12000), null, Date.now())
assert.equal(controller.session.observedDrivingSeconds, 2, "Inactive and duplicate frames must not count")
controller.disconnect()
const callsBefore = lan.calls
controller.resume()
await wait(5)
assert.equal(lan.calls, callsBefore, "Resume cannot undo Disconnect")

// A healthy LAN probe must remain healthy before replacing BLE.
lan.fail = true
controller.probeDelayMs = 10
controller.switchbackMs = 30
await controller.connect()
lan.fail = false
await until(() => lan.state === "connected")
assert.equal(controller.source, "bluetooth")
lan.callbacks.onLive(frame(13000), null, Date.now())
await wait(40)
assert.equal(controller.source, "lan")
assert.equal(ble.state, "idle")
controller.configure({ mode: "bluetooth", identity: "other", address: config.address })
assert.equal(controller.session.observedDrivingSeconds, 0)
controller.close()

// Superseded failures cannot disconnect the replacement attempt.
const slowLAN = new Transport(), replacementBLE = new BLE()
let rejectOld
slowLAN.connect = () => new Promise((_, reject) => { rejectOld = reject })
const switched = new TelematicsConnection({ lan: slowLAN, ble: replacementBLE })
switched.configure({ mode: "lan", identity: "comma", address: config.address })
const old = switched.connect()
switched.configure({ mode: "bluetooth", identity: "comma", address: config.address })
await switched.connect()
const dropsBefore = replacementBLE.drops
rejectOld(new Error("old failure"))
await old
assert.equal(switched.source, "bluetooth")
assert.equal(replacementBLE.drops, dropsBefore)
switched.close()
