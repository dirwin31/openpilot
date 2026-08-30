// StarPilot Live Link standalone app. This file intentionally has no imports:
// the complete app shell is cached on the phone and all runtime data comes from BLE.
const SERVICE_UUID = "9b6d1000-6f7a-4a5b-8c3d-2e1f0a9b8c7d"
const STATUS_UUID = "9b6d1001-6f7a-4a5b-8c3d-2e1f0a9b8c7d"
const COMMAND_UUID = "9b6d1002-6f7a-4a5b-8c3d-2e1f0a9b8c7d"
const RESPONSE_UUID = "9b6d1003-6f7a-4a5b-8c3d-2e1f0a9b8c7d"
const LIVE_UUID = "9b6d1004-6f7a-4a5b-8c3d-2e1f0a9b8c7d"

const FRAME_SIZE = 64
const FRAME_MAGIC = 0x5053
const FRAME_TYPE_STATE = 1
const LIVE_PROTOCOL_VERSION = 1
const FRAGMENT_COUNT = 4
const FRAGMENT_SIZE = 20
const FRAGMENT_HEADER_SIZE = 4
const FRAGMENT_PAYLOAD_SIZE = 16
const STALE_AFTER_MS = 1500

const FLAG = {
  CONNECTED: 1 << 0,
  STARTED: 1 << 1,
  ENGAGED: 1 << 2,
  ALWAYS_ON_LATERAL: 1 << 6,
  EXPERIMENTAL_MODE: 1 << 7,
  CONDITIONAL_CHILL: 1 << 8,
  SPEED_LIMIT_CONTROL: 1 << 9,
  SPEED_LIMIT_ACTIVE: 1 << 10,
  CURVE_CONTROL: 1 << 11,
  CURVE_CONTROL_ACTIVE: 1 << 12,
  LEAD_PRESENT: 1 << 13,
  ALERT_PRESENT: 1 << 24,
  TELEMETRY_VALID: 1 << 25,
  METRIC: 1 << 29,
}

const BORDER_STATE_LABEL = [
  "Off", "Disengaged", "Engaged", "Always-On Lateral", "Longitudinal Only",
  "Override", "Experimental", "Conditional Override", "Switchback", "Traffic", "Pulse & Glide",
]
const CHILL_REASON_LABEL = ["Active", "Lead vehicle", "Speed", "Manual"]

const MS_TO_MPH = 2.23693629
const MS_TO_KMH = 3.6
const M_TO_FT = 3.2808399

const elements = Object.fromEntries([
  "connectionBadge", "connectionText", "setupCard", "setupTitle", "setupMessage", "setupSteps", "connectButton", "connectionError",
  "secureAppLink", "installHint", "beacioInstallLink", "checkSetupButton", "reloadSetupButton", "permissionHelp",
  "browserStep", "browserStepMarker", "browserStepDetail", "beacioStep", "beacioStepMarker", "beacioStepDetail",
  "permissionStep", "permissionStepMarker", "permissionStepDetail", "pairStep", "pairStepMarker", "pairStepDetail",
  "connectStep", "connectStepMarker", "connectStepDetail", "livePanel", "vehicleSpeed", "speedUnit", "setSpeed", "driveStatus", "driveStatusLabel",
  "driveStatusDetail", "leadCard", "leadDistance", "leadRelativeSpeed", "engagementChip", "engagementValue",
  "aolChip", "aolValue", "experimentalChip", "experimentalValue", "slcChip", "slcValue", "slcDetail",
  "curveChip", "curveValue", "alertCard", "alertText", "disconnectButton", "offlineReady",
].map((id) => [id, document.getElementById(id)]))

const state = {
  phase: "idle",
  frame: null,
  metadata: null,
  lastPacketAt: 0,
  error: "",
}

const setupState = {
  checked: false,
  checking: false,
  bluetoothApi: Boolean(navigator.bluetooth),
  bluetoothAvailable: null,
  companionAuthorized: false,
  authorizationFailed: false,
}

