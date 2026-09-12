import { api, showSnackbar } from "../api.js"
import { usePolling } from "../composables.js"
import { bluetoothPlatform } from "../browser.js"
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
    if (this.platform !== "ready") return
    // Telematics shares this client, so a restore it already made is visible here.
    this.restoredAfterReload = !!getLiveBLEClient().restoredDeviceID
    this.poll = usePolling(() => this.refresh(), { interval: 1000 })
    this.poll.start()
    void this.refreshBrowserStatus()
  },
  beforeUnmount() { this.poll?.destroy() },
  computed: {
    secureURL() {
      const target = new URL(window.location.href)
      if (target.protocol === "https:") return target.toString()
      target.protocol = "https:"
      target.port = "8443"
      return target.toString()
    },
    secureHost() {
      try { return new URL(this.secureURL).hostname } catch (error) { return "this device" }
    },
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
      <BluetoothSupportNotice v-if="['ios', 'firefox', 'unsupported'].includes(platform)" :platform="platform" :secure-url="secureURL" />

      <div v-else-if="platform === 'insecure'" class="telematics-gate">
        <GxNotice tone="warn" icon="bi-shield-lock-fill" title="Bluetooth pairing needs the HTTPS page">
          Web Bluetooth only works on a secure page. Galaxy serves one on port 8443.
        </GxNotice>

        <a class="gx-btn gx-btn--block telematics-gate__open" :href="secureURL" rel="noopener">
          <i class="bi bi-box-arrow-up-right"></i> Open the secure page
        </a>
        <p class="telematics-gate__url"><code>{{ secureURL }}</code></p>

        <ol class="telematics-gate__steps">
          <li>Chrome warns <strong>&ldquo;Your connection is not private&rdquo;</strong>. Expected — keep going.</li>
          <li>Tap <strong>Advanced</strong>, then <strong>Proceed to {{ secureHost }} (unsafe)</strong>.</li>
          <li>You return to this tab. Follow the pairing steps there.</li>
        </ol>
        <p class="telematics-gate__once">You do this once per phone.</p>

        <details class="telematics-gate__more">
          <summary>Why does Chrome call it unsafe?</summary>
          <p>Chrome shows <code>NET::ERR_CERT_AUTHORITY_INVALID</code> because the certificate is generated on your device and signed by the device itself, so no public authority vouches for it. Traffic is still encrypted and never leaves your local network.</p>
        </details>
        <details class="telematics-gate__more">
          <summary>Warning keeps coming back?</summary>
          <p>Chrome remembers the exception per address. Reach the device by name — <code>https://starpilot-&lt;device&gt;.local:8443</code> — so a new DHCP lease does not undo it.</p>
        </details>
      </div>

      <div v-else class="telematics-bluetooth-setup">
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

        <p v-if="bluetoothSetupReady" class="telematics-setup-done">
          <i class="bi bi-check-circle-fill"></i> Your saved comma reconnected after a reload.
        </p>

        <details class="telematics-setup__more" :open="!bluetoothFlagsReady || !restoredAfterReload">
          <summary>Chrome settings for reconnect</summary>
          <p>Enable both settings in Chrome. This page checks Bluetooth features, but cannot read or change the flags. Available features alone do not prove the pairing is saved.</p>
          <ol>
            <li>
              <strong>Web Bluetooth new permissions backend</strong>
              <code>chrome://flags/#enable-web-bluetooth-new-permissions-backend</code>
              <button class="gx-btn gx-btn--outlined" type="button" @click="copyBluetoothSetting('enable-web-bluetooth-new-permissions-backend')">Copy address</button>
              <p class="telematics-check__hint">Saves the device so you do not have to pick it after every reload.</p>
            </li>
            <li>
              <strong>Experimental Web Platform features</strong>
              <code>chrome://flags/#enable-experimental-web-platform-features</code>
              <button class="gx-btn gx-btn--outlined" type="button" @click="copyBluetoothSetting('enable-experimental-web-platform-features')">Copy address</button>
              <p class="telematics-check__hint">Finds the saved device after a reload. Without a fresh signal, Chrome may report it as out of range.</p>
            </li>
          </ol>
          <p>Copy each address into Chrome's address bar. Set both to <strong class="telematics-inline">Enabled</strong>, then <strong class="telematics-inline">Relaunch</strong> Chrome and return here. Pair once, then reload Telematics near the comma to verify reconnect.</p>
        </details>

        <p class="telematics-setup__heading">Pairing a phone</p>
        <ol>
          <li>
            While parked, open the comma's <strong class="telematics-inline">discoverable / 120s</strong> pairing window. New phones can pair only during the countdown.
            <div>
              <button v-if="pairingRemaining > 0" class="gx-btn gx-btn--outlined" type="button" disabled>Pairing window open · {{ pairingRemaining }}s</button>
              <button v-else class="gx-btn gx-btn--outlined" type="button" :disabled="!offroad || !enabled || !!busy" @click="openPairingWindow">Open pairing window</button>
            </div>
            <p v-if="!offroad" class="telematics-check__hint">Available offroad only.</p>
            <p v-else-if="!enabled" class="telematics-check__hint">Turn Bluetooth on in the Bluetooth tab first.</p>
          </li>
          <li>
            Tap <strong class="telematics-inline">Pair now</strong>, then pick the comma from Chrome's list.
            <div><button class="gx-btn" type="button" :disabled="!!busy" @click="pair">{{ busy === 'pair' ? 'Pairing…' : 'Pair now' }}</button></div>
          </li>
          <li>Accept Android's pairing prompt if one appears.</li>
        </ol>
        <p v-if="pairMessage" class="telematics-setup-done" role="status"><i class="bi bi-check-circle-fill"></i> {{ pairMessage }}</p>
        <GxNotice v-if="error" tone="danger" icon="bi-exclamation-circle-fill" :text="error" />
        <GxNotice tone="warn" icon="bi-phone-fill" title="Do not pair from Android's Bluetooth settings">
          The comma may appear there during the countdown. Pairing there only creates a system bond, without granting this page access, and can block Chrome pairing. Pair here; use Android settings only to forget the device.
        </GxNotice>
        <p class="telematics-check__hint"><strong class="telematics-inline">No device or pairing failed?</strong> Open the 120-second pairing window again. If you paired in Android settings, forget the device in both Chrome and Android first.</p>

        <details v-if="rememberedDevices > 0" class="telematics-setup__more">
          <summary>Make Chrome forget this device</summary>
          <p><strong class="telematics-inline">Disconnect</strong> on Telematics ends the connection; it does not forget the device. To remove the saved pairing:</p>
          <ol>
            <li><strong class="telematics-inline">Chrome:</strong> address-bar icon &rarr; <strong class="telematics-inline">Permissions &rarr; Bluetooth devices</strong> &rarr; remove the comma. Labels vary by Chrome version. <strong class="telematics-inline">Reset permissions</strong> also works; you may need to accept the certificate warning again.</li>
            <li><strong class="telematics-inline">In Android</strong> open <strong class="telematics-inline">Settings &rarr; Connected devices</strong>, tap the gear beside the device, then <strong class="telematics-inline">Forget</strong>.</li>
          </ol>
          <p>Clear both: removing Chrome's permission leaves the Android pairing, which can block pairing again.</p>
        </details>
        <p v-if="!bluetoothFlagsReady" class="telematics-setup__fallback"><strong class="telematics-inline">Reconnect is not ready.</strong> If you cannot enable the settings, you can still pair now. After a reload, you may need to pair again.</p>
      </div>
    </div>
  `,
}
