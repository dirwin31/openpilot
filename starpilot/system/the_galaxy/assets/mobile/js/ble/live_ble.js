import { LiveReassembler, LiveSessionStats } from "./live_frames.js"

export const COMPANION_UUIDS = Object.freeze({
  service: "9b6d1000-6f7a-4a5b-8c3d-2e1f0a9b8c7d",
  status: "9b6d1001-6f7a-4a5b-8c3d-2e1f0a9b8c7d",
  command: "9b6d1002-6f7a-4a5b-8c3d-2e1f0a9b8c7d",
  response: "9b6d1003-6f7a-4a5b-8c3d-2e1f0a9b8c7d",
  live: "9b6d1004-6f7a-4a5b-8c3d-2e1f0a9b8c7d",
})

const LAST_DEVICE_KEY = "galaxy-live-ble-device"
const PAIRING_MESSAGE = "Pair the device first: open the 120-second companion pairing window in the device Bluetooth settings, then reconnect."
const SECURITY_READ_RETRIES = 2
const SECURITY_READ_RETRY_MS = 750
const NOTIFICATION_RETRIES = 2
const NOTIFICATION_RETRY_MS = 400
// Android Chrome's gatt.connect() can hang indefinitely when the peripheral is
// mid-teardown or briefly out of range. Bound it so a stuck attempt rejects and
// the reconnect backoff can try again instead of leaving the view stuck.
const CONNECT_TIMEOUT_MS = 12000
// A device handed back by getDevices() after a page reload carries no scan
// result, so Android Chrome rejects the first gatt.connect() with "no longer in
// range" even though the peripheral is sitting right there. watchAdvertisements()
// is how a page asks Chrome to look again; the connect only works once an
// advertisement has arrived. It needs the experimental web platform features
// flag on Android, so treat its absence as a setup problem, not a dead device.
const ADVERTISEMENT_TIMEOUT_MS = 20000
const OUT_OF_RANGE_MESSAGE = "The device has not advertised yet. Keep the phone near it with the companion running, then try Reconnect. Saved connections also retry automatically."
const ADVERTISEMENT_UNSUPPORTED_MESSAGE = "Chrome cannot rescan for this device after a reload. Enable chrome://flags/#enable-experimental-web-platform-features, then reload this page."

class AdvertisementError extends Error {
  constructor(message, retryable = false) {
    super(message)
    this.name = "AdvertisementError"
    this.retryable = retryable
  }
}

class PairingRequiredError extends Error {
  constructor(cause) {
    super(PAIRING_MESSAGE, { cause })
    this.name = "PairingRequiredError"
  }
}

function errorText(error) {
  return String(error?.message || error || "Bluetooth connection failed")
}

function isOutOfRangeError(error) {
  return /no longer in range|not in range/i.test(errorText(error))
}

function isPairingError(error, allowGenericGattError = false) {
  const value = `${error?.name || ""} ${errorText(error)}`.toLowerCase()
  return /authentication|authorization|encrypt|security|bond|pair/.test(value)
    || (allowGenericGattError && /gatt operation not permitted/.test(value))
}

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds))

function decodeJSON(value) {
  const bytes = value instanceof DataView || ArrayBuffer.isView(value)
    ? new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
    : new Uint8Array(value)
  return JSON.parse(new TextDecoder().decode(bytes))
}

export class LiveBLEClient {
  constructor(callbacks = {}) {
    this.callbacks = callbacks
    this.reassembler = new LiveReassembler()
    this.session = new LiveSessionStats()
    this.state = "idle"
    this.message = ""
    this.device = null
    this.characteristics = {}
    this.pendingFrame = null
    this.lastPublishedAt = 0
    this.uiTimer = null
    this.reconnectTimer = null
    this.reconnectAttempt = 0
    this.closed = false
    this.manualDisconnect = false
    this.metadataPending = false
    this.connectionGeneration = 0
    this.connectAttempt = 0
    this.advertisementTimeoutMs = ADVERTISEMENT_TIMEOUT_MS
    this.advertisementController = null
    this.restoredDeviceID = null
    this.savedDeviceAtLoad = null
    try { this.savedDeviceAtLoad = localStorage.getItem(LAST_DEVICE_KEY) } catch (error) { /* Storage is optional. */ }
    this.selectedDevices = new Set()
    this.lastMetadataRevision = null
    this.lastAlertID = null
    this.lastLive = null
    this.lastHealth = null
    this.lastMetadata = undefined
    this._commandTail = Promise.resolve()
    this._onDisconnected = () => this._handleDisconnected()
    this._onNotification = (event) => this._handleNotification(event)
  }