let device = null
let liveCharacteristic = null
let commandCharacteristic = null
let responseCharacteristic = null
let reconnectTimer = null
let reconnectDelay = 1000
let clockTimer = null
let metadataInFlight = false
let metadataPending = false
let lastMetadataRevision = null
let lastAlertId = null
let assembly = emptyAssembly()
let connectionStage = "idle"
let beacioReadyHandled = false

const textEncoder = new TextEncoder()
const textDecoder = new TextDecoder()

function emptyAssembly() {
  return { sequence: -1, parts: new Array(FRAGMENT_COUNT).fill(null), count: 0 }
}

function bytesFrom(value) {
  if (value instanceof Uint8Array) return value
  return new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
}

function reassemble(bytes) {
  if (bytes.length !== FRAGMENT_SIZE || bytes[0] !== LIVE_PROTOCOL_VERSION) return null

  const sequence = bytes[1] | (bytes[2] << 8)
  const index = bytes[3] & 0x0f
  const count = (bytes[3] >> 4) & 0x0f
  if (count !== FRAGMENT_COUNT || index >= FRAGMENT_COUNT) return null

  if (sequence !== assembly.sequence) {
    assembly = { sequence, parts: new Array(FRAGMENT_COUNT).fill(null), count: 0 }
  }
  if (assembly.parts[index] === null) {
    assembly.parts[index] = bytes.slice(FRAGMENT_HEADER_SIZE, FRAGMENT_HEADER_SIZE + FRAGMENT_PAYLOAD_SIZE)
    assembly.count += 1
  }
  if (assembly.count !== FRAGMENT_COUNT) return null

  const frame = new Uint8Array(FRAME_SIZE)
  for (let part = 0; part < FRAGMENT_COUNT; part += 1) {
    frame.set(assembly.parts[part], part * FRAGMENT_PAYLOAD_SIZE)
  }
  assembly = emptyAssembly()
  return { frame, sequence }
}

function decodeFrame(frame, notificationSequence = null) {
  if (frame.length !== FRAME_SIZE) return null
  const view = new DataView(frame.buffer, frame.byteOffset, frame.byteLength)
  const sequence = view.getUint16(6, true)
  if (
    view.getUint16(0, true) !== FRAME_MAGIC ||
    view.getUint8(2) !== LIVE_PROTOCOL_VERSION ||
    view.getUint8(3) !== FRAME_TYPE_STATE ||
    view.getUint16(4, true) !== FRAME_SIZE ||
    (notificationSequence !== null && sequence !== notificationSequence)
  ) return null

  return {
    sequence,
    monotonicMs: view.getUint32(8, true),
    flags: view.getUint32(12, true),
    vehicleSpeed: view.getInt16(16, true) / 100,
    setSpeed: view.getUint16(18, true) / 100,
    acceleration: view.getInt16(20, true) / 100,
    targetAcceleration: view.getInt16(22, true) / 100,
    steeringAngle: view.getInt16(24, true) / 10,
    desiredSteeringAngle: view.getInt16(26, true) / 10,
    steeringTorque: view.getInt16(28, true) / 10,
    leadDistance: view.getUint16(30, true) / 10,
    leadRelativeSpeed: view.getInt16(32, true) / 100,
    leadProbability: view.getUint16(34, true) / 1000,
    speedLimit: view.getUint16(36, true) / 100,
    speedLimitOffset: view.getInt16(38, true) / 100,
    curveTargetSpeed: view.getUint16(40, true) / 100,
    cruiseState: view.getUint8(42),
    borderState: view.getUint8(43),
    alertStatus: view.getUint8(44),
    chillReason: view.getUint8(45),
    drivingProfile: view.getUint8(46),
    longitudinalProfile: view.getUint8(47),
    laneChangeState: view.getUint8(48),
    laneChangeDirection: view.getUint8(49),
    longControlState: view.getUint8(50),
    modelSource: view.getUint8(51),
    borderColor: [view.getUint8(52), view.getUint8(53), view.getUint8(54), view.getUint8(55)],
    alertId: view.getUint32(56, true),
    metadataRevision: view.getUint32(60, true),
  }
}

function has(frame, flag) {
  return Boolean(frame && (frame.flags & flag) !== 0)
}

