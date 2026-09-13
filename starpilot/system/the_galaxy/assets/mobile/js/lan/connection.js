import { LiveLANClient } from "./live_lan.js"
import { LiveSessionStats } from "../ble/live_frames.js"

// Owns retries, source selection and statistics. Transports never select each
// other or open a chooser during recovery.
export class TelematicsConnection {
  constructor({ ble = null, lan = new LiveLANClient(), callbacks = {} } = {}) {
    this.ble = ble
    this.lan = lan
    this.callbacks = callbacks
    this.session = new LiveSessionStats()
    this.generation = 0
    this.running = false
    this.source = ""
    this.cache = { lan: {}, bluetooth: {} }
    this.retryDelay = 1000
    if (ble) ble.autoReconnect = false
    for (const [source, client] of [["lan", lan], ["bluetooth", ble]]) {
      if (!client) continue
      const handlers = {
        onState: state => {
          this.cache[source].state = state
          if (!this.running || source !== this.source) return
          if (state.state === "connected") return // Activated only after identity validation.
          if (["error", "needs-pairing", "reconnecting", "idle"].includes(state.state) && !this.busy) {
            this._clearLive()
            this._state("reconnecting", state.message || "Connection interrupted")
            this._schedule()
          }
        },
        onCapabilities: status => {
          if (status.device_id !== this.identity) throw new Error("Bluetooth selected a different comma or needs a telematics update")
        },
        onSample: (frame, at) => {
          if (this.running && source === this.source && !this.busy && !this.retryTimer) this._live(frame, at, false)
        },
        onLive: (frame, _session, at) => {
          this.cache[source].live = [frame, at]
          if (this.running && source === this.source && !this.busy && !this.retryTimer) this._live(frame, at)
        },
        onHealth: (frame, at) => {
          this.cache[source].health = [frame, at]
          if (this.running && source === this.source && !this.busy && !this.retryTimer) this.callbacks.onHealth?.(frame, at)
        },
        onMetadata: metadata => {
          this.cache[source].metadata = metadata
          if (this.running && source === this.source && !this.busy && !this.retryTimer) this.callbacks.onMetadata?.(metadata)
        },
      }
      if (client.setCallbacks) client.setCallbacks(handlers)
      else client.callbacks = handlers
    }
  }

  configure({ mode, address, identity }) {
    if (identity !== this.identity) { this.session.reset(); this.lastTimestamp = null; this._clearLive() }
    this.mode = mode
    this.address = address
    this.identity = identity
  }

  _state(state, message = "") {
    this.callbacks.onState?.({ state, message, source: state === "connected" ? this.source : "",
      deviceName: this.identity })
  }

  _clearLive() {
    // Exclude disconnected time and time between independent publishers.
    this.session.lastMonotonicMilliseconds = null
    this.lastLiveAt = null
    this.callbacks.onLive?.(null, this.session.snapshot(), null)
    this.callbacks.onHealth?.(null, null)
    this.callbacks.onMetadata?.(null)
  }

  _live(frame, at, emit = true) {
    if (!frame || !at || Date.now() - at >= 2000) return
    const timestamp = frame.monotonicMilliseconds
    if (this.lastTimestamp !== null && this.lastTimestamp !== undefined) {
      const delta = (timestamp - this.lastTimestamp) >>> 0
      if (delta === 0) {
        if (emit && this.lastLiveAt) this.callbacks.onLive?.(frame, this.session.snapshot(), this.lastLiveAt)
        return
      }
      if (delta > 0x7fffffff) {
        if (this.lastTimestamp - timestamp < 2000) return // Overlap between publishers.
        this.session.reset() // Device restarted.
      }
    }
    this.lastTimestamp = timestamp
    this.lastLiveAt = at
    this.session.consume(frame)
    if (emit) this.callbacks.onLive?.(frame, this.session.snapshot(), at)
  }

  connect() {
    this.disconnect(false)
    this.running = true
    this.retryDelay = 1000
    const generation = this.generation
    this.watchdog = setInterval(() => {
      if (!this.running || this.busy || !this.source || Date.now() - (this.lastLiveAt || this.activatedAt) < 2000) return
      this._clearLive()
      this._state("reconnecting", "Live data stopped; reconnecting…")
      this._schedule()
    }, 500)
    return this._run(generation)
  }

  async _run(generation) {
    if (!this.running || generation !== this.generation || this.busy) return
    this.busy = true
    clearTimeout(this.retryTimer)
    this.retryTimer = null
    this._state("connecting", "Connecting…")
    let message = "Local Wi-Fi unavailable"
    let connected = false
    const source = this.mode === "bluetooth" ? "bluetooth" : "lan"
    try {
      this.source = source
      const client = source === "lan" ? this.lan : this.ble
      if (!client) message = "Open your Galaxy link in Chrome on Android to use Bluetooth."
      else {
        this.cache[source] = {}
        try {
          let result
          if (source === "lan") result = await client.connect({ address: this.address, expectedIdentity: this.identity })
          else {
            client.manualDisconnect = false
            result = client.device ? await client.reconnect() : await client.reconnectRemembered()
          }
          if (generation !== this.generation) return
          if (result === false || client.state !== "connected" || !client.isActive()) throw new Error(this.cache[source].state?.message || "No paired phone. Pair it under Tools → Bluetooth → Phone.")
          connected = true
          this.busy = false
          this._activate(source)
          return true
        } catch (error) {
          if (generation !== this.generation) return
          message = String(error.message || error)
          client.disconnect()
        }
      }
    } finally {
      if (generation === this.generation) {
        this.busy = false
        if (!connected) {
          this.source = ""
          this._clearLive()
          this._state("error", message)
          // Without a Bluetooth client this page can never succeed, so do not retry.
          if (source === "lan" || this.ble) this._schedule()
        }
      }
    }
    return false
  }

  _activate(source) {
    clearTimeout(this.retryTimer)
    this.retryTimer = null
    this.source = source
    this.retryDelay = 1000
    this.session.lastMonotonicMilliseconds = null
    this.lastLiveAt = null
    this.activatedAt = Date.now()
    this._state("connected")
    const cache = this.cache[source]
    this.callbacks.onMetadata?.(cache.metadata || null)
    if (cache.health) this.callbacks.onHealth?.(...cache.health)
    if (cache.live) this._live(...cache.live)
  }

  _schedule() {
    if (!this.running || this.retryTimer) return
    const generation = this.generation
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      if (generation !== this.generation) return
      this.source = ""
      this.lan.disconnect()
      this.ble?.disconnect()
      void this._run(generation)
    }, this.retryDelay)
    this.retryDelay = Math.min(15000, this.retryDelay * 2)
  }

  resume() {
    if (this.running) return this.connect()
  }

  disconnect(notify = true) {
    this.generation += 1
    this.running = false
    this.busy = false
    this.source = ""
    clearTimeout(this.retryTimer)
    this.retryTimer = null
    clearInterval(this.watchdog)
    this.lan.disconnect()
    this.ble?.disconnect()
    this._clearLive()
    if (notify) this._state("idle", "Disconnected")
  }

  close() { this.disconnect(); this.lan.close(); this.ble?.detach(); this.callbacks = {} }
}
