import { api, showSnackbar } from "../api.js"
import { usePolling } from "../composables.js"
import { GxNotice } from "../components/GxNotice.js"
import { GalaxyModal } from "../components/GalaxyModal.js"
import { BluetoothSupportNotice } from "../components/BluetoothSupportNotice.js"
import { store, navigate } from "../store.js"
import { isIOSDevice, bluetoothPlatform, isGalaxyLink, galaxyRoute, galaxyAppBase } from "../browser.js"
import { getLiveBLEClient } from "../ble/live_ble.js"
import { offlineState, prepareOffline } from "../offline.js"
import { LiveLANClient, localOrigin } from "../lan/live_lan.js"
import { TelematicsConnection } from "../lan/connection.js"
import { hasFlag, LIVE_FLAGS } from "../ble/live_frames.js"

const flag = (frame, name) => !!frame && hasFlag(frame.flags, LIVE_FLAGS[name])
const supportsBluetoothRestore = () => typeof navigator.bluetooth?.getDevices === "function"
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

// Shared connection controls for both orientations: status with its connect or
// disconnect action on the first row, local settings and full screen below.
export const TelematicsConnectBar = {
  name: "TelematicsConnectBar",
  props: {
    statusLabel: String,
    statusDetail: String,
    statusTint: String,
    deviceName: String,
    connected: Boolean,
    pending: Boolean,
    canConnect: Boolean,
    retry: Boolean,
    connectionMode: String,
    isFullscreen: Boolean,
    actionHidden: Boolean,
  },
  emits: ["connect", "disconnect", "mode", "settings", "fullscreen"],
  template: `
    <div class="telematics-connect-bar">
      <div class="telematics-connect-bar__row">
        <span class="telematics-status" :title="deviceName"><i :style="{ background: statusTint }"></i>{{ statusLabel }}<small>{{ statusDetail }}</small></span>
        <template v-if="!actionHidden">
          <button v-if="connected" class="gx-btn gx-btn--outlined" type="button" @click="$emit('disconnect')">Disconnect</button>
          <button v-else-if="pending" class="gx-btn gx-btn--outlined" type="button" @click="$emit('disconnect')">Cancel</button>
          <button v-else class="gx-btn" type="button" :disabled="!canConnect" @click="$emit('connect')"><i class="bi" :class="connectionMode === 'bluetooth' ? 'bi-bluetooth' : 'bi-wifi'"></i> {{ retry ? 'Reconnect' : 'Connect' }}</button>
        </template>
      </div>
      <div class="telematics-connect-bar__row">
        <button v-if="connectionMode === 'lan'" class="gx-btn gx-btn--outlined" type="button" aria-label="Local Wi-Fi settings" title="Local Wi-Fi settings" @click="$emit('settings')"><i class="bi bi-gear"></i></button>
        <button class="gx-btn gx-btn--outlined" type="button" :aria-pressed="isFullscreen" @click="$emit('fullscreen')">{{ isFullscreen ? 'Exit full screen' : 'Full screen' }}</button>
      </div>
    </div>
  `,
}