function isMetric(frame = state.frame) {
  return has(frame, FLAG.METRIC)
}

function convertedSpeed(ms, frame = state.frame) {
  return ms * (isMetric(frame) ? MS_TO_KMH : MS_TO_MPH)
}

function speedText(ms, { zeroIsEmpty = false } = {}) {
  if (!Number.isFinite(ms) || (zeroIsEmpty && ms <= 0)) return "--"
  return String(Math.round(Math.max(0, convertedSpeed(ms))))
}

function offsetText(ms) {
  if (!Number.isFinite(ms)) return "Offset --"
  const converted = convertedSpeed(ms)
  const value = Math.abs(converted) >= 10 ? Math.round(converted).toString() : converted.toFixed(1)
  return `Offset ${converted > 0 ? "+" : ""}${value} ${isMetric() ? "km/h" : "mph"}`
}

function distanceText(meters) {
  return isMetric() ? `${Math.round(meters)} m` : `${Math.round(meters * M_TO_FT)} ft`
}

function relativeSpeedText(ms) {
  const converted = Math.abs(convertedSpeed(ms))
  if (converted < 0.1) return "Steady"
  return `${ms < 0 ? "Closing" : "Opening"} ${converted.toFixed(1)} ${isMetric() ? "km/h" : "mph"}`
}

function borderColor(frame) {
  if (!frame) return "transparent"
  const [red, green, blue, alpha] = frame.borderColor
  return alpha === 0 ? "transparent" : `rgba(${red}, ${green}, ${blue}, ${(alpha / 255).toFixed(3)})`
}

function setChip(element, enabled) {
  element.classList.toggle("stateChipOn", enabled)
}

function isIOSDevice() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent || "") ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
}

function isIOSSafari() {
  const agent = navigator.userAgent || ""
  return isIOSDevice() && /Safari/.test(agent) && !/(CriOS|FxiOS|EdgiOS|OPiOS)/.test(agent)
}

function isStandaloneApp() {
  return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true
}

function setSetupStep(element, marker, status) {
  const statusClass = {
    complete: "setupStepComplete",
    current: "setupStepCurrent",
    attention: "setupStepNeedsAttention",
    pending: "setupStepPending",
  }[status] || "setupStepPending"
  element.className = `setupStep ${statusClass}`
  const markers = [
    elements.browserStepMarker,
    elements.beacioStepMarker,
    elements.permissionStepMarker,
    elements.pairStepMarker,
    elements.connectStepMarker,
  ]
  marker.textContent = status === "complete" ? "✓" : String(markers.indexOf(marker) + 1)
}

