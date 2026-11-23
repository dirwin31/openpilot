import { WikiHelp } from './wiki-help.js'
import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { GalaxySelect } from "./galaxy-select.js"
import { reportSave } from "./save-feedback.js"
import { ModelManagerFeed, modelActionAllowed } from "./models.js"

export const JETSON_ADDONS = [
  { field: "vasm", icon: "bi-eye", title: "V-ASM on JetLink", detail: "Checks both side windows about five times a second while JetLink answers, instead of once a second on the comma." },
  { field: "signVision", icon: "bi-sign-stop", title: "Sign vision on JetLink", detail: "Runs the speed-limit and stop-sign detectors on the JetLink computer or phone. The speed classifier stays on the comma." },
]

export function addonState(addon, linked = true) {
  if (!addon) return "Unavailable"
  if (addon.enabled && !linked) return "Needs a JetLink connection"
  if (!addon.enabled) return "Off"
  if (addon.preparing) return `Preparing… ${addon.preparing}`
  if (addon.jetson === "unsupported") return "Update JetLink on the computer or phone"
  if (addon.jetson === "failed") return "JetLink can't run this model"
  if (addon.jetson === "missing") return "Prepares next time you park"
  if (!addon.present) return "Waiting for JetLink"
  if (!addon.running) return "Idle"
  if (addon.modelMismatch) return "Prepares next time you park"
  if (addon.yielding) return "Yielding to driving model"
  if (addon.connected) return "On JetLink"
  return "Waiting for JetLink"
}

