export function detectorStatus(state) {
  if (state?.connected) return { label: 'Connected', detail: 'Ready to receive detector alerts.', tone: 'connected' }
  const code = state?.connection_state
  if (code === 'radio_off') return { label: 'Bluetooth off', detail: 'Turn on Bluetooth on the Devices tab.', tone: 'waiting' }
  if (code === 'detector_pairing_required') return { label: 'Check pairing', detail: 'Pair the detector in Bluetooth, then select it here.', tone: 'waiting' }
  if (code === 'session_expired') return { label: 'Sign in required', detail: 'Sign in to Galaxy again to reconnect.', tone: 'waiting' }
  if (code === 'busy') return { label: 'Waiting for Bluetooth', detail: 'Finish any device search or pairing. Connection will resume automatically.', tone: 'waiting' }
  if (code === 'unavailable') return { label: 'Connection unavailable', detail: 'Check that the detector is on and another app is not connected. Retrying automatically.', tone: 'waiting' }
  return { label: 'Reconnecting', detail: 'Keep the detector on and close other apps connected to it. Retrying automatically.', tone: 'waiting' }
}

const defaults = () => ({ enabled: false, address: '', model: 'R8', transparency: 8, hide_when_no_alert: true,
  auto_mute: false, hide_filtered: false, frequency_rules: [] })