function renderSetup() {
  const ios = isIOSDevice()
  const safariContext = isIOSSafari() || isStandaloneApp()
  const secure = window.isSecureContext
  const apiReady = secure && setupState.bluetoothApi
  const radioReady = apiReady && setupState.bluetoothAvailable !== false
  const browserReady = secure && (!ios || safariContext)

  elements.browserStep.hidden = !ios && secure
  elements.beacioStep.hidden = !ios
  elements.permissionStep.hidden = !ios
  elements.beacioInstallLink.hidden = !ios || apiReady
  elements.checkSetupButton.hidden = !ios || apiReady
  elements.checkSetupButton.disabled = setupState.checking
  elements.checkSetupButton.textContent = setupState.checking ? "Checking…" : "Check beacio setup"
  elements.reloadSetupButton.hidden = !(ios && setupState.checked && !apiReady)
  elements.permissionHelp.hidden = !(ios && setupState.checked && !apiReady)
  elements.connectButton.hidden = !radioReady

  if (!secure) {
    setSetupStep(elements.browserStep, elements.browserStepMarker, "attention")
    elements.browserStepDetail.textContent = "Bluetooth requires the HTTPS Live Link address. This HTTP page cannot request Bluetooth."
    elements.setupTitle.textContent = "Open secure Live Link"
    elements.setupMessage.textContent = "Open the secure page before setting up Bluetooth."
  } else if (ios && !safariContext) {
    setSetupStep(elements.browserStep, elements.browserStepMarker, "attention")
    elements.browserStepDetail.textContent = "Open this address in Safari. Chrome, Firefox, and in-app browsers on iPhone cannot load Safari extensions."
    elements.setupTitle.textContent = "Open Live Link in Safari"
    elements.setupMessage.textContent = "beacio only runs in Safari or the installed Live Link app."
  } else {
    setSetupStep(elements.browserStep, elements.browserStepMarker, "complete")
    elements.browserStepDetail.textContent = isStandaloneApp()
      ? "Secure installed Live Link app detected."
      : "Secure Safari page detected. Avoid Private Browsing during setup."
  }

  if (ios) {
    if (apiReady) {
      setSetupStep(elements.beacioStep, elements.beacioStepMarker, "complete")
      elements.beacioStepDetail.textContent = "beacio is installed and active on this page."
      setSetupStep(elements.permissionStep, elements.permissionStepMarker, setupState.bluetoothAvailable === false ? "attention" : "complete")
      elements.permissionStepDetail.textContent = setupState.bluetoothAvailable === false
        ? "Website access works, but Bluetooth is unavailable. Turn on Bluetooth and allow beacio under Settings → Privacy & Security → Bluetooth."
        : "beacio website access and the Bluetooth API are available."
    } else {
      const status = setupState.checked ? "attention" : "current"
      setSetupStep(elements.beacioStep, elements.beacioStepMarker, status)
      setSetupStep(elements.permissionStep, elements.permissionStepMarker, setupState.checked ? "attention" : "pending")
      elements.beacioStepDetail.textContent = setupState.checked
        ? "Live Link still cannot detect beacio. Install it if you have not already."
        : "Install the free beacio Safari extension from the App Store."
      elements.permissionStepDetail.textContent = setupState.checked
        ? "If beacio is installed, it is probably disabled or not allowed on this website. Check Safari extension permissions, then reload."
        : "After installing, turn the extension on and choose Allow on Every Website."
    }
  }

  if (browserReady && ios && !apiReady) {
    elements.setupTitle.textContent = "Set up Bluetooth on iPhone"
    elements.setupMessage.textContent = setupState.checked
      ? "Live Link still cannot see beacio. Check its Safari website permission, then reload and verify."
      : "Install beacio, enable its Safari extension, and allow it on websites."
  } else if (browserReady && apiReady && setupState.bluetoothAvailable === false) {
    elements.setupTitle.textContent = "Turn on Bluetooth"
    elements.setupMessage.textContent = "beacio is active, but Bluetooth is currently unavailable."
  } else if (browserReady && !ios && !apiReady) {
    elements.setupTitle.textContent = "Bluetooth browser required"
    elements.setupMessage.textContent = "This browser does not provide Web Bluetooth."
  }

  if (setupState.companionAuthorized) {
    setSetupStep(elements.pairStep, elements.pairStepMarker, "complete")
    setSetupStep(elements.connectStep, elements.connectStepMarker, "complete")
    elements.pairStepDetail.textContent = "This phone is registered as a StarPilot companion."
    elements.connectStepDetail.textContent = "The protected Live Link service authorized this phone."
  } else if (setupState.authorizationFailed) {
    setSetupStep(elements.pairStep, elements.pairStepMarker, "attention")
    setSetupStep(elements.connectStep, elements.connectStepMarker, "pending")
    elements.pairStepDetail.textContent = "StarPilot rejected the protected read. On the comma, tap Pair a Phone and try again while that window is open."
    elements.setupTitle.textContent = "Finish pairing on the comma"
    elements.setupMessage.textContent = "Bluetooth reached StarPilot, but this phone is not registered as a companion yet."
  } else if (radioReady && browserReady) {
    setSetupStep(elements.pairStep, elements.pairStepMarker, "current")
    setSetupStep(elements.connectStep, elements.connectStepMarker, "pending")
    elements.setupTitle.textContent = "Pair with StarPilot"
    elements.setupMessage.textContent = "Bluetooth is ready. Open Pair a Phone on the comma, then connect here."
  } else {
    setSetupStep(elements.pairStep, elements.pairStepMarker, "pending")
    setSetupStep(elements.connectStep, elements.connectStepMarker, "pending")
  }
}

