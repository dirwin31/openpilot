import { api, showSnackbar } from "../api.js"
import { usePolling } from "../composables.js"
import { bluetoothPlatform, isGalaxyLink, galaxyRoute } from "../browser.js"
import { getLiveBLEClient } from "../ble/live_ble.js"
import { GxNotice } from "./GxNotice.js"
import { BluetoothSupportNotice } from "./BluetoothSupportNotice.js"

// API exposure does not prove that Chrome's persistent permissions backend is on.
// Only a successful connection to a device saved before this page loaded does.
const supportsBluetoothRestore = () => typeof navigator.bluetooth?.getDevices === "function"
// watchAdvertisements() is what lets a restored device be found again after a
// reload, and Chrome keeps it behind the experimental features flag.
const supportsAdvertisementWatch = () => typeof globalThis.BluetoothDevice?.prototype?.watchAdvertisements === "function"

export const PhonePanel = {
  name: "PhonePanel",
  components: { GxNotice, BluetoothSupportNotice },
  data() {
    return {
      platform: bluetoothPlatform(),
      onGalaxyLink: isGalaxyLink(),
      galaxyURL: "",
      galaxyLinkError: false,
      galaxyLinkLoading: true,
      bluetoothRadio: "unknown",
      rememberedDevices: null,
      restoredAfterReload: false,
      offroad: false,
      enabled: false,
      pairingRemaining: 0,
      busy: "",
      pairMessage: "",
      error: "",
    }
  },
  created() {
    if (!this.onGalaxyLink) { void this.loadGalaxyLink(); return }
    if (this.platform !== "ready") return
    // Telematics shares this client, so a restore it already made is visible here.
    this.restoredAfterReload = !!getLiveBLEClient().restoredDeviceID
    this.poll = usePolling(() => this.refresh(), { interval: 1000 })
    this.poll.start()
    void this.refreshBrowserStatus()
  },
  beforeUnmount() { this.poll?.destroy() },
  computed: {
    secureURL() { return this.onGalaxyLink ? window.location.href : this.galaxyURL },
    canRestoreBluetooth() { return supportsBluetoothRestore() },
    canWatchAdvertisements() { return supportsAdvertisementWatch() },
    bluetoothFlagsReady() { return this.canRestoreBluetooth && this.canWatchAdvertisements },
    // Capability probes and observed restoration are separate from flag settings.
    bluetoothChecks() {
      const radio = {
        available: { value: "On", ok: true },
        unavailable: { value: "Off or blocked", ok: false, hint: "Turn on Bluetooth in Android settings, then reopen this page." },
        unknown: { value: "Unknown", hint: "Chrome cannot report the radio state. Tap Pair now to check." },
      }[this.bluetoothRadio]
      return [
        { label: "Bluetooth radio", ...radio },
        this.canRestoreBluetooth
          ? { label: "Saved device access", value: "Available", ok: true }
          : { label: "Saved device access", value: "Unavailable", ok: false, hint: "Enable both Chrome settings below." },
        this.canWatchAdvertisements
          ? { label: "Find device after reload", value: "Enabled", ok: true }
          : { label: "Find device after reload", value: "Unavailable", ok: false, hint: "Chrome needs a fresh Bluetooth signal to reconnect. Enable setting 2 below." },
        !this.canRestoreBluetooth
          ? { label: "Remembered device", value: "Setup needed" }
          : this.rememberedDevices > 0
            ? { label: "Remembered device", value: this.rememberedDevices === 1 ? "1 saved" : `${this.rememberedDevices} saved`, ok: true }
            : { label: "Remembered device", value: "None yet", ok: false, hint: "Enable both settings, then pair once below." },
        this.restoredAfterReload
          ? { label: "Reconnect after reload", value: "Verified", ok: true }
          : { label: "Reconnect after reload", value: "Not verified", hint: "After pairing, reload Telematics near the comma. Verified means the saved device reconnected." },
      ]
    },
    bluetoothSetupReady() { return this.bluetoothChecks.every((check) => check.ok) },
  },
  methods: {
    async loadGalaxyLink() {
      this.galaxyLinkError = false
      this.galaxyLinkLoading = true
      try {
        const status = await api.getGalaxyStatus()
        this.galaxyURL = status?.paired ? galaxyRoute(status.url, "/bluetooth/phone") : ""
      } catch { this.galaxyLinkError = true }
      finally { this.galaxyLinkLoading = false }
    },
    setupGalaxy() { window.location.hash = "/galaxy" },
    openTelematics() { window.location.hash = "/telematics" },
    async refresh() {
      try {
        const p = await api.getBluetoothStatus()
        this.offroad = !!p.offroad
        this.enabled = !!p.enabled
        this.pairingRemaining = Number(p.companion_pairing_remaining) || 0
      } catch (e) {
        this.pairingRemaining = 0
      }
    },
    async refreshBrowserStatus() {
      try {
        const available = await navigator.bluetooth?.getAvailability?.()
        this.bluetoothRadio = available === undefined ? "unknown" : available ? "available" : "unavailable"
      } catch (error) { this.bluetoothRadio = "unknown" }
      try {
        this.rememberedDevices = supportsBluetoothRestore() ? (await navigator.bluetooth.getDevices()).length : null
      } catch (error) { this.rememberedDevices = null }
    },
    async openPairingWindow() {
      if (this.busy) return
      this.busy = "window"
      try {
        await api.bluetoothOp("companion_pair")
        this.error = ""
        await this.refresh()
      } catch (e) {
        this.error = e?.message || "Unable to open the pairing window"
      } finally {
        this.busy = ""
      }
    },
    // The chooser must open inside this click's activation, so nothing is awaited
    // before connect(). A connection proves the bond; it is closed right after,
    // and Telematics reuses the chosen device.
    async pair() {
      if (this.busy) return
      this.busy = "pair"
      this.pairMessage = ""
      const client = getLiveBLEClient()
      try {
        await client.connect()
      } catch (error) { /* The client reports failures through its state. */ }
      if (client.state === "connected") {
        this.pairMessage = `Paired with ${client.device?.name || "the comma"}. Telematics will connect over Bluetooth.`
        this.error = ""
        client.disconnect()
      } else if (client.state !== "idle") {
        this.error = client.message
      }
      this.busy = ""
      await this.refreshBrowserStatus()
    },
    async copyBluetoothSetting(flag) {
      try {
        await navigator.clipboard.writeText(`chrome://flags/#${flag}`)
        showSnackbar("Copied. Paste into Chrome's address bar.")
      } catch (error) {
        showSnackbar("Unable to copy. Select and copy the address shown below the setting.", "error")
      }
    },
  },
  template: `
    <div style="padding: var(--sp-3);">
      <BluetoothSupportNotice v-if="platform === 'ios' || (onGalaxyLink && ['firefox', 'unsupported'].includes(platform))" :platform="platform" :secure-url="secureURL" />

      <div v-else-if="!onGalaxyLink || platform === 'insecure'" class="telematics-gate">
        <GxNotice tone="info" icon="bi-bluetooth" title="Use Bluetooth in Galaxy">
          Use this device page for Wi-Fi Telematics. Pair your phone through your Galaxy link for Bluetooth and automatic offline saving.
        </GxNotice>
        <a v-if="galaxyURL" class="gx-btn gx-btn--block telematics-gate__open" :href="galaxyURL">Use Bluetooth in Galaxy</a>
        <button v-else-if="galaxyLinkLoading" class="gx-btn gx-btn--outlined" disabled>Checking Galaxy link…</button>
        <button v-else-if="galaxyLinkError" class="gx-btn gx-btn--outlined" @click="loadGalaxyLink">Retry Galaxy link</button>
        <button v-else class="gx-btn gx-btn--outlined" @click="setupGalaxy">Set up Galaxy remote access</button>
        <ol class="telematics-gate__steps">
          <li>Open your Galaxy link in Chrome on Android while connected to the internet.</li>
          <li>Follow the Chrome settings and pairing steps on the Phone tab.</li>
          <li>Open Telematics and wait for “Available without internet”. Install Galaxy to return from your home screen.</li>
        </ol>
      </div>

      <div v-else class="telematics-bluetooth-setup">
        <p class="telematics-setup__heading">Pair a phone</p>
        <GxNotice tone="warn" icon="bi-phone-fill" title="Do not pair from Android's Bluetooth settings">
          Start pairing here. Accept Android’s pairing prompt when it appears.
        </GxNotice>

        <ol class="telematics-pair-steps">
          <li>
            <strong>Set up Chrome once</strong>
            <p>Turn on your phone’s Bluetooth. Copy each address into Chrome, choose <b>Enabled</b>, then <b>Relaunch</b> and return here.</p>
            <div class="telematics-pair-setting">
              <span>Web Bluetooth new permissions backend</span>
              <code>chrome://flags/#enable-web-bluetooth-new-permissions-backend</code>
              <button class="gx-btn gx-btn--outlined" type="button" @click="copyBluetoothSetting('enable-web-bluetooth-new-permissions-backend')">Copy first address</button>
            </div>
            <div class="telematics-pair-setting">
              <span>Experimental Web Platform features</span>
              <code>chrome://flags/#enable-experimental-web-platform-features</code>
              <button class="gx-btn gx-btn--outlined" type="button" @click="copyBluetoothSetting('enable-experimental-web-platform-features')">Copy second address</button>
            </div>
            <p class="telematics-check__hint">Already enabled both? Go to step 2.</p>
          </li>
          <li>
            <strong>Pair your comma</strong>
            <p>While parked near your comma, open its pairing window, then tap <b>Pair now</b>. Choose your comma and accept the pairing prompt.</p>
            <div class="telematics-pair-actions">
              <button v-if="pairingRemaining > 0" class="gx-btn gx-btn--outlined" type="button" disabled>Pairing window open · {{ pairingRemaining }}s</button>
              <button v-else class="gx-btn gx-btn--outlined" type="button" :disabled="!offroad || !enabled || !!busy" @click="openPairingWindow">Open pairing window</button>
              <button class="gx-btn" type="button" :disabled="!!busy" @click="pair">{{ busy === 'pair' ? 'Pairing…' : 'Pair now' }}</button>
            </div>
            <p class="telematics-check__hint">The comma is discoverable for 120 seconds.</p>
            <p v-if="!offroad" class="telematics-check__hint">Park before opening the pairing window.</p>
            <p v-else-if="!enabled" class="telematics-check__hint">Turn Bluetooth on in the Bluetooth tab first.</p>
            <p v-if="pairMessage" class="telematics-setup-done" role="status"><i class="bi bi-check-circle-fill"></i> {{ pairMessage }}</p>
            <GxNotice v-if="error" tone="danger" icon="bi-exclamation-circle-fill" :text="error" />
          </li>
          <li>
            <strong>Open Telematics and check reconnect</strong>
            <p>Connect, then reload near your comma to check it reconnects. Wait for <b>Available without internet</b> before using Galaxy offline.</p>
            <button class="gx-btn" type="button" @click="openTelematics">Open Telematics</button>
            <p v-if="bluetoothSetupReady" class="telematics-setup-done"><i class="bi bi-check-circle-fill"></i> Reconnect verified.</p>
          </li>
        </ol>

        <p class="telematics-setup__heading">Connection checks</p>
        <ul class="telematics-checks">
          <li v-for="check in bluetoothChecks" :key="check.label" class="telematics-check"
            :class="check.ok ? 'telematics-check--ok' : check.ok === false ? 'telematics-check--bad' : 'telematics-check--unknown'">
            <i class="bi" :class="check.ok ? 'bi-check-circle-fill' : check.ok === false ? 'bi-x-circle-fill' : 'bi-dash-circle-fill'"></i>
            <div class="telematics-check__body">
              <div class="telematics-check__row">
              <span class="telematics-check__label">{{ check.label }}</span>
              <span class="telematics-check__value">{{ check.value }}</span>
              </div>
              <p v-if="check.hint" class="telematics-check__hint">{{ check.hint }}</p>
            </div>
          </li>
        </ul>

        <p class="telematics-check__hint">These checks show available features. Reload Telematics to verify the saved pairing.</p>
        <p class="telematics-check__hint"><strong class="telematics-inline">Can’t find your comma?</strong> Open the pairing window again. If you paired in Android settings, remove the pairing below and retry.</p>

        <details class="telematics-setup__more">
          <summary>Remove an old pairing</summary>
          <p>Remove it from both places, then repeat step 2.</p>
          <ol>
            <li><strong class="telematics-inline">Chrome:</strong> address-bar icon → Permissions → Bluetooth devices → remove the comma. If needed, use Reset permissions.</li>
            <li><strong class="telematics-inline">Android:</strong> Settings → Connected devices → comma → Forget.</li>
          </ol>
        </details>
        <p v-if="!bluetoothFlagsReady" class="telematics-setup__fallback"><strong class="telematics-inline">Reconnect is not ready.</strong> Complete step 1 to reconnect after a reload. You can still pair now.</p>
      </div>
    </div>
  `,
}
