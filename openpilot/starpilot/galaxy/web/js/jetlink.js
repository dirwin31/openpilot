import { WikiHelp } from './wiki-help.js'
import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { GalaxySelect } from "./galaxy-select.js"
import { reportSave } from "./save-feedback.js"
import { ModelManagerFeed, modelActionAllowed } from "./models.js"

export const JETSON_ADDONS = [
  { field: "vasm", icon: "bi-eye", title: "V-ASM on Jetson", detail: "Checks both side windows about five times a second while the Jetson answers, instead of once a second on the comma." },
  { field: "signVision", icon: "bi-sign-stop", title: "Sign vision on Jetson", detail: "Runs the speed-limit and stop-sign detectors on the Jetson. The speed classifier stays on the comma." },
]

export function addonState(addon, usb = true) {
  if (!addon) return "Unavailable"
  if (addon.enabled && !usb) return "Needs USB computer"
  if (!addon.enabled) return "Off"
  if (addon.preparing) return `Preparing… ${addon.preparing}`
  if (addon.jetson === "unsupported") return "Update JetLink on the Jetson"
  if (addon.jetson === "failed") return "Jetson can't run this model"
  if (addon.jetson === "missing") return "Prepares next time you park"
  if (!addon.present) return "Waiting for JetLink"
  if (!addon.running) return "Idle"
  if (addon.modelMismatch) return "Prepares next time you park"
  if (addon.yielding) return "Yielding to driving model"
  if (addon.connected) return "On Jetson"
  return "Waiting for Jetson"
}

export const JetlinkPage = {
  name: "JetlinkPage",
  components: { WikiHelp, GxState, GxNotice, GalaxySelect },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data() { return { loading: true, status: null, error: "", message: "", busy: "", pending: null } },
  mounted() {
    this.feed = new ModelManagerFeed({ unauthorized: this.unauthorized, publish: ({ loading, data, error }) => {
      this.loading = loading
      if (data !== undefined) this.status = data
      this.error = error || ""
    } })
    if (this.mode === "local") this.feed.start()
  },
  beforeUnmount() { this.feed?.stop() },
  computed: {
    jetlink() { return this.status?.jetlink || null },
    canChange() { return !this.busy && modelActionAllowed(this.status, "jetlink") },
    usb() { return this.jetlink?.mode === "usb" },
    addonRows() {
      return JETSON_ADDONS.map(item => {
        const addon = this.addon(item.field)
        return { ...item, addon, label: addonState(addon, this.usb), timing: this.timing(addon), enabled: addon?.enabled === true,
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
    wake() { return this.configure({ wake: true }) },
    awakeText() {
      if (this.jetlink?.awakeFor) return `Awake until ${new Date(Date.now() + this.jetlink.awakeFor * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
      return this.jetlink?.asleep ? "Asleep" : "Awake"
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
                  <span class="gx-note">Wakes the Jetson over USB and keeps it up for 15 minutes, so it joins its Wi-Fi and you can reach it.</span></span>
                <button type="button" class="gx-btn gx-btn--tonal" :disabled="!canChange" @click="wake"><i class="bi bi-power" aria-hidden="true"></i> Wake Jetson</button>
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
            <div class="gx-section__header"><i aria-hidden="true" class="bi bi-gpu-card"></i><span id="gx-jetlink-addons" class="gx-section__title">Jetson add-ons</span></div>
            <div class="gx-panel gx-stack">
              <p class="gx-model-reason">For a Jetson on USB. The comma keeps running each model itself whenever the Jetson is busy with the driving model or doesn't answer. The Jetson gets each model while parked, then sleeps.</p>
              <div v-for="item in addonRows" :key="item.field" class="gx-row" :class="{disabled: !canChange || !usb}">
                <span class="gx-row__info">
                  <span class="gx-row__label"><i :class="['bi', item.icon]" aria-hidden="true"></i> {{ item.title }}</span>
                  <span class="gx-note">{{ item.detail }}</span>
                  <span class="gx-note" role="status"><span class="gx-chip">{{ item.label }}</span><span v-if="item.timing"> · {{ item.timing }}</span></span>
                  <span v-if="item.addon?.modelMismatch" class="gx-model-reason">The comma runs this model itself until the Jetson has the same file. JetLink sends it next time you park.</span>
                  <span v-else-if="item.idle" class="gx-note">Starts when the comma runs this model, usually once driving.</span>
                </span>
                <label class="gx-switch"><input type="checkbox" role="switch" :aria-label="item.title" :checked="pending?.[item.field] ?? item.enabled" :disabled="!canChange || (!usb && !item.enabled)" @change="toggle(item.field, $event)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label>
              </div>
              <p v-if="!usb" class="gx-model-reason">Jetson add-ons need the USB computer connection.</p>
            </div>
          </section>
        </div>
      </template>
    </section>`,
}