function connectionLabel() {
  if (state.phase === "connecting") return ["connectionBusy", "Connecting"]
  if (state.phase === "reconnecting") return ["connectionBusy", "Reconnecting"]
  if (state.phase === "connected") {
    const fresh = state.lastPacketAt > 0 && performance.now() - state.lastPacketAt < STALE_AFTER_MS
    return fresh ? ["connectionLive", "Direct"] : ["connectionBusy", "Stale"]
  }
  return ["connectionOffline", "Offline"]
}

function renderConnection() {
  const [className, label] = connectionLabel()
  elements.connectionBadge.className = `connectionBadge ${className}`
  elements.connectionText.textContent = label
  elements.connectButton.disabled = state.phase === "connecting" || !setupState.bluetoothApi || setupState.bluetoothAvailable === false
  elements.connectButton.textContent = state.phase === "connecting" ? "Connecting…" : "Connect over Bluetooth"
  elements.connectionError.hidden = !state.error
  elements.connectionError.textContent = state.error
}

function renderFrame() {
  const frame = state.frame
  if (!frame) return

  elements.setupCard.hidden = true
  elements.livePanel.hidden = false
  elements.livePanel.style.setProperty("--drive-border", borderColor(frame))

  elements.vehicleSpeed.textContent = speedText(frame.vehicleSpeed)
  elements.speedUnit.textContent = isMetric(frame) ? "KM/H" : "MPH"
  elements.setSpeed.textContent = speedText(frame.setSpeed, { zeroIsEmpty: true })

  const conditionalChill = has(frame, FLAG.CONDITIONAL_CHILL)
  elements.driveStatusLabel.textContent = conditionalChill
    ? "Conditional Chill"
    : (BORDER_STATE_LABEL[frame.borderState] || "StarPilot")
  if (conditionalChill) {
    elements.driveStatusDetail.textContent = `Active — ${CHILL_REASON_LABEL[frame.chillReason] || "Active"}`
  } else if (!has(frame, FLAG.STARTED)) {
    elements.driveStatusDetail.textContent = "Vehicle offroad"
  } else if (!has(frame, FLAG.TELEMETRY_VALID)) {
    elements.driveStatusDetail.textContent = "Waiting for vehicle state"
  } else {
    elements.driveStatusDetail.textContent = has(frame, FLAG.ENGAGED) ? "openpilot engaged" : "openpilot disengaged"
  }

  const leadPresent = has(frame, FLAG.LEAD_PRESENT)
  elements.leadCard.hidden = !leadPresent
  if (leadPresent) {
    elements.leadDistance.textContent = distanceText(frame.leadDistance)
    elements.leadRelativeSpeed.textContent = relativeSpeedText(frame.leadRelativeSpeed)
  }

  const engaged = has(frame, FLAG.ENGAGED)
  elements.engagementValue.textContent = engaged ? "Engaged" : "Disengaged"
  setChip(elements.engagementChip, engaged)

  const aol = has(frame, FLAG.ALWAYS_ON_LATERAL)
  elements.aolValue.textContent = aol ? "On" : "Off"
  setChip(elements.aolChip, aol)

  const experimental = has(frame, FLAG.EXPERIMENTAL_MODE)
  elements.experimentalValue.textContent = experimental ? "On" : "Off"
  setChip(elements.experimentalChip, experimental)

  const slcEnabled = has(frame, FLAG.SPEED_LIMIT_CONTROL)
  const slcActive = has(frame, FLAG.SPEED_LIMIT_ACTIVE)
  elements.slcValue.textContent = slcActive ? speedText(frame.speedLimit) : (slcEnabled ? "On" : "Off")
  elements.slcDetail.textContent = slcEnabled ? offsetText(frame.speedLimitOffset) : ""
  setChip(elements.slcChip, slcEnabled)

  const curveEnabled = has(frame, FLAG.CURVE_CONTROL)
  const curveActive = has(frame, FLAG.CURVE_CONTROL_ACTIVE)
  elements.curveValue.textContent = curveActive ? speedText(frame.curveTargetSpeed) : (curveEnabled ? "On" : "Off")
  setChip(elements.curveChip, curveEnabled)

  const alert = state.metadata && state.metadata.alert
  const alertText = alert ? [alert.text1, alert.text2].filter(Boolean).join(" — ") : ""
  elements.alertCard.hidden = !(has(frame, FLAG.ALERT_PRESENT) && alertText)
  elements.alertText.textContent = alertText
}

