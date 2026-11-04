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
  auto_mute: false, hide_filtered: false, frequency_rules: [], quiet_ride: false, quiet_margin: 0, quiet_bands: ['X', 'K'],
  auto_learn: false, learn_bands: ['X', 'K'], border_bands: ['KA', 'LASER'], border_colors: { KA: 'purple', K: 'yellow', LASER: 'white' },
  border_styles: { KA: 'chase', K: 'corners', LASER: 'pulse' },
  timed_mute: false, timed_mute_seconds: 5, timed_mute_strength: 6, timed_mute_bands: ['X', 'K', 'KA'], unmute_on_breakthrough: false })
// openpilot already uses green, blue, cyan, pink, orange, red and grey.
export const SWATCHES = { purple: '#B026FF', violet: '#8F7BFF', yellow: '#E3FF00', white: '#FFFFFF' }
export const STYLES = { solid: 'Solid', pulse: 'Pulse', chase: 'Chase', corners: 'Corners', direction: 'Direction' }
export const STYLE_HELP = {
  solid: 'A steady frame.',
  pulse: 'The frame blinks.',
  chase: 'A bright segment circles the screen. It runs faster and longer as the signal gets stronger.',
  corners: 'Only the four corners light up.',
  direction: 'R8: the edge facing the signal lights up. Top is ahead, bottom is behind, both sides is beside you. An R4 gets the chase.',
}
const StylePreview = {
  props: { kind: { type: String, default: 'solid' }, color: { type: String, default: '#FFFFFF' } },
  template: `<svg class="gx-uniden__preview" viewBox="0 0 160 90" role="img" :aria-label="'Preview of the ' + kind + ' frame'">
    <rect width="160" height="90" fill="#0b1217" />
    <rect x="3" y="3" width="154" height="84" fill="none" stroke="#167F40" stroke-width="6" />
    <rect v-if="kind === 'solid' || kind === 'pulse'" :class="{ 'gx-uniden__pulse': kind === 'pulse' }" x="3" y="3" width="154" height="84" fill="none" :stroke="color" stroke-width="3" />
    <template v-if="kind === 'chase'">
      <rect x="3" y="3" width="154" height="84" fill="none" :stroke="color" stroke-width="3" opacity=".27" />
      <rect class="gx-uniden__chase" x="3" y="3" width="154" height="84" pathLength="100" fill="none" :stroke="color" stroke-width="3" />
    </template>
    <path v-if="kind === 'corners'" d="M3 26V3H34 M126 3H157V26 M157 64V87H126 M34 87H3V64" fill="none" :stroke="color" stroke-width="3" />
    <template v-if="kind === 'direction'">
      <rect x="3" y="3" width="154" height="84" fill="none" :stroke="color" stroke-width="3" opacity=".27" />
      <path class="gx-uniden__front" d="M3 3H157" fill="none" :stroke="color" stroke-width="3" />
      <path class="gx-uniden__side" d="M3 3V87 M157 3V87" fill="none" :stroke="color" stroke-width="3" />
      <path class="gx-uniden__rear" d="M3 87H157" fill="none" :stroke="color" stroke-width="3" />
    </template>
  </svg>`,
}
const BANDS = { X: 'X', K: 'K', KA: 'Ka', LASER: 'Laser' }
const copy = value => JSON.parse(JSON.stringify(value))
export const UnidenPanel = {
  components: { StylePreview },
  props: { devices: { type: Array, default: () => [] }, parked: Boolean, unauthorized: Function, settings: Boolean },
  data: () => ({ config: defaults(), saved: null, loaded: false, busy: false, error: '', state: null, editing: false,
    locks: [], candidates: 0, metric: false, swatches: SWATCHES, styles: STYLES, help: STYLE_HELP, bands: BANDS }),
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
    this.visibility = () => { clearTimeout(this.timer); this.locksLoaded = false; if (!document.hidden) this.refresh() }
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
        if (!this.locksLoaded) { this.locksLoaded = true; this.loadLocks() }
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
    toggleBand(key, band) {
      const list = this.config[key]
      this.config[key] = list.includes(band) ? list.filter(item => item !== band) : [...list, band]
    },
    async unlock(id) { await this.loadLocks({ operation: 'unlock', id }) },
    async loadLocks(payload = { operation: 'locks' }) {
      try {
        const value = await this.request(payload)
        if (this.alive) { this.locks = value.locks; this.candidates = value.candidates; this.metric = value.metric }
      } catch (error) { if (this.alive && payload.operation === 'unlock') this.error = error.message }
    },
    lockLabel(lock) { return BANDS[lock.band] + (lock.frequency ? ` ${lock.frequency.toFixed(3)} GHz` : '') },
    lockDetail(lock) {
      return `${lock.source === 'auto' ? 'Learned' : 'Held on screen'} · ${lock.count} ${lock.count === 1 ? 'pass' : 'passes'} · ${lock.lat.toFixed(4)}, ${lock.lon.toFixed(4)} · ${lock.radius_m} m`
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
            <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Quiet Ride at the posted limit</span><span class="gx-row__desc">Mute the beep, keep the alert on screen, while you are at or under the posted limit plus the margin. Sudden strong signals are never quieted. Needs a known limit and a GPS fix.</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Quiet Ride at the posted limit" v-model="config.quiet_ride" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <div v-if="config.quiet_ride" class="gx-uniden__sub">
              <label class="gx-uniden__field gx-uniden__narrow">Margin over the limit ({{ metric ? 'km/h' : 'mph' }}) <input class="gx-field" type="number" min="0" max="15" step="1" inputmode="numeric" v-model.number="config.quiet_margin" /></label>
              <div class="gx-uniden__choices" role="group" aria-label="Bands Quiet Ride may mute"><span class="gx-note">Mute</span>
                <button v-for="band in Object.keys(bands)" :key="band" type="button" class="gx-chip gx-uniden__choice" :class="{ 'gx-chip--selected': config.quiet_bands.includes(band) }" :aria-pressed="config.quiet_bands.includes(band)" @click="toggleBand('quiet_bands', band)">{{ bands[band] }}</button></div>
              <p v-if="config.quiet_bands.includes('LASER')" class="gx-note">Laser is muted too. The detector mutes everything at once, so a new signal that arrives after a mute may stay quiet until the detector re-alerts.</p>
            </div>
            <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Mute after a few seconds</span><span class="gx-row__desc">Let each new signal beep for a moment, then mute it. The alert stays on screen. If it reaches the chosen strength the card says Strong signal and the frame lights up again. Laser is never muted this way.</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Mute after a few seconds" v-model="config.timed_mute" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <div v-if="config.timed_mute" class="gx-uniden__sub">
              <div class="gx-uniden__pair">
                <label class="gx-uniden__field">Mute after (seconds) <input class="gx-field" type="number" min="1" max="60" step="1" inputmode="numeric" v-model.number="config.timed_mute_seconds" /></label>
                <label class="gx-uniden__field">Warn again at strength <select class="gx-field" v-model.number="config.timed_mute_strength"><option v-for="n in [2, 3, 4, 5, 6, 7, 8]" :key="n" :value="n">{{ n }} of 8</option></select></label>
              </div>
              <div class="gx-uniden__choices" role="group" aria-label="Bands to mute after a delay"><span class="gx-note">Mute</span>
                <button v-for="band in ['X', 'K', 'KA']" :key="band" type="button" class="gx-chip gx-uniden__choice" :class="{ 'gx-chip--selected': config.timed_mute_bands.includes(band) }" :aria-pressed="config.timed_mute_bands.includes(band)" @click="toggleBand('timed_mute_bands', band)">{{ bands[band] }}</button></div>
            </div>
            <template v-if="config.timed_mute">
            <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Unmute at that strength</span><span class="gx-row__desc">Also tell the detector to unmute so it beeps again. This command has not been tested on the detector, so try it parked first. It only undoes a mute this feature sent.</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Unmute when the signal strengthens" v-model="config.unmute_on_breakthrough" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            </template>
            <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Learn lockouts automatically</span><span class="gx-row__desc">After the same fixed source is passed on separate trips (three for X and K, five for Ka and Laser), lock it out at that place. A source that repeats at one spot but is missing on many drive-bys, like a patrol spot, is not learned. A signal stronger than ever seen there still alerts.</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Learn lockouts automatically" v-model="config.auto_learn" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <div v-if="config.auto_learn" class="gx-uniden__sub">
              <div class="gx-uniden__choices" role="group" aria-label="Bands to learn"><span class="gx-note">Learn</span>
                <button v-for="band in Object.keys(bands)" :key="band" type="button" class="gx-chip gx-uniden__choice" :class="{ 'gx-chip--selected': config.learn_bands.includes(band) }" :aria-pressed="config.learn_bands.includes(band)" @click="toggleBand('learn_bands', band)">{{ bands[band] }}</button></div>
            </div>
          </fieldset>
        </section>
        <section class="gx-card" aria-label="Full-screen warning">
          <div class="gx-section__header"><span class="gx-section__title">Full-screen warning</span></div>
          <fieldset :disabled="!parked || busy" class="gx-uniden__fieldset"><div class="gx-panel gx-stack">
            <p class="gx-note">A frame over the engagement border (the green line in the previews) for a live alert, drawn above any openpilot alert. Choose a style and color for each band.</p>
            <div v-for="band in ['KA', 'K', 'LASER']" :key="band" class="gx-uniden__rule" :class="{ 'gx-uniden__band--off': !config.border_bands.includes(band) }">
              <div class="gx-uniden__heading">
                <h4>{{ bands[band] }}</h4>
                <label class="gx-uniden__enable"><span>{{ config.border_bands.includes(band) ? 'Frame on' : 'Frame off' }}</span><span class="gx-switch"><input type="checkbox" role="switch" :aria-label="bands[band] + ' warning frame'" :checked="config.border_bands.includes(band)" @change="toggleBand('border_bands', band)" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></span></label>
              </div>
              <div class="gx-uniden__band-body">
                <style-preview :kind="config.border_styles[band]" :color="swatches[config.border_colors[band]]" />
                <div class="gx-uniden__band-controls">
                  <div class="gx-uniden__choices" role="group" :aria-label="bands[band] + ' frame style'">
                    <button v-for="(label, name) in styles" :key="name" type="button" class="gx-chip gx-uniden__choice" :class="{ 'gx-chip--selected': config.border_styles[band] === name }" :aria-pressed="config.border_styles[band] === name" @click="config.border_styles[band] = name">{{ label }}</button>
                  </div>
                  <p class="gx-note">{{ help[config.border_styles[band]] }}</p>
                  <div class="gx-uniden__choices" role="group" :aria-label="bands[band] + ' frame color'">
                    <button v-for="(hex, name) in swatches" :key="name" type="button" class="gx-uniden__dot" :style="{ background: hex }" :aria-pressed="config.border_colors[band] === name" :aria-label="name" :title="name" @click="config.border_colors[band] = name"></button>
                  </div>
                </div>
              </div>
            </div>
            <p class="gx-note">Colors avoid the green, blue, cyan, pink, orange, red and grey openpilot already uses.</p>
          </div></fieldset>
        </section>
        <section class="gx-card" aria-label="Saved lockouts">
          <div class="gx-section__header"><span class="gx-section__title">Saved lockouts</span></div>
          <div class="gx-panel gx-stack">
            <p class="gx-note">Hold an alert on the driving screen to lock it out where you are. Lockouts only apply near that place. Learning has seen {{ candidates }} other {{ candidates === 1 ? 'source' : 'sources' }} so far.</p>
            <div v-if="!locks.length" class="gx-uniden__empty"><strong>No saved lockouts</strong></div>
            <div v-for="lock in locks" :key="lock.id" class="gx-uniden__rule-footer">
              <div><h4>{{ lockLabel(lock) }}</h4><p class="gx-note">{{ lockDetail(lock) }}</p></div>
              <button class="gx-btn gx-btn--tonal" @click="unlock(lock.id)" :aria-label="'Remove lockout ' + lockLabel(lock)">Remove</button>
            </div>
          </div>
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