  _setState(state, message = "") {
    this.state = state
    this.message = message
    this.callbacks.onState?.({ state, message, deviceName: this.device?.name || "Galaxy device", restoredAfterReload: this.restoredDeviceID !== null && this.restoredDeviceID === this.device?.id })
  }

  setCallbacks(callbacks = {}) {
    this.callbacks = callbacks
    this.closed = false
  }

  detach() {
    this.callbacks = {}
  }

  isActive() {
    if (["connecting", "reconnecting"].includes(this.state)) return true
    return this.state === "connected" && this.device?.gatt?.connected === true
  }

  emitCurrent() {
    this._setState(this.state, this.message)
    if (this.lastLive) this.callbacks.onLive?.(this.lastLive.frame, this.lastLive.session, this.lastLive.at)
    if (this.lastHealth) this.callbacks.onHealth?.(this.lastHealth.frame, this.lastHealth.at)
    if (this.lastMetadata !== undefined) this.callbacks.onMetadata?.(this.lastMetadata)
  }

  async _withTimeout(promise, ms, message, { onTimeout = null, onResolve = null } = {}) {
    let timer = null
    let timedOut = false
    const observed = Promise.resolve(promise).then((value) => {
      // A Web Bluetooth connect promise cannot be cancelled. Observe a late
      // resolution so the caller can tear down a link that outlived its attempt.
      onResolve?.({ timedOut })
      return value
    })
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => {
        timedOut = true
        onTimeout?.()
        reject(new DOMException(message, "TimeoutError"))
      }, ms)
    })
    try {
      return await Promise.race([observed, timeout])
    } finally {
      clearTimeout(timer)
    }
  }

  async reconnectRemembered() {
    if (!navigator.bluetooth?.getDevices || this.closed) return false
    const attempt = this.connectAttempt
    try {
      const devices = await navigator.bluetooth.getDevices()
      if (attempt !== this.connectAttempt || this.closed || this.manualDisconnect) return false
      let savedID = null
      try { savedID = localStorage.getItem(LAST_DEVICE_KEY) } catch (error) { /* Storage is optional. */ }
      const device = devices.find((candidate) => candidate.id === savedID) || (devices.length === 1 ? devices[0] : null)
      if (!device) return false
      await this._connectDevice(device, true)
      return this.state === "connected" && this.device === device
    } catch (error) {
      // _connectDevice owns connection errors; a cancelled attempt must stay idle.
      if (attempt === this.connectAttempt) this._handleError(error)
      return false
    }
  }

  async connect() {
    // Choosing a device is an explicit replacement of any pending restore/retry.
    this.disconnect()
    this.device?.removeEventListener("gattserverdisconnected", this._onDisconnected)
    this.device = null
    this.restoredDeviceID = null
    this.closed = false
    this.manualDisconnect = false
    const attempt = this.connectAttempt
    this._setState("connecting", "Choose a device in Chrome")
    try {
      const device = await navigator.bluetooth.requestDevice({ filters: [{ services: [COMPANION_UUIDS.service] }] })
      if (attempt !== this.connectAttempt || this.closed || this.manualDisconnect) return
      this.selectedDevices.add(device.id)
      await this._connectDevice(device, false)
    } catch (error) {
      if (attempt !== this.connectAttempt) return
      if (error?.name === "NotFoundError") {
        this._setState("idle", "No device selected")
        return
      }
      this._handleError(error)
      throw error
    }
  }

  async reconnect() {
    if (this.device) return this._connectDevice(this.device, true)
    return this.connect()
  }

  async _connectDevice(device, reconnecting) {
    clearTimeout(this.reconnectTimer)
    this.advertisementController?.abort()
    const attempt = ++this.connectAttempt
    this.device = device
    this.manualDisconnect = false
    device.removeEventListener("gattserverdisconnected", this._onDisconnected)
    device.addEventListener("gattserverdisconnected", this._onDisconnected)
    this._setState(reconnecting ? "reconnecting" : "connecting", reconnecting ? "Reconnecting…" : "Connecting…")
    try {
      const server = await this._connectGATT(device, attempt)
      if (attempt !== this.connectAttempt) return
      const service = await this._withTimeout(server.getPrimaryService(COMPANION_UUIDS.service), CONNECT_TIMEOUT_MS, "Bluetooth service discovery timed out")
      if (attempt !== this.connectAttempt) return
      const [status, command, response, live] = await this._withTimeout(Promise.all([
        service.getCharacteristic(COMPANION_UUIDS.status),
        service.getCharacteristic(COMPANION_UUIDS.command),
        service.getCharacteristic(COMPANION_UUIDS.response),
        service.getCharacteristic(COMPANION_UUIDS.live),
      ]), CONNECT_TIMEOUT_MS, "Bluetooth characteristic discovery timed out")
      if (attempt !== this.connectAttempt) return
      this.characteristics = { status, command, response, live }
      const capabilities = await this._withTimeout(this._readAuthenticatedStatus(status), CONNECT_TIMEOUT_MS, "Bluetooth authentication timed out")
      if (attempt !== this.connectAttempt) return
      this.callbacks.onCapabilities?.(capabilities)
      if (attempt !== this.connectAttempt) return
      live.removeEventListener("characteristicvaluechanged", this._onNotification)
      live.addEventListener("characteristicvaluechanged", this._onNotification)
      await this._withTimeout(this._startNotifications(live), CONNECT_TIMEOUT_MS, "Bluetooth notifications timed out")
      if (attempt !== this.connectAttempt) return
      this.connectionGeneration += 1
      this.reconnectAttempt = 0
      this.reassembler.reset()
      this.session.reset()
      try { localStorage.setItem(LAST_DEVICE_KEY, device.id) } catch (error) { /* Storage is optional. */ }
      if (reconnecting && device.id === this.savedDeviceAtLoad && !this.selectedDevices.has(device.id)) this.restoredDeviceID = device.id
      this._setState("connected", `Connected to ${device.name || "Galaxy device"}`)
      void this._refreshMetadata()
    } catch (error) {
      if (attempt !== this.connectAttempt) return
      this._handleError(error)
      const retryable = !(error instanceof AdvertisementError) || error.retryable
      if (!retryable) {
        this.manualDisconnect = true
        clearTimeout(this.reconnectTimer)
        this.reconnectTimer = null
      }
      if (reconnecting && retryable && !this.closed && !this.manualDisconnect) this._scheduleReconnect()
      throw error
    }
  }

  // Chrome will not connect to a device it has not seen advertise since the page
  // loaded, which is every remembered device after a refresh. Take the rejection
  // as the cue to scan for the peripheral, then connect to the fresh sighting.
  async _connectGATT(device, attempt) {
    try {
      return await this._openGATT(device, attempt)
    } catch (error) {
      if (!isOutOfRangeError(error) || attempt !== this.connectAttempt) throw error
      if (typeof device.watchAdvertisements !== "function") throw new AdvertisementError(ADVERTISEMENT_UNSUPPORTED_MESSAGE)
      this._setState(this.state, "Looking for the device…")
      await this._awaitAdvertisement(device)
      if (attempt !== this.connectAttempt) throw error
      return await this._openGATT(device, attempt)
    }
  }

  _openGATT(device, attempt) {
    return this._withTimeout(
      device.gatt.connect(),
      CONNECT_TIMEOUT_MS,
      "Bluetooth connection timed out",
      {
        onTimeout: () => {
          if (attempt !== this.connectAttempt) return
          try { device.gatt.disconnect() } catch (error) { /* The timed-out link is already gone. */ }
        },
        onResolve: ({ timedOut }) => {
          if (!timedOut && attempt === this.connectAttempt) return
          // The GATT server is shared by attempts on the same BluetoothDevice.
          // An old promise must not tear down a newer connection to that device.
          if (attempt !== this.connectAttempt && this.device === device && !this.manualDisconnect && !this.closed) return
          try { device.gatt.disconnect() } catch (error) { /* The stale link is already gone. */ }
        },
      },
    )
  }

  // Resolves once Chrome sees the peripheral advertise. The scan is stopped
  // on every exit: an abandoned watch keeps the phone's radio busy for nothing.
  async _awaitAdvertisement(device) {
    const controller = new AbortController()
    this.advertisementController = controller
    let timer = null
    try {
      const advertised = new Promise((resolve, reject) => {
        device.addEventListener("advertisementreceived", () => resolve(true), { once: true, signal: controller.signal })
        controller.signal.addEventListener("abort", () => reject(new DOMException("Bluetooth scan cancelled", "AbortError")), { once: true })
        timer = setTimeout(() => reject(new AdvertisementError(OUT_OF_RANGE_MESSAGE, true)), this.advertisementTimeoutMs)
      })
      // Observe startup and the deadline together: startup itself can hang.
      const started = Promise.resolve().then(() => device.watchAdvertisements({ signal: controller.signal }))
      await Promise.race([Promise.all([started, advertised]), advertised])
    } catch (error) {
      if (error instanceof AdvertisementError || controller.signal.aborted) throw error
      throw new AdvertisementError(`Chrome could not start the Bluetooth scan: ${errorText(error)}. Check Chrome's Bluetooth/Nearby devices permission and the phone's Bluetooth setting, then try Reconnect.`)
    } finally {
      clearTimeout(timer)
      controller.abort()
      if (this.advertisementController === controller) this.advertisementController = null
    }
  }

  async _readAuthenticatedStatus(characteristic) {
    let failure = null
    for (let attempt = 0; attempt <= SECURITY_READ_RETRIES; attempt += 1) {
      try {
        return decodeJSON(await characteristic.readValue())
      } catch (error) {
        if (!isPairingError(error, true)) throw error
        failure = error
        if (attempt < SECURITY_READ_RETRIES) await wait(SECURITY_READ_RETRY_MS)
      }
    }
    throw new PairingRequiredError(failure)
  }

  async _startNotifications(characteristic) {
    let failure = null
    for (let attempt = 0; attempt <= NOTIFICATION_RETRIES; attempt += 1) {
      try {
        return await characteristic.startNotifications()
      } catch (error) {
        failure = error
        const value = `${error?.name || ""} ${errorText(error)}`.toLowerCase()
        const transient = /network|gatt|operation|in progress|already/.test(value) && !isPairingError(error)
        if (!transient || attempt === NOTIFICATION_RETRIES) throw error
        await wait(NOTIFICATION_RETRY_MS)
      }
    }
    throw failure
  }

  _handleNotification(event) {
    const frame = this.reassembler.consume(event.target?.value)
    if (!frame) return
    if (frame.kind === "health") {
      const at = Date.now()
      this.lastHealth = { frame, at }
      this.callbacks.onHealth?.(frame, at)
      return
    }

    this.session.consume(frame)
    this.pendingFrame = frame
    this._scheduleLiveUIUpdate()
    if (this.lastMetadataRevision !== frame.metadataRevision || this.lastAlertID !== frame.alertID) {
      void this._refreshMetadata(frame.metadataRevision, frame.alertID)
    }
  }

  _scheduleLiveUIUpdate() {
    const elapsed = Date.now() - this.lastPublishedAt
    if (this.lastPublishedAt === 0 || elapsed >= 200) {
      this._publishLiveUIUpdate()
    } else if (this.uiTimer === null) {
      this.uiTimer = setTimeout(() => {
        this.uiTimer = null
        this._publishLiveUIUpdate()
      }, 200 - elapsed)
    }
  }

  _publishLiveUIUpdate() {
    if (!this.pendingFrame) return
    const frame = this.pendingFrame
    this.pendingFrame = null
    this.lastPublishedAt = Date.now()
    const session = this.session.snapshot()
    this.lastLive = { frame, session, at: this.lastPublishedAt }
    this.callbacks.onLive?.(frame, session, this.lastPublishedAt)
  }

  async command(op, payload = {}) {
    const task = this._commandTail.then(async () => {
      const { command, response } = this.characteristics
      if (!command || !response || !this.device?.gatt?.connected) throw new Error("Bluetooth is not connected")
      const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
      const encoded = new TextEncoder().encode(JSON.stringify({ ...payload, id, op }))
      if (encoded.byteLength > 512) throw new Error("Command exceeds 512 bytes")
      if (command.writeValueWithResponse) await command.writeValueWithResponse(encoded)
      else await command.writeValue(encoded)
      const envelope = decodeJSON(await response.readValue())
      if (!envelope.ok) throw new Error(envelope.error || "Companion command failed")
      if (envelope.id && envelope.id !== id) throw new Error("Companion response did not match the request")
      return envelope.data
    })
    this._commandTail = task.catch(() => {})
    return task
  }

  async _refreshMetadata(revision = null, alertID = null) {
    if (this.metadataPending || !this.characteristics.command) return
    this.metadataPending = true
    const generation = this.connectionGeneration
    try {
      const metadata = await this.command("get_live_metadata")
      if (generation !== this.connectionGeneration || !this.device?.gatt?.connected) return
      this.lastMetadataRevision = revision
      this.lastAlertID = alertID
      this.lastMetadata = metadata
      this.callbacks.onMetadata?.(metadata)
    } catch (error) {
      if (isPairingError(error)) this._handleError(error)
    } finally {
      this.metadataPending = false
    }
  }

  _handleDisconnected() {
    this.characteristics = {}
    this.reassembler.reset()
    this._clearTelemetry()
    clearTimeout(this.uiTimer)
    this.uiTimer = null
    if (this.closed || this.manualDisconnect) {
      this._setState("idle", "Disconnected")
      return
    }
    this._setState("reconnecting", "Bluetooth disconnected; reconnecting…")
    this._scheduleReconnect()
  }

  _scheduleReconnect() {
    clearTimeout(this.reconnectTimer)
    const delay = Math.min(10000, 1000 * (2 ** Math.min(this.reconnectAttempt, 3)))
    this.reconnectAttempt += 1
    this.reconnectTimer = setTimeout(async () => {
      if (!this.device || this.closed || this.manualDisconnect) return
      try {
        await this._connectDevice(this.device, true)
      } catch (error) { /* _connectDevice schedules the next attempt. */ }
    }, delay)
  }

  _handleError(error) {
    if (error instanceof AdvertisementError || error?.name === "TimeoutError") this._setState("error", error.message)
    else if (error instanceof PairingRequiredError || isPairingError(error)) this._setState("needs-pairing", PAIRING_MESSAGE)
    else if (isOutOfRangeError(error)) {
      const rescannable = typeof this.device?.watchAdvertisements === "function"
      this._setState("error", rescannable ? OUT_OF_RANGE_MESSAGE : ADVERTISEMENT_UNSUPPORTED_MESSAGE)
    }
    else this._setState("error", errorText(error))
  }

  _clearTelemetry() {
    this.connectionGeneration += 1
    this.pendingFrame = null
    this.lastPublishedAt = 0
    this.lastMetadataRevision = null
    this.lastAlertID = null
    this.lastLive = null
    this.lastHealth = null
    this.lastMetadata = undefined
    this.session.reset()
    this.callbacks.onLive?.(null, this.session.snapshot(), null)
    this.callbacks.onHealth?.(null, null)
    this.callbacks.onMetadata?.(null)
  }

  disconnect() {
    this.connectAttempt += 1
    this.manualDisconnect = true
    this.advertisementController?.abort()
    clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
    try { this.device?.gatt?.disconnect() } catch (error) { /* Already disconnected. */ }
    this.characteristics = {}
    this._clearTelemetry()
    this._setState("idle", "Disconnected")
  }

  close() {
    this.connectAttempt += 1
    this.closed = true
    this.manualDisconnect = true
    this.advertisementController?.abort()
    clearTimeout(this.reconnectTimer)
    clearTimeout(this.uiTimer)
    this.device?.removeEventListener("gattserverdisconnected", this._onDisconnected)
    this.characteristics.live?.removeEventListener("characteristicvaluechanged", this._onNotification)
    if (this.device?.gatt?.connected) this.device.gatt.disconnect()
    this._clearTelemetry()
    this.characteristics = {}
  }
}
let sharedLiveBLEClient = null
export function getLiveBLEClient() {
  if (!sharedLiveBLEClient) sharedLiveBLEClient = new LiveBLEClient()
  return sharedLiveBLEClient
}