function render() {
  renderSetup()
  renderConnection()
  if (state.frame && state.phase !== "idle") renderFrame()
  if (state.phase === "idle") {
    elements.setupCard.hidden = false
    elements.livePanel.hidden = true
  }
}

async function fetchMetadata() {
  if (!commandCharacteristic || !responseCharacteristic) return
  if (metadataInFlight) {
    metadataPending = true
    return
  }

  metadataInFlight = true
  metadataPending = false
  try {
    const request = JSON.stringify({ id: `live-${Date.now()}`, op: "get_live_metadata" })
    await commandCharacteristic.writeValue(textEncoder.encode(request))
    const payload = JSON.parse(textDecoder.decode(bytesFrom(await responseCharacteristic.readValue())))
    if (payload && payload.ok && payload.data) {
      state.metadata = payload.data
      renderFrame()
    }
  } catch (error) {
    console.warn("[live_link] metadata request failed", error)
  } finally {
    metadataInFlight = false
    if (metadataPending) fetchMetadata()
  }
}

function acceptDecodedFrame(decoded) {
  if (!decoded) return
  state.frame = decoded
  state.lastPacketAt = performance.now()
  render()

  if (decoded.metadataRevision !== lastMetadataRevision || decoded.alertId !== lastAlertId) {
    lastMetadataRevision = decoded.metadataRevision
    lastAlertId = decoded.alertId
    fetchMetadata()
  }
}

function consumeValue(value) {
  const bytes = bytesFrom(value)
  if (bytes.length === FRAME_SIZE) {
    acceptDecodedFrame(decodeFrame(bytes))
    return
  }
  const complete = reassemble(bytes)
  if (complete) acceptDecodedFrame(decodeFrame(complete.frame, complete.sequence))
}

function handleValueChanged(event) {
  consumeValue(event.target.value)
}

function resetCharacteristics() {
  if (liveCharacteristic) {
    liveCharacteristic.removeEventListener("characteristicvaluechanged", handleValueChanged)
  }
  liveCharacteristic = null
  commandCharacteristic = null
  responseCharacteristic = null
  assembly = emptyAssembly()
}

async function openConnection() {
  connectionStage = "gatt"
  const server = await device.gatt.connect()
  connectionStage = "service"
  const service = await server.getPrimaryService(SERVICE_UUID)
  const statusCharacteristic = await service.getCharacteristic(STATUS_UUID)
  commandCharacteristic = await service.getCharacteristic(COMMAND_UUID)
  responseCharacteristic = await service.getCharacteristic(RESPONSE_UUID)
  liveCharacteristic = await service.getCharacteristic(LIVE_UUID)

  connectionStage = "authorization"
  const status = JSON.parse(textDecoder.decode(bytesFrom(await statusCharacteristic.readValue())))
  connectionStage = "protocol"
  if (!status.live || status.live.protocol_version !== LIVE_PROTOCOL_VERSION || status.live.frame_size !== FRAME_SIZE) {
    throw new Error("This StarPilot version does not provide the supported Live Link protocol.")
  }
  setupState.companionAuthorized = true
  setupState.authorizationFailed = false

  lastMetadataRevision = null
  lastAlertId = null
  connectionStage = "metadata"
  await fetchMetadata()

  liveCharacteristic.addEventListener("characteristicvaluechanged", handleValueChanged)
  connectionStage = "notifications"
  await liveCharacteristic.startNotifications()

  state.phase = "connected"
  state.error = ""
  reconnectDelay = 1000
  connectionStage = "connected"
  render()

  // Notifications are authoritative, but a read provides an immediate complete frame.
  try {
    consumeValue(await liveCharacteristic.readValue())
  } catch (error) {
    console.warn("[live_link] initial live read failed", error)
  }
}

