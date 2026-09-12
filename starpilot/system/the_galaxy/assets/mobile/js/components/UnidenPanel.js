import { api, showSnackbar } from "../api.js"
import { usePolling } from "../composables.js"
import { GxNotice } from "./GxNotice.js"

export const UnidenPanel = {
  name: "UnidenPanel",
  components: { GxNotice },
  data() {
    return {
      loading: true,
      busy: "",
      error: "",
      status: {
        connected: false,
        name: "Uniden Radar Detector",
        mac: "",
        rssi: null,
        pairing_state: "idle",
        pairing_message: "",
        trusted: false,
      },
      settings: {
        UnidenR4Enabled: true,
        UnidenR4Mode: "all_threat",
        UnidenR4AutoMute: true,
        UnidenR4QuietRideSpeed: 35,
        UnidenR4Volume: 5,
        UnidenR4Brightness: "auto",
        UnidenR4KBand: true,
        UnidenR4KaBand: true,
        UnidenR4Laser: true,
        UnidenR4MRCD: true,
        UnidenR4POP: false,
        UnidenR4MuteMemory: true,
        UnidenR4AlertVolume: 5,
        UnidenAutoSlowdown: true,
        UnidenSlowdownOffset1_2: 14,
        UnidenSlowdownOffset3_5: 9,
        UnidenSlowdownOffset6_8: 5,
        UnidenSoundSignal1_2: "prompt.wav",
        UnidenSoundSignal3_5: "warning_soft.wav",
        UnidenSoundSignal6_8: "warning_immediate.wav",
      },
      prevPairingState: "idle",
    }
  },
  created() {
    this.poll = usePolling(() => this.refresh(), { interval: 2500 })
    this.poll.start()
  },
  beforeUnmount() {
    this.poll?.destroy()
  },
  computed: {
    isPairingActive() {
      const state = this.status.pairing_state || "idle"
      return ["searching", "pairing", "verifying"].includes(state)
    },
    pairingBannerClass() {
      const state = this.status.pairing_state || "idle"
      if (state === "success") return "gx-banner gx-banner--success"
      if (state === "failed") return "gx-banner gx-banner--error"
      if (state === "unreachable") return "gx-banner gx-banner--warn"
      return "gx-banner gx-banner--active"
    },
    pairingBannerIcon() {
      const state = this.status.pairing_state || "idle"
      if (state === "success") return "bi-check-circle-fill"
      if (state === "failed") return "bi-x-octagon-fill"
      if (state === "unreachable") return "bi-exclamation-triangle-fill"
      return "bi-broadcast-pin"
    },
    statusLabel() {
      if (this.status.connected) {
        return this.status.rssi ? `Connected (${this.status.rssi} dBm)` : "Connected"
      }
      return "Disconnected"
    },
    pairedDeviceLabel() {
      if (this.status.mac) {
        return `${this.status.name || "Uniden Detector"} (${this.status.mac})`
      }
      return "No detector paired yet"
    },
  },
  methods: {
    async refresh() {
      try {
        const [resStatus, resSettings] = await Promise.all([
          api.getUnidenStatus(),
          api.getUnidenSettings(),
        ])
        if (resStatus) {
          Object.assign(this.status, resStatus)
          const ps = this.status.pairing_state || "idle"
          if (ps !== this.prevPairingState) {
            if (ps === "success") {
              showSnackbar(this.status.pairing_message || "Uniden R4 paired & bonded!")
            } else if (ps === "failed") {
              showSnackbar(this.status.pairing_message || "Pairing failed", "error")
            } else if (ps === "unreachable") {
              showSnackbar(this.status.pairing_message || "Detector bonded but not reachable", "warning")
            }
            this.prevPairingState = ps
          }
        }
        if (resSettings) {
          const settings = resSettings.settings || resSettings
          Object.assign(this.settings, settings)
        }
        this.error = ""
      } catch (e) {
        this.error = e?.message || "Failed to load radar detector data"
      } finally {
        this.loading = false
      }
    },
    async updateSetting(key, val) {
      this.settings[key] = val
      try {
        const resp = await api.updateUnidenSettings({ [key]: val })
        if (resp) {
          const updated = resp.settings || resp
          Object.assign(this.settings, updated)
        }
        showSnackbar(`Updated ${key}`)
      } catch (e) {
        showSnackbar(e?.message || "Failed to update setting", "error")
      }
    },
    formatOffset(val) {
      const n = parseInt(val, 10)
      if (isNaN(n) || n === -1) return "Disabled"
      return `${n >= 0 ? "+" : ""}${n} mph`
    },
    async sendAction(action) {
      if (this.busy) return
      this.busy = action
      try {
        const data = await api.unidenAction(action)
        showSnackbar(data?.message || "Action sent")
        if (action === "pair" && data?.status) {
          this.status.pairing_state = data.status
          this.status.pairing_message = data.message || ""
        }
        await this.refresh()
      } catch (e) {
        showSnackbar(e?.message || `Action ${action} failed`, "error")
      } finally {
        this.busy = ""
      }
    },
    confirmForget() {
      if (confirm("Forget this Uniden detector?")) {
        this.sendAction("forget")
      }
    },
  },
  template: `
    <div style="display:grid; gap:var(--sp-4);">
      <GxNotice v-if="error" tone="danger" :text="error" />

      <!-- Device & Connection Card -->
      <section class="gx-card" style="padding:var(--sp-4);">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:var(--sp-3); flex-wrap:wrap; gap:8px;">
          <div style="display:flex; align-items:center; gap:8px;">
            <i class="bi bi-broadcast" style="font-size:1.25rem; color:var(--primary);"></i>
            <span style="font-weight:var(--fw-bold); font-size:var(--fs-base);">Radar Detector Connection</span>
          </div>
          <span class="gx-status-pill">
            <span class="gx-status-dot" :class="status.connected ? 'online' : 'offline'"></span>
            {{ statusLabel }}
          </span>
        </div>

        <div v-if="status.pairing_state && status.pairing_state !== 'idle' && status.pairing_message"
             :class="pairingBannerClass">
          <i class="bi" :class="pairingBannerIcon"></i>
          <span>{{ status.pairing_message }}</span>
        </div>

        <div class="gx-row" style="margin-bottom:var(--sp-3);">
          <div class="gx-row__info">
            <span class="gx-row__label">Paired Detector</span>
            <span class="gx-row__desc">{{ pairedDeviceLabel }}</span>
          </div>
          <span v-if="status.connected && status.rssi" class="gx-chip">{{ status.rssi }} dBm</span>
        </div>

        <div class="gx-row" style="margin-bottom:var(--sp-3);">
          <div class="gx-row__info">
            <span class="gx-row__label">Enable Radar Integration</span>
            <span class="gx-row__desc">Process BLE alerts from Uniden R4</span>
          </div>
          <label class="gx-switch">
            <input type="checkbox" :checked="!!settings.UnidenR4Enabled"
              @change="updateSetting('UnidenR4Enabled', $event.target.checked)" />
            <span class="gx-switch__track"></span>
            <span class="gx-switch__thumb"></span>
          </label>
        </div>

        <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:var(--sp-2);">
          <button type="button" class="gx-btn gx-btn--primary"
            :disabled="isPairingActive || !!busy" @click="sendAction('pair')">
            <i class="bi bi-bluetooth"></i>
            <span>{{ isPairingActive ? 'Pairing...' : 'Scan & Pair' }}</span>
          </button>
          <button type="button" class="gx-btn gx-btn--tonal"
            :disabled="!!busy" @click="sendAction('connect')">
            <i class="bi bi-arrow-repeat"></i>
            <span>Reconnect</span>
          </button>
          <button type="button" class="gx-btn gx-btn--tonal"
            :disabled="!!busy" @click="sendAction('mute')">
            <i class="bi bi-volume-mute-fill"></i>
            <span>Mute Alert</span>
          </button>
          <button v-if="status.mac" type="button" class="gx-btn gx-btn--danger"
            :disabled="!!busy" @click="confirmForget">
            <i class="bi bi-trash3"></i>
            <span>Forget</span>
          </button>
        </div>
      </section>

      <!-- Sensitivity & Audio Settings -->
      <section class="gx-card" style="padding:var(--sp-4);">
        <div class="gx-section__header" style="margin-bottom:var(--sp-3);">
          <i class="bi bi-sliders"></i>
          <span class="gx-section__title">Sensitivity & Audio</span>
        </div>

        <div class="gx-row gx-row--stack" style="margin-bottom:var(--sp-3);">
          <div class="gx-row__info">
            <span class="gx-row__label">Detection Mode</span>
            <span class="gx-row__desc">Radar sensitivity profile</span>
          </div>
          <select class="gx-field" :value="String(settings.UnidenR4Mode || 'all_threat')"
            @change="updateSetting('UnidenR4Mode', $event.target.value)">
            <option value="all_threat">All Threat</option>
            <option value="highway">Highway</option>
            <option value="city">City</option>
            <option value="advanced">Advanced</option>
          </select>
        </div>

        <div class="gx-row gx-row--stack" style="margin-bottom:var(--sp-3);">
          <div class="gx-row__info">
            <span class="gx-row__label">Main Volume</span>
            <span class="gx-row__desc">Alert speaker level (0 - 8)</span>
          </div>
          <div class="gx-slider-row">
            <span class="gx-row__value" style="min-width:32px;">{{ settings.UnidenR4Volume }}</span>
            <input class="gx-slider" type="range" min="0" max="8" step="1"
              :value="settings.UnidenR4Volume"
              @change="updateSetting('UnidenR4Volume', parseInt($event.target.value))" />
          </div>
        </div>

        <div class="gx-row" style="margin-bottom:var(--sp-3);">
          <div class="gx-row__info">
            <span class="gx-row__label">Auto Mute</span>
            <span class="gx-row__desc">Automatically reduce volume after initial alert</span>
          </div>
          <label class="gx-switch">
            <input type="checkbox" :checked="!!settings.UnidenR4AutoMute"
              @change="updateSetting('UnidenR4AutoMute', $event.target.checked)" />
            <span class="gx-switch__track"></span>
            <span class="gx-switch__thumb"></span>
          </label>
        </div>

        <div class="gx-row gx-row--stack">
          <div class="gx-row__info">
            <span class="gx-row__label">Quiet Ride Speed</span>
            <span class="gx-row__desc">Mute all alerts below this speed</span>
          </div>
          <div class="gx-slider-row">
            <span class="gx-row__value" style="min-width:60px;">{{ settings.UnidenR4QuietRideSpeed }} mph</span>
            <input class="gx-slider" type="range" min="0" max="90" step="5"
              :value="settings.UnidenR4QuietRideSpeed"
              @change="updateSetting('UnidenR4QuietRideSpeed', parseInt($event.target.value))" />
          </div>
        </div>
      </section>

      <!-- Radar & Laser Bands -->
      <section class="gx-card" style="padding:var(--sp-4);">
        <div class="gx-section__header" style="margin-bottom:var(--sp-3);">
          <i class="bi bi-reception-4"></i>
          <span class="gx-section__title">Radar & Laser Bands</span>
        </div>

        <div class="gx-row" style="margin-bottom:var(--sp-2);">
          <div class="gx-row__info">
            <span class="gx-row__label">Ka Band</span>
            <span class="gx-row__desc">Police radar standard (33.4 - 36.0 GHz)</span>
          </div>
          <label class="gx-switch">
            <input type="checkbox" :checked="!!settings.UnidenR4KaBand"
              @change="updateSetting('UnidenR4KaBand', $event.target.checked)" />
            <span class="gx-switch__track"></span>
            <span class="gx-switch__thumb"></span>
          </label>
        </div>

        <div class="gx-row" style="margin-bottom:var(--sp-2);">
          <div class="gx-row__info">
            <span class="gx-row__label">K Band</span>
            <span class="gx-row__desc">24.050 - 24.250 GHz</span>
          </div>
          <label class="gx-switch">
            <input type="checkbox" :checked="!!settings.UnidenR4KBand"
              @change="updateSetting('UnidenR4KBand', $event.target.checked)" />
            <span class="gx-switch__track"></span>
            <span class="gx-switch__thumb"></span>
          </label>
        </div>

        <div class="gx-row" style="margin-bottom:var(--sp-2);">
          <div class="gx-row__info">
            <span class="gx-row__label">Laser Detection</span>
            <span class="gx-row__desc">LIDAR optical alert</span>
          </div>
          <label class="gx-switch">
            <input type="checkbox" :checked="!!settings.UnidenR4Laser"
              @change="updateSetting('UnidenR4Laser', $event.target.checked)" />
            <span class="gx-switch__track"></span>
            <span class="gx-switch__thumb"></span>
          </label>
        </div>

        <div class="gx-row" style="margin-bottom:var(--sp-2);">
          <div class="gx-row__info">
            <span class="gx-row__label">MRCD</span>
            <span class="gx-row__desc">Multaradar photo enforcement</span>
          </div>
          <label class="gx-switch">
            <input type="checkbox" :checked="!!settings.UnidenR4MRCD"
              @change="updateSetting('UnidenR4MRCD', $event.target.checked)" />
            <span class="gx-switch__track"></span>
            <span class="gx-switch__thumb"></span>
          </label>
        </div>

        <div class="gx-row">
          <div class="gx-row__info">
            <span class="gx-row__label">POP Mode</span>
            <span class="gx-row__desc">Brief radar pulse detection</span>
          </div>
          <label class="gx-switch">
            <input type="checkbox" :checked="!!settings.UnidenR4POP"
              @change="updateSetting('UnidenR4POP', $event.target.checked)" />
            <span class="gx-switch__track"></span>
            <span class="gx-switch__thumb"></span>
          </label>
        </div>
      </section>

      <!-- StarPilot Auto-Slowdown -->
      <section class="gx-card" style="padding:var(--sp-4);">
        <div class="gx-section__header" style="margin-bottom:var(--sp-3);">
          <i class="bi bi-shield-check"></i>
          <span class="gx-section__title">StarPilot Auto-Slowdown</span>
        </div>

        <div class="gx-row" style="margin-bottom:var(--sp-3);">
          <div class="gx-row__info">
            <span class="gx-row__label">Radar Auto Slowdown</span>
            <span class="gx-row__desc">Automatically adjust cruise speed to speed limit offsets when radar alerts are received</span>
          </div>
          <label class="gx-switch">
            <input type="checkbox" :checked="!!settings.UnidenAutoSlowdown"
              @change="updateSetting('UnidenAutoSlowdown', $event.target.checked)" />
            <span class="gx-switch__track"></span>
            <span class="gx-switch__thumb"></span>
          </label>
        </div>

        <div class="gx-row gx-row--stack" style="margin-bottom:var(--sp-3);">
          <div class="gx-row__info">
            <span class="gx-row__label">Weak Alert (1-2 Bars) Offset</span>
            <span class="gx-row__desc">Offset added to the road speed limit during weak radar alerts</span>
          </div>
          <div class="gx-slider-row">
            <span class="gx-row__value" style="min-width:70px;">{{ formatOffset(settings.UnidenSlowdownOffset1_2) }}</span>
            <input class="gx-slider" type="range" min="-1" max="30" step="1"
              :value="settings.UnidenSlowdownOffset1_2 !== undefined ? settings.UnidenSlowdownOffset1_2 : 14"
              @input="settings.UnidenSlowdownOffset1_2 = parseInt($event.target.value, 10)"
              @change="updateSetting('UnidenSlowdownOffset1_2', parseInt($event.target.value, 10))" />
          </div>
        </div>

        <div class="gx-row gx-row--stack" style="margin-bottom:var(--sp-3);">
          <div class="gx-row__info">
            <span class="gx-row__label">Medium Alert (3-5 Bars) Offset</span>
            <span class="gx-row__desc">Offset added to the road speed limit during medium radar alerts</span>
          </div>
          <div class="gx-slider-row">
            <span class="gx-row__value" style="min-width:70px;">{{ formatOffset(settings.UnidenSlowdownOffset3_5) }}</span>
            <input class="gx-slider" type="range" min="-1" max="30" step="1"
              :value="settings.UnidenSlowdownOffset3_5 !== undefined ? settings.UnidenSlowdownOffset3_5 : 9"
              @input="settings.UnidenSlowdownOffset3_5 = parseInt($event.target.value, 10)"
              @change="updateSetting('UnidenSlowdownOffset3_5', parseInt($event.target.value, 10))" />
          </div>
        </div>

        <div class="gx-row gx-row--stack">
          <div class="gx-row__info">
            <span class="gx-row__label">Strong Alert (6-8 Bars) Offset</span>
            <span class="gx-row__desc">Offset added to the road speed limit during strong radar alerts</span>
          </div>
          <div class="gx-slider-row">
            <span class="gx-row__value" style="min-width:70px;">{{ formatOffset(settings.UnidenSlowdownOffset6_8) }}</span>
            <input class="gx-slider" type="range" min="-1" max="30" step="1"
              :value="settings.UnidenSlowdownOffset6_8 !== undefined ? settings.UnidenSlowdownOffset6_8 : 5"
              @input="settings.UnidenSlowdownOffset6_8 = parseInt($event.target.value, 10)"
              @change="updateSetting('UnidenSlowdownOffset6_8', parseInt($event.target.value, 10))" />
          </div>
        </div>
      </section>
    </div>
  `,
}
