import { api } from "../api.js"
import { usePolling } from "../composables.js"
import { GxNotice } from "./GxNotice.js"

function address(device) { return String(device.address || "").toUpperCase() }

export const BluetoothPanel = {
  name: "BluetoothPanel",
  components: { GxNotice },
  props: { detectorOnly: { type: Boolean, default: false } },
  data() {
    return {
      loading: true, busy: "", available: false, enabled: false, powered: false, discovering: false,
      offroad: false, setupAllowed: false, selectedAudio: "", pairingAddress: "", devices: [], prompt: null, pairValue: "", error: "", operationError: "",
    }
  },
  created() { this.poll = usePolling(() => this.refresh(), { interval: 2000 }); this.poll.start() },
  beforeUnmount() { this.poll?.destroy() },
  computed: {
    visibleDevices() { return this.detectorOnly ? this.devices.filter((d) => d.uniden) : this.devices },
    known() { return this.visibleDevices.filter((d) => d.paired || d.trusted || d.connected) },
    availableDevices() { return this.visibleDevices.filter((d) => !d.paired && !d.trusted && !d.connected) },
  },
  methods: {
    address,
    async refresh() {
      try {
        const p = await api.getBluetoothStatus()
        this.available = !!p.available
        this.enabled = !!p.enabled
        this.powered = !!p.powered
        this.discovering = !!p.discovering
        this.offroad = !!p.offroad
        this.setupAllowed = !!(p.setup_allowed ?? p.offroad)
        this.selectedAudio = String(p.selected_audio || "")
        this.pairingAddress = String(p.pairing_address || "")
        this.devices = Array.isArray(p.devices) ? p.devices : []
        if (this.prompt?.id !== p.prompt?.id) this.pairValue = ""
        this.prompt = p.prompt || null
        this.error = p.error || ""
      } catch (e) {
        this.available = false
        this.offroad = false
        this.setupAllowed = false
        this.devices = []
        this.prompt = null
        this.error = e?.message || "Bluetooth service unavailable"
      } finally {
        this.loading = false
      }
    },
    async request(operation, body = {}) {
      if (this.busy || !this.available) return
      this.busy = operation
      this.operationError = ""
      try {
        await api.bluetoothOp(operation, body)
        this.error = ""
        await this.refresh()
      } catch (e) {
        this.operationError = e?.message || "Bluetooth operation failed"
      } finally {
        this.busy = ""
      }
    },
    pair(d) { this.request("pair", { address: d.address }) },
    connect(d) { this.request(d.connected ? "disconnect" : "connect", { address: d.address }) },
    forget(d) { this.request("forget", { address: d.address }) },
    audio(d) { const isSel = this.selectedAudio.toUpperCase() === address(d); this.request("select_audio", { address: isSel ? "" : d.address }) },
    testAudio(d) { this.request("test_audio", { address: d.address }) },
    respondPairing(accepted) {
      const prompt = this.prompt
      if (!prompt || this.busy === "pairing_response") return
      if (accepted && (prompt.kind === "pin" || prompt.kind === "passkey") && !this.pairValue.trim()) {
        this.error = "Enter the value to continue pairing."
        return
      }
      this.request("pairing_response", { prompt_id: prompt.id, accepted, value: this.pairValue.trim() })
    },
    isPairing(d) { return !!this.pairingAddress && this.pairingAddress.toUpperCase() === address(d) },
    statusOf(d) {
      if (this.isPairing(d)) return "Pairing…"
      if (d.uniden && d.connected) return d.services_resolved ? "Connected · Services ready" : "Connected · Discovering services…"
      if (d.connected) {
        const audioSel = this.selectedAudio.toUpperCase() === address(d)
        return audioSel ? "Connected · Audio output" : "Connected"
      }
      return d.paired ? "Paired · Disconnected" : d.trusted ? "Trusted · Pairing required" : "Ready to pair"
    },
    setupDisabled() { return !this.available || !this.setupAllowed || !!this.busy },
    offroadDisabled() { return !this.available || !this.offroad || !!this.busy },
    needsPairValue() { return this.prompt && (this.prompt.kind === "pin" || this.prompt.kind === "passkey") },
  },
  template: `
    <div>
      <div style="padding: var(--sp-3);">
        <div v-if="detectorOnly" style="margin-bottom:var(--sp-3);">
          <p>Pair your detector directly with the comma. This page uses the comma’s Bluetooth radio.</p>
          <ol>
            <li>On the detector, enable Bluetooth (BT/WiFi on some models), then select BT Pairing with the Menu key.</li>
            <li>Enable Bluetooth below, search, and select your detector’s name and address.</li>
            <li>Wait for pairing to finish and the connection to show Services ready.</li>
          </ol>
          <p class="gx-row__desc">If it is missing, disconnect any phone app using the detector and enter BT Pairing again.
            Device names identify candidates; model and firmware compatibility still require a successful connection.</p>

          <a href="https://support.uniden.com/support/solutions/articles/153000224590-r-tach-application-start-up-guide-r4w-r8w-r9w-and-non-w" target="_blank" rel="noopener noreferrer">Uniden pairing guide</a>
        </div>
        <div style="display:flex; align-items:center; gap:12px; justify-content:space-between;">
          <span>Bluetooth {{ enabled ? 'On' : 'Off' }}</span>
          <button type="button" class="gx-btn gx-btn--tonal" :disabled="!available || setupDisabled()" @click="request('power', { enabled: !enabled })">{{ enabled ? 'Turn Off' : 'Turn On' }}</button>
        </div>
        <GxNotice v-if="!available && !loading" text="Bluetooth service unavailable. Check the comma connection and refresh." />
        <GxNotice v-if="!setupAllowed" text="Bluetooth setup is available offroad or while stationary in Park. Keep the ignition on to power your detector." style="margin:0 0 var(--sp-2);" />
        <GxNotice v-if="operationError || error" tone="danger" :text="operationError || error" style="margin:0 0 var(--sp-2);" />

        <div v-if="prompt" class="gx-card" style="margin:12px 0; background:var(--surface-variant);">
          <div class="gx-section__header"><i class="bi bi-shield-check"></i><span class="gx-section__title">Pairing request · {{ prompt.name }}</span></div>
          <div style="padding: var(--sp-3);">
            <p style="color:var(--text-muted);">{{ prompt.display_only ? 'Enter this value on the other device if requested.' : prompt.kind === 'confirmation' ? 'Confirm the pairing request.' : prompt.kind === 'authorization' ? 'Allow this device to connect?' : prompt.kind === 'pin' ? 'Enter the PIN supplied by the device.' : 'Enter the device passkey.' }}</p>
            <p v-if="prompt.value" style="font-size:1.5em; font-variant-numeric:tabular-nums;">{{ prompt.value }}</p>
            <input v-if="needsPairValue() && !prompt.display_only" v-model="pairValue" class="gx-field" style="width:100%;" inputmode="numeric" placeholder="Value" />
            <div v-if="!prompt.display_only" style="display:flex; gap:8px; margin-top:8px;">
              <button type="button" class="gx-btn gx-btn--tonal" :disabled="setupDisabled()" @click="respondPairing(false)">Cancel</button>
              <button type="button" class="gx-btn" :disabled="setupDisabled()" @click="respondPairing(true)">Allow</button>
            </div>
          </div>
        </div>

        <div style="display:flex; gap:8px; margin:12px 0;">
          <button type="button" class="gx-btn" :disabled="!enabled || setupDisabled()" @click="request(discovering ? 'stop_scan' : 'scan')">{{ discovering ? 'Stop Search' : detectorOnly ? 'Search for Detectors' : 'Search for Devices' }}</button>
          <button type="button" class="gx-btn gx-btn--tonal" :disabled="!!busy" @click="refresh"><i class="bi bi-arrow-clockwise"></i> Refresh</button>
        </div>

        <p v-if="detectorOnly" class="gx-row__desc">Bluetooth power applies to all devices. Saved detectors reconnect automatically while Bluetooth is on and the car is on. Automatic searches pause when the car is off. Disconnect pauses retries for 5 minutes; Connect resumes immediately. Forget removes the saved pairing.</p>
        <h4 style="margin:12px 0 8px;">{{ detectorOnly ? 'My Detectors' : 'My Devices' }}</h4>
        <div v-if="!known.length" class="gx-empty" style="padding: var(--sp-2) 0;">No saved devices yet.</div>
        <div v-for="d in known" :key="d.address" class="gx-row" style="flex-wrap:wrap;">
          <div class="gx-row__info">
            <span class="gx-row__label">{{ d.name }} <span v-if="d.connected" class="gx-chip gx-chip--dev">Connected</span></span>
            <span class="gx-row__desc">{{ d.uniden ? 'Radar detector' : d.audio && d.controller ? 'Audio · Controller' : d.audio ? 'Audio' : d.controller ? 'Controller' : 'Bluetooth' }} · {{ statusOf(d) }}</span>
          </div>
          <div v-if="detectorOnly" class="gx-row__desc" style="width:100%;">{{ d.address }} · {{ d.paired ? 'Paired' : 'Not paired' }} · {{ d.trusted ? 'Trusted' : 'Not trusted' }}<span v-if="d.rssi != null"> · Last signal {{ d.rssi }} dBm</span></div>
          <div style="display:flex; gap:6px; flex-wrap:wrap;">
            <button v-if="!d.paired" type="button" class="gx-btn" :disabled="setupDisabled() || !enabled || !!pairingAddress" @click="pair(d)">{{ isPairing(d) ? 'Pairing…' : 'Pair' }}</button>
            <button v-if="d.paired || d.connected" type="button" class="gx-btn gx-btn--tonal" :disabled="!available || !enabled || !!busy || !!pairingAddress" @click="connect(d)">{{ d.connected ? 'Disconnect' : 'Connect' }}</button>
            <button v-if="d.audio" type="button" class="gx-btn gx-btn--tonal" :disabled="!!busy" @click="audio(d)">{{ selectedAudio.toUpperCase() === address(d) ? 'Stop Using for Audio' : 'Use for Audio' }}</button>
            <button v-if="d.audio && d.connected" type="button" class="gx-btn gx-btn--tonal" :disabled="offroadDisabled()" @click="testAudio(d)">Test Audio</button>
            <button v-if="d.paired || d.trusted || d.connected" type="button" class="gx-btn gx-btn--danger" :disabled="setupDisabled()" :aria-label="'Forget ' + d.name" @click="forget(d)">Forget</button>
          </div>
        </div>

        <h4 style="margin:12px 0 8px;">{{ detectorOnly ? 'Available Detectors' : 'Available Devices' }}</h4>
        <div v-if="!availableDevices.length" class="gx-empty" style="padding: var(--sp-2) 0;">{{ discovering ? 'Searching for nearby devices…' : 'No nearby devices found.' }}</div>
        <div v-for="d in availableDevices" :key="d.address" class="gx-row">
          <div class="gx-row__info">
            <span class="gx-row__label">{{ d.name }}</span>
            <span class="gx-row__desc">{{ statusOf(d) }}<template v-if="detectorOnly"> · {{ d.address }}<span v-if="d.rssi != null"> · Last signal {{ d.rssi }} dBm</span></template></span>
          </div>
          <button type="button" class="gx-btn" :disabled="setupDisabled() || !enabled || !!pairingAddress" @click="pair(d)">{{ isPairing(d) ? 'Pairing…' : 'Pair' }}</button>
        </div>
      </div>
    </div>
  `,
}