function handleDisconnected() {
  if (!device || state.phase === "idle") return
  resetCharacteristics()
  state.phase = "reconnecting"
  render()
  scheduleReconnect()
}

function bindDevice(nextDevice) {
  if (device && device !== nextDevice) {
    device.removeEventListener("gattserverdisconnected", handleDisconnected)
  }
  device = nextDevice
  device.addEventListener("gattserverdisconnected", handleDisconnected)
}

function scheduleReconnect() {
  if (reconnectTimer !== null) clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null
    if (!device || state.phase !== "reconnecting") return
    try {
      await openConnection()
    } catch (error) {
      console.warn("[live_link] reconnect failed", error)
      if (connectionStage === "authorization" || connectionStage === "protocol") {
        state.phase = "idle"
        state.error = connectionErrorMessage(error)
        resetCharacteristics()
        render()
        return
      }
      reconnectDelay = Math.min(reconnectDelay * 2, 10000)
      scheduleReconnect()
    }
  }, reconnectDelay)
}

function bluetoothUnavailableMessage() {
  if (!window.isSecureContext) {
    return "Bluetooth is unavailable on this HTTP page. Open the secure HTTPS Live Link address first."
  }
  if (isIOSDevice()) {
    return setupState.checked
      ? "Live Link still cannot see beacio. If it is installed, enable it in Safari and choose Allow on Every Website, then reload."
      : "Live Link cannot see beacio on this page yet. Install it, enable the Safari extension, and allow website access."
  }
  return "This browser does not provide Web Bluetooth. Open Live Link in a supported Bluetooth browser."
}

function connectionErrorMessage(error, stage = connectionStage) {
  const name = error && error.name ? error.name : ""
  const message = error && error.message ? error.message : ""
  const normalized = `${name} ${message}`.toLowerCase()

  if (stage === "chooser" && name === "NotFoundError") {
    return "No StarPilot device was selected. On the comma, open Galaxy → Bluetooth → Pair a Phone, then try again and choose StarPilot."
  }
  if (stage === "authorization" || /authentication|not authorized|not permitted|insufficient encryption|bond/.test(normalized)) {
    setupState.authorizationFailed = true
    return "The comma did not authorize this phone. While parked, open Galaxy → Bluetooth → Pair a Phone on the comma, reconnect here, and accept any matching-code prompts."
  }
  if (name === "NotAllowedError" || name === "SecurityError" || normalized.includes("permission denied")) {
    return "Bluetooth access was denied. In Safari, allow beacio on this website. Also verify Settings → Privacy & Security → Bluetooth → beacio is on."
  }
  if (stage === "gatt" || stage === "service") {
    return "Could not reach StarPilot. Keep Pair a Phone open on the comma, stay nearby, and try again."
  }
  return message || "Could not connect to StarPilot. Check the setup steps and try again."
}

async function detectBluetoothSetup({ manual = false } = {}) {
  setupState.checking = true
  if (manual) setupState.checked = true
  render()

  // Safari extensions normally inject at document_start. A brief wait also
  // catches an extension that becomes ready while this page is already open.
  if (manual && isIOSDevice() && !navigator.bluetooth) {
    await new Promise((resolve) => setTimeout(resolve, 1200))
  }

  setupState.bluetoothApi = Boolean(navigator.bluetooth)
  setupState.bluetoothAvailable = null
  if (setupState.bluetoothApi && typeof navigator.bluetooth.getAvailability === "function") {
    try {
      setupState.bluetoothAvailable = await navigator.bluetooth.getAvailability()
    } catch (error) {
      console.warn("[live_link] Bluetooth availability check failed", error)
    }
  }
  setupState.checking = false
  if (manual) state.error = setupState.bluetoothApi ? "" : bluetoothUnavailableMessage()
  render()
}

async function checkSetup() {
  await detectBluetoothSetup({ manual: true })
  if (setupState.bluetoothApi) reconnectGrantedDevice()
}

function reloadSetup() {
  window.location.reload()
}