export const Telematics = {
  name: "Telematics",
  components: { TelematicsTile, RoadPill, TelematicsHeader, TelematicsConnectBar, GxNotice, GalaxyModal, BluetoothSupportNotice },
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
      rememberedDevices: null,
      // The page origin owns the transport; saved preferences cannot override it.
      connectionMode: isGalaxyLink() ? "bluetooth" : "lan",
      onGalaxyLink: isGalaxyLink(),
      galaxyURL: "",
      galaxyLinkError: false,
      galaxyLinkLoading: true,
      offlineState,
      connectionSource: "",
      connection: null,
      localAddress: "",
      showConnectionSetup: false,
      localTestMessage: "",
      testingLocal: false,
      identity: "",
      bluetoothSecure: false,
      bluetoothSupport: "",
      manuallyDisconnected: false,
    }
  },
  computed: {
    offlinePageURL() {
      return new URL("assets/mobile/telematics.html#/telematics", galaxyAppBase()).href
    },
    logoURL() { return new URL("assets/images/main_logo.png", galaxyAppBase()).href },
    standalonePage() { return window.location.pathname.endsWith("/assets/mobile/telematics.html") },
    connected() { return this.bleState === "connected" },
    connectionPending() { return this.connecting || ["connecting", "reconnecting"].includes(this.bleState) },
    // Matches the connect bar: connecting, retrying, or a live link awaiting its first frame.
    syncing() { return this.connectionPending || (this.connected && !this.frame) },
    canConnect() { return this.capability === "ready" && !this.connectionPending && !!this.connectionMode },
    connectionSourceLabel() {
      return this.connectionSource === "lan" ? "Local Wi-Fi" : this.connectionSource === "bluetooth" ? "Bluetooth" : "—"
    },
    localGalaxyURL() {
      try { return `${localOrigin(this.localAddress)}/#/telematics` } catch { return "" }
    },
    secureURL() { return this.onGalaxyLink ? window.location.href : this.galaxyURL },
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
    // One binding for both orientations keeps their connection bars identical.
    connectBar() {
      return {
        statusLabel: this.statusLabel,
        statusDetail: `${this.deviceStatusLabel} · ${this.freshness} · ${this.connectionSourceLabel}`,
        statusTint: this.freshnessTint,
        deviceName: this.deviceName,
        connected: this.connected,
        pending: this.connectionPending,
        canConnect: this.canConnect,
        retry: this.bleState === "error" || this.bleState === "needs-pairing",
        connectionMode: this.connectionMode,
        isFullscreen: this.isFullscreen,
        actionHidden: this.bluetoothUnavailable,
      }
    },
    // Unsupported browsers on the Galaxy link show setup guidance instead of
    // a dashboard or connect action they cannot use.
    bluetoothUnavailable() { return this.connectionMode === "bluetooth" && this.bluetoothSupport !== "ready" },
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
        if (this.syncing) {
          const via = this.connectionMode === "bluetooth" ? "Bluetooth" : "Local Wi-Fi"
          const detail = this.connected ? "Waiting for live data" : this.bleState === "reconnecting" ? `Reconnecting over ${via}` : `Connecting over ${via}`
          return { title: "Syncing", detail, icon: "bi-arrow-repeat", tint: "var(--text-muted)" }
        }
        return { title: "No connection", detail: this.onGalaxyLink ? "Connect to your nearby comma over Bluetooth for live readings." : "Connect to your comma on the same Wi-Fi or hotspot for live readings.", icon: "bi-broadcast-pin", tint: "var(--text-muted)" }
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
    async connect() {
      const request = ++this.connectionRequest
      this.manuallyDisconnected = false
      this.connecting = true
      try {
        if (this.connectionMode === "bluetooth" && !this.ble) {
          this.bleState = "error"
          this.bleMessage = "Open your Galaxy link in Chrome on Android to use Bluetooth."
          return
        }
        if (!this.identity) await this.loadDeviceStatus()
        if (request !== this.connectionRequest) return
        this.configureConnection()
        await this.connection.connect()
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
    // Resume an already permitted device without opening a chooser.
    defaultToBluetooth() {
      if (!this.onGalaxyLink || !(this.rememberedDevices > 0)) return
      this.connectionMode = "bluetooth"
      this.configureConnection()
      if (this.identity && !this.manuallyDisconnected && !this.connection?.running) void this.connection?.connect()
    },
    saveOfflineIdentity() {
      try {
        const target = new URL(this.offlinePageURL)
        const identity = this.identity || localStorage.getItem(`galaxy-telematics-page:${window.location.pathname}`)
        if (identity) localStorage.setItem(`galaxy-telematics-page:${target.pathname}`, identity)
      } catch { /* The standalone page can also fetch identity while online. */ }
    },
    pairPhone() { navigate("/bluetooth/phone") },
    async loadGalaxyLink() {
      this.galaxyLinkError = false
      this.galaxyLinkLoading = true
      try {
        const status = await api.getGalaxyStatus()
        this.galaxyURL = status?.paired ? galaxyRoute(status.url) : ""
      } catch { this.galaxyLinkError = true }
      finally { this.galaxyLinkLoading = false }
    },
    setupGalaxy() { navigate("/galaxy") },
    retryOffline() { void prepareOffline(true) },
    configureConnection() {
      this.connection?.configure({ mode: this.connectionMode, address: this.localAddress, identity: this.identity })
    },
    restoreConnectionSettings(identity, status = {}) {
      this.identity = identity
      let saved = {}
      try { saved = JSON.parse(localStorage.getItem(`galaxy-telematics:${identity}`) || "{}") } catch { /* Storage is optional. */ }
      this.connectionMode = this.onGalaxyLink ? "bluetooth" : "lan"
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
      if (mode !== (this.onGalaxyLink ? "bluetooth" : "lan") || mode === this.connectionMode) return
      const running = this.connection?.running || this.connected || this.connectionPending
      this.disconnect()
      this.connectionMode = mode
      this.saveConnectionSettings()
      this.configureConnection()
      // Resume only the transport allowed by this page origin.
      if (reconnect && (running || mode === "lan")) void this.connect()
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
        this.rememberedDevices = supportsBluetoothRestore() ?(await navigator.bluetooth.getDevices()).length : null
      } catch (error) { this.rememberedDevices = null }
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
        this.saveOfflineIdentity()
        try { localStorage.setItem(`galaxy-telematics-page:${window.location.pathname}`, identity) } catch { /* Storage is optional. */ }
        if (resume && this.connectionMode) void this.connection?.connect()
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

    if (this.onGalaxyLink) void prepareOffline()
    else void this.loadGalaxyLink()

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
    this.bluetoothSupport = bluetoothPlatform()
    if (this.onGalaxyLink && this.bluetoothSecure && navigator.bluetooth) this.ble = getLiveBLEClient()
    this.connection = new TelematicsConnection({
      ble: this.ble,
      callbacks: {
        onState: ({ state, message, deviceName, source }) => {
          this.bleState = state; this.bleMessage = message; this.deviceName = deviceName || "Galaxy device"
          this.connectionSource = source
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
        if (this.connectionMode) void this.connection.connect()
      }
    } catch { /* Storage is optional. */ }
    this.devicePoll = usePolling(() => this.loadDeviceStatus(), { interval: 5000 })
    this.devicePoll.start()
    void this.refreshBluetoothStatus().then(() => this.defaultToBluetooth())
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
      <GxNotice v-if="!onGalaxyLink && (!isLandscape || !connected)" class="telematics-pairing" tone="info" icon="bi-wifi" title="Wi-Fi Telematics">
        Connect your phone and comma to the same Wi-Fi or hotspot. To use Bluetooth and save Telematics on this phone, open your Galaxy link.
        <a v-if="galaxyURL" class="gx-btn gx-btn--outlined" :href="galaxyURL">Use Bluetooth in Galaxy</a>
        <button v-else-if="galaxyLinkLoading" class="gx-btn gx-btn--outlined" disabled>Checking Galaxy link…</button>
        <button v-else-if="galaxyLinkError" class="gx-btn gx-btn--outlined" @click="loadGalaxyLink">Retry Galaxy link</button>
        <button v-else class="gx-btn gx-btn--outlined" @click="setupGalaxy">Set up Galaxy remote access</button>
      </GxNotice>
      <GxNotice v-else-if="onGalaxyLink && (!isLandscape || !connected)" class="telematics-pairing" tone="info" icon="bi-bluetooth" title="Bluetooth Telematics">
        Set up once while online: pair this phone under Tools → Bluetooth → Phone, then connect near your comma. Install Galaxy from Galaxy &amp; App Install to reopen it from your home screen.
        <p role="status">{{ offlineState.message }}</p>
        <button class="gx-btn gx-btn--outlined" @click="pairPhone">Phone setup</button>
        <button class="gx-btn gx-btn--outlined" @click="setupGalaxy">Galaxy &amp; App Install</button>
        <button v-if="offlineState.state === 'error'" class="gx-btn gx-btn--outlined" @click="retryOffline">Retry saving</button>
      </GxNotice>
      <GalaxyModal v-if="!onGalaxyLink" v-model="showConnectionSetup" title="Local Wi-Fi connection" confirm-label="Done" cancel-label="Close" @cancel="cancelLocalTest" @confirm="cancelLocalTest">
        <p>Connect the phone and comma to the same Wi-Fi or hotspot.</p>
        <p v-if="identity">Comma: {{ identity }}</p>
        <label for="telematics-local-address">Comma address</label>
        <input id="telematics-local-address" v-model="localAddress" class="gx-field" placeholder="starpilot-device.local:8082" autocapitalize="none" spellcheck="false">
        <p>Use the comma’s .local name when possible; an IP address can change.</p>
        <button v-if="testingLocal" class="gx-btn gx-btn--outlined" @click="cancelLocalTest">Cancel test</button>
        <button v-else class="gx-btn gx-btn--outlined" @click="testLocalConnection">Test and save connection</button>
        <p v-if="localTestMessage" role="status">{{ localTestMessage }}</p>
        <a v-if="localGalaxyURL" class="gx-btn gx-btn--outlined" :href="localGalaxyURL">Open local Galaxy</a>
      </GalaxyModal>
      <template v-if="capability === 'ready'">
        <TelematicsConnectBar v-if="!isLandscape || bluetoothUnavailable" v-bind="connectBar" @connect="connect()" @disconnect="disconnect" @mode="setConnectionMode" @settings="showConnectionSetup = true" @fullscreen="toggleFullscreen" />
        <BluetoothSupportNotice v-if="bluetoothUnavailable && bluetoothSupport !== 'insecure'" class="telematics-pairing" :platform="bluetoothSupport" :secure-url="secureURL" pair-elsewhere />
        <GxNotice v-else-if="bluetoothUnavailable" class="telematics-pairing" tone="warn" icon="bi-shield-lock-fill" title="Open your Galaxy link in Chrome">
          Reload your Galaxy link in Chrome on Android to enable Bluetooth.
        </GxNotice>
        <GxNotice v-else-if="bleState === 'needs-pairing'" class="telematics-pairing" tone="warn" icon="bi-bluetooth" title="Pair the device first">
          {{ bleMessage }}
          <button class="telematics-setup-link" type="button" @click="pairPhone">Pair a phone</button>
        </GxNotice>
        <GxNotice v-else-if="bleState === 'error'" class="telematics-pairing" tone="danger" icon="bi-exclamation-circle-fill" title="Connection error">
          {{ bleMessage }}
          <button v-if="connectionMode === 'bluetooth'" class="telematics-setup-link" type="button" @click="pairPhone">Pair a phone</button>
        </GxNotice>

        <div v-if="isLandscape && !bluetoothUnavailable" class="telematics-landscape">
          <aside class="telematics-side telematics-side--left">
            <button class="telematics-menu-button" type="button" aria-label="Open menu" @click="openMenu"><i class="bi bi-list"></i></button>
            <TelematicsTile landscape title="TEMP" :value="tempText" :tint="tempTint" />
            <TelematicsTile landscape title="CPU" :value="cpuText" :tint="cpuTint" />
            <TelematicsTile landscape title="MEMORY" :value="memoryText" :tint="memoryTint" />
            <TelematicsTile landscape title="MODEL" :value="modelText.toUpperCase()" :tint="flag(frame, 'bigModel') ? 'var(--primary)' : 'var(--text-muted)'" />
            <TelematicsTile landscape title="LATERAL" :value="lateralState.toUpperCase()" :tint="flag(frame, 'lateralActive') ? 'var(--success)' : 'var(--text-muted)'" />
            <div class="telematics-logo"><img :src="logoURL" alt="Galaxy" /></div>
          </aside>

          <section class="telematics-center" :style="{ '--mode-color': modeColor }">
            <TelematicsConnectBar v-bind="connectBar" @connect="connect()" @disconnect="disconnect" @mode="setConnectionMode" @settings="showConnectionSetup = true" @fullscreen="toggleFullscreen" />
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

        <div v-else-if="!bluetoothUnavailable" class="telematics-portrait">
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
    </div>
  `,
}
