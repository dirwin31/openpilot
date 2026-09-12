import { api } from "../api.js"
import { usePolling } from "../composables.js"
import { GxNotice } from "./GxNotice.js"

export const UnidenPanel = {
  name: "UnidenPanel",
  components: { GxNotice },
  data() {
    return { config: null, state: {}, slowdown: {}, devices: [], bands: [], settings: [], values: {},
      dirty: false, busy: false, offroad: false, online: false, error: "", message: "" }
  },
  created() { this.poll = usePolling(() => this.refresh(), { interval: 1500 }); this.poll.start() },
  beforeUnmount() { this.poll?.destroy() },
  computed: {
    disabled() { return !this.online || !this.offroad || this.busy },
    writeDisabled() { return this.disabled || !this.state.connected || !this.state.can_write || this.dirty },
    writeHint() {
      if (this.disabled) return "Detector commands are available while parked and connected to StarPilot."
      if (this.dirty) return "Save your configuration before sending detector commands."
      if (!this.state.connected) return "Connect a detector to send commands."
      if (!this.state.can_write) return "This detector connection does not support commands."
      return ""
    },
    targetText() {
      const speed = Number(this.slowdown.target_mps)
      return speed > 0 ? `${Math.round(speed * 2.236936)} mph / ${Math.round(speed * 3.6)} km/h` : "—"
    },
  },
  methods: {
    async refresh() {
      try {
        const [data, bt] = await Promise.all([api.getUniden(), api.getBluetoothStatus()])
        this.online = true
        this.offroad = !!data.offroad
        if (!this.dirty && !this.busy) this.config = { ...data.config, bands: [...data.config.bands] }
        this.state = data.state || {}
        this.slowdown = data.slowdown || {}
        this.settings = data.settings || []
        this.bands = data.bands || []
        this.devices = (bt.devices || []).filter(d => d.uniden && d.paired)
      } catch (e) {
        this.online = false
        this.offroad = false
        this.state = {}
        this.slowdown = { reason: "Status unavailable" }
        this.error = e?.message || "Unable to load Uniden status"
      }
    },
    async save() {
      if (this.disabled || !this.config) return
      await this.perform({ operation: "configure", config: this.config })
    },
    async send(setting) {
      if (this.writeDisabled) return
      await this.perform({ operation: "setting", setting, value: this.values[setting] })
    },
    async perform(body) {
      if (this.busy) return
      this.busy = true
      this.error = ""
      this.message = ""
      try {
        const result = await api.unidenOp(body)
        if (body.operation === "configure") {
          this.config = result.config
          this.dirty = false
          this.message = "Uniden configuration saved."
        } else {
          this.message = "Command sent; detector confirmation is unavailable. Check the detector’s display."
        }
      } catch (e) {
        this.error = e?.message || "Uniden operation failed"
      } finally {
        this.busy = false
        await this.refresh()
      }
    },
  },
  template: `
    <div class="gx-uniden">
      <h3>Detector &amp; Auto Slowdown</h3>
      <GxNotice v-if="error" tone="danger" :text="error" />
      <GxNotice v-if="message" :text="message" />
      <GxNotice v-if="!offroad" text="Configuration and detector commands are available while parked." />
      <div class="gx-card gx-uniden__status">
        <strong>{{ state.connected ? state.name : 'Detector monitor disconnected' }}</strong>
        <p>{{ slowdown.reason || 'Planner inactive' }} · Target {{ targetText }}</p>
        <p v-if="state.firmware" class="gx-row__desc">Firmware: {{ state.firmware }}</p>
        <p v-if="state.error" class="gx-row__desc">{{ state.error }}</p>
        <div v-for="(alert, index) in state.alerts || []" :key="index" class="gx-row">
          <strong>{{ alert.band }} · {{ alert.strength }}/8</strong>
          <span>{{ alert.direction }} · {{ alert.frequency }} {{ alert.frequency ? 'GHz' : '' }} {{ alert.muted ? '· Muted' : '' }}</span>
        </div>
      </div>
      <fieldset v-if="config" :disabled="disabled" class="gx-uniden__config" @change="dirty = true">
        <label class="gx-row"><span>Enable detector monitoring</span><input type="checkbox" v-model="config.enabled" /></label>
        <label class="gx-row gx-uniden__field"><span>Active detector</span>
          <select class="gx-field" v-model="config.address" aria-label="Active detector">
            <option value="">Select a paired detector</option>
            <option v-for="d in devices" :key="d.address" :value="d.address">{{ d.name }} · {{ d.address }}</option>
            <option v-if="config.address && !devices.some(d => d.address === config.address)" :value="config.address">{{ config.address }} (unavailable)</option>
          </select>
        </label>
        <label class="gx-row"><span>Auto slowdown to posted speed limit</span><input type="checkbox" v-model="config.auto_slowdown" /></label>
        <p class="gx-row__desc">Requires active openpilot longitudinal control and a valid speed limit from Speed Limit Controller or Show Speed Limits.
          Uses the posted limit without your offset; never raises your cruise target. Pressing gas overrides slowdown until the alert clears.
          Stale detector data cancels this speed ceiling.</p>
        <label class="gx-row gx-uniden__field"><span>Minimum signal strength</span><select class="gx-field" v-model.number="config.min_strength" aria-label="Minimum signal strength"><option v-for="n in 8" :value="n">{{ n }} / 8</option></select></label>
        <label class="gx-row"><span>Ignore muted alerts</span><input type="checkbox" v-model="config.ignore_muted" /></label>
        <div class="gx-uniden__bands" role="group" aria-label="Alert bands">
          <label v-for="band in bands" :key="band"><input type="checkbox" v-model="config.bands" :value="band" /> {{ band }}</label>
        </div>
      </fieldset>
      <button type="button" class="gx-btn" :disabled="disabled || !dirty" @click="save">Save Configuration</button>

      <details class="gx-uniden__settings">
        <summary>Detector Settings (experimental)</summary>
        <p>These command mappings come from the Uniden integration branch and are not verified across models or firmware.
          Current settings cannot be read back here. Select a value and send one command at a time, then check it on the detector.</p>
        <p class="gx-row__desc">R4/R4W/R8/R8W and R9 variants are candidates when they expose the required Bluetooth services. R7 is not supported.</p>
        <p v-if="writeHint" class="gx-row__desc" role="status">{{ writeHint }}</p>
        <div v-for="setting in settings" :key="setting.key" class="gx-uniden__command">
          <label :for="'uniden-' + setting.key">{{ setting.label }}</label>
          <select :id="'uniden-' + setting.key" class="gx-field" v-model="values[setting.key]" :disabled="disabled">
            <option :value="undefined" disabled>Select a value</option>
            <option v-for="value in setting.choices" :key="value" :value="value">{{ setting.choices.length === 2 && typeof value === 'number' ? (value ? 'On' : 'Off') : value }}</option>
          </select>
          <button type="button" class="gx-btn gx-btn--tonal" :disabled="writeDisabled || values[setting.key] == null" @click="send(setting.key)">Send</button>
        </div>
        <div class="gx-uniden__actions">
          <button type="button" class="gx-btn" :disabled="writeDisabled" @click="send('mute')">Mute Alert</button>
          <button type="button" class="gx-btn gx-btn--tonal" :disabled="writeDisabled" @click="send('unmute')">Unmute Alert</button>
        </div>
        <p v-if="state.last_command" class="gx-row__desc">Last sent: {{ state.last_command.setting }} {{ state.last_command.value }} · Unconfirmed</p>
      </details>
    </div>
  `,
}