export const JetlinkPage = {
  name: "JetlinkPage",
  components: { WikiHelp, GxState, GxNotice, GalaxySelect },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data() { return { loading: true, status: null, error: "", message: "", busy: "", pending: null, now: Date.now(), awakeUntil: null } },
  mounted() {
    this.feed = new ModelManagerFeed({ unauthorized: this.unauthorized, publish: ({ loading, data, error }) => {
      this.loading = loading
      if (data !== undefined) this.status = data
      this.error = error || ""
    } })
    if (this.mode === "local") this.feed.start()
    // the wake's countdown ticks between polls
    this.clock = setInterval(() => { this.now = Date.now() }, 1000)
  },
  beforeUnmount() { this.feed?.stop(); clearInterval(this.clock) },
  watch: {
    "jetlink.awakeFor": { immediate: true, handler(seconds) { this.awakeUntil = seconds ? Date.now() + seconds * 1000 : null } },
  },
  computed: {
    jetlink() { return this.status?.jetlink || null },
    canChange() { return !this.busy && modelActionAllowed(this.status, "jetlink") },
    usb() { return this.jetlink?.mode === "usb" },
    linked() { return ["usb", "ios"].includes(this.jetlink?.mode) },
    addonRows() {
      return JETSON_ADDONS.map(item => {
        const addon = this.addon(item.field)
        return { ...item, addon, label: addonState(addon, this.linked), timing: this.timing(addon), enabled: addon?.enabled === true,
          idle: !!(addon?.enabled && addon.present && !addon.running) }
      })
    },
  },
  methods: {
    addon(field) { return this.jetlink?.addons?.[field] || null },
    async configure(change) {
      if (!this.canChange) return
      this.pending = change
      this.busy = Object.keys(change)[0]
      this.message = ""
      try {
        const payload = "mode" in change ? { mode: change.mode, chargePhone: this.jetlink.chargePhone === true } : change
        const result = await reportSave(() => this.feed.action("jetlink", null, payload))
        if (result) this.message = result.message
      } finally { this.pending = null; this.busy = "" }
    },
    toggle(field, event) {
      const enabled = event.target.checked
      event.target.checked = field === "chargePhone" ? this.jetlink.chargePhone === true : this.addon(field)?.enabled === true
      return this.configure({ [field]: enabled })
    },
    wake(action = "start") { return this.configure({ wake: action }) },
    /** Seconds left on Galaxy's wake, counted down between polls. */
    awakeLeft() {
      const until = this.awakeUntil ?? (this.jetlink?.awakeFor ? Date.now() + this.jetlink.awakeFor * 1000 : null)
      return until ? Math.max(0, Math.round((until - (this.now ?? Date.now())) / 1000)) : 0
    },
    awakeText() {
      const left = this.awakeLeft()
      if (left > 0) {
        const until = new Date(Date.now() + left * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
        return `Awake · ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} left, until ${until}`
      }
      return this.jetlink?.asleep ? "Asleep" : "Awake"
    },
    checkTime(at) {
      return at ? new Date(at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : ""
    },
    timing(addon) {
      if (!addon?.connected || addon.roundTripMs == null) return ""
      return `${addon.answered} passes · ${addon.roundTripMs} ms round trip${addon.jetsonMs != null ? ` · ${addon.jetsonMs} ms on Jetson` : ""}`
    },
  },
  template: `
    <section class="gx-settings gx-jetlink" aria-labelledby="gx-jetlink-title" :inert="!!busy" :aria-busy="!!busy || undefined">
      <header class="gx-settings__header gx-page-header"><div><h2 id="gx-jetlink-title">JetLink</h2>
        <p>Run the big driving model on a computer or iPhone over USB, and move camera models onto a Jetson.</p></div>
        <WikiHelp /></header>
      <GxState v-if="mode !== 'local'">JetLink is unavailable in the offline preview.</GxState>
      <GxState v-else-if="loading && !status" loading>Reading JetLink status…</GxState>
      <template v-else>
        <GxNotice v-if="message" tone="info">{{ message }}</GxNotice>
        <GxNotice v-if="error" tone="danger" title="JetLink">{{ error }}</GxNotice>
        <GxNotice v-if="status?.isOnroad" tone="warn">Park to change JetLink.</GxNotice>
        <div v-if="jetlink" class="gx-settings__body">
          <section class="gx-card gx-settings__section" aria-labelledby="gx-jetlink-connection">
            <div class="gx-section__header"><i aria-hidden="true" class="bi bi-usb-symbol"></i><span id="gx-jetlink-connection" class="gx-section__title">Connection</span>
              <span class="gx-section__count">{{ jetlink.mode === 'off' ? 'Off' : jetlink.state }}</span></div>
            <div class="gx-panel gx-stack">
              <div class="gx-field-group">
                <label for="gx-jetlink-mode" class="gx-row__label">Remote model connection</label>
                <GalaxySelect id="gx-jetlink-mode" class="gx-field" :value="pending?.mode ?? jetlink.mode" :disabled="!canChange" @change="configure({ mode: $event.target.value })">
                  <option value="off">Off</option>
                  <option value="usb" :disabled="!jetlink.supported || jetlink.chestnut">USB computer</option>
                  <option value="ios" :disabled="!jetlink.supported || jetlink.chestnut">iPhone / iPad</option>
                </GalaxySelect>
              </div>
              <p class="gx-note" role="status"><span class="gx-chip">{{ jetlink.state }}</span><span v-if="jetlink.model && !jetlink.active"> · selected: {{ jetlink.model }}</span></p>
              <p v-if="jetlink.progress">{{ jetlink.progress }}</p>
              <p v-if="jetlink.reason" class="gx-model-reason">{{ jetlink.reason }}</p>
              <p v-if="jetlink.chestnut" class="gx-model-reason">Chestnut is connected. JetLink stays off while Chestnut is fitted.</p>
              <div v-if="usb" class="gx-row" :class="{disabled: !canChange}">
                <span class="gx-row__info"><span class="gx-row__label">Jetson</span>
                  <span class="gx-note" role="status"><span class="gx-chip">{{ awakeText() }}</span></span>
                  <span class="gx-note">Wakes the Jetson over USB and keeps it up for 15 minutes, so it joins its Wi-Fi and you can reach it. Add time, start the 15 minutes over, or release it to let it sleep.</span></span>
                <button v-if="awakeLeft() === 0" type="button" class="gx-btn gx-btn--tonal" :disabled="!canChange" @click="wake('start')"><i class="bi bi-power" aria-hidden="true"></i> Wake Jetson</button>
              </div>
              <div v-if="usb && awakeLeft() > 0" class="gx-actions" aria-label="Keep the Jetson awake">
                <button type="button" class="gx-btn gx-btn--tonal" :disabled="!canChange" @click="wake('extend')"><i class="bi bi-plus-lg" aria-hidden="true"></i> 15 min</button>
                <button type="button" class="gx-btn gx-btn--tonal" :disabled="!canChange" @click="wake('restart')"><i class="bi bi-arrow-clockwise" aria-hidden="true"></i> Restart 15 min</button>
                <button type="button" class="gx-btn gx-btn--tonal" :disabled="!canChange" @click="wake('release')"><i class="bi bi-moon" aria-hidden="true"></i> Release</button>
              </div>
              <div v-if="jetlink.mode === 'ios'" class="gx-row" :class="{disabled: !canChange}">
                <span class="gx-row__info gx-row__label">Charge phone over USB</span>
                <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Charge phone over USB" :checked="jetlink.chargePhone === true" :disabled="!canChange" @change="toggle('chargePhone', $event)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label>
              </div>
              <p class="gx-model-reason">Prepared means the remote model is built, not active. The local Small model remains available, and Small and Chestnut catalog selections are unchanged. Set up parked with a USB 3 data cable and separate power. Keep the computer awake; keep the iPhone app open and unlocked.</p>
              <div class="gx-actions">
                <a class="gx-btn gx-btn--tonal" href="#/manage_models"><i class="bi bi-cpu" aria-hidden="true"></i> Choose the big model</a>
                <a class="gx-btn gx-btn--tonal" href="https://github.com/StarPilotHQ/StarPilot/blob/testing/openpilot/starpilot/models/JETLINK.md" target="_blank" rel="noopener noreferrer"><i class="bi bi-box-arrow-up-right" aria-hidden="true"></i> Open setup guide</a>
              </div>
            </div>
          </section>

          <section class="gx-card gx-settings__section" aria-labelledby="gx-jetlink-addons">
            <div class="gx-section__header"><i aria-hidden="true" class="bi bi-gpu-card"></i><span id="gx-jetlink-addons" class="gx-section__title">JetLink add-ons</span></div>
            <div class="gx-panel gx-stack">
              <p class="gx-model-reason">For a Jetson, computer or iPhone running JetLink. The comma keeps running each model itself whenever JetLink is busy with the driving model or doesn't answer. JetLink gets each model while parked.</p>
              <div v-for="item in addonRows" :key="item.field" class="gx-row" :class="{disabled: !canChange || !linked}">
                <span class="gx-row__info">
                  <span class="gx-row__label"><i :class="['bi', item.icon]" aria-hidden="true"></i> {{ item.title }}</span>
                  <span class="gx-note">{{ item.detail }}</span>
                  <span class="gx-note" role="status"><span class="gx-chip">{{ item.label }}</span><span v-if="item.timing"> · {{ item.timing }}</span></span>
                  <span v-if="item.addon?.lastCheck" class="gx-note">Last check {{ checkTime(item.addon.lastCheckAt) }}: {{ item.addon.lastCheck }}</span>
                  <span v-if="item.addon?.modelMismatch" class="gx-model-reason">The comma runs this model itself until JetLink has the same file. JetLink sends it next time you park.</span>
                  <span v-else-if="item.idle" class="gx-note">Starts when the comma runs this model, usually once driving.</span>
                </span>
                <label class="gx-switch"><input type="checkbox" role="switch" :aria-label="item.title" :checked="pending?.[item.field] ?? item.enabled" :disabled="!canChange || (!linked && !item.enabled)" @change="toggle(item.field, $event)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label>
              </div>
              <p v-if="!linked" class="gx-model-reason">Add-ons need the USB computer or iPhone connection.</p>
            </div>
          </section>
        </div>
      </template>
    </section>`,
}
