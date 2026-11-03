export const UnidenPanel = {
  props: { devices: { type: Array, default: () => [] }, parked: Boolean, unauthorized: Function },
  data: () => ({ config: { enabled: false, address: '', model: 'R8', transparency: 8, hide_when_no_alert: true }, loaded: false, busy: false, error: '', state: null }),
  computed: {
    detectors() { return this.devices.filter(d => d.paired && /(?:^|[^a-z0-9])r[48]w?(?=$|[^a-z0-9])/i.test(d.name)) },
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
        if (!this.loaded) { this.config = { ...value.config }; this.loaded = true }
        this.error = value.error || ''
      } catch (error) { if (this.alive && this.epoch === epoch) this.error = error.message }
      finally { if (this.alive && this.epoch === epoch && !document.hidden) this.timer = setTimeout(() => this.refresh(), 2000) }
    },
    async save() {
      if (!this.parked || this.busy) return
      this.busy = true
      this.epoch++
      clearTimeout(this.timer)
      try {
        const value = await this.request({ operation: 'configure', config: this.config })
        if (this.alive) { this.config = { ...value.config }; this.state = value; this.error = '' }
      } catch (error) { if (this.alive) this.error = error.message }
      finally { this.busy = false; if (this.alive) this.refresh() }
    },
  },
  template: `<section class="gx-card">
    <div class="gx-section__header"><i class="bi bi-broadcast" aria-hidden="true"></i><span class="gx-section__title">Uniden radar detector</span></div>
    <div class="gx-inset">
      <p class="gx-note">Pair your R4 or R8 using Nearby Devices above, then select it here. On the detector, enable Bluetooth and open BT Pairing. Close any phone app connected to the detector. Finish Android Auto pairing before detector setup.</p>
      <p v-if="error" role="status" class="gx-note">{{ error }}</p>
      <fieldset :disabled="!parked || busy || !loaded" style="border:0;padding:0">
        <label class="gx-row">Detector <select v-model="config.address" class="gx-field" aria-label="Uniden detector"><option value="">Select a paired detector</option>
          <option v-for="d in detectors" :key="d.address" :value="d.address">{{ d.name }} · {{ d.address }}</option></select></label>
        <label class="gx-row">Model <select v-model="config.model" class="gx-field" aria-label="Detector model"><option>R4</option><option>R8</option></select></label>
        <label class="gx-row"><span>Show detector alerts</span><input type="checkbox" v-model="config.enabled" /></label>
        <button class="gx-btn" @click="save">{{ busy ? 'Saving…' : 'Save detector' }}</button>
      </fieldset>
      <p class="gx-note">{{ !parked ? 'Park to change detector setup. ' : '' }}{{ state?.connected ? 'Detector connected' : config.enabled ? 'Waiting for detector' : 'Detector alerts off' }}</p>
      <p class="gx-note">Alerts appear in the Uniden widget on Android Auto and comma 3X. R8 shows front, side and rear arrows; tap the card to mute. Select the Uniden widget in the theme maker to adjust transparency, hide when no alert, and position.</p>
      <p class="gx-note">R4/R8 firmware compatibility and touchscreen mute still need hardware validation. “Muted” appears only when reported by the detector.</p>
    </div></section>`,
}
