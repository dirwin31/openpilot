import { html, reactive } from "/assets/vendor/arrow-core.js"

const state = reactive({
  loading: true,
  busy: "",
  available: false,
  enabled: false,
  powered: false,
  discovering: false,
  offroad: false,
  selectedAudio: "",
  pairingAddress: "",
  companionPairing: false,
  companionPairingRemaining: 0,
  companionConnected: false,
  companionDevices: [],
  devices: [],
  prompt: null,
  audioTestAddress: "",
  audioTestLabel: "",
  operationError: "",
  statusError: "",
})

let initialized = false
let lastPromptId = ""
let audioTestTimer = null

function startAudioTestCountdown(address, delayMs, requestStartedAt) {
  if (audioTestTimer !== null) clearInterval(audioTestTimer)
  const halfRoundTripMs = Math.max(0, (performance.now() - requestStartedAt) / 2)
  const deadline = performance.now() + Math.max(0, delayMs - halfRoundTripMs)
  state.audioTestAddress = address

  const update = () => {
    const remaining = deadline - performance.now()
    if (remaining > 0) {
      state.audioTestLabel = String(Math.max(1, Math.ceil(remaining / 1000)))
    } else if (remaining > -3000) {
      state.audioTestLabel = "NOW"
    } else {
      state.audioTestLabel = ""
      state.audioTestAddress = ""
      clearInterval(audioTestTimer)
      audioTestTimer = null
    }
  }
  update()
  audioTestTimer = setInterval(update, 50)
}

