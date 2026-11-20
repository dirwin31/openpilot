import { requestJson } from "./startup.js"
import { GxNotice } from "./notice.js"
import { StarPortTabs } from "./starport-tabs.js"
import { beginSave, finishSave } from "./save-feedback.js"
import { CarPlayKey } from "./carplay-key.js"

const STATES = { off: "Off", starting: "Starting…", waiting: "Waiting for a phone", connecting: "Connecting…", active: "Showing" }

// Setup steps in the order projectiond checks them; a runner's blocked state names the first step still to do
const STEPS = {
  carplay: [
    { id: "key", blocked: "unavailable", title: "Add the CarPlay key", hint: "Once per comma, from the CarLink app" },
    { id: "hotspot", blocked: "waiting_hotspot", title: "Turn on the Galaxy hotspot", hint: "The iPhone joins it for the picture" },
    { id: "pair", blocked: "waiting_phone", title: "Pair your iPhone", hint: "In Galaxy Bluetooth, once" },
    { id: "connect", title: "Get in with your iPhone", hint: "CarPlay starts by itself" },
  ],
  android: [
    { id: "hotspot", blocked: "waiting_hotspot", title: "Turn on the Galaxy hotspot", hint: "The phone joins it for the picture" },
    { id: "pair", blocked: "waiting_phone", title: "Pair your Android phone", hint: "In Galaxy Bluetooth, once" },
    { id: "wireless", blocked: "waiting_projection", title: "Turn on wireless Android Auto", hint: "On the phone, once" },
    { id: "connect", title: "Get in with your phone", hint: "Android Auto starts by itself" },
  ],
}

export function connectSteps(status, kind) {
  const steps = STEPS[kind] || []
  const state = status?.enabled ? status?.[kind]?.state || "" : ""
  const known = state && state !== "starting"
  const at = !known ? -1 : Math.max(0, steps.findIndex(step => step.blocked === state))
  const finished = known && !steps.some(step => step.blocked === state)
  return steps.map((step, index) => {
    let progress = "todo"
    if (step.id === "connect") {
      progress = state === "active" ? "done" : state === "connecting" ? "active" : finished ? "current" : "todo"
    } else if (finished || known && index < at) progress = "done"
    else if (known && index === at) progress = "current"
    return { ...step, progress }
  })
}

export function validPhoneScreen(value) {
  return typeof value?.enabled === "boolean" && ["left", "right"].includes(value.side) &&
    ["phone", "music"].includes(value.audio) && Object.hasOwn(STATES, value.state) &&
    typeof value.phone === "string" && typeof value.message === "string" && typeof value.kind === "string"
}

export const PhoneScreenPage = {
  components: { GxNotice, StarPortTabs, CarPlayKey },
  props: { go: { type: Function, default: path => { location.hash = path } }, mode: { type: String, required: true },
    unauthorized: { type: Function, required: true } },
  data: () => ({ status: null, busy: false, error: "", timer: null, active: false, phoneKind: "" }),
  computed: {
    stateLabel() { return STATES[this.status?.state] || "" },
    summary() {
      const s = this.status
      if (!s?.enabled) return "Turn on to show your phone’s screen beside the driving view."
      if (s.state === "active") return `Showing ${s.phone || "your phone"} with ${s.kind === "android" ? "Android Auto" : "CarPlay"}`
      return s.message || this.stateLabel
    },
    kind() { return this.phoneKind || (this.status?.kind === "android" ? "android" : "carplay") },
    steps() { return connectSteps(this.status, this.kind) },
    runnerMessage() {
      const runner = this.status?.enabled ? this.status?.[this.kind] : null
      return runner && !["active", "starting", ""].includes(runner.state) ? runner.message : ""
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
            <nav class="gx-settings-tabs" aria-label="Phone type">
              <button v-for="option in [['carplay', 'iPhone', 'bi-apple'], ['android', 'Android', 'bi-android2']]" :key="option[0]" type="button" class="gx-chip" :aria-pressed="kind === option[0]" @click="phoneKind = option[0]"><i :class="'bi ' + option[2]" aria-hidden="true"></i> {{ option[1] }}</button>
            </nav>
            <p v-if="!status.enabled" class="gx-note">Turn on <strong>Show phone screen</strong> above and these steps tick themselves off as you go.</p>
            <ol class="gx-steps gx-steps--compact" :aria-label="kind === 'android' ? 'Android Auto setup' : 'CarPlay setup'">
              <li v-for="(step, index) in steps" :key="kind + step.id" class="gx-step-card" :class="{ 'gx-step--done': step.progress === 'done' }" :aria-current="step.progress === 'current' ? 'step' : undefined">
                <div class="gx-step-card__head">
                  <span class="gx-step__num gx-step-card__num" :aria-label="step.progress === 'done' ? 'Done' : 'Step ' + (index + 1)"><i v-if="step.progress === 'done'" class="bi bi-check-lg" aria-hidden="true"></i><template v-else>{{ index + 1 }}</template></span>
                  <div class="gx-step-card__title"><strong>{{ step.title }}</strong><small>{{ step.hint }}</small></div>
                </div>
                <div v-if="step.progress !== 'done' || step.id === 'key'" class="gx-step-card__body">
                  <CarPlayKey v-if="step.id === 'key'" :unauthorized="unauthorized" />
                  <template v-else-if="step.id === 'hotspot'">
                    <p class="gx-note">In <strong>Networks</strong>, turn on <strong>Galaxy Wi-Fi Hotspot</strong>. Leave it on: the phone screen uses it every drive.</p>
                    <div class="gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="go('/networks')"><i class="bi bi-broadcast-pin" aria-hidden="true"></i> Open Networks</button></div>
                  </template>
                  <template v-else-if="step.id === 'pair'">
                    <p class="gx-note">In <strong>Bluetooth</strong>, tap <strong>Add a device</strong> while parked. Then tap the comma in the phone’s Bluetooth settings and confirm the code.</p>
                    <p v-if="kind === 'carplay'" class="gx-note">Pair in the iPhone’s <strong>Settings → Bluetooth</strong>. Pairing only in the StarPilot app is not enough for CarPlay.</p>
                    <div class="gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="go('/bluetooth')"><i class="bi bi-bluetooth" aria-hidden="true"></i> Open Bluetooth</button></div>
                  </template>
                  <p v-else-if="step.id === 'wireless'" class="gx-note">On the phone, open <strong>Settings</strong>, search for <strong>Android Auto</strong>, and turn on <strong>Wireless Android Auto</strong>. Keep Bluetooth and Wi-Fi on.</p>
                  <template v-else-if="step.id === 'connect'">
                    <p v-if="kind === 'carplay'" class="gx-note">Keep the iPhone unlocked with Bluetooth and Wi-Fi on. The first time, accept the CarPlay prompt on the iPhone.</p>
                    <p v-else class="gx-note">Keep the phone unlocked the first time and accept any Android Auto prompts on it.</p>
                    <p v-if="step.progress === 'active'" class="gx-note" role="status">Connecting…</p>
                  </template>
                  <small v-if="step.progress === 'current' && runnerMessage" class="gx-note" role="status">{{ runnerMessage }}</small>
                </div>
              </li>
            </ol>
          </div>
        </section>
      </template>
    </section>`,
}
