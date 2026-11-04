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
    connectionLabel() { return !this.saved?.address ? 'No detector selected' : !this.ready ? 'Selected detector is no longer paired' :
      !this.saved.enabled ? 'Integration off' : this.state?.connected ? 'Connected' : 'Saved · waiting for detector' },
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
          this.config = copy({ ...defaults(), ...value.config }); this.saved = copy(this.config); this.loaded = true
        }
        this.error = value.error || ''
      } catch (error) { if (this.alive && this.epoch === epoch) this.error = error.message }
      finally { if (this.alive && this.epoch === epoch && !document.hidden) this.timer = setTimeout(() => this.refresh(), 2000) }
    },
    selectDetector() {
      const match = this.selected?.name.match(/(?:^|[^a-z0-9])(r[48])w?(?=$|[^a-z0-9])/i)
      if (match) { this.config.model = match[1].toUpperCase(); this.config.enabled = true }
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
          this.config = copy(value.config); this.saved = copy(value.config); this.state = value; this.error = ''; this.editing = false
        }
      } catch (error) { if (this.alive) this.error = error.message }
      finally { this.busy = false; if (this.alive) this.refresh() }
    },
  },
  template: `<section class="gx-card">
    <div class="gx-section__header"><i class="bi bi-broadcast" aria-hidden="true"></i><span class="gx-section__title">{{ settings ? 'Uniden settings' : 'Uniden radar detector' }}</span></div>
    <div class="gx-panel gx-stack">
      <p v-if="!loaded" class="gx-note" role="status">Loading detector setup…</p>
      <p v-if="error" role="status" class="gx-note">{{ error }}</p>
      <template v-if="loaded">
        <p role="status"><strong>{{ saved.model }}{{ saved.address ? ' · ' + saved.address : '' }}</strong> · {{ connectionLabel }}</p>
        <p v-if="!parked" class="gx-note">Park to change detector settings.</p>
        <template v-if="!settings || !ready || editing">
          <p v-if="!detectors.length" class="gx-note">No paired detector found. <a class="gx-link" href="#/bluetooth">Pair in Bluetooth</a>, then return here to select it.</p>
          <fieldset v-else-if="!ready || editing" :disabled="!parked || busy" style="border:0;padding:0;min-width:0;margin:0">
            <label class="gx-row">Detector <select v-model="config.address" @change="selectDetector" class="gx-field" aria-label="Uniden detector"><option value="">Select a paired detector</option>
              <option v-for="d in detectors" :key="d.address" :value="d.address">{{ d.name }} · {{ d.address }}</option></select></label>
            <p class="gx-note">{{ selected ? config.model + ' detected. Saving enables the integration.' : 'Choose a paired detector to finish setup.' }}</p>
            <div class="gx-actions"><button class="gx-btn" :disabled="!dirty || !selected" @click="save">{{ busy ? 'Saving…' : saved.address ? 'Save changes' : 'Use this detector' }}</button>
              <button v-if="ready" class="gx-btn gx-btn--tonal" @click="cancel">Cancel</button></div>
          </fieldset>
          <div v-else class="gx-actions">
            <a class="gx-btn" href="#/bluetooth/uniden">Uniden settings</a>
            <button class="gx-btn gx-btn--tonal" :disabled="!parked || busy" @click="editing = true">Change detector</button>
          </div>
        </template>
        <template v-if="settings && ready && !editing">
          <button class="gx-btn gx-btn--tonal" :disabled="!parked || busy || dirty" @click="editing = true">Change detector</button>
          <fieldset :disabled="!parked || busy" style="border:0;padding:0;min-width:0;margin:0">
            <label class="gx-row"><span class="gx-row__info">Enable Uniden integration</span><span class="gx-switch"><input type="checkbox" role="switch" v-model="config.enabled" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></span></label>
            <p class="gx-note">Read alerts on the driving screen, including when the Uniden widget is hidden.</p>
            <label class="gx-row"><span class="gx-row__info">Automatically mute matching frequencies</span><span class="gx-switch"><input type="checkbox" role="switch" v-model="config.auto_mute" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></span></label>
            <p class="gx-note">Mute immediately after a matching alert is read. Only mutes when every active signal matches an enabled rule. A brief initial beep may still occur.</p>
            <label class="gx-row"><span class="gx-row__info">Hide matching alerts from the widget</span><span class="gx-switch"><input type="checkbox" role="switch" v-model="config.hide_filtered" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></span></label>
            <h3>Frequency rules</h3>
            <p class="gx-note">Rules apply everywhere while connected. Start with an exact frequency; increase tolerance only if the reading drifts. Another source at the same frequency will also match.</p>
            <p v-if="!config.frequency_rules.length" class="gx-note">No custom frequencies saved. Add a frequency to get started.</p>
            <section v-for="(rule, index) in config.frequency_rules" :key="index" class="gx-inset">
              <label class="gx-row"><span>Rule {{ index + 1 }} enabled</span><input type="checkbox" v-model="rule.enabled" /></label>
              <label class="gx-row">Label <input class="gx-field" v-model="rule.label" maxlength="60" placeholder="Head unit" /></label>
              <label class="gx-row">Band <select class="gx-field" v-model="rule.band"><option>X</option><option>K</option><option>KA</option></select></label>
              <label class="gx-row">Frequency (GHz) <input class="gx-field" type="number" step="0.001" v-model.number="rule.frequency" /></label>
              <label class="gx-row">Tolerance (± MHz) <input class="gx-field" type="number" min="0" max="10" step="0.1" v-model.number="rule.tolerance_mhz" /></label>
              <button class="gx-btn gx-btn--tonal" @click="config.frequency_rules.splice(index, 1)">Remove rule {{ index + 1 }}</button>
            </section>
            <button class="gx-btn gx-btn--tonal" :disabled="config.frequency_rules.length >= 32" @click="addRule">Add frequency</button>
            <div v-if="dirty" class="gx-actions"><button class="gx-btn" @click="save">{{ busy ? 'Saving…' : 'Save changes' }}</button>
              <button class="gx-btn gx-btn--tonal" @click="cancel">Discard changes</button></div>
            <p v-else class="gx-note" role="status">All changes saved.</p>
          </fieldset>
          <p class="gx-note">Widget size, position and appearance are in the theme maker. These rules are saved in StarPilot; they do not change the detector’s stored lockouts.</p>
        </template>
      </template>
    </div></section>`,
}