async function request(operation, body = {}) {
  const requestStartedAt = performance.now()
  state.busy = operation
  try {
    const response = await fetch(`/api/bluetooth/${operation}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.error || "Bluetooth operation failed")
    if (operation === "test_audio") {
      startAudioTestCountdown(String(body.address || ""), Number(payload.audio_test_delay_ms || 3000), requestStartedAt)
    }
    state.operationError = ""
  } catch (error) {
    state.operationError = error?.message || "Bluetooth operation failed"
  } finally {
    state.busy = ""
    await refresh()
  }
}

async function handlePrompt(prompt) {
  if (!prompt || prompt.id === lastPromptId) return
  lastPromptId = prompt.id
  if (prompt.display_only) return

  let accepted = true
  let value = ""
  if (prompt.kind === "pin") {
    value = window.prompt(`Enter the PIN for ${prompt.name || "Bluetooth device"}`) ?? ""
    accepted = value.length > 0
  } else if (prompt.kind === "passkey") {
    value = window.prompt(`Enter the passkey for ${prompt.name || "Bluetooth device"}`) ?? ""
    accepted = /^\d{1,6}$/.test(value)
  } else {
    const suffix = prompt.value ? `\n\nPasskey: ${prompt.value}` : ""
    accepted = window.confirm(`Allow ${prompt.name || "Bluetooth device"} to pair?${suffix}`)
  }
  await request("pairing_response", { prompt_id: prompt.id, accepted, value })
}

async function refresh() {
  try {
    const response = await fetch("/api/bluetooth/status", { cache: "no-store" })
    const payload = await response.json()
    state.available = !!payload.available
    state.enabled = !!payload.enabled
    state.powered = !!payload.powered
    state.discovering = !!payload.discovering
    state.offroad = !!payload.offroad
    state.selectedAudio = String(payload.selected_audio || "")
    state.pairingAddress = String(payload.pairing_address || "")
    state.companionPairing = !!payload.companion_pairing
    state.companionPairingRemaining = Math.max(0, Number(payload.companion_pairing_remaining || 0))
    state.companionConnected = !!payload.companion_connected
    state.companionDevices = Array.isArray(payload.companion_devices) ? payload.companion_devices : []
    state.devices = Array.isArray(payload.devices) ? payload.devices : []
    state.prompt = payload.prompt || null
    state.statusError = payload.error || (response.ok ? "" : "Bluetooth service unavailable")
    handlePrompt(state.prompt)
  } catch (error) {
    state.available = false
    state.statusError = error?.message || "Bluetooth service unavailable"
  } finally {
    state.loading = false
  }
}

function initialize() {
  if (initialized) return
  initialized = true
  refresh()
  setInterval(() => {
    if (window.location.pathname === "/bluetooth") refresh()
  }, 2000)
}

function currentError() {
  return state.operationError || state.statusError
}

function normalizedAddress(device) {
  return String(device.address || "").toUpperCase()
}

function isPairing(device) {
  return !!state.pairingAddress && state.pairingAddress.toUpperCase() === normalizedAddress(device)
}

function deviceIcon(device) {
  if (device.audio && device.controller) return "bi-headset"
  if (device.audio) return "bi-headphones"
  if (device.controller) return "bi-controller"
  return "bi-bluetooth"
}

function deviceCapabilities(device) {
  const capabilities = []
  if (device.audio) capabilities.push("Audio")
  if (device.controller) capabilities.push("Controller")
  return capabilities.join(" · ") || "Bluetooth device"
}

function deviceStatus(device) {
  if (isPairing(device)) return "Pairing…"
  if (device.connected) {
    const audioSelected = state.selectedAudio.toUpperCase() === normalizedAddress(device)
    return audioSelected ? "Connected · Audio output" : "Connected"
  }
  return device.paired ? "Saved" : "Ready to pair"
}

function knownDevices() {
  return state.devices.filter((device) => device.paired || device.trusted || device.connected)
}

function availableDevices() {
  return state.devices.filter((device) => !device.paired && !device.trusted && !device.connected)
}

function isCompanionDevice(device) {
  const address = normalizedAddress(device)
  return state.companionDevices.some((companionAddress) => String(companionAddress).toUpperCase() === address)
}

function deviceActions(device) {
  const audioSelected = () => state.selectedAudio.toUpperCase() === device.address.toUpperCase()
  const pairing = () => isPairing(device)
  return html`
    <div class="bluetoothActions">
      ${!device.paired ? html`
        <button disabled="${() => !state.offroad || !!state.busy || pairing()}" @click="${() => request("pair", { address: device.address })}">
          ${() => pairing() ? "Pairing…" : "Pair"}
        </button>
      ` : html`
        ${isCompanionDevice(device) && !device.connected ? html`
          <span class="bluetoothReconnectHint"><i class="bi bi-phone" aria-hidden="true"></i> Reconnect from phone</span>
        ` : html`
          <button disabled="${() => !!state.busy}" @click="${() => request(device.connected ? "disconnect" : "connect", { address: device.address })}">
            ${device.connected ? "Disconnect" : "Connect"}
          </button>
        `}
        ${device.audio ? html`
          <button class="${() => audioSelected() ? "selected" : ""}"
                  disabled="${() => !!state.busy}" @click="${() => request("select_audio", { address: audioSelected() ? "" : device.address })}">
            ${() => audioSelected() ? "Stop Using for Audio" : "Use for Audio"}
          </button>
          ${device.connected ? html`
            <button disabled="${() => !state.offroad || !!state.busy || !!state.audioTestLabel}" @click="${() => request("test_audio", { address: device.address })}">
              ${() => state.audioTestAddress === device.address && state.audioTestLabel ? `Test Audio: ${state.audioTestLabel}` : "Test Audio"}
            </button>
          ` : ""}
        ` : ""}
        <button class="bluetoothIconButton bluetoothForgetButton" title="Forget device" aria-label="Forget ${device.name}"
                disabled="${() => !state.offroad || !!state.busy}" @click="${() => {
          if (window.confirm(`Forget ${device.name}?`)) request("forget", { address: device.address })
        }}"><i class="bi bi-trash3" aria-hidden="true"></i></button>
      `}
    </div>
  `
}

function deviceRow(device) {
  return html`
    <div class="${() => `bluetoothDeviceRow ${device.connected ? "connected" : ""}`}">
      <div class="bluetoothDeviceIcon"><i class="bi ${deviceIcon(device)}" aria-hidden="true"></i></div>
      <div class="bluetoothDeviceDetails">
        <div class="bluetoothDeviceName">
          <h3>${device.name}</h3>
          ${device.connected ? html`<span class="bluetoothConnectedDot" title="Connected"></span>` : ""}
        </div>
        <p>${deviceCapabilities(device)}</p>
        <span class="bluetoothDeviceStatus">${() => deviceStatus(device)}</span>
      </div>
      ${deviceActions(device)}
    </div>
  `
}

function deviceSection(title, icon, selectDevices, emptyText) {
  return html`
    <section class="bluetoothSection">
      <div class="bluetoothSectionHeader">
        <div><i class="bi ${icon}" aria-hidden="true"></i><h3>${title}</h3></div>
        <span>${() => selectDevices().length}</span>
      </div>
      <div class="bluetoothSectionBody">
        ${() => {
          const devices = selectDevices()
          return devices.length ? devices.map(deviceRow) : html`<div class="bluetoothEmptyState">${emptyText()}</div>`
        }}
      </div>
    </section>
  `
}

export function Bluetooth() {
  initialize()
  return html`
    <div class="bluetoothPage">
      <div class="bluetoothHeader">
        <div class="bluetoothTitle">
          <i class="bi bi-bluetooth" aria-hidden="true"></i>
          <div>
          <h2>Bluetooth</h2>
          <p>Connect speakers, headphones, media controls, and controllers.</p>
          </div>
        </div>
        <label class="bluetoothSwitch">
          <input type="checkbox" checked="${() => state.enabled}" disabled="${() => !state.available || !state.offroad || !!state.busy}"
                 @change="${(event) => request("power", { enabled: event.target.checked })}" />
          <span>${() => state.enabled ? "On" : "Off"}</span>
        </label>
      </div>

      ${() => !state.offroad ? html`<div class="bluetoothNotice">Scanning, pairing, and forgetting devices are available offroad only.</div>` : ""}
      ${() => currentError() ? html`<div class="bluetoothError">${currentError()}</div>` : ""}
      ${() => state.prompt?.display_only ? html`
        <div class="bluetoothPrompt">${state.prompt.name || "Bluetooth device"}: ${state.prompt.value}</div>
      ` : ""}
      ${() => state.audioTestLabel ? html`
        <div class="bluetoothAudioCountdown">
          <strong>${state.audioTestLabel}</strong>
          <span>The test sound is sent at NOW. The audible gap is Bluetooth latency.</span>
        </div>
      ` : ""}

      ${() => state.enabled && !state.companionDevices.length ? html`
        <div class="bluetoothCompanionCard">
          <div class="bluetoothDeviceHeader">
            <div>
              <h3>Phone connection</h3>
              <p>Pair a phone over an encrypted Bluetooth LE connection.</p>
            </div>
            <span class="bluetoothBadge">Not paired</span>
          </div>
          <div class="bluetoothActions">
            <button disabled="${() => !state.offroad || !!state.busy}"
                    @click="${() => request(state.companionPairing ? "companion_pair_stop" : "companion_pair")}">
              ${() => state.companionPairing ? `Stop Pairing (${state.companionPairingRemaining}s)` : "Pair a Phone"}
            </button>
          </div>
          ${() => state.companionPairing ? html`
            <div class="bluetoothCompanionHint">
              <strong>On an iPhone:</strong>
              <ol>
                <li>Open the secure Live Link page in Safari.</li>
                <li>Follow its beacio install and website-permission checks.</li>
                <li>Tap Connect over Bluetooth and choose StarPilot.</li>
                <li>Keep this pairing window open until Live Link connects. StarPilot authorizes the phone automatically; no confirmation slider is shown.</li>
              </ol>
            </div>
          ` : ""}
        </div>
      ` : ""}

      <div class="bluetoothToolbar">
        <button disabled="${() => !state.offroad || !state.enabled || !!state.busy}"
                class="${() => state.discovering ? "scanning" : ""}"
                @click="${() => request(state.discovering ? "stop_scan" : "scan")}">
          <i class="${() => `bi ${state.discovering ? "bi-arrow-repeat" : "bi-search"}`}" aria-hidden="true"></i>
          ${() => state.discovering ? "Searching…" : "Search for Devices"}
        </button>
        <button class="bluetoothSecondaryButton" disabled="${() => !!state.busy}" @click="${refresh}">
          <i class="bi bi-arrow-clockwise" aria-hidden="true"></i> Refresh
        </button>
        <span class="bluetoothScanHint">Put a device in pairing mode before searching.</span>
      </div>

      <div class="bluetoothDeviceList">
        ${() => state.loading ? html`<div class="bluetoothLoading"><span></span><span></span><span></span></div>` : ""}
        ${() => !state.loading && !state.enabled ? html`
          <div class="bluetoothEmptyPage">
            <i class="bi bi-bluetooth" aria-hidden="true"></i>
            <h3>Bluetooth is off</h3>
            <p>Turn it on to reconnect saved devices or find something new.</p>
          </div>
        ` : ""}
        ${() => !state.loading && state.enabled ? html`
          ${deviceSection("My Devices", "bi-check2-circle", knownDevices, () => "No saved devices yet.")}
          ${deviceSection("Available Devices", "bi-radar", availableDevices,
                          () => state.discovering ? "Searching for nearby devices…" : "No nearby devices found. Start a search to try again.")}
        ` : ""}
      </div>
    </div>
  `
}