async function connect() {
  state.error = ""
  if (!window.isSecureContext || !navigator.bluetooth) {
    setupState.checked = true
    setupState.bluetoothApi = false
    state.error = bluetoothUnavailableMessage()
    render()
    return
  }

  state.phase = "connecting"
  state.frame = null
  setupState.authorizationFailed = false
  connectionStage = "chooser"
  render()
  try {
    const selected = await navigator.bluetooth.requestDevice({
      filters: [{ services: [SERVICE_UUID] }],
      optionalServices: [SERVICE_UUID],
    })
    bindDevice(selected)
    await openConnection()
    startClock()
  } catch (error) {
    state.phase = "idle"
    state.error = connectionErrorMessage(error)
    resetCharacteristics()
    render()
  }
}

function disconnect() {
  if (reconnectTimer !== null) clearTimeout(reconnectTimer)
  reconnectTimer = null
  if (device) device.removeEventListener("gattserverdisconnected", handleDisconnected)
  resetCharacteristics()
  if (device && device.gatt && device.gatt.connected) device.gatt.disconnect()
  device = null
  state.phase = "idle"
  state.frame = null
  state.metadata = null
  state.lastPacketAt = 0
  state.error = ""
  render()
}

function startClock() {
  if (clockTimer !== null) clearInterval(clockTimer)
  clockTimer = setInterval(renderConnection, 250)
}

async function reconnectGrantedDevice() {
  if (state.phase !== "idle") return
  if (!window.isSecureContext || !navigator.bluetooth || typeof navigator.bluetooth.getDevices !== "function") return
  try {
    const devices = await navigator.bluetooth.getDevices()
    const granted = devices.find((candidate) => candidate.name === "StarPilot")
    if (!granted) return
    bindDevice(granted)
    state.phase = "reconnecting"
    render()
    await openConnection()
    startClock()
  } catch (error) {
    console.warn("[live_link] granted-device reconnect failed", error)
    if (device) device.removeEventListener("gattserverdisconnected", handleDisconnected)
    resetCharacteristics()
    device = null
    state.phase = "idle"
    state.error = connectionErrorMessage(error)
    render()
  }
}

async function installOfflineShell() {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return
  try {
    const serviceWorkerUrl = new URL("service-worker.js", document.baseURI)
    const scope = new URL(".", serviceWorkerUrl).pathname
    await navigator.serviceWorker.register(serviceWorkerUrl, { scope })
    await navigator.serviceWorker.ready
    elements.offlineReady.hidden = false
  } catch (error) {
    console.warn("[live_link] offline app installation failed", error)
  }
}

function configureInstallHint() {
  const installed = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true
  elements.installHint.hidden = installed
}

function configureLaunchContext() {
  if (window.isSecureContext) return
  const secureBaseUrl = String(document.body.dataset.secureLiveUrl || "").replace(/\/$/, "")
  elements.connectButton.hidden = true
  elements.installHint.hidden = true
  if (secureBaseUrl) {
    elements.setupMessage.textContent = "Bluetooth requires the secure installable page. Open it once, then add it to your Home Screen for offline use."
    elements.secureAppLink.href = `${secureBaseUrl}/live`
    elements.secureAppLink.hidden = false
  } else {
    elements.setupMessage.textContent = "Set up Galaxy remote access once to create the secure installable Live Link page."
  }
}

async function handleBeacioReady() {
  if (beacioReadyHandled) return
  beacioReadyHandled = true
  await detectBluetoothSetup()
  reconnectGrantedDevice()
}

async function initializeBluetoothSetup() {
  await detectBluetoothSetup()
  reconnectGrantedDevice()
}

elements.connectButton.addEventListener("click", connect)
elements.checkSetupButton.addEventListener("click", checkSetup)
elements.reloadSetupButton.addEventListener("click", reloadSetup)
elements.disconnectButton.addEventListener("click", disconnect)
window.addEventListener("beacio:ready", handleBeacioReady, { once: true })
window.addEventListener("beacio:extension:ready", handleBeacioReady, { once: true })
window.addEventListener("online", installOfflineShell)

configureInstallHint()
configureLaunchContext()
render()
installOfflineShell()
initializeBluetoothSetup()
