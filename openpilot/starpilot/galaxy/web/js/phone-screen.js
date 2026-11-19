import { requestJson } from "./startup.js"
import { GxNotice } from "./notice.js"
import { StarPortTabs } from "./starport-tabs.js"
import { beginSave, finishSave } from "./save-feedback.js"
import { CarPlayKey } from "./carplay-key.js"

const STATES = { off: "Off", starting: "Starting…", waiting: "Waiting for a phone", connecting: "Connecting…", active: "Showing" }

export function validPhoneScreen(value) {
  return typeof value?.enabled === "boolean" && ["left", "right"].includes(value.side) &&
    ["phone", "music"].includes(value.audio) && Object.hasOwn(STATES, value.state) &&
    typeof value.phone === "string" && typeof value.message === "string" && typeof value.kind === "string"
}

export const PhoneScreenPage = {
  components: { GxNotice, StarPortTabs, CarPlayKey },
  props: { go: { type: Function, default: path => { location.hash = path } }, mode: { type: String, required: true },
    unauthorized: { type: Function, required: true } },
  data: () => ({ status: null, busy: false, error: "", timer: null, active: false }),
  computed: {
    stateLabel() { return STATES[this.status?.state] || "" },
    summary() {
      const s = this.status
      if (!s?.enabled) return "Turn on to show your phone’s screen beside the driving view."
      if (s.state === "active") return `Showing ${s.phone || "your phone"} with ${s.kind === "android" ? "Android Auto" : "CarPlay"}`
      return s.message || this.stateLabel
    },
    chipTone() { return this.status?.state === "active" ? "gx-starport-pill--ready" : "gx-starport-pill--none" },
  },
  mounted() { this.start() },
  beforeUnmount() { this.stop() },
  watch: { mode() { this.stop(); this.start() } },
  methods: {
    start() { if (this.mode === "local") { this.active = true; this.refresh() } },
    stop() { this.active = false; clearTimeout(this.timer) },
    adopt(value) { if (!validPhoneScreen(value)) throw new Error("Invalid phone screen status"); this.status = value },
    async refresh() {
      if (!this.active) return
      clearTimeout(this.timer)
      try {
        if (!this.busy) { this.adopt(await requestJson("./api/phone-projection")); this.error = "" }
      } catch (error) {
        if (error.status === 401) { this.stop(); this.unauthorized(); return }
        this.error = error.message
      } finally {
        if (this.active) this.timer = setTimeout(() => this.refresh(), 3000)
      }
    },
    async save(change) {
      if (!this.active || this.busy || !this.status) return
      const feedback = beginSave()
      this.busy = true; this.error = ""
      try {
        this.adopt(await requestJson("./api/phone-projection", { request: { method: "POST",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(change) } }))
        finishSave(feedback, true)
      } catch (error) {
        finishSave(feedback, false)
        if (error.status === 401) { this.stop(); this.unauthorized(); return }
        this.error = error.message
      } finally { this.busy = false }
    },
  },
  template: `
    <section class="gx-driving" aria-label="Phone Screen">
      <header class="gx-settings__header gx-page-header"><div><h2>Phone Screen</h2><p>Add your phone’s screen to StarPilot. CarPlay or Android Auto shows on one half, the driving view on the other.</p></div></header>
      <StarPortTabs active="phone" :go="go" />
      <p v-if="mode !== 'local'" class="gx-note">Connect to local Galaxy to set up the phone screen.</p>
      <p v-else-if="!status && !error" class="gx-note">Loading phone screen settings…</p>
      <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
      <template v-if="status">
        <section class="gx-card" aria-label="Phone Screen Settings" :aria-busy="busy || undefined">
          <div class="gx-section__header"><i class="bi bi-phone-landscape" aria-hidden="true"></i><span class="gx-section__title">Phone Screen</span><span v-if="status.enabled" class="gx-starport-pill" :class="chipTone" role="status">{{ stateLabel }}</span></div>
          <div class="gx-panel gx-stack">
            <div class="gx-row gx-row--borderless"><div class="gx-row__info"><span class="gx-row__label">Show phone screen</span><span class="gx-row__desc">{{ summary }}</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Show phone screen" :checked="status.enabled" :disabled="busy" @change="save({ enabled: $event.target.checked })"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <div class="gx-row"><div class="gx-row__info"><span class="gx-row__label">Phone side</span><span class="gx-row__desc">Which half the phone uses, on the comma screen and on the car screen.</span></div>
              <nav class="gx-settings-tabs" aria-label="Phone screen side">
                <button v-for="side in ['left', 'right']" :key="side" type="button" class="gx-chip" :aria-pressed="status.side === side" :disabled="busy" @click="save({ side })">{{ side === 'left' ? 'Left' : 'Right' }}</button>
              </nav></div>
            <div class="gx-row"><div class="gx-row__info"><span class="gx-row__label">Phone audio</span><span class="gx-row__desc">Keep audio on the phone, or play it where music plays (Bluetooth speaker or StarPort).</span></div>
              <nav class="gx-settings-tabs" aria-label="Phone audio">
                <button v-for="route in [['phone', 'Phone'], ['music', 'Music output']]" :key="route[0]" type="button" class="gx-chip" :aria-pressed="status.audio === route[0]" :disabled="busy" @click="save({ audio: route[0] })">{{ route[1] }}</button>
              </nav></div>
            <ul class="gx-install-benefits">
              <li><i class="bi bi-display" aria-hidden="true"></i><span><strong>comma 3 or 3X:</strong> the phone takes half of the comma screen while driving, once a phone connects.</span></li>
              <li><i class="bi bi-car-front" aria-hidden="true"></i><span><strong>StarPort:</strong> the phone takes half of the car screen. Reconnect StarPort after turning this on or off.</span></li>
            </ul>
          </div>
        </section>
        <section class="gx-card" aria-label="Phone Screen Layout">
          <div class="gx-section__header"><i class="bi bi-layout-split" aria-hidden="true"></i><span class="gx-section__title">Layout Beside the Phone</span></div>
          <div class="gx-panel gx-stack">
            <p class="gx-note">The driving view has half the room beside a phone, so it gets its own widget layout. Your full-screen layouts stay as they are.</p>
            <div class="gx-actions">
              <button type="button" class="gx-btn gx-btn--tonal" @click="go('/theme_maker/phone')"><i class="bi bi-display" aria-hidden="true"></i> Comma Screen Layout</button>
              <button type="button" class="gx-btn gx-btn--tonal" @click="go('/theme_maker/starport/phone')"><i class="bi bi-car-front" aria-hidden="true"></i> StarPort Layout</button>
            </div>
          </div>
        </section>
        <section class="gx-card" aria-label="Connect a Phone">
          <div class="gx-section__header"><i class="bi bi-link-45deg" aria-hidden="true"></i><span class="gx-section__title">Connect a Phone</span></div>
          <div class="gx-panel gx-stack">
            <ul class="gx-install-benefits">
              <li><i class="bi bi-broadcast-pin" aria-hidden="true"></i><span>Turn on the <a class="gx-link" href="#/networks">Galaxy hotspot</a>. Phones join it for video.</span></li>
              <li><i class="bi bi-bluetooth" aria-hidden="true"></i><span>Pair the phone with the comma in <a class="gx-link" href="#/bluetooth">Bluetooth</a>.</span></li>
              <li><i class="bi bi-android2" aria-hidden="true"></i><span>Android: turn on wireless Android Auto on the phone. It starts once the phone sees the comma.</span></li>
              <li><i class="bi bi-apple" aria-hidden="true"></i><span>iPhone: add the CarPlay key below once. CarPlay then starts by itself when the paired iPhone is near.</span></li>
            </ul>
            <CarPlayKey :unauthorized="unauthorized" />
          </div>
        </section>
      </template>
    </section>`,
}