const copy = value => JSON.parse(JSON.stringify(value))
export const UnidenPanel = {
  props: { devices: { type: Array, default: () => [] }, parked: Boolean, unauthorized: Function, settings: Boolean },
  data: () => ({ config: defaults(), saved: null, loaded: false, busy: false, error: '', state: null, editing: false }),
  computed: {
    detectors() { return this.devices.filter(d => d.paired && /(?:^|[^a-z0-9])r[48]w?(?=$|[^a-z0-9])/i.test(d.name)) },
    selected() { return this.detectors.find(d => d.address.toUpperCase() === this.config.address.toUpperCase()) },
    dirty() { return this.saved !== null && JSON.stringify(this.config) !== JSON.stringify(this.saved) },
    ready() { return !!this.saved?.address && this.detectors.some(d => d.address.toUpperCase() === this.saved.address.toUpperCase()) },
    savedName() { return this.detectors.find(d => d.address.toUpperCase() === this.saved?.address)?.name || (this.saved?.address ? `Uniden ${this.saved.model}` : 'Select your detector') },
    connection() {
      if (!this.saved?.address) return { label: 'Not configured', detail: 'Choose a paired detector to get started.', tone: 'waiting' }
      if (!this.ready) return { label: 'Not paired', detail: 'Pair the selected detector again on the Devices tab.', tone: 'waiting' }
      if (!this.saved.enabled) return { label: 'Integration off', detail: 'Enable the integration below to receive alerts.', tone: 'off' }
      return detectorStatus(this.state)
    },
  },
  mounted() {
    this.alive = true; this.epoch = 0
    this.visibility = () => { clearTimeout(this.timer); if (!document.hidden) this.refresh() }
    document.addEventListener('visibilitychange', this.visibility)
    if (!document.hidden) this.refresh()
  },
  beforeUnmount() { this.alive = false; this.epoch++; clearTimeout(this.timer); document.removeEventListener('visibilitychange', this.visibility) },
  methods: {
    async request(payload) {
      const response = await fetch('./api/bluetooth/uniden', { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal: AbortSignal.timeout(15000) })
      const value = await response.json()
      if (response.status === 401) this.unauthorized?.()
      if (!response.ok) throw new Error(value.error || 'Detector unavailable')
      return value
    },
    async refresh() {
      if (!this.alive || this.busy || document.hidden) return
      const epoch = ++this.epoch
      try {
        const value = await this.request({ operation: 'status' })
        if (!this.alive || this.epoch !== epoch) return
        this.state = value
        if (!this.loaded || !this.dirty && !this.editing) {
          this.config = copy({ ...defaults(), ...value.config }); this.saved = copy(this.config)
          if (!this.loaded) this.error = ''
          this.loaded = true
        }
      } catch {
        if (this.alive && this.epoch === epoch) {
          this.state = { connected: false, alerts: [], connection_state: 'unavailable' }
          if (!this.loaded) this.error = 'Unable to load detector settings. Retrying automatically.'
        }
      }
      finally { if (this.alive && this.epoch === epoch && !document.hidden) this.timer = setTimeout(() => this.refresh(), 2000) }
    },
    selectDetector() {
      const match = this.selected?.name.match(/(?:^|[^a-z0-9])(r[48])w?(?=$|[^a-z0-9])/i)
      if (match) { this.config.model = match[1].toUpperCase(); this.config.enabled = true }
    },
    ruleRange(rule) {
      const frequency = Number(rule.frequency), tolerance = Number(rule.tolerance_mhz)
      if (rule.frequency === '' || rule.tolerance_mhz === '' || !Number.isFinite(frequency) || !Number.isFinite(tolerance) || tolerance < 0) return 'Enter a frequency and tolerance.'
      return tolerance === 0 ? `Matches ${frequency.toFixed(3)} GHz exactly` :
        `Matches ${(frequency - tolerance / 1000).toFixed(4)}–${(frequency + tolerance / 1000).toFixed(4)} GHz`
    },
    cancel() { this.config = copy(this.saved); this.editing = false; this.error = '' },
    addRule() {
      if (this.config.frequency_rules.length < 32) this.config.frequency_rules.push({ enabled: true, label: '', band: 'KA', frequency: 34.313, tolerance_mhz: 0 })
    },
    async save() {
      if (!this.parked || this.busy || !this.dirty || !this.selected) return
      this.busy = true
      this.epoch++
      clearTimeout(this.timer)
      try {
        const value = await this.request({ operation: 'configure', config: copy(this.config) })
        if (this.alive) {
          this.state = this.saved?.address === value.config.address ? { ...this.state, config: value.config } : value
          this.config = copy(value.config); this.saved = copy(value.config); this.error = ''; this.editing = false
        }
      } catch (error) { if (this.alive) this.error = error.message }
      finally { this.busy = false; if (this.alive) this.refresh() }
    },
  },
  template: `<div class="gx-uniden">
    <p v-if="!loaded" class="gx-note" role="status">Loading detector setup…</p>
    <p v-if="error" role="alert" class="gx-uniden__error">{{ error }}</p>
    <template v-if="loaded">
      <section class="gx-card" aria-label="Detector connection">
        <div class="gx-panel gx-stack">
          <div class="gx-uniden__heading">
            <div><span class="gx-uniden__eyebrow">DETECTOR</span><h3>{{ savedName }}</h3></div>
            <span class="gx-uniden__status" :class="'gx-uniden__status--' + connection.tone" role="status">{{ connection.label }}</span>
          </div>
          <p class="gx-note">{{ connection.detail }}</p>
          <div v-if="ready && !editing" class="gx-actions">
            <button class="gx-btn gx-btn--tonal" :disabled="!parked || busy || dirty" @click="editing = true">Change detector</button>
            <a v-if="!settings" class="gx-btn" href="#/bluetooth/uniden">Uniden settings</a>
          </div>
          <template v-if="!ready || editing">
            <p v-if="!detectors.length" class="gx-note">No paired detector found. <a class="gx-link" href="#/bluetooth">Pair in Bluetooth</a>, then return here.</p>
            <fieldset v-else :disabled="!parked || busy" class="gx-uniden__fieldset gx-stack">
              <label class="gx-uniden__field">Detector <select v-model="config.address" @change="selectDetector" class="gx-field" aria-label="Uniden detector"><option value="">Select a paired detector</option>
                <option v-for="d in detectors" :key="d.address" :value="d.address">{{ d.name }} · {{ d.address }}</option></select></label>
              <p class="gx-note">{{ selected ? config.model + ' detected. Saving enables the integration.' : 'Choose a paired detector to finish setup.' }}</p>
              <div class="gx-actions"><button class="gx-btn" :disabled="!dirty || !selected" @click="save">{{ busy ? 'Saving…' : saved.address ? 'Save changes' : 'Use this detector' }}</button>
                <button v-if="ready" class="gx-btn gx-btn--tonal" @click="cancel">Cancel</button></div>
            </fieldset>
          </template>
        </div>
      </section>
      <p v-if="!parked" class="gx-note">Park to change detector settings.</p>
      <template v-if="settings && ready && !editing">
        <section class="gx-card" aria-label="Alert preferences">
          <div class="gx-section__header"><span class="gx-section__title">Alert preferences</span></div>
          <fieldset :disabled="!parked || busy" class="gx-uniden__fieldset">
            <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Enable Uniden integration</span><span class="gx-row__desc">Receive alerts while the driving screen is active, even with the widget hidden.</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Enable Uniden integration" v-model="config.enabled" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Auto-mute saved frequencies</span><span class="gx-row__desc">Mute when every active signal matches an enabled rule. A brief initial beep may occur.</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Automatically mute matching frequencies" v-model="config.auto_mute" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Hide filtered alerts</span><span class="gx-row__desc">Keep matching signals off the driving-screen widget.</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Hide matching alerts from the widget" v-model="config.hide_filtered" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
          </fieldset>
        </section>
        <section class="gx-card" aria-label="Frequency rules">
          <div class="gx-section__header"><span class="gx-section__title">Frequency rules</span>
            <button class="gx-btn gx-btn--tonal" :disabled="!parked || busy || config.frequency_rules.length >= 32" @click="addRule">Add frequency</button></div>
          <div class="gx-panel gx-stack">
            <p class="gx-note">Save known false alerts such as your head unit. Rules apply wherever you drive.</p>
            <div v-if="!config.frequency_rules.length" class="gx-uniden__empty"><strong>No saved frequencies</strong><p class="gx-note">Choose Add frequency, then enter the reading shown by your detector.</p></div>
            <fieldset v-for="(rule, index) in config.frequency_rules" :key="index" :disabled="!parked || busy" class="gx-uniden__fieldset gx-uniden__rule">
              <div class="gx-uniden__heading">
                <h4>{{ rule.label || 'Frequency ' + (index + 1) }}</h4>
                <label class="gx-uniden__enable"><span>{{ rule.enabled ? 'Enabled' : 'Off' }}</span><span class="gx-switch"><input type="checkbox" role="switch" :aria-label="'Rule ' + (index + 1) + ' enabled'" v-model="rule.enabled" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></span></label>
              </div>
              <div class="gx-uniden__fields">
                <label class="gx-uniden__field gx-uniden__name">Label <input class="gx-field" v-model="rule.label" maxlength="60" placeholder="e.g. Head unit" /></label>
                <label class="gx-uniden__field">Band <select class="gx-field" v-model="rule.band"><option>X</option><option>K</option><option>KA</option></select></label>
                <label class="gx-uniden__field">Frequency (GHz) <input class="gx-field" type="number" step="0.001" inputmode="decimal" v-model.number="rule.frequency" /></label>
                <label class="gx-uniden__field">Tolerance (± MHz) <input class="gx-field" type="number" min="0" max="10" step="0.1" inputmode="decimal" v-model.number="rule.tolerance_mhz" /></label>
              </div>
              <div class="gx-uniden__rule-footer"><div><p class="gx-note">{{ ruleRange(rule) }}</p><p class="gx-note">Keep tolerance at 0 unless the reading drifts.</p></div>
                <button class="gx-btn gx-btn--tonal" @click="config.frequency_rules.splice(index, 1)" :aria-label="'Remove rule ' + (index + 1)">Remove</button></div>
            </fieldset>
            <p v-if="config.frequency_rules.length" class="gx-note">Another source at the same frequency will also match. These rules are saved in StarPilot, not the detector’s lockout memory.</p>
          </div>
        </section>
        <div class="gx-uniden__savebar">
          <span class="gx-note" role="status">{{ dirty ? 'Unsaved changes' : 'All changes saved.' }}</span>
          <div v-if="dirty" class="gx-actions"><button class="gx-btn gx-btn--tonal" :disabled="!parked || busy" @click="cancel">Discard changes</button>
            <button class="gx-btn" :disabled="!parked || busy" @click="save">{{ busy ? 'Saving…' : 'Save changes' }}</button></div>
        </div>
        <p class="gx-note">Widget size, position and appearance are in the theme maker.</p>
      </template>
    </template>
  </div>`,
}
