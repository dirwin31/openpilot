import { api, showSnackbar } from "../api.js"
import { usePolling } from "../composables.js"
import { GxNotice } from "../components/GxNotice.js"
import { GalaxyModal } from "../components/GalaxyModal.js"
import { store } from "../store.js"
import { isIOSDevice } from "../browser.js"
import { getLiveBLEClient } from "../ble/live_ble.js"
import { LiveLANClient, localOrigin } from "../lan/live_lan.js"
import { TelematicsConnection } from "../lan/connection.js"
import { hasFlag, LIVE_FLAGS } from "../ble/live_frames.js"

const flag = (frame, name) => !!frame && hasFlag(frame.flags, LIVE_FLAGS[name])
// API exposure does not prove that Chrome's persistent permissions backend is on.
// Only a successful connection to a device saved before this page loaded does.
const supportsBluetoothRestore = () => typeof navigator.bluetooth?.getDevices === "function"
// watchAdvertisements() is what lets a restored device be found again after a
// reload, and Chrome keeps it behind the experimental features flag.
const supportsAdvertisementWatch = () => typeof globalThis.BluetoothDevice?.prototype?.watchAdvertisements === "function"
const finite = (value) => Number.isFinite(Number(value)) ? Number(value) : null
const signed = (value, digits, suffix = "") => value === null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(digits)}${suffix}`

export const TelematicsTile = {
  name: "TelematicsTile",
  props: { title: String, value: String, tint: { type: String, default: "var(--primary)" }, landscape: Boolean },
  template: `
    <div class="telematics-tile" :class="{ 'telematics-tile--landscape': landscape }" :style="{ '--tile-tint': tint }">
      <span class="telematics-tile__title">{{ title }}</span>
      <strong class="telematics-tile__value">{{ value }}</strong>
    </div>
  `,
}

export const RoadPill = {
  name: "RoadPill",
  props: { icon: String, title: String, value: String, tint: String },
  template: `
    <div class="telematics-road-pill" :style="{ '--pill-tint': tint }">
      <div class="telematics-road-pill__label"><i class="bi" :class="icon"></i><span>{{ title }}</span></div>
      <strong>{{ value }}</strong>
    </div>
  `,
}

// Shared status header for both orientations: set speed on the left, what the
// system is doing in the centre, speed limit on the right. `compact` is portrait.
// The left sign shows cruise speed with current vehicle speed as a badge beneath
// it; the right sign shows the posted limit with its active offset the same way.
export const TelematicsHeader = {
  name: "TelematicsHeader",
  props: {
    compact: Boolean,
    setSpeedText: String,
    currentSpeedBadge: { type: String, default: null },
    hasSpeedLimit: Boolean,
    speedLimitText: String,
    speedLimitOffsetText: { type: String, default: null },
    speedUnit: String,
    status: Object,
  },
  template: `
    <div class="telematics-status-header" :class="{ 'telematics-status-header--compact': compact }">
      <div class="telematics-speed-sign">
        <span class="telematics-speed-sign__title">MAX</span>
        <strong class="telematics-speed-sign__value">{{ setSpeedText }}</strong>
        <span v-if="currentSpeedBadge" class="telematics-speed-sign__badge telematics-speed-sign__badge--accent"
          :aria-label="'Current speed ' + currentSpeedBadge + ' ' + speedUnit">{{ currentSpeedBadge }}</span>
      </div>

      <div class="telematics-hero-status">
        <div class="telematics-hero-status__title-row">
          <i class="bi" :class="status.icon" :style="{ color: status.tint }"></i>
          <strong :style="{ color: status.tint }">{{ status.title }}</strong>
        </div>
        <span v-if="status.detail" class="telematics-hero-status__detail">{{ status.detail }}</span>
      </div>

      <!-- Reserved slot: the sign renders only when a limit is detected, but the
           width is always held so the hero stays centred and nothing shifts. -->
      <div class="telematics-speed-sign" :class="{ 'telematics-speed-sign--empty': !hasSpeedLimit }">
        <template v-if="hasSpeedLimit">
          <span class="telematics-speed-sign__title">LIMIT</span>
          <strong class="telematics-speed-sign__value">{{ speedLimitText }}</strong>
          <span v-if="speedLimitOffsetText" class="telematics-speed-sign__badge telematics-speed-sign__badge--warn"
            :aria-label="'Speed limit offset ' + speedLimitOffsetText + ' ' + speedUnit">{{ speedLimitOffsetText }}</span>
        </template>
      </div>
    </div>
  `,
}

export const Telematics = {
  name: "Telematics",
  components: { TelematicsTile, RoadPill, TelematicsHeader, GxNotice, GalaxyModal },
  data() {
    return {
      isLandscape: false,
      isFullscreen: false,
      capability: "checking",
      bleState: "idle",
      bleMessage: "",
      deviceName: "Galaxy device",
      frame: null,
      health: null,
      metadata: null,
      session: { lateralPercent: null, longitudinalPercent: null, stoppedSeconds: 0 },
      liveUpdatedAt: null,
      healthUpdatedAt: null,
      params: {},
      deviceStatus: null,
      now: Date.now(),
      connecting: false,
      connectionRequest: 0,
      restoredAfterReload: false,
      showBluetoothSetup: false,
      bluetoothSetupSkipped: false,
      bluetoothSetupMode: "gate",
      bluetoothRadio: "unknown",
      rememberedDevices: null,
      connectionMode: "automatic",
      connectionSource: "",
      connection: null,
      localAddress: "",
      showConnectionSetup: false,
      localTestMessage: "",
      testingLocal: false,
      identity: "",
      bluetoothSecure: false,
      bluetoothAvailable: false,
      manuallyDisconnected: false,
    }
  },
  computed: {
    connected() { return this.bleState === "connected" },
    connectionPending() { return this.connecting || ["connecting", "reconnecting"].includes(this.bleState) },
    canConnect() { return this.capability === "ready" && !this.connectionPending },
    connectionSourceLabel() {
      return this.connectionSource === "lan" ? "Local Wi-Fi" : this.connectionSource === "bluetooth" ? "Bluetooth" : "—"
    },
    bluetoothControlsRelevant() { return this.connectionMode === "bluetooth" || this.connectionSource === "bluetooth" || this.showBluetoothSetup },
    localGalaxyURL() {
      try { return `${localOrigin(this.localAddress)}/#/telematics` } catch { return "" }
    },
    secureURL() {
      const target = new URL(window.location.href)
      if (target.protocol === "https:") return target.toString()
      target.protocol = "https:"
      target.port = "8443"
      return target.toString()
    },
    secureHost() {
      try { return new URL(this.secureURL).hostname } catch (error) { return "this device" }
    },
    // True while Bluetooth is the relevant path but this page can't offer it at all
    // (needs the HTTPS page). The dashboard and connect bar stay hidden in favor of
    // just the gate, instead of showing controls a user on this page can't use.
    bluetoothHttpsGate() { return this.bluetoothControlsRelevant && !this.bluetoothSecure },
    canRestoreBluetooth() { return supportsBluetoothRestore() },
    canWatchAdvertisements() { return supportsAdvertisementWatch() },
    bluetoothFlagsReady() { return this.canRestoreBluetooth && this.canWatchAdvertisements },
    bluetoothSetupConfirmLabel() {
      if (this.bluetoothSetupMode === "info") return "Done"
      return this.bluetoothFlagsReady ? "Pair now" : "Connect anyway"
    },
    // Capability probes and observed restoration are separate from flag settings.
    bluetoothChecks() {
      const radio = {
        available: { value: "On", ok: true },
        unavailable: { value: "Off or blocked", ok: false, hint: "Turn on Bluetooth in Android settings, then reopen this page." },
        unknown: { value: "Unknown", hint: "Chrome cannot report the radio state. Tap Connect to check." },
      }[this.bluetoothRadio]
      return [
        { label: "Bluetooth radio", ...radio },
        this.canRestoreBluetooth
          ? { label: "Saved device access", value: "Available", ok: true }
          : { label: "Saved device access", value: "Unavailable", ok: false, hint: "Enable both Chrome settings below." },
        this.canWatchAdvertisements
          ? { label: "Find device after reload", value: "Enabled", ok: true }
          : { label: "Find device after reload", value: "Unavailable", ok: false, hint: "Chrome needs a fresh Bluetooth signal to reconnect. Enable setting 2 below." },
        !this.canRestoreBluetooth
          ? { label: "Remembered device", value: "Setup needed" }
          : this.rememberedDevices > 0
            ? { label: "Remembered device", value: this.rememberedDevices === 1 ? "1 saved" : `${this.rememberedDevices} saved`, ok: true }
            : { label: "Remembered device", value: "None yet", ok: false, hint: "Enable both settings, then pair once with Connect." },
        this.restoredAfterReload
          ? { label: "Reconnect after reload", value: "Verified", ok: true }
          : { label: "Reconnect after reload", value: "Not verified", hint: "After setup and pairing, reload near the comma. Verified means the saved device reconnected." },
      ]
    },
    bluetoothSetupReady() { return this.bluetoothChecks.every((check) => check.ok) },
    // Drives both the dot on the status button and the banner below the connect bar.
    bluetoothNeedsAttention() { return this.bluetoothControlsRelevant && (!this.bluetoothFlagsReady || this.bluetoothRadio === "unavailable") },
    // Null whenever everything is fine, so the banner disappears instead of nagging.
    // A device not yet paired is not a fault, so it never raises one.
    bluetoothBanner() {
      if (!this.bluetoothControlsRelevant) return null
      if (this.bluetoothRadio === "unavailable") {
        return {
          tone: "warn", icon: "bi-bluetooth", title: "Bluetooth is off",
          text: "Turn Bluetooth on in Android settings, then come back to this page.",
          action: "Check status",
        }
      }
      if (!this.bluetoothFlagsReady) {
        return {
          tone: "info", icon: "bi-arrow-repeat", title: "Set up Bluetooth reconnect",
          text: "Enable both Chrome settings before pairing so this page can find your comma again after a reload.",
          action: "Show me how",
        }
      }
      return null
    },
    // Mirrors the banner's place in the notice chain: a pairing or error notice
    // outranks it, and landscape has no room for it at all.
    bluetoothBannerVisible() {
      return !this.isLandscape && !["needs-pairing", "error"].includes(this.bleState) && this.bluetoothBanner !== null
    },
    showPairingSteps() { return !this.connected },
    statusLabel() {
      if (this.bleState === "connected") return "Connected"
      if (this.bleState === "connecting") return "Connecting"
      if (this.bleState === "reconnecting") return "Reconnecting"
      if (this.bleState === "needs-pairing") return "Pairing required"
      return "Not connected"
    },
    deviceStatusLabel() {
      if (!this.deviceStatus) return "—"
      return this.deviceStatus.status || (this.deviceStatus.onroad ? "Driving" : "Parked")
    },
    usesMetric() { return flag(this.frame, "metric") },
    speedUnit() { return this.usesMetric ? "km/h" : "mph" },
    // Badge under the MAX sign. Null (not "—") when there is no frame, so the
    // badge slot disappears instead of showing a placeholder.
    currentSpeedBadge() { return this.frame ? String(Math.round(Math.max(0, this.convertedSpeed(this.frame.vehicleSpeed)))) : null },
    setSpeedText() {
      return flag(this.frame, "cruiseEnabled") && this.frame.setSpeed > 0 ? String(Math.round(this.convertedSpeed(this.frame.setSpeed))) : "—"
    },
    hasSpeedLimit() { return flag(this.frame, "speedLimitActive") && this.frame.speedLimit > 0 },
    speedLimitText() { return this.hasSpeedLimit ? String(Math.round(this.convertedSpeed(this.frame.speedLimit))) : "—" },
    // Badge under the LIMIT sign: the active offset from the posted limit.
    speedLimitOffsetText() {
      if (!this.hasSpeedLimit) return null
      const offset = Math.round(this.convertedSpeed(this.frame.speedLimitOffset))
      return `${offset >= 0 ? "+" : ""}${offset}`
    },
    modeColor() {
      if (!this.frame) return "var(--outline)"
      const color = this.frame.borderColor
      return `rgba(${color.red}, ${color.green}, ${color.blue}, ${color.alpha / 255})`
    },
    driveStateTitle() {
      if (!this.frame) return this.connected ? "Waiting for telemetry" : "Not connected"
      if (flag(this.frame, "conditionalChill") && flag(this.frame, "longitudinalActive")) return "Conditional Chill"
      return ["System off", "Ready", "Engaged", "Steering assist", "Speed control only", "Driver override", "Experimental", "Conditional override", "Switchback", "Traffic mode", "Pulse and glide"][this.frame.borderState] || "Galaxy"
    },
    alertText() {
      if (!flag(this.frame, "alertPresent")) return ""
      return [this.metadata?.alert?.text1, this.metadata?.alert?.text2].filter(Boolean).join(" — ")
    },
    // The dashboard headline: the active mode, with the transient reason for it
    // (a lead, a curve, a stop signal, driver input…) centered underneath. The
    // mode stays prominent while the reason is what's actually changing moment
    // to moment.
    heroStatus() {
      const frame = this.frame
      if (!frame) {
        return this.connected
          ? { title: "Waiting", detail: "Waiting for telemetry", icon: "bi-hourglass-split", tint: "var(--text-muted)" }
          : { title: "Not connected", detail: "Connect to the device over Local Wi-Fi or Bluetooth to populate telematics.", icon: "bi-broadcast-pin", tint: "var(--text-muted)" }
      }
      if (!flag(frame, "started")) return { title: "Vehicle offroad", detail: null, icon: "bi-car-front", tint: "var(--text-muted)" }
      if (!flag(frame, "telemetryValid")) return { title: "Waiting", detail: "Waiting for valid vehicle state", icon: "bi-hourglass-split", tint: "var(--text-muted)" }
      const mode = this.modeStatus(frame)
      return { title: mode.title, detail: this.reasonText(frame), icon: mode.icon, tint: mode.tint }
    },
    leadText() {
      if (!flag(this.frame, "leadPresent")) return "No tracked lead"
      return `${this.distance(this.frame.leadDistance)} · ${Math.round(this.frame.leadProbability * 100)}% confidence`
    },
    curveText() {
      if (!flag(this.frame, "curveControl")) return "Disabled"
      if (!flag(this.frame, "curveControlActive") || this.frame.curveTargetSpeed <= 0) return "No Curve Detected"
      return `${Math.round(this.convertedSpeed(this.frame.curveTargetSpeed))} ${this.speedUnit}`
    },
    stopSignalText() { return this.frame ? (flag(this.frame, "redLight") ? "Detected" : "Clear") : "—" },
    lateralState() {
      if (!this.frame) return "—"
      if (flag(this.frame, "lateralPaused")) return "Paused"
      return flag(this.frame, "lateralActive") ? "Active" : "Inactive"
    },
    torqueText() { return signed(finite(this.frame?.steeringTorque), 1) },
    modelText() { return this.metadata?.model?.name || this.metadata?.model?.key || "—" },
    tempText() { return this.health ? `${Math.round(this.health.maxTempC)}°C` : "NOT SENT" },
    cpuText() { return this.health ? `${this.health.cpuPercent}%` : "NOT SENT" },
    memoryText() { return this.health ? `${this.health.memoryPercent}%` : "NOT SENT" },
    tempTint() { return !this.health ? "var(--text-muted)" : this.health.maxTempC >= 85 ? "var(--error)" : this.health.maxTempC >= 72 ? "var(--warning)" : "var(--on-surface)" },
    cpuTint() { return this.healthTint(this.health?.cpuPercent) },
    memoryTint() { return this.healthTint(this.health?.memoryPercent) },
    stoppedText() {
      const seconds = Math.floor(this.session.stoppedSeconds || 0)
      return `Stopped ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
    },
    freshness() {
      if (!this.connected) return "Not connected"
      if (!this.liveUpdatedAt) return "Waiting"
      const age = Math.max(0, (this.now - this.liveUpdatedAt) / 1000)
      return age < 1 ? "Live" : `${Math.round(age)} s old`
    },
    freshnessTint() { return this.connected && this.liveUpdatedAt && this.now - this.liveUpdatedAt < 2000 ? "var(--success)" : "var(--warning)" },
    roadPills() {
      return [
        { title: "LEAD VEHICLE", icon: "bi-car-front-fill", value: this.leadText, tint: flag(this.frame, "leadPresent") ? "var(--success)" : "var(--text-muted)" },
        { title: "CURVE TARGET", icon: "bi-sign-turn-right-fill", value: this.curveText, tint: flag(this.frame, "curveControlActive") ? "var(--warning)" : "var(--text-muted)" },
        { title: "STOP SIGNAL", icon: "bi-sign-stop-fill", value: this.stopSignalText, tint: flag(this.frame, "redLight") ? "var(--error)" : "var(--success)" },
      ]
    },
    portraitTiles() {
      return [
        ["STEER DELAY", this.param("SteerDelay")], ["LAT ACCEL", this.param("SteerLatAccel")],
        ["STEER RATIO", this.param("SteerRatio")], ["FRICTION", this.param("SteerFriction")],
        ["STEER ANGLE", this.angle(this.frame?.steeringAngle)], ["DESIRED ANGLE", this.angle(this.frame?.desiredSteeringAngle)],
        ["DRIVER TORQUE", this.torqueText], ["LATERAL %", this.percent(this.session.lateralPercent)],
        ["LONGITUDINAL %", this.percent(this.session.longitudinalPercent)], ["ACCEL", this.acceleration(this.frame?.acceleration)],
        ["TARGET ACCEL", this.acceleration(this.frame?.targetAcceleration)], ["LATERAL STATE", this.lateralState],
      ]
    },
  },
  methods: {
    flag(frame, name) { return flag(frame, name) },
    openMenu() { store.drawerOpen = true },
    // heroStatus's mode half: which state names the header, independent of
    // whatever transient reason is currently shown underneath it.
    modeStatus(frame) {
      if (flag(frame, "conditionalChill") && flag(frame, "longitudinalActive")) {
        return { title: "Conditional Chill", icon: "bi-snow", tint: this.modeColor }
      }
      if (flag(frame, "experimentalMode") && flag(frame, "longitudinalActive")) {
        return { title: "Experimental", icon: "bi-stars", tint: "var(--warning)" }
      }
      // Always-On Lateral can report Steering Assist without the fully-engaged
      // flag. Keep its header and icon tied to the panel border like the rest.
      if (frame.borderState === 3) return { title: "Steering assist", icon: "bi-speedometer2", tint: this.modeColor }
      if (flag(frame, "engaged")) return { title: this.driveStateTitle, icon: "bi-speedometer2", tint: this.modeColor }
      return { title: this.driveStateTitle, icon: "bi-stars", tint: "var(--text-muted)" }
    },
    // heroStatus's reason half: the most urgent transient reason wins, in the
    // same priority order regardless of which mode is currently active.
    reasonText(frame) {
      if (flag(frame, "standstill")) return this.stoppedText
      if (flag(frame, "redLight") && flag(frame, "forcingStop")) {
        return flag(frame, "experimentalMode") ? "Red light or stop sign ahead" : "Stop signal detected"
      }
      if (flag(frame, "forcingStop")) return flag(frame, "experimentalMode") ? "Intersection ahead" : "Intersection stop active"
      if (flag(frame, "leadPresent") && flag(frame, "trackingLead") && frame.leadDistance < 18) {
        if (flag(frame, "stopping")) return "Stopping for lead"
        if (frame.leadRelativeSpeed < -0.5 || frame.targetAcceleration < -0.3) return "Slowing for lead"
        return "Following lead"
      }
      if (flag(frame, "curveControlActive") && frame.curveTargetSpeed > 0) return "Slowing for curve"
      if (flag(frame, "conditionalChill") && flag(frame, "longitudinalActive")) {
        return ["Auto", "Vehicle Ahead", "Speed Threshold", "Manual"][frame.conditionalChillReason] || "Auto"
      }
      if (flag(frame, "experimentalMode") && flag(frame, "longitudinalActive")) return "End-to-end longitudinal active"
      if (flag(frame, "gasPressed") || flag(frame, "brakePressed")) return flag(frame, "gasPressed") ? "Accelerator input" : "Brake input"
      if (flag(frame, "lateralPaused")) return "Speed control remains active"
      return flag(frame, "engaged") ? "Assistance engaged" : "Assistance ready"
    },
    convertedSpeed(value) { return value * (this.usesMetric ? 3.6 : 2.23693629) },
    distance(meters) {
      if (this.usesMetric) return meters >= 1000 ? `${(meters / 1000).toFixed(1)} km` : `${Math.round(meters)} m`
      const feet = meters * 3.2808399
      return feet >= 5280 ? `${(feet / 5280).toFixed(1)} mi` : `${Math.round(feet)} ft`
    },
    angle(value) { return signed(finite(value), 1, "°") },
    percent(value) { const number = finite(value); return number === null ? "—" : `${number.toFixed(1)}%` },
    acceleration(value) { return signed(finite(value), 2, " m/s²") },
    param(key) {
      const value = finite(this.params[key])
      if (value === null) return "—"
      return value.toFixed(Math.abs(value) < 1 ? 2 : 1)
    },
    healthTint(value) { return value === undefined || value === null ? "var(--text-muted)" : value >= 85 ? "var(--error)" : value >= 70 ? "var(--warning)" : "var(--on-surface)" },
    syncFullscreen() {
      this.isFullscreen = !!(document.fullscreenElement || document.webkitFullscreenElement)
    },
    async toggleFullscreen() {
      const active = document.fullscreenElement || document.webkitFullscreenElement
      try {
        if (active) {
          const exit = document.exitFullscreen || document.webkitExitFullscreen
          if (exit) await exit.call(document)
        } else {
          const target = document.documentElement
          const enter = target.requestFullscreen || target.webkitRequestFullscreen
          if (!enter) throw new Error("Fullscreen is not supported by this browser")
          await enter.call(target, { navigationUI: "hide" })
        }
      } catch (error) {
        showSnackbar("Unable to enter fullscreen: " + (error?.message || error), "error")
      } finally {
        this.syncFullscreen()
      }
    },
    async connect({ chooseNew = false, forceBluetooth = false } = {}) {
      const request = ++this.connectionRequest
      this.manuallyDisconnected = false
      this.connecting = true
      try {
        if (chooseNew || forceBluetooth || this.connectionMode === "bluetooth") {
          if (!this.ble) {
            this.bleState = "error"
            this.bleMessage = "Bluetooth needs Chrome on an HTTPS page. Local Wi-Fi is still available."
            return
          }
          const firstPair = !this.ble.device && /Android/i.test(navigator.userAgent) && !(this.rememberedDevices > 0)
          if (!this.bluetoothSetupSkipped && (!this.bluetoothFlagsReady || firstPair)) {
            this.bluetoothSetupMode = "gate"
            this.showBluetoothSetup = true
            return
          }
        }
        if (!this.identity) {
          // A chooser must stay in this click's activation; never await HTTP first.
          if (chooseNew) throw new Error("Device identity is loading. Try Pair now again in a moment.")
          await this.loadDeviceStatus()
        }
        if (request !== this.connectionRequest) return
        this.showBluetoothSetup = false
        this.configureConnection()
        await this.connection.connect({ chooseNew })
      } catch (error) {
        if (request === this.connectionRequest) {
          this.bleState = "error"
          this.bleMessage = String(error?.message || error)
        }
      } finally {
        if (request === this.connectionRequest) this.connecting = false
        void this.refreshBluetoothStatus()
      }
    },
    chooseDevice() {
      this.setConnectionMode("bluetooth", false)
      this.disconnect()
      this.bluetoothSetupSkipped = true
      return this.connect({ chooseNew: true })
    },
    configureConnection() {
      this.connection?.configure({ mode: this.connectionMode, address: this.localAddress, identity: this.identity })
    },
    restoreConnectionSettings(identity, status = {}) {
      this.identity = identity
      let saved = {}
      try { saved = JSON.parse(localStorage.getItem(`galaxy-telematics:${identity}`) || "{}") } catch { /* Storage is optional. */ }
      if (["automatic", "lan", "bluetooth"].includes(saved.mode)) this.connectionMode = saved.mode
      try { this.localAddress = localOrigin(window.location.origin) } catch {
        this.localAddress = saved.address || (status.localHostname ? `http://${status.localHostname}:8082` : "")
      }
      this.configureConnection()
    },
    saveConnectionSettings() {
      if (!this.identity) return
      try { localStorage.setItem(`galaxy-telematics:${this.identity}`, JSON.stringify({ mode: this.connectionMode, address: this.localAddress })) } catch { /* Storage is optional. */ }
    },
    setConnectionMode(mode, reconnect = true) {
      if (!["automatic", "lan", "bluetooth"].includes(mode)) return
      const running = this.connection?.running || this.connected || this.connectionPending
      this.disconnect()
      this.connectionMode = mode
      if (mode !== "bluetooth") this.showBluetoothSetup = false
      this.saveConnectionSettings()
      this.configureConnection()
      if (reconnect && running) void this.connect()
    },
    async testLocalConnection() {
      this.testClient?.close()
      const client = new LiveLANClient()
      this.testClient = client
      this.testingLocal = true
      this.localTestMessage = "Checking the comma and live data…"
      try {
        if (!this.identity) await this.loadDeviceStatus()
        if (this.testClient !== client) return
        const address = localOrigin(this.localAddress)
        if (!await client.connect({ address, expectedIdentity: this.identity })) return
        if (this.testClient !== client) return
        this.localAddress = address
        this.saveConnectionSettings()
        this.localTestMessage = "Verified: this comma is sending fresh live data."
        const running = this.connection?.running
        this.configureConnection()
        if (running && (this.connectionMode === "lan" || this.connectionSource === "lan" || !this.connected)) void this.connect()
      } catch (error) {
        if (this.testClient === client) this.localTestMessage = String(error.message || error)
      } finally {
        client.close()
        if (this.testClient === client) { this.testingLocal = false; this.testClient = null }
      }
    },
    cancelLocalTest() {
      if (!this.testClient && !this.testingLocal) return
      this.testClient?.close()
      this.testClient = null
      this.testingLocal = false
      this.localTestMessage = "Test cancelled."
    },
    async refreshBluetoothStatus() {
      try {
        const available = await navigator.bluetooth?.getAvailability?.()
        this.bluetoothRadio = available === undefined ? "unknown" : available ? "available" : "unavailable"
      } catch (error) { this.bluetoothRadio = "unknown" }
      try {
        this.rememberedDevices = supportsBluetoothRestore() ? (await navigator.bluetooth.getDevices()).length : null
      } catch (error) { this.rememberedDevices = null }
    },
    // Reachable any time from the connect bar, so the flag instructions are not a
    // one-shot interstitial the user can never get back to.
    openBluetoothSetup() {
      this.bluetoothSetupMode = "info"
      this.showBluetoothSetup = true
      return this.refreshBluetoothStatus()
    },
    confirmBluetoothSetup() {
      if (this.bluetoothSetupMode === "info") return
      return this.continueBluetoothPairing()
    },
    continueBluetoothPairing() {
      this.showBluetoothSetup = false
      this.bluetoothSetupSkipped = true
      // Keep the device chooser in the button's user activation.
      return this.connect({ chooseNew: true, forceBluetooth: true })
    },
    async copyBluetoothSetting(flag) {
      try {
        await navigator.clipboard.writeText(`chrome://flags/#${flag}`)
        showSnackbar("Copied. Paste into Chrome's address bar.")
      } catch (error) {
        showSnackbar("Unable to copy. Select and copy the address shown below the setting.", "error")
      }
    },
    disconnect() {
      if (this.testClient) this.cancelLocalTest()
      this.manuallyDisconnected = true
      this.connectionRequest += 1
      this.connecting = false
      this.connection?.disconnect()
    },
    async loadParams() {
      try { this.params = (await api.getParams()) || {} } catch (error) { this.params = {} }
    },
    async loadDeviceStatus() {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 3000)
      let value
      try { value = await api.getDeviceStatus({ signal: controller.signal, cache: "no-store" }) }
      finally { clearTimeout(timer) }
      if (this.disposed || !value) return
      this.deviceStatus = value
      const identity = value.telematicsDeviceId
      if (identity && identity !== this.identity) {
        const resume = !this.connecting && (this.connection?.running || (!this.identity && !this.manuallyDisconnected))
        // Do not cancel a user Connect that is waiting for initial identity.
        this.connection?.disconnect()
        this.restoreConnectionSettings(identity, value)
        this.saveConnectionSettings()
        try { localStorage.setItem(`galaxy-telematics-page:${window.location.pathname}`, identity) } catch { /* Storage is optional. */ }
        if (resume) void this.connection?.connect()
      }
    },
    setOrientation(event) { this.isLandscape = event.matches },
  },
  mounted() {
    if (isIOSDevice()) {
      const homeURL = new URL(window.location.href)
      homeURL.hash = "/"
      window.location.replace(homeURL.toString())
      return
    }

    this.orientation = window.matchMedia("(orientation: landscape)")
    this.isLandscape = this.orientation.matches
    this.orientation.addEventListener?.("change", this.setOrientation)
    this.onFullscreenChange = () => this.syncFullscreen()
    document.addEventListener("fullscreenchange", this.onFullscreenChange)
    document.addEventListener("webkitfullscreenchange", this.onFullscreenChange)
    this.syncFullscreen()
    this.clock = setInterval(() => { this.now = Date.now() }, 1000)
    void this.loadParams()
    this.capability = "ready"
    this.bluetoothSecure = window.isSecureContext && window.location.protocol === "https:"
    this.bluetoothAvailable = !!navigator.bluetooth
    if (this.bluetoothSecure && this.bluetoothAvailable) this.ble = getLiveBLEClient()
    this.connection = new TelematicsConnection({
      ble: this.ble,
      callbacks: {
        onState: ({ state, message, deviceName, source, restoredAfterReload }) => {
          this.bleState = state; this.bleMessage = message; this.deviceName = deviceName || "Galaxy device"
          this.connectionSource = source; this.restoredAfterReload = restoredAfterReload
        },
        onLive: (frame, session, updatedAt) => { this.frame = frame; this.session = session; this.liveUpdatedAt = updatedAt },
        onHealth: (frame, updatedAt) => { this.health = frame; this.healthUpdatedAt = updatedAt },
        onMetadata: metadata => { this.metadata = metadata },
      },
    })
    // A cached page can restore its last verified comma even if the tunnel is
    // offline. Live LAN/BLE status must still match this identity. The page's
    // scalar status remains authoritative if it reports a different device.
    try {
      const identity = localStorage.getItem(`galaxy-telematics-page:${window.location.pathname}`)
      if (identity) {
        this.restoreConnectionSettings(identity)
        void this.connection.connect()
      }
    } catch { /* Storage is optional. */ }
    this.devicePoll = usePolling(() => this.loadDeviceStatus(), { interval: 5000 })
    this.devicePoll.start()
    void this.refreshBluetoothStatus()
    this.onResume = () => { if (document.visibilityState !== "hidden") void this.connection?.resume() }
    document.addEventListener("visibilitychange", this.onResume)
    window.addEventListener("pageshow", this.onResume)

  },
  beforeUnmount() {
    this.orientation?.removeEventListener?.("change", this.setOrientation)
    if (this.onFullscreenChange) {
      document.removeEventListener("fullscreenchange", this.onFullscreenChange)
      document.removeEventListener("webkitfullscreenchange", this.onFullscreenChange)
    }
    clearInterval(this.clock)
    this.devicePoll?.destroy()
    this.disposed = true
    this.connectionRequest += 1
    document.removeEventListener("visibilitychange", this.onResume)
    window.removeEventListener("pageshow", this.onResume)
    this.cancelLocalTest()
    this.connection?.close()
  },
  template: `
    <div class="telematics-page" :class="{ 'telematics-page--landscape': isLandscape }">
      <GalaxyModal v-model="showBluetoothSetup" title="Bluetooth status" :confirm-label="bluetoothSetupConfirmLabel" cancel-label="Close" @confirm="confirmBluetoothSetup">
        <div class="telematics-bluetooth-setup">
          <ul class="telematics-checks">
            <li v-for="check in bluetoothChecks" :key="check.label" class="telematics-check"
              :class="check.ok ? 'telematics-check--ok' : check.ok === false ? 'telematics-check--bad' : 'telematics-check--unknown'">
              <i class="bi" :class="check.ok ? 'bi-check-circle-fill' : check.ok === false ? 'bi-x-circle-fill' : 'bi-dash-circle-fill'"></i>
              <div class="telematics-check__body">
                <div class="telematics-check__row">
                <span class="telematics-check__label">{{ check.label }}</span>
                <span class="telematics-check__value">{{ check.value }}</span>
                </div>
                <p v-if="check.hint" class="telematics-check__hint">{{ check.hint }}</p>
              </div>
            </li>
          </ul>

          <p v-if="bluetoothSetupReady" class="telematics-setup-done">
            <i class="bi bi-check-circle-fill"></i> Your saved comma reconnected after a reload.
          </p>

          <details class="telematics-setup__more" :open="!bluetoothFlagsReady || !restoredAfterReload">
            <summary>Chrome settings for reconnect</summary>
            <p>Enable both settings in Chrome. This page checks Bluetooth features, but cannot read or change the flags. Available features alone do not prove the pairing is saved.</p>
            <ol>
              <li>
                <strong>Web Bluetooth new permissions backend</strong>
                <code>chrome://flags/#enable-web-bluetooth-new-permissions-backend</code>
                <button class="gx-btn gx-btn--outlined" type="button" @click="copyBluetoothSetting('enable-web-bluetooth-new-permissions-backend')">Copy address</button>
                <p class="telematics-check__hint">Saves the device so you do not have to pick it after every reload.</p>
              </li>
              <li>
                <strong>Experimental Web Platform features</strong>
                <code>chrome://flags/#enable-experimental-web-platform-features</code>
                <button class="gx-btn gx-btn--outlined" type="button" @click="copyBluetoothSetting('enable-experimental-web-platform-features')">Copy address</button>
                <p class="telematics-check__hint">Finds the saved device after a reload. Without a fresh signal, Chrome may report it as out of range.</p>
              </li>
            </ol>
            <p>Copy each address into Chrome's address bar. Set both to <strong class="telematics-inline">Enabled</strong>, then <strong class="telematics-inline">Relaunch</strong> Chrome and return here. Pair once, then reload near the comma to verify reconnect.</p>
          </details>

          <template v-if="showPairingSteps">
            <p class="telematics-setup__heading">Pairing a phone</p>
            <ol>
              <li>While parked, open <strong class="telematics-inline">Settings &rarr; Bluetooth &rarr; pair a phone</strong> on the comma. New phones can pair only during the <strong class="telematics-inline">discoverable / 120s</strong> countdown.</li>
              <li v-if="bluetoothSetupMode === 'info'">Tap <strong class="telematics-inline">Choose device</strong> below, then pick the comma from Chrome's list.</li>
              <li v-else>Tap <strong class="telematics-inline">{{ bluetoothSetupConfirmLabel }}</strong> at the bottom of this panel, then choose the comma in Chrome.</li>
              <li>Accept Android's pairing prompt if one appears.</li>
            </ol>
            <GxNotice tone="warn" icon="bi-phone-fill" title="Do not pair from Android's Bluetooth settings">
              The comma may appear there during the countdown. Pairing there only creates a system bond, without granting this page access, and can block Chrome pairing. Pair here; use Android settings only to forget the device.
            </GxNotice>
            <p class="telematics-check__hint"><strong class="telematics-inline">No device or pairing failed?</strong> Restart the 120-second window with <strong class="telematics-inline">pair a phone</strong>. If you paired in Android settings, forget the device in both Chrome and Android first.</p>
          </template>

          <details v-if="rememberedDevices > 0" class="telematics-setup__more">
            <summary>Make Chrome forget this device</summary>
            <p><strong class="telematics-inline">Disconnect</strong> ends the connection; it does not forget the device. Use <strong class="telematics-inline">Choose device</strong> to switch commas. To remove the saved pairing:</p>
            <ol>
              <li><strong class="telematics-inline">Chrome:</strong> address-bar icon &rarr; <strong class="telematics-inline">Permissions &rarr; Bluetooth devices</strong> &rarr; remove the comma. Labels vary by Chrome version. <strong class="telematics-inline">Reset permissions</strong> also works; you may need to accept the certificate warning again.</li>
              <li><strong class="telematics-inline">In Android</strong> open <strong class="telematics-inline">Settings &rarr; Connected devices</strong>, tap the gear beside the device, then <strong class="telematics-inline">Forget</strong>.</li>
            </ol>
            <p>Clear both: removing Chrome's permission leaves the Android pairing, which can block pairing again.</p>
          </details>
          <p v-if="!bluetoothFlagsReady" class="telematics-setup__fallback"><strong class="telematics-inline">Reconnect is not ready.</strong> If you cannot enable the settings, you can still connect now. After a reload, you may need to choose the comma again.</p>
          <button v-if="bluetoothSetupMode === 'info' && capability === 'ready'" class="gx-btn gx-btn--outlined" type="button" @click="chooseDevice">Choose device</button>
        </div>
      </GalaxyModal>
      <GalaxyModal v-model="showConnectionSetup" title="Local Wi-Fi connection" confirm-label="Done" cancel-label="Close" @cancel="cancelLocalTest" @confirm="cancelLocalTest">
        <p>Connect the phone and comma to the same Wi-Fi or hotspot. From galaxy.link, allow Chrome’s local network permission when asked.</p>
        <p v-if="identity">Comma: {{ identity }}</p>
        <label for="telematics-local-address">Comma address</label>
        <input id="telematics-local-address" v-model="localAddress" class="gx-field" placeholder="starpilot-device.local:8082" autocapitalize="none" spellcheck="false">
        <p>Use the comma’s .local name when possible; an IP address can change.</p>
        <button v-if="testingLocal" class="gx-btn gx-btn--outlined" @click="cancelLocalTest">Cancel test</button>
        <button v-else class="gx-btn gx-btn--outlined" @click="testLocalConnection">Test and save connection</button>
        <p v-if="localTestMessage" role="status">{{ localTestMessage }}</p>
        <a v-if="localGalaxyURL" class="gx-btn gx-btn--outlined" :href="localGalaxyURL">Open local Galaxy</a>
        <p>If Chrome blocks local access, open local Galaxy above. This page and the local page save their settings separately.</p>
      </GalaxyModal>
      <div v-if="bluetoothHttpsGate" class="telematics-gate">
        <GxNotice tone="warn" icon="bi-shield-lock-fill" title="Bluetooth pairing needs the HTTPS page">
          Web Bluetooth only works on a secure page. Galaxy serves one on port 8443.
        </GxNotice>

        <a class="gx-btn gx-btn--block telematics-gate__open" :href="secureURL" rel="noopener">
          <i class="bi bi-box-arrow-up-right"></i> Open the secure page
        </a>
        <p class="telematics-gate__url"><code>{{ secureURL }}</code></p>

        <ol class="telematics-gate__steps">
          <li>Chrome warns <strong>&ldquo;Your connection is not private&rdquo;</strong>. Expected — keep going.</li>
          <li>Tap <strong>Advanced</strong>, then <strong>Proceed to {{ secureHost }} (unsafe)</strong>.</li>
          <li>Back on this page, tap <strong>Connect</strong> and accept the Android pairing prompt.</li>
        </ol>
        <p class="telematics-gate__once">You do this once per phone.</p>

        <details class="telematics-gate__more">
          <summary>Why does Chrome call it unsafe?</summary>
          <p>Chrome shows <code>NET::ERR_CERT_AUTHORITY_INVALID</code> because the certificate is generated on your device and signed by the device itself, so no public authority vouches for it. Traffic is still encrypted and never leaves your local network.</p>
        </details>
        <details class="telematics-gate__more">
          <summary>Warning keeps coming back?</summary>
          <p>Chrome remembers the exception per address. Reach the device by name — <code>https://starpilot-&lt;device&gt;.local:8443</code> — so a new DHCP lease does not undo it.</p>
        </details>
      </div>
      <div v-else-if="bluetoothControlsRelevant && !bluetoothAvailable" class="telematics-gate">
        <GxNotice tone="info" icon="bi-phone" title="Chrome on Android required">
          This browser does not provide Web Bluetooth. Open this telematics page in Chrome on Android (or another browser with Web Bluetooth support).
        </GxNotice>
      </div>
      <template v-if="capability === 'ready'">
        <div v-if="!isLandscape" class="telematics-connect-bar">
          <span class="telematics-status" :title="deviceName"><i :style="{ background: freshnessTint }"></i>{{ statusLabel }}<small>{{ deviceStatusLabel }} · {{ freshness }} · {{ connectionSourceLabel }}</small></span>
          <button v-if="!bluetoothHttpsGate" class="telematics-setup-button" type="button" aria-label="Local Wi-Fi settings" title="Local Wi-Fi settings" @click="showConnectionSetup = true"><i class="bi bi-wifi"></i></button>
          <select class="gx-field telematics-connection-select" aria-label="Connection method" :value="connectionMode" @change="setConnectionMode($event.target.value)">
            <option value="automatic">Automatic</option><option value="lan">Local Wi-Fi</option><option value="bluetooth">Bluetooth</option>
          </select>
          <template v-if="!bluetoothHttpsGate">
            <button v-if="connectionMode !== 'lan' && !bluetoothBannerVisible" class="telematics-setup-button" type="button" :class="{ 'telematics-setup-button--alert': bluetoothNeedsAttention }"
              :title="bluetoothNeedsAttention ? 'Bluetooth status — needs attention' : 'Bluetooth status'"
              :aria-label="bluetoothNeedsAttention ? 'Bluetooth status, needs attention' : 'Bluetooth status'"
              @click="openBluetoothSetup"><i class="bi bi-lightbulb-fill"></i></button>
            <button v-if="connected" class="gx-btn gx-btn--outlined" type="button" @click="disconnect">Disconnect</button>
            <button v-else-if="connectionPending" class="gx-btn gx-btn--outlined" type="button" @click="disconnect">Cancel</button>
            <button v-else class="gx-btn" type="button" :disabled="!canConnect" @click="connect()"><i class="bi" :class="connectionMode === 'bluetooth' ? 'bi-bluetooth' : 'bi-wifi'"></i> {{ bleState === 'error' || bleState === 'needs-pairing' ? 'Reconnect' : 'Connect' }}</button>
          </template>
        </div>
        <template v-if="!bluetoothHttpsGate">
        <GxNotice v-if="bleState === 'needs-pairing'" class="telematics-pairing" tone="warn" icon="bi-bluetooth" title="Pair the device first" :text="bleMessage" />
        <GxNotice v-else-if="bleState === 'error'" class="telematics-pairing" tone="danger" icon="bi-exclamation-circle-fill" title="Connection error" :text="bleMessage" />
        <GxNotice v-else-if="bluetoothBannerVisible" class="telematics-pairing" :tone="bluetoothBanner.tone"
          :icon="bluetoothBanner.icon" :title="bluetoothBanner.title">
          {{ bluetoothBanner.text }}
          <button class="telematics-setup-link" type="button" @click="openBluetoothSetup">{{ bluetoothBanner.action }}</button>
        </GxNotice>

        <div v-if="isLandscape" class="telematics-landscape">
          <aside class="telematics-side telematics-side--left">
            <button class="telematics-menu-button" type="button" aria-label="Open menu" @click="openMenu"><i class="bi bi-list"></i></button>
            <TelematicsTile landscape title="TEMP" :value="tempText" :tint="tempTint" />
            <TelematicsTile landscape title="CPU" :value="cpuText" :tint="cpuTint" />
            <TelematicsTile landscape title="MEMORY" :value="memoryText" :tint="memoryTint" />
            <TelematicsTile landscape title="MODEL" :value="modelText.toUpperCase()" :tint="flag(frame, 'bigModel') ? 'var(--primary)' : 'var(--text-muted)'" />
            <TelematicsTile landscape title="LATERAL" :value="lateralState.toUpperCase()" :tint="flag(frame, 'lateralActive') ? 'var(--success)' : 'var(--text-muted)'" />
            <div class="telematics-logo"><img src="/assets/images/main_logo.png" alt="Galaxy" /></div>
          </aside>

          <section class="telematics-center" :style="{ '--mode-color': modeColor }">
            <div class="telematics-center__connection">
              <span :title="deviceName">{{ statusLabel }} · {{ connectionSourceLabel }}<small>{{ deviceStatusLabel }} · {{ freshness }}</small></span>
              <button class="telematics-setup-button" type="button" aria-label="Local Wi-Fi settings" title="Local Wi-Fi settings" @click="showConnectionSetup = true"><i class="bi bi-wifi"></i></button>
              <select class="gx-field telematics-connection-select" aria-label="Connection method" :value="connectionMode" @change="setConnectionMode($event.target.value)">
                <option value="automatic">Automatic</option><option value="lan">Local Wi-Fi</option><option value="bluetooth">Bluetooth</option>
              </select>
              <button v-if="connectionMode !== 'lan'" class="telematics-fullscreen-button telematics-setup-button--inline" type="button"
                :class="{ 'telematics-setup-button--alert': bluetoothNeedsAttention }"
                :title="bluetoothNeedsAttention ? 'Bluetooth status — needs attention' : 'Bluetooth status'"
                :aria-label="bluetoothNeedsAttention ? 'Bluetooth status, needs attention' : 'Bluetooth status'"
                @click="openBluetoothSetup"><i class="bi bi-lightbulb-fill"></i></button>
              <button class="telematics-fullscreen-button" type="button" :aria-label="isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'"
                :title="isFullscreen ? 'Exit fullscreen' : 'Hide browser controls'" :aria-pressed="isFullscreen" @click="toggleFullscreen">
                <i class="bi" :class="isFullscreen ? 'bi-fullscreen-exit' : 'bi-arrows-fullscreen'"></i>
              </button>
              <button v-if="connected" type="button" @click="disconnect">Disconnect</button>
              <button v-else-if="connectionPending" type="button" @click="disconnect">Cancel</button>
              <button v-else type="button" :disabled="!canConnect" @click="connect()">{{ bleState === 'error' || bleState === 'needs-pairing' ? 'Reconnect' : 'Connect' }}</button>
            </div>
            <TelematicsHeader
              :set-speed-text="setSpeedText" :current-speed-badge="currentSpeedBadge"
              :has-speed-limit="hasSpeedLimit" :speed-limit-text="speedLimitText" :speed-limit-offset-text="speedLimitOffsetText"
              :speed-unit="speedUnit" :status="heroStatus" />
            <div class="telematics-alert-slot"><div v-if="alertText" class="telematics-alert"><i class="bi bi-exclamation-triangle-fill"></i><span>{{ alertText }}</span></div></div>
            <div class="telematics-road-list"><RoadPill v-for="pill in roadPills" :key="pill.title" v-bind="pill" /></div>
          </section>

          <aside class="telematics-side telematics-side--right">
            <TelematicsTile landscape title="STEER DELAY" :value="param('SteerDelay')" />
            <TelematicsTile landscape title="LAT ACCEL" :value="param('SteerLatAccel')" />
            <TelematicsTile landscape title="STEER RATIO" :value="param('SteerRatio')" />
            <TelematicsTile landscape title="STEER ANGLE" :value="angle(frame?.steeringAngle)" tint="var(--success)" />
            <TelematicsTile landscape title="DRIVER TORQUE" :value="torqueText" tint="var(--success)" />
            <TelematicsTile landscape title="FRICTION" :value="param('SteerFriction')" />
            <TelematicsTile landscape title="LATERAL %" :value="percent(session.lateralPercent)" tint="var(--success)" />
          </aside>
        </div>

        <div v-else class="telematics-portrait">
          <section class="telematics-instrument" :style="{ '--mode-color': modeColor }">
            <TelematicsHeader compact
              :set-speed-text="setSpeedText" :current-speed-badge="currentSpeedBadge"
              :has-speed-limit="hasSpeedLimit" :speed-limit-text="speedLimitText" :speed-limit-offset-text="speedLimitOffsetText"
              :speed-unit="speedUnit" :status="heroStatus" />
            <div v-if="alertText" class="telematics-alert"><i class="bi bi-exclamation-triangle-fill"></i><span>{{ alertText }}</span></div>
            <div class="telematics-road-list"><RoadPill v-for="pill in roadPills" :key="pill.title" v-bind="pill" /></div>
          </section>
          <section class="telematics-steering">
            <h2><i class="bi bi-speedometer2"></i> Steering and control</h2>
            <div class="telematics-tile-grid"><TelematicsTile v-for="tile in portraitTiles" :key="tile[0]" :title="tile[0]" :value="tile[1]" /></div>
          </section>
        </div>
        </template>
      </template>
    </div>
  `,
}
