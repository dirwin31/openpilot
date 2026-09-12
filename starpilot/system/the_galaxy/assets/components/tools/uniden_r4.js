import { html, reactive } from "/assets/vendor/arrow-core.js"

const state = reactive({
  loading: true,
  status: {
    connected: false,
    name: "Uniden Radar Detector",
    mac: "",
    rssi: null,
    pairing_state: "idle",
    pairing_message: "",
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
  }
})

function notify(msg, level) {
  if (typeof window.showSnackbar === "function") {
    window.showSnackbar(msg, level)
  } else {
    console.log("[Snackbar]", level || "info", msg)
  }
}

async function loadData() {
  try {
    const [resStatus, resSettings] = await Promise.all([
      fetch("/api/uniden/status", { cache: "no-store" }),
      fetch("/api/uniden/settings", { cache: "no-store" })
    ])
    if (resStatus.ok) {
      const s = await resStatus.json()
      for (const [k, v] of Object.entries(s)) {
        state.status[k] = v
      }
      const ps = state.status.pairing_state || "idle"
      if (ps !== prevPairingState) {
        if (ps === "success") {
          notify(state.status.pairing_message || "Uniden R4 paired & bonded!")
        } else if (ps === "failed") {
          notify(state.status.pairing_message || "Pairing failed", "error")
        } else if (ps === "unreachable") {
          notify(state.status.pairing_message || "Detector bonded but not reachable", "warning")
        }
        prevPairingState = ps
      }
    }
    if (resSettings.ok) {
      const data = await resSettings.json()
      for (const [k, v] of Object.entries(data)) {
        state.settings[k] = v
      }
    }
  } catch (e) {
    console.error("Failed to load Uniden R4 data:", e)
  } finally {
    state.loading = false
  }
}

async function updateSetting(key, val) {
  state.settings[key] = val
  try {
    const res = await fetch("/api/uniden/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: val })
    })
    if (res.ok) {
      const data = await res.json()
      for (const [k, v] of Object.entries(data)) {
        state.settings[k] = v
      }
      notify(`Updated ${key}`)
    }
  } catch (e) {
    notify("Failed to update setting", "error")
  }
}

async function sendAction(action) {
  try {
    const res = await fetch(`/api/uniden/action/${action}`, { method: "POST" })
    const data = await res.json()
    notify(data.message || "Action sent")
    if (action === "pair" && data.status) {
      state.status.pairing_state = data.status
      state.status.pairing_message = data.message || ""
    }
    loadData()
  } catch (e) {
    notify(`Action failed: ${e.message}`, "error")
  }
}

let prevPairingState = "idle"

let loadedOnce = false

