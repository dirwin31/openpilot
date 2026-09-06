import { api } from "../api.js"
import { usePolling } from "../composables.js"
import { GxNotice } from "../components/GxNotice.js"
import { store } from "../store.js"
import { LiveBLEClient } from "../ble/live_ble.js"
import { hasFlag, LIVE_FLAGS } from "../ble/live_frames.js"

const flag = (frame, name) => !!frame && hasFlag(frame.flags, LIVE_FLAGS[name])
const finite = (value) => Number.isFinite(Number(value)) ? Number(value) : null
const signed = (value, digits, suffix = "") => value === null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(digits)}${suffix}`

export const DashTile = {
  name: "DashTile",
  props: { title: String, value: String, tint: { type: String, default: "var(--primary)" }, landscape: Boolean },
  template: `
    <div class="dash-tile" :class="{ 'dash-tile--landscape': landscape }" :style="{ '--tile-tint': tint }">
      <span class="dash-tile__title">{{ title }}</span>
      <strong class="dash-tile__value">{{ value }}</strong>
    </div>
  `,
}

export const RoadPill = {
  name: "RoadPill",
  props: { icon: String, title: String, value: String, tint: String },
  template: `
    <div class="dash-road-pill" :style="{ '--pill-tint': tint }">
      <div class="dash-road-pill__label"><i class="bi" :class="icon"></i><span>{{ title }}</span></div>
      <strong>{{ value }}</strong>
    </div>
  `,
}

export const Dashboard = {
  name: "Dashboard",
  components: { DashTile, RoadPill, GxNotice },
  data() {
    return {
      isLandscape: false,
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
    }
  },
  computed: {
    connected() { return this.bleState === "connected" },
    canConnect() { return this.capability === "ready" && !["connecting", "reconnecting"].includes(this.bleState) },
    insecureURL() {
      const target = new URL(window.location.href)
      target.protocol = "https:"
      target.port = "8443"
      return target.toString()
    },
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
      if (!frame) return "Connect to the device over Bluetooth to populate the dashboard."
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
    async connect() {
      this.connecting = true
      try {
        if (["error", "needs-pairing"].includes(this.bleState)) await this.ble.reconnect()
        else await this.ble.connect()
      } catch (error) { /* State includes the actionable error. */ }
      finally { this.connecting = false }
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
    this.orientation = window.matchMedia("(orientation: landscape)")
    this.isLandscape = this.orientation.matches
    this.orientation.addEventListener?.("change", this.setOrientation)
    this.clock = setInterval(() => { this.now = Date.now() }, 1000)
    void this.loadParams()
    this.devicePoll = usePolling(() => this.loadDeviceStatus(), { interval: 5000 })
    this.devicePoll.start()

    if (!window.isSecureContext) this.capability = "insecure"
    else if (!navigator.bluetooth) this.capability = "unsupported"
    else {
      this.capability = "ready"
      this.ble = new LiveBLEClient({
        onState: ({ state, message, deviceName }) => { this.bleState = state; this.bleMessage = message; this.deviceName = deviceName },
        onLive: (frame, session, updatedAt) => { this.frame = frame; this.session = session; this.liveUpdatedAt = updatedAt },
        onHealth: (frame, updatedAt) => { this.health = frame; this.healthUpdatedAt = updatedAt },
        onMetadata: (metadata) => { this.metadata = metadata },
      })
      void this.ble.reconnectRemembered()
    }
  },
  beforeUnmount() {
    this.orientation?.removeEventListener?.("change", this.setOrientation)
    clearInterval(this.clock)
    this.devicePoll?.destroy()
    this.ble?.close()
  },
  template: `
    <div class="dash-page" :class="{ 'dash-page--landscape': isLandscape }">
      <div v-if="capability === 'insecure'" class="dash-gate">
        <GxNotice tone="warn" icon="bi-shield-lock-fill" title="HTTPS required">
          Web Bluetooth needs a secure page. Open the HTTPS Galaxy listener, accept its one-time certificate warning, then connect. Prefer the stable https://starpilot-&lt;device&gt;.local:8443 address.
        </GxNotice>
        <a class="gx-btn gx-btn--block" :href="insecureURL">Open secure dashboard</a>
      </div>
      <div v-else-if="capability === 'unsupported'" class="dash-gate">
        <GxNotice tone="info" icon="bi-phone" title="Chrome on Android required">
          This browser does not provide Web Bluetooth. Open this dashboard in Chrome on Android (or another browser with Web Bluetooth support).
        </GxNotice>
      </div>
      <template v-else>
        <div v-if="!isLandscape" class="dash-connect-bar">
          <span class="dash-status"><i :style="{ background: freshnessTint }"></i>{{ statusLabel }}<small>{{ deviceStatusLabel }} · {{ freshness }}</small></span>
          <button v-if="connected" class="gx-btn gx-btn--outlined" type="button" @click="disconnect">Disconnect</button>
          <button v-else class="gx-btn" type="button" :disabled="!canConnect || connecting" @click="connect"><i class="bi bi-bluetooth"></i> {{ bleState === 'error' || bleState === 'needs-pairing' ? 'Reconnect' : 'Connect' }}</button>
        </div>
        <GxNotice v-if="bleState === 'needs-pairing'" class="dash-pairing" tone="warn" icon="bi-bluetooth" title="Pair the device first" :text="bleMessage" />
        <GxNotice v-else-if="bleState === 'error'" class="dash-pairing" tone="danger" icon="bi-exclamation-circle-fill" title="Bluetooth error" :text="bleMessage" />

        <div v-if="isLandscape" class="dash-landscape">
          <aside class="dash-side dash-side--left">
            <button class="dash-menu-button" type="button" aria-label="Open menu" @click="openMenu"><i class="bi bi-list"></i></button>
            <DashTile landscape title="TEMP" :value="tempText" :tint="tempTint" />
            <DashTile landscape title="CPU" :value="cpuText" :tint="cpuTint" />
            <DashTile landscape title="MEMORY" :value="memoryText" :tint="memoryTint" />
            <DashTile landscape title="MODEL" :value="modelText.toUpperCase()" :tint="flag(frame, 'bigModel') ? 'var(--primary)' : 'var(--text-muted)'" />
            <DashTile landscape title="LATERAL" :value="lateralState.toUpperCase()" :tint="flag(frame, 'lateralActive') ? 'var(--success)' : 'var(--text-muted)'" />
            <div class="dash-logo"><img src="/assets/images/main_logo.png" alt="Galaxy" /></div>
          </aside>

          <section class="dash-center" :style="{ '--mode-color': modeColor }">
            <div class="dash-center__connection">
              <span>{{ statusLabel }} · {{ deviceStatusLabel }}</span>
              <button v-if="connected" type="button" @click="disconnect">Disconnect</button>
              <button v-else type="button" :disabled="!canConnect || connecting" @click="connect">{{ bleState === 'error' || bleState === 'needs-pairing' ? 'Reconnect' : 'Connect' }}</button>
            </div>
            <div class="dash-speed-row">
              <div class="dash-target-sign"><span>MAX</span><strong>{{ setSpeedText }}</strong></div>
              <div class="dash-hero">
                <div class="dash-brand">Galaxy <i class="bi bi-stars"></i></div>
                <template v-if="connected && frame && flag(frame, 'standstill')">
                  <strong class="dash-stopped-main">{{ stoppedMinutes }} minute{{ stoppedMinutes === 1 ? '' : 's' }}</strong>
                  <span class="dash-stopped-detail">{{ stoppedSecondsPart }} second{{ stoppedSecondsPart === 1 ? '' : 's' }} stopped</span>
                </template>
                <template v-else-if="connected">
                  <div class="dash-current-speed"><strong>{{ currentSpeedText }}</strong><span>{{ speedUnit }}</span></div>
                  <span class="dash-drive-title" :style="{ color: modeColor }">{{ driveStateTitle }}</span>
                </template>
                <strong v-else class="dash-not-connected">Not connected</strong>
              </div>
              <div class="dash-limit-slot"><div v-if="hasSpeedLimit" class="dash-limit-sign"><span>SPEED<br>LIMIT</span><strong>{{ speedLimitText }}</strong></div></div>
            </div>
            <div class="dash-status-slot">
              <div v-if="experimentalInfo" class="dash-mode-pill" :style="{ '--pill-tint': experimentalInfo.tint }"><span>{{ experimentalInfo.text }}</span><i class="bi" :class="experimentalInfo.icon"></i></div>
            </div>
            <div class="dash-alert-slot"><div v-if="alertText" class="dash-alert"><i class="bi bi-exclamation-triangle-fill"></i><span>{{ alertText }}</span></div></div>
            <div class="dash-road-list"><RoadPill v-for="pill in roadPills" :key="pill.title" v-bind="pill" /></div>
          </section>

          <aside class="dash-side dash-side--right">
            <DashTile landscape title="STEER DELAY" :value="param('SteerDelay')" />
            <DashTile landscape title="LAT ACCEL" :value="param('SteerLatAccel')" />
            <DashTile landscape title="STEER RATIO" :value="param('SteerRatio')" />
            <DashTile landscape title="STEER ANGLE" :value="angle(frame?.steeringAngle)" tint="var(--success)" />
            <DashTile landscape title="DRIVER TORQUE" :value="torqueText" tint="var(--success)" />
            <DashTile landscape title="FRICTION" :value="param('SteerFriction')" />
            <DashTile landscape title="LATERAL %" :value="percent(session.lateralPercent)" tint="var(--success)" />
          </aside>
        </div>

        <div v-else class="dash-portrait">
          <section class="dash-instrument" :style="{ '--mode-color': modeColor }">
            <div class="dash-portrait-top">
              <div class="dash-target-sign"><span>MAX</span><strong>{{ setSpeedText }}</strong></div>
              <div class="dash-portrait-title"><strong>Galaxy <i class="bi bi-stars"></i></strong><span>{{ connected ? driveStateDetail : 'Not connected' }}</span></div>
              <div v-if="hasSpeedLimit" class="dash-limit-sign dash-limit-sign--portrait"><span>SPEED<br>LIMIT</span><strong>{{ speedLimitText }}</strong></div>
              <div class="dash-speed-badge"><span>{{ speedUnit.toUpperCase() }}</span><strong>{{ currentSpeedText }}</strong></div>
            </div>
            <div class="dash-status-slot"><div v-if="experimentalInfo" class="dash-mode-pill" :style="{ '--pill-tint': experimentalInfo.tint }"><span>{{ experimentalInfo.text }}</span><i class="bi" :class="experimentalInfo.icon"></i></div></div>
            <div v-if="connected && frame && flag(frame, 'standstill')" class="dash-stopped-line"><i class="bi bi-stopwatch"></i> {{ stoppedText }}</div>
            <div class="dash-road-list"><RoadPill v-for="pill in roadPills" :key="pill.title" v-bind="pill" /></div>
            <div v-if="alertText" class="dash-alert"><i class="bi bi-exclamation-triangle-fill"></i><span>{{ alertText }}</span></div>
          </section>
          <section class="dash-steering">
            <h2><i class="bi bi-speedometer2"></i> Steering and control</h2>
            <div class="dash-tile-grid"><DashTile v-for="tile in portraitTiles" :key="tile[0]" :title="tile[0]" :value="tile[1]" /></div>
          </section>
        </div>
      </template>
    </div>
  `,
}
