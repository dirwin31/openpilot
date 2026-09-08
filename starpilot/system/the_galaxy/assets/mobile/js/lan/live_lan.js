import { decodeFrame } from "../ble/live_frames.js"

export function localOrigin(address) {
  const value = String(address || "").trim()
  if (!value) throw new Error("Enter the comma's local address")
  const url = new URL(value.includes("://") ? value : `http://${value}`)
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Use a local HTTP or HTTPS address")
  const host = url.hostname.toLowerCase()
  const ipv4 = host.split(".").map(Number)
  const privateIP = ipv4.length === 4 && ipv4.every(n => Number.isInteger(n) && n >= 0 && n < 256) &&
    (ipv4[0] === 10 || (ipv4[0] === 172 && ipv4[1] >= 16 && ipv4[1] <= 31) || (ipv4[0] === 192 && ipv4[1] === 168) || (ipv4[0] === 169 && ipv4[1] === 254))
  if (!privateIP && !host.endsWith(".local") && !/^\[(?:f[cd][0-9a-f]{2}:|fe[89ab][0-9a-f]:)/i.test(host)) {
    throw new Error("Use the comma's .local name or private Wi-Fi IP address")
  }
  if (!url.port && url.protocol === "http:") url.port = "8082"
  return url.origin
}

function parseEvent(raw) {
  let event = "message"
  const data = []
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim()
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart())
  }
  if (!data.length) return null
  try { return { event, data: JSON.parse(data.join("\n")) } } catch { return null }
}

export class LiveLANClient {
  constructor(callbacks = {}) {
    this.callbacks = callbacks
    this.state = "idle"
    this.generation = 0
    this.connectTimeoutMs = 3000
    this.staleTimeoutMs = 2000
  }

  _setState(state, message = "") {
    this.state = state
    this.callbacks.onState?.({ state, message, deviceName: this.deviceID, source: "lan" })
  }

  isActive() { return this.state === "connected" }

  async connect({ address, expectedIdentity } = {}) {
    this.disconnect(false)
    const generation = this.generation
    const controller = new AbortController()
    this.controller = controller
    const active = () => generation === this.generation && !controller.signal.aborted
    let ready = false
    let resolveReady, rejectReady
    const firstFrame = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject })
    // Observe immediately: cancellation can occur before fetch resolves.
    firstFrame.catch(() => {})
    const cancelled = new Promise((_, reject) => controller.signal.addEventListener("abort", () => {
      const error = controller.signal.reason || new Error("Connection cancelled")
      rejectReady(error)
      reject(error)
    }, { once: true }))
    cancelled.catch(() => {})
    const deadline = setTimeout(() => controller.abort(new Error("Local Wi-Fi timed out. Check the address and allow local network access in Chrome.")), this.connectTimeoutMs)
    const fetchLocal = async path => {
      const response = await Promise.race([fetch(`${this.origin}${path}`, {
        signal: controller.signal, cache: "no-store", credentials: "omit", targetAddressSpace: "local",
      }), cancelled])
      if (!active()) throw new Error("Connection cancelled")
      if (!response.ok) throw new Error(`Local Wi-Fi request failed (${response.status})`)
      return response
    }
    this._setState("connecting", "Checking local Wi-Fi…")
    try {
      this.origin = localOrigin(address)
      if (!expectedIdentity) throw new Error("Device identity unavailable. Reload Galaxy and try again.")
      const response = await fetchLocal("/api/telematics/status")
      const status = await Promise.race([response.json(), cancelled])
      if (!active()) return false
      if (status.device_id !== expectedIdentity) throw new Error("That address belongs to a different comma")
      if (status.available !== true || status.protocol_version !== 1 || status.frame_size !== 64 || ![1, 2].every(type => status.frame_types?.includes(type))) {
        throw new Error("The local comma needs a compatible telematics update")
      }
      if (!Number.isFinite(status.monotonic_ms)) throw new Error("Local comma does not report source timing")
      this.serverClock = status.monotonic_ms
      this.clockReceivedAt = performance.now()
      this.deviceID = status.device_id
      const stream = await fetchLocal("/api/telematics/stream")
      if (!stream.body) throw new Error("This browser cannot stream local telematics")
      const fail = error => {
        if (!active()) return
        rejectReady(error)
        controller.abort(error)
        clearTimeout(this.watchdog)
        if (ready) this._setState("error", String(error.message || error))
      }
      const fresh = () => {
        clearTimeout(this.watchdog)
        this.watchdog = setTimeout(() => fail(new Error("Local data is stale; reconnecting…")), this.staleTimeoutMs)
        if (!ready) { ready = true; this._setState("connected"); resolveReady(true) }
      }
      void this._read(stream.body, active, fresh).then(() => fail(new Error("Local connection interrupted")), fail)
      return await Promise.race([firstFrame, cancelled])
    } catch (error) {
      if (generation !== this.generation) return false
      controller.abort(error)
      clearTimeout(this.watchdog)
      this._setState("error", String(error.message || error))
      throw error
    } finally { clearTimeout(deadline) }
  }

  async _read(body, active, fresh) {
    const reader = body.getReader()
    this.reader = reader
    const decoder = new TextDecoder()
    const lastTimestamp = {}
    let buffer = ""
    try {
      while (active()) {
        const { value, done } = await reader.read()
        if (!active() || done) return
        buffer += decoder.decode(value, { stream: true })
        if (buffer.length > 65536) throw new Error("Invalid local telemetry stream")
        const events = buffer.split(/\r?\n\r?\n/)
        buffer = events.pop() || ""
        for (const raw of events) {
          if (!active()) return
          const message = parseEvent(raw)
          if (message?.event === "metadata") { this.callbacks.onMetadata?.(message.data); continue }
          if (message?.event !== "frame" || message.data?.encoding !== "base64") continue
          const { source_age_sec: age, fresh: valid, data } = message.data
          if (valid !== true || !Number.isFinite(age) || age < 0 || age >= this.staleTimeoutMs / 1000) continue
          let frame
          try { frame = decodeFrame(Uint8Array.from(atob(data), char => char.charCodeAt(0))) } catch { continue }
          if (!frame) continue
          const previous = lastTimestamp[frame.kind]
          const delta = (frame.monotonicMilliseconds - previous) >>> 0
          if (previous !== undefined && (delta === 0 || delta > 0x7fffffff)) continue
          lastTimestamp[frame.kind] = frame.monotonicMilliseconds
          // Detect old data buffered in the network as well as stale cereal data.
          const wireAge = (this.serverClock + performance.now() - this.clockReceivedAt - frame.monotonicMilliseconds) | 0
          if (wireAge >= this.staleTimeoutMs) continue
          const at = Date.now() - Math.max(age * 1000, wireAge, 0)
          if (frame.kind === "health") this.callbacks.onHealth?.(frame, at)
          else { fresh(); if (active()) this.callbacks.onLive?.(frame, null, at) }
        }
      }
    } finally {
      void reader.cancel().catch(() => {})
      reader.releaseLock()
    }
  }

  disconnect(notify = true) {
    this.generation += 1
    clearTimeout(this.watchdog)
    this.controller?.abort(new Error("Connection cancelled"))
    void this.reader?.cancel().catch(() => {})
    this.reader = null
    this.controller = null
    if (notify) this._setState("idle", "Disconnected")
  }

  close() { this.disconnect(); this.callbacks = {} }
}
