import { api, showSnackbar } from "../api.js"
import { usePolling } from "../composables.js"
import { GxNotice } from "../components/GxNotice.js"
import { GalaxyModal } from "../components/GalaxyModal.js"
import { store } from "../store.js"
import { isIOSDevice } from "../browser.js"
import { getLiveBLEClient } from "../ble/live_ble.js"
import { hasFlag, LIVE_FLAGS } from "../ble/live_frames.js"

const flag = (frame, name) => !!frame && hasFlag(frame.flags, LIVE_FLAGS[name])
// getDevices() only exists once the new Web Bluetooth permissions backend flag is on,
// so its absence is the signal that a page reload will drop the pairing.
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

export const Telematics = {
  name: "Telematics",
  components: { TelematicsTile, RoadPill, GxNotice, GalaxyModal },
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
      showBluetoothSetup: false,
      bluetoothSetupSkipped: false,
      bluetoothSetupMode: "gate",
    }
  },
  computed: {
    connected() { return this.bleState === "connected" },
    canConnect() { return this.capability === "ready" && !["connecting", "reconnecting"].includes(this.bleState) },
    secureURL() {
      const target = new URL(window.location.href)
      target.protocol = "https:"
      target.port = "8443"
      return target.toString()
    },
    secureHost() {
      try { return new URL(this.secureURL).hostname } catch (error) { return "this device" }
    },
    canRestoreBluetooth() { return supportsBluetoothRestore() },
    bluetoothSetupConfirmLabel() { return this.bluetoothSetupMode === "info" ? "Done" : "Continue pairing" },
    statusLabel() {
      if (this.bleState === "connected") return this.deviceName
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
    currentSpeedText() { return this.frame ? String(Math.round(Math.max(0, this.convertedSpeed(this.frame.vehicleSpeed)))) : "—" },
    setSpeedText() {
      return flag(this.frame, "cruiseEnabled") && this.frame.setSpeed > 0 ? String(Math.round(this.convertedSpeed(this.frame.setSpeed))) : "—"
    },
    hasSpeedLimit() { return flag(this.frame, "speedLimitActive") && this.frame.speedLimit > 0 },
    speedLimitText() { return this.hasSpeedLimit ? String(Math.round(this.convertedSpeed(this.frame.speedLimit))) : "—" },
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
    driveStateDetail() {
      const frame = this.frame
      if (!frame) return "Connect to the device over Bluetooth to populate telematics."
      if (!flag(frame, "started")) return "Vehicle offroad"
      if (!flag(frame, "telemetryValid")) return "Waiting for valid vehicle state"
      if (flag(frame, "conditionalChill") && flag(frame, "longitudinalActive")) return ["Auto", "Vehicle Ahead", "Speed Threshold", "Manual"][frame.conditionalChillReason] || "Auto"
      if (flag(frame, "redLight") && flag(frame, "forcingStop")) return "Stopping for a detected stop signal"
      if (flag(frame, "trackingLead")) return "Following the tracked vehicle ahead"
      if (flag(frame, "lateralPaused")) return "Steering paused; speed control remains active"
      if (flag(frame, "gasPressed")) return "Accelerator input is overriding control"
      if (flag(frame, "brakePressed")) return "Brake input is overriding control"
      return flag(frame, "engaged") ? "Assistance is engaged" : "Assistance is ready"
    },
    alertText() {
      if (!flag(this.frame, "alertPresent")) return ""
      return [this.metadata?.alert?.text1, this.metadata?.alert?.text2].filter(Boolean).join(" — ")
    },
    experimentalInfo() {
      const frame = this.frame
      if (!this.connected || !frame || flag(frame, "standstill")) return null
      const experimental = flag(frame, "experimentalMode")
      if (flag(frame, "redLight") && flag(frame, "forcingStop")) return { text: experimental ? "Stopping at red light / stop sign" : "Stop signal detected · slowing down", icon: "bi-sign-stop-fill", tint: "var(--error)" }
      if (flag(frame, "forcingStop")) return { text: experimental ? "Stopping for intersection" : "Intersection stop active", icon: "bi-hand-index-thumb-fill", tint: "var(--warning)" }
      if (flag(frame, "leadPresent") && flag(frame, "trackingLead") && frame.leadDistance < 18) {
        const verb = flag(frame, "stopping") ? "Stopping for" : (frame.leadRelativeSpeed < -0.5 || frame.targetAcceleration < -0.3) ? "Slowing for" : "Following"
        return { text: `${verb} lead vehicle (${this.distance(frame.leadDistance)})`, icon: "bi-car-front-fill", tint: "var(--warning)" }
      }
      if (flag(frame, "curveControlActive") && frame.curveTargetSpeed > 0) return { text: "Slowing for curve", icon: "bi-sign-turn-right-fill", tint: "var(--warning)" }
      if (experimental && flag(frame, "longitudinalActive")) return { text: "End-to-end longitudinal active", icon: "bi-stars", tint: "var(--warning)" }
      if (flag(frame, "conditionalChill") && flag(frame, "longitudinalActive")) return { text: ["Auto", "Vehicle Ahead", "Speed Threshold", "Manual"][frame.conditionalChillReason] || "Auto", icon: "bi-snow", tint: "var(--primary)" }
      if (flag(frame, "engaged")) return { text: "Assistance engaged", icon: "bi-speedometer2", tint: "var(--success)" }
      return { text: "Assistance ready", icon: "bi-stars", tint: "var(--text-muted)" }
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
    stoppedMinutes() { return Math.floor((this.session.stoppedSeconds || 0) / 60) },
    stoppedSecondsPart() { return Math.floor(this.session.stoppedSeconds || 0) % 60 },
    freshness() {
      if (!this.connected) return "Not connected"
      if (!this.liveUpdatedAt) return "Waiting"
      const age = Math.max(0, (this.now - this.liveUpdatedAt) / 1000)
      return age < 1 ? "Live" : `${Math.round(age)} s old`
    },
    freshnessTint() { return this.connected && this.liveUpdatedAt && this.now - this.liveUpdatedAt < 1500 ? "var(--success)" : "var(--warning)" },
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
      if (!this.bluetoothSetupSkipped && !this.ble.device && /Android/i.test(navigator.userAgent) && !supportsBluetoothRestore()) {
        this.bluetoothSetupMode = "gate"
        this.showBluetoothSetup = true
        return
      }
      this.connecting = true
      try {
        if (["error", "needs-pairing"].includes(this.bleState)) await this.ble.reconnect()
        else await this.ble.connect()
      } catch (error) { /* State includes the actionable error. */ }
      finally { this.connecting = false }
    },
    // Reachable any time from the connect bar, so the flag instructions are not a
    // one-shot interstitial the user can never get back to.
    openBluetoothSetup() {
      this.bluetoothSetupMode = "info"
      this.showBluetoothSetup = true
    },
    confirmBluetoothSetup() {
      if (this.bluetoothSetupMode === "info") return
      return this.continueBluetoothPairing()
    },
    continueBluetoothPairing() {
      this.showBluetoothSetup = false
      this.bluetoothSetupSkipped = true
      // Keep the device chooser in the button's user activation.
      return this.connect()
    },
    async copyBluetoothSetting(flag) {
      try {
        await navigator.clipboard.writeText(`chrome://flags/#${flag}`)
        showSnackbar("Copied. Paste into Chrome's address bar.")
      } catch (error) {
        showSnackbar("Unable to copy. Select and copy the address shown below the setting.", "error")
      }
    },
    disconnect() { this.ble?.disconnect() },
    async loadParams() {
      try { this.params = (await api.getParams()) || {} } catch (error) { this.params = {} }
    },
    async loadDeviceStatus() {
      const value = await api.getDeviceStatus()
      if (value) this.deviceStatus = value
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

    // Deliberately no automatic redirect to :8443. The jump is invisible and lands the
    // user on a certificate interstitial with no idea why, so explain it and let them tap.
    if (window.location.protocol !== "https:") {
      this.capability = "insecure"
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
    this.devicePoll = usePolling(() => this.loadDeviceStatus(), { interval: 5000 })
    this.devicePoll.start()

    if (!window.isSecureContext) this.capability = "insecure"
    else if (!navigator.bluetooth) this.capability = "unsupported"
    else {
      this.capability = "ready"
      this.ble = getLiveBLEClient()
      this.ble.setCallbacks({
        onState: ({ state, message, deviceName }) => { this.bleState = state; this.bleMessage = message; this.deviceName = deviceName },
        onLive: (frame, session, updatedAt) => { this.frame = frame; this.session = session; this.liveUpdatedAt = updatedAt },
        onHealth: (frame, updatedAt) => { this.health = frame; this.healthUpdatedAt = updatedAt },
        onMetadata: (metadata) => { this.metadata = metadata },
      })
      this.ble.emitCurrent()
      if (!this.ble.manualDisconnect && !this.ble.isActive()) void this.ble.reconnectRemembered()
    }
  },
  beforeUnmount() {
    this.orientation?.removeEventListener?.("change", this.setOrientation)
    if (this.onFullscreenChange) {
      document.removeEventListener("fullscreenchange", this.onFullscreenChange)
      document.removeEventListener("webkitfullscreenchange", this.onFullscreenChange)
    }
    clearInterval(this.clock)
    this.devicePoll?.destroy()
    this.ble?.detach()
  },
  template: `
    <div class="telematics-page" :class="{ 'telematics-page--landscape': isLandscape }">
      <GalaxyModal v-model="showBluetoothSetup" title="Reconnect after page reload" :confirm-label="bluetoothSetupConfirmLabel" cancel-label="Close" @confirm="confirmBluetoothSetup">
        <div class="telematics-bluetooth-setup">
          <p>This browser cannot restore Bluetooth access after a page reload. You can still pair normally and tap Connect again after reloading.</p>
          <p>To try automatic reconnect in Chrome on Android, enable these experimental settings. This page cannot open or change Chrome settings.</p>
          <ol>
            <li>
              <strong>Experimental Web Platform features</strong>
              <code>chrome://flags/#enable-experimental-web-platform-features</code>
              <button class="gx-btn gx-btn--outlined" type="button" @click="copyBluetoothSetting('enable-experimental-web-platform-features')">Copy address</button>
            </li>
            <li>
              <strong>Web Bluetooth new permissions backend</strong>
              <code>chrome://flags/#enable-web-bluetooth-new-permissions-backend</code>
              <button class="gx-btn gx-btn--outlined" type="button" @click="copyBluetoothSetting('enable-web-bluetooth-new-permissions-backend')">Copy address</button>
            </li>
          </ol>
          <p>Paste each address into Chrome's address bar and select Enabled. After changing both, relaunch Chrome, return to this same HTTPS page, and tap Connect to grant access again.</p>
          <p>If either setting is unavailable, continue pairing. Automatic reconnect after reload may remain unavailable.</p>
        </div>
      </GalaxyModal>
      <div v-if="capability === 'insecure'" class="telematics-gate">
        <GxNotice tone="warn" icon="bi-shield-lock-fill" title="Bluetooth pairing needs the HTTPS page">
          Chrome only allows Web Bluetooth on a secure page, and this one is plain HTTP. Galaxy runs a second listener on port 8443 for it. Nothing happens automatically — open it yourself when you have read the steps below.
        </GxNotice>
        <ol class="telematics-gate__steps">
          <li>Open <code>{{ secureURL }}</code> with the button at the bottom of this page.</li>
          <li>
            Chrome will warn <strong>&ldquo;Your connection is not private&rdquo;</strong> (<code>NET::ERR_CERT_AUTHORITY_INVALID</code>).
            That is expected. The certificate is generated on the device and signed by the device itself, so no public authority vouches for it.
            The connection is still encrypted and never leaves your local network.
          </li>
          <li>Tap <strong>Advanced</strong>, then <strong>Proceed to {{ secureHost }} (unsafe)</strong>. Chrome remembers the exception, so this is a one-time step per phone.</li>
          <li>The telematics page reloads over HTTPS. Tap <strong>Connect</strong> and accept the Android Bluetooth pairing prompt.</li>
        </ol>
        <p class="telematics-gate__tip">
          <i class="bi bi-lightbulb-fill"></i>
          Reach the device by name (<code>https://starpilot-&lt;device&gt;.local:8443</code>) rather than by IP address where you can.
          The certificate exception is remembered per address, so a new DHCP lease would make you accept the warning all over again.
        </p>
        <a class="gx-btn gx-btn--block" :href="secureURL" rel="noopener">Open the secure telematics page</a>
      </div>
      <div v-else-if="capability === 'unsupported'" class="telematics-gate">
        <GxNotice tone="info" icon="bi-phone" title="Chrome on Android required">
          This browser does not provide Web Bluetooth. Open this telematics page in Chrome on Android (or another browser with Web Bluetooth support).
        </GxNotice>
      </div>
      <template v-else>
        <div v-if="!isLandscape" class="telematics-connect-bar">
          <span class="telematics-status"><i :style="{ background: freshnessTint }"></i>{{ statusLabel }}<small>{{ deviceStatusLabel }} · {{ freshness }}</small></span>
          <button class="telematics-setup-button" type="button" title="Chrome setup for Bluetooth reconnect"
            aria-label="Chrome setup for Bluetooth reconnect" @click="openBluetoothSetup"><i class="bi bi-gear-fill"></i></button>
          <button v-if="connected" class="gx-btn gx-btn--outlined" type="button" @click="disconnect">Disconnect</button>
          <button v-else class="gx-btn" type="button" :disabled="!canConnect || connecting" @click="connect"><i class="bi bi-bluetooth"></i> {{ bleState === 'error' || bleState === 'needs-pairing' ? 'Reconnect' : 'Connect' }}</button>
        </div>
        <GxNotice v-if="bleState === 'needs-pairing'" class="telematics-pairing" tone="warn" icon="bi-bluetooth" title="Pair the device first" :text="bleMessage" />
        <GxNotice v-else-if="bleState === 'error'" class="telematics-pairing" tone="danger" icon="bi-exclamation-circle-fill" title="Bluetooth error" :text="bleMessage" />
        <GxNotice v-else-if="!canRestoreBluetooth && !isLandscape" class="telematics-pairing" tone="info" icon="bi-gear-fill" title="Chrome forgets this pairing on reload">
          Two Chrome flags let this page reconnect on its own instead of asking you to pick the device every time.
          <button class="telematics-setup-link" type="button" @click="openBluetoothSetup">Show me how</button>
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
              <span>{{ statusLabel }} · {{ deviceStatusLabel }}</span>
              <button class="telematics-fullscreen-button" type="button" title="Chrome setup for Bluetooth reconnect"
                aria-label="Chrome setup for Bluetooth reconnect" @click="openBluetoothSetup"><i class="bi bi-gear-fill"></i></button>
              <button class="telematics-fullscreen-button" type="button" :aria-label="isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'"
                :title="isFullscreen ? 'Exit fullscreen' : 'Hide browser controls'" :aria-pressed="isFullscreen" @click="toggleFullscreen">
                <i class="bi" :class="isFullscreen ? 'bi-fullscreen-exit' : 'bi-arrows-fullscreen'"></i>
              </button>
              <button v-if="connected" type="button" @click="disconnect">Disconnect</button>
              <button v-else type="button" :disabled="!canConnect || connecting" @click="connect">{{ bleState === 'error' || bleState === 'needs-pairing' ? 'Reconnect' : 'Connect' }}</button>
            </div>
            <div class="telematics-speed-row">
              <div class="telematics-target-sign"><span>MAX</span><strong>{{ setSpeedText }}</strong></div>
              <div class="telematics-hero">
                <div class="telematics-brand">Galaxy <i class="bi bi-stars"></i></div>
                <template v-if="connected && frame && flag(frame, 'standstill')">
                  <strong class="telematics-stopped-main">{{ stoppedMinutes }} minute{{ stoppedMinutes === 1 ? '' : 's' }}</strong>
                  <span class="telematics-stopped-detail">{{ stoppedSecondsPart }} second{{ stoppedSecondsPart === 1 ? '' : 's' }} stopped</span>
                </template>
                <template v-else-if="connected">
                  <div class="telematics-current-speed"><strong>{{ currentSpeedText }}</strong><span>{{ speedUnit }}</span></div>
                  <span class="telematics-drive-title" :style="{ color: modeColor }">{{ driveStateTitle }}</span>
                </template>
                <strong v-else class="telematics-not-connected">Not connected</strong>
              </div>
              <div class="telematics-limit-slot"><div v-if="hasSpeedLimit" class="telematics-limit-sign"><span>SPEED<br>LIMIT</span><strong>{{ speedLimitText }}</strong></div></div>
            </div>
            <div class="telematics-status-slot">
              <div v-if="experimentalInfo" class="telematics-mode-pill" :style="{ '--pill-tint': experimentalInfo.tint }"><span>{{ experimentalInfo.text }}</span><i class="bi" :class="experimentalInfo.icon"></i></div>
            </div>
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
            <div class="telematics-portrait-top">
              <div class="telematics-target-sign"><span>MAX</span><strong>{{ setSpeedText }}</strong></div>
              <div class="telematics-portrait-title"><strong>Galaxy <i class="bi bi-stars"></i></strong><span>{{ connected ? driveStateDetail : 'Not connected' }}</span></div>
              <div v-if="hasSpeedLimit" class="telematics-limit-sign telematics-limit-sign--portrait"><span>SPEED<br>LIMIT</span><strong>{{ speedLimitText }}</strong></div>
              <div class="telematics-speed-badge"><span>{{ speedUnit.toUpperCase() }}</span><strong>{{ currentSpeedText }}</strong></div>
            </div>
            <div class="telematics-status-slot"><div v-if="experimentalInfo" class="telematics-mode-pill" :style="{ '--pill-tint': experimentalInfo.tint }"><span>{{ experimentalInfo.text }}</span><i class="bi" :class="experimentalInfo.icon"></i></div></div>
            <div v-if="connected && frame && flag(frame, 'standstill')" class="telematics-stopped-line"><i class="bi bi-stopwatch"></i> {{ stoppedText }}</div>
            <div class="telematics-road-list"><RoadPill v-for="pill in roadPills" :key="pill.title" v-bind="pill" /></div>
            <div v-if="alertText" class="telematics-alert"><i class="bi bi-exclamation-triangle-fill"></i><span>{{ alertText }}</span></div>
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