export function UnidenR4View() {
  if (!loadedOnce) {
    loadedOnce = true
    loadData()
    setInterval(loadData, 4000)
  }

  return html`
    <div class="uniden-container">
      <div class="uniden-header">
        <h1><i class="bi bi-broadcast"></i> Uniden R4/R8/R9 Radar Settings</h1>
        <div class="${() => `uniden-status-badge ${state.status.connected ? 'uniden-status-connected' : 'uniden-status-disconnected'}`}">
          <i class="${() => `bi ${state.status.connected ? 'bi-bluetooth' : 'bi-slash-circle'}`}"></i>
          <span>${() => state.status.connected ? `Connected (${state.status.rssi ? state.status.rssi + ' dBm' : 'BLE'})` : 'Disconnected'}</span>
        </div>
      </div>

      <div class="uniden-grid">
        <!-- Connection Card -->
        <div class="uniden-card">
          <h2 class="uniden-card-title"><i class="bi bi-link-45deg"></i> Device & Connection</h2>
          
          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Enable Radar Integration</span>
              <span class="uniden-setting-desc">Process BLE alerts from Uniden R4 / R8 / R9</span>
            </div>
            <label class="uniden-switch">
              <input type="checkbox" 
                     checked="${() => !!state.settings.UnidenR4Enabled}" 
                     @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenR4Enabled', el.checked); }}" />
              <span class="uniden-slider"></span>
            </label>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Paired Device</span>
              <span class="uniden-setting-desc">${() => state.status.mac ? `${state.status.name || 'Uniden Detector'} (${state.status.mac})` : 'No detector paired yet'}</span>
            </div>
          </div>

          <div class="uniden-actions">
            <button class="uniden-btn uniden-btn-warning" @click="${() => sendAction('mute')}">
              <i class="bi bi-volume-mute-fill"></i> Mute Current Alert
            </button>
            <button class="uniden-btn uniden-btn-primary" @click="${() => sendAction('connect')}">
              <i class="bi bi-arrow-repeat"></i> Reconnect
            </button>
          </div>

          ${() => {
            const ps = state.status.pairing_state || "idle"
            if (ps === "idle" || !state.status.pairing_message) return ""
            const cls = ps === "success" ? "uniden-pair-banner uniden-pair-success"
              : ps === "failed" ? "uniden-pair-banner uniden-pair-error"
              : ps === "unreachable" ? "uniden-pair-banner uniden-pair-warn"
              : "uniden-pair-banner uniden-pair-active"
            const icon = ps === "success" ? "bi-check-circle-fill"
              : ps === "failed" ? "bi-x-octagon-fill"
              : ps === "unreachable" ? "bi-exclamation-triangle-fill"
              : "bi-broadcast-pin"
            return html`
              <div class="${cls}">
                <i class="bi ${icon}"></i>
                <span>${() => state.status.pairing_message}</span>
              </div>
            `
          }}

          <div class="uniden-actions">
            <button class="uniden-btn uniden-btn-secondary" @click="${() => sendAction('pair')}" disabled="${() => ['searching', 'pairing', 'verifying'].includes(state.status.pairing_state)}">
              <i class="bi bi-bluetooth"></i> Scan & Pair Detector
            </button>
            ${() => state.status.mac ? html`
              <button class="uniden-btn uniden-btn-danger" @click="${() => { if (confirm('Forget this Uniden detector?')) sendAction('forget'); }}">
                <i class="bi bi-trash3"></i> Forget Device
              </button>
            ` : ''}
          </div>
        </div>

        <!-- Sensitivity & Sound Card -->
        <div class="uniden-card">
          <h2 class="uniden-card-title"><i class="bi bi-sliders"></i> Sensitivity & Audio</h2>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Detection Mode</span>
              <span class="uniden-setting-desc">Radar sensitivity profile</span>
            </div>
            <select class="uniden-select" 
                    value="${() => String(state.settings.UnidenR4Mode)}"
                    @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenR4Mode', el.value); }}">
              <option value="all_threat" selected="${() => state.settings.UnidenR4Mode === 'all_threat'}">All Threat</option>
              <option value="highway" selected="${() => state.settings.UnidenR4Mode === 'highway'}">Highway</option>
              <option value="city" selected="${() => state.settings.UnidenR4Mode === 'city'}">City</option>
              <option value="advanced" selected="${() => state.settings.UnidenR4Mode === 'advanced'}">Advanced</option>
            </select>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Main Volume</span>
              <span class="uniden-setting-desc">Alert speaker level (0-8)</span>
            </div>
            <div class="uniden-range-container">
              <input type="range" min="0" max="8" class="uniden-range" 
                     value="${() => state.settings.UnidenR4Volume}"
                     @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenR4Volume', parseInt(el.value)); }}" />
              <span class="uniden-range-val">${() => state.settings.UnidenR4Volume}</span>
            </div>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Auto Mute</span>
              <span class="uniden-setting-desc">Automatically reduce volume after initial alert beep</span>
            </div>
            <label class="uniden-switch">
              <input type="checkbox" 
                     checked="${() => !!state.settings.UnidenR4AutoMute}" 
                     @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenR4AutoMute', el.checked); }}" />
              <span class="uniden-slider"></span>
            </label>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Quiet Ride Speed</span>
              <span class="uniden-setting-desc">Mute all alerts below this speed (MPH)</span>
            </div>
            <div class="uniden-range-container">
              <input type="range" min="0" max="90" step="5" class="uniden-range"
                     value="${() => state.settings.UnidenR4QuietRideSpeed}"
                     @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenR4QuietRideSpeed', parseInt(el.value)); }}" />
              <span class="uniden-range-val">${() => `${state.settings.UnidenR4QuietRideSpeed} mph`}</span>
            </div>
          </div>
        </div>

        <!-- Radar Bands Card -->
        <div class="uniden-card">
          <h2 class="uniden-card-title"><i class="bi bi-reception-4"></i> Radar & Laser Bands</h2>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Ka Band</span>
              <span class="uniden-setting-desc">Police radar standard (33.4 - 36.0 GHz)</span>
            </div>
            <label class="uniden-switch">
              <input type="checkbox" 
                     checked="${() => !!state.settings.UnidenR4KaBand}" 
                     @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenR4KaBand', el.checked); }}" />
              <span class="uniden-slider"></span>
            </label>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">K Band</span>
              <span class="uniden-setting-desc">24.050 - 24.250 GHz</span>
            </div>
            <label class="uniden-switch">
              <input type="checkbox" 
                     checked="${() => !!state.settings.UnidenR4KBand}" 
                     @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenR4KBand', el.checked); }}" />
              <span class="uniden-slider"></span>
            </label>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Laser Detection</span>
              <span class="uniden-setting-desc">LIDAR optical alert</span>
            </div>
            <label class="uniden-switch">
              <input type="checkbox" 
                     checked="${() => !!state.settings.UnidenR4Laser}" 
                     @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenR4Laser', el.checked); }}" />
              <span class="uniden-slider"></span>
            </label>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">MRCD / MRCT</span>
              <span class="uniden-setting-desc">MultaRadar speed cameras</span>
            </div>
            <label class="uniden-switch">
              <input type="checkbox" 
                     checked="${() => !!state.settings.UnidenR4MRCD}" 
                     @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenR4MRCD', el.checked); }}" />
              <span class="uniden-slider"></span>
            </label>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">POP Mode</span>
              <span class="uniden-setting-desc">Super-fast pulse radar detection</span>
            </div>
            <label class="uniden-switch">
              <input type="checkbox" 
                     checked="${() => !!state.settings.UnidenR4POP}" 
                     @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenR4POP', el.checked); }}" />
              <span class="uniden-slider"></span>
            </label>
          </div>
        </div>

        <!-- Openpilot Cruise Integration Card -->
        <div class="uniden-card">
          <h2 class="uniden-card-title"><i class="bi bi-shield-shaded"></i> Openpilot Radar Slowdown</h2>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Auto-Slowdown on Radar Alert</span>
              <span class="uniden-setting-desc">Automatically drop cruise speed relative to the speed limit when police radar is detected</span>
            </div>
            <label class="uniden-switch">
              <input type="checkbox" 
                     checked="${() => !!state.settings.UnidenAutoSlowdown}" 
                     @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenAutoSlowdown', el.checked); }}" />
              <span class="uniden-slider"></span>
            </label>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Signal 1–2 Target Speed (Low Alert)</span>
              <span class="uniden-setting-desc">Cruise speed limit offset when distant or weak radar signal is detected</span>
            </div>
            <select class="uniden-select" 
                    value="${() => String(state.settings.UnidenSlowdownOffset1_2 !== undefined ? state.settings.UnidenSlowdownOffset1_2 : 14)}"
                    @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenSlowdownOffset1_2', parseInt(el.value, 10)); }}">
              <option value="14" selected="${() => Number(state.settings.UnidenSlowdownOffset1_2) === 14}">Speed Limit + 14 mph</option>
              <option value="9" selected="${() => Number(state.settings.UnidenSlowdownOffset1_2) === 9}">Speed Limit + 9 mph</option>
              <option value="5" selected="${() => Number(state.settings.UnidenSlowdownOffset1_2) === 5}">Speed Limit + 5 mph</option>
              <option value="-1" selected="${() => Number(state.settings.UnidenSlowdownOffset1_2) === -1}">Disabled (No Slowdown)</option>
            </select>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Signal 1–2 Alert Sound</span>
              <span class="uniden-setting-desc">Audio played by openpilot when Signal 1–2 radar alert is detected</span>
            </div>
            <div style="display: flex; gap: 8px; align-items: center;">
              <select class="uniden-select" 
                      value="${() => String(state.settings.UnidenSoundSignal1_2 || 'prompt.wav')}"
                      @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenSoundSignal1_2', el.value); }}">
                <option value="disabled" selected="${() => state.settings.UnidenSoundSignal1_2 === 'disabled'}">Disabled (Mute)</option>
                <option value="prompt.wav" selected="${() => state.settings.UnidenSoundSignal1_2 === 'prompt.wav'}">Prompt (Chime)</option>
                <option value="pre_alert.wav" selected="${() => state.settings.UnidenSoundSignal1_2 === 'pre_alert.wav'}">Pre-Alert (Double Beep)</option>
                <option value="warning_soft.wav" selected="${() => state.settings.UnidenSoundSignal1_2 === 'warning_soft.wav'}">Warning Soft (Soft Chime)</option>
                <option value="warning_immediate.wav" selected="${() => state.settings.UnidenSoundSignal1_2 === 'warning_immediate.wav'}">Warning Immediate (Urgent)</option>
                <option value="engage.wav" selected="${() => state.settings.UnidenSoundSignal1_2 === 'engage.wav'}">Engage Tone</option>
                <option value="disengage.wav" selected="${() => state.settings.UnidenSoundSignal1_2 === 'disengage.wav'}">Disengage Tone</option>
                <option value="refuse.wav" selected="${() => state.settings.UnidenSoundSignal1_2 === 'refuse.wav'}">Refuse Tone</option>
              </select>
              ${() => state.settings.UnidenSoundSignal1_2 && state.settings.UnidenSoundSignal1_2 !== 'disabled' ? html`
                <button class="uniden-btn uniden-btn-secondary" style="padding: 6px 10px; font-size: 0.85rem;" title="Test Sound" @click="${() => sendAction(`play_sound:${state.settings.UnidenSoundSignal1_2}`)}">
                  <i class="bi bi-play-fill"></i>
                </button>
              ` : ''}
            </div>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Signal 3–5 Target Speed (Medium Alert)</span>
              <span class="uniden-setting-desc">Cruise speed limit offset when approaching moderate radar signal</span>
            </div>
            <select class="uniden-select" 
                    value="${() => String(state.settings.UnidenSlowdownOffset3_5 !== undefined ? state.settings.UnidenSlowdownOffset3_5 : 9)}"
                    @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenSlowdownOffset3_5', parseInt(el.value, 10)); }}">
              <option value="14" selected="${() => Number(state.settings.UnidenSlowdownOffset3_5) === 14}">Speed Limit + 14 mph</option>
              <option value="9" selected="${() => Number(state.settings.UnidenSlowdownOffset3_5) === 9}">Speed Limit + 9 mph</option>
              <option value="5" selected="${() => Number(state.settings.UnidenSlowdownOffset3_5) === 5}">Speed Limit + 5 mph</option>
              <option value="-1" selected="${() => Number(state.settings.UnidenSlowdownOffset3_5) === -1}">Disabled (No Slowdown)</option>
            </select>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Signal 3–5 Alert Sound</span>
              <span class="uniden-setting-desc">Audio played by openpilot when Signal 3–5 radar alert is detected</span>
            </div>
            <div style="display: flex; gap: 8px; align-items: center;">
              <select class="uniden-select" 
                      value="${() => String(state.settings.UnidenSoundSignal3_5 || 'warning_soft.wav')}"
                      @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenSoundSignal3_5', el.value); }}">
                <option value="disabled" selected="${() => state.settings.UnidenSoundSignal3_5 === 'disabled'}">Disabled (Mute)</option>
                <option value="prompt.wav" selected="${() => state.settings.UnidenSoundSignal3_5 === 'prompt.wav'}">Prompt (Chime)</option>
                <option value="pre_alert.wav" selected="${() => state.settings.UnidenSoundSignal3_5 === 'pre_alert.wav'}">Pre-Alert (Double Beep)</option>
                <option value="warning_soft.wav" selected="${() => state.settings.UnidenSoundSignal3_5 === 'warning_soft.wav'}">Warning Soft (Soft Chime)</option>
                <option value="warning_immediate.wav" selected="${() => state.settings.UnidenSoundSignal3_5 === 'warning_immediate.wav'}">Warning Immediate (Urgent)</option>
                <option value="engage.wav" selected="${() => state.settings.UnidenSoundSignal3_5 === 'engage.wav'}">Engage Tone</option>
                <option value="disengage.wav" selected="${() => state.settings.UnidenSoundSignal3_5 === 'disengage.wav'}">Disengage Tone</option>
                <option value="refuse.wav" selected="${() => state.settings.UnidenSoundSignal3_5 === 'refuse.wav'}">Refuse Tone</option>
              </select>
              ${() => state.settings.UnidenSoundSignal3_5 && state.settings.UnidenSoundSignal3_5 !== 'disabled' ? html`
                <button class="uniden-btn uniden-btn-secondary" style="padding: 6px 10px; font-size: 0.85rem;" title="Test Sound" @click="${() => sendAction(`play_sound:${state.settings.UnidenSoundSignal3_5}`)}">
                  <i class="bi bi-play-fill"></i>
                </button>
              ` : ''}
            </div>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Signal 6–8 Target Speed (High Alert)</span>
              <span class="uniden-setting-desc">Cruise speed limit offset when close or strong radar signal is detected</span>
            </div>
            <select class="uniden-select" 
                    value="${() => String(state.settings.UnidenSlowdownOffset6_8 !== undefined ? state.settings.UnidenSlowdownOffset6_8 : 5)}"
                    @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenSlowdownOffset6_8', parseInt(el.value, 10)); }}">
              <option value="14" selected="${() => Number(state.settings.UnidenSlowdownOffset6_8) === 14}">Speed Limit + 14 mph</option>
              <option value="9" selected="${() => Number(state.settings.UnidenSlowdownOffset6_8) === 9}">Speed Limit + 9 mph</option>
              <option value="5" selected="${() => Number(state.settings.UnidenSlowdownOffset6_8) === 5}">Speed Limit + 5 mph</option>
              <option value="-1" selected="${() => Number(state.settings.UnidenSlowdownOffset6_8) === -1}">Disabled (No Slowdown)</option>
            </select>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Signal 6–8 Alert Sound</span>
              <span class="uniden-setting-desc">Audio played by openpilot when Signal 6–8 radar alert is detected</span>
            </div>
            <div style="display: flex; gap: 8px; align-items: center;">
              <select class="uniden-select" 
                      value="${() => String(state.settings.UnidenSoundSignal6_8 || 'warning_immediate.wav')}"
                      @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenSoundSignal6_8', el.value); }}">
                <option value="disabled" selected="${() => state.settings.UnidenSoundSignal6_8 === 'disabled'}">Disabled (Mute)</option>
                <option value="prompt.wav" selected="${() => state.settings.UnidenSoundSignal6_8 === 'prompt.wav'}">Prompt (Chime)</option>
                <option value="pre_alert.wav" selected="${() => state.settings.UnidenSoundSignal6_8 === 'pre_alert.wav'}">Pre-Alert (Double Beep)</option>
                <option value="warning_soft.wav" selected="${() => state.settings.UnidenSoundSignal6_8 === 'warning_soft.wav'}">Warning Soft (Soft Chime)</option>
                <option value="warning_immediate.wav" selected="${() => state.settings.UnidenSoundSignal6_8 === 'warning_immediate.wav'}">Warning Immediate (Urgent)</option>
                <option value="engage.wav" selected="${() => state.settings.UnidenSoundSignal6_8 === 'engage.wav'}">Engage Tone</option>
                <option value="disengage.wav" selected="${() => state.settings.UnidenSoundSignal6_8 === 'disengage.wav'}">Disengage Tone</option>
                <option value="refuse.wav" selected="${() => state.settings.UnidenSoundSignal6_8 === 'refuse.wav'}">Refuse Tone</option>
              </select>
              ${() => state.settings.UnidenSoundSignal6_8 && state.settings.UnidenSoundSignal6_8 !== 'disabled' ? html`
                <button class="uniden-btn uniden-btn-secondary" style="padding: 6px 10px; font-size: 0.85rem;" title="Test Sound" @click="${() => sendAction(`play_sound:${state.settings.UnidenSoundSignal6_8}`)}">
                  <i class="bi bi-play-fill"></i>
                </button>
              ` : ''}
            </div>
          </div>
        </div>

        <!-- Display & Memory Card -->
        <div class="uniden-card">
          <h2 class="uniden-card-title"><i class="bi bi-display"></i> Display & Memory</h2>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">OLED Brightness</span>
              <span class="uniden-setting-desc">R4 Screen display level</span>
            </div>
            <select class="uniden-select" 
                    value="${() => String(state.settings.UnidenR4Brightness)}"
                    @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenR4Brightness', el.value); }}">
              <option value="auto" selected="${() => state.settings.UnidenR4Brightness === 'auto'}">Auto</option>
              <option value="bright" selected="${() => state.settings.UnidenR4Brightness === 'bright'}">Bright</option>
              <option value="dim" selected="${() => state.settings.UnidenR4Brightness === 'dim'}">Dim</option>
              <option value="dimmer" selected="${() => state.settings.UnidenR4Brightness === 'dimmer'}">Dimmer</option>
              <option value="dark" selected="${() => state.settings.UnidenR4Brightness === 'dark'}">Dark</option>
              <option value="off" selected="${() => state.settings.UnidenR4Brightness === 'off'}">Off</option>
            </select>
          </div>

          <div class="uniden-setting-row">
            <div class="uniden-setting-info">
              <span class="uniden-setting-label">Mute Memory</span>
              <span class="uniden-setting-desc">Auto-lockout known stationary false alerts via GPS</span>
            </div>
            <label class="uniden-switch">
              <input type="checkbox" 
                     checked="${() => !!state.settings.UnidenR4MuteMemory}" 
                     @change="${(e) => { const el = e && (e.currentTarget || e.target); if (el) updateSetting('UnidenR4MuteMemory', el.checked); }}" />
              <span class="uniden-slider"></span>
            </label>
          </div>
        </div>
      </div>
    </div>
  `
}
