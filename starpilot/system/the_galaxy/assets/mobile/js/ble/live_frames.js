export const LIVE_PROTOCOL_VERSION = 1
export const LIVE_FRAME_SIZE = 64
export const LIVE_NOTIFICATION_SIZE = 20
export const LIVE_FRAGMENT_COUNT = 4

export const LIVE_FLAGS = Object.freeze({
  connected: 2 ** 0,
  started: 2 ** 1,
  engaged: 2 ** 2,
  active: 2 ** 3,
  cruiseAvailable: 2 ** 4,
  cruiseEnabled: 2 ** 5,
  alwaysOnLateral: 2 ** 6,
  experimentalMode: 2 ** 7,
  conditionalChill: 2 ** 8,
  speedLimitControl: 2 ** 9,
  speedLimitActive: 2 ** 10,
  curveControl: 2 ** 11,
  curveControlActive: 2 ** 12,
  leadPresent: 2 ** 13,
  lateralActive: 2 ** 14,
  longitudinalActive: 2 ** 15,
  gasPressed: 2 ** 16,
  brakePressed: 2 ** 17,
  stopping: 2 ** 18,
  standstill: 2 ** 19,
  bigModel: 2 ** 20,
  lateralPaused: 2 ** 21,
  trafficMode: 2 ** 22,
  switchbackMode: 2 ** 23,
  alertPresent: 2 ** 24,
  telemetryValid: 2 ** 25,
  forcingStop: 2 ** 26,
  trackingLead: 2 ** 27,
  pulseAndGlide: 2 ** 28,
  metric: 2 ** 29,
  overriding: 2 ** 30,
  redLight: 2 ** 31,
})

export const HEALTH_FLAGS = Object.freeze({
  onroad: 2 ** 0,
  offroad: 2 ** 1,
  networkMetered: 2 ** 2,
  wifiConnected: 2 ** 3,
  ethernetConnected: 2 ** 4,
  cellularConnected: 2 ** 5,
  directLanAllowed: 2 ** 6,
  thermalOk: 2 ** 7,
  thermalOverheated: 2 ** 8,
  thermalCritical: 2 ** 9,
  fanActive: 2 ** 10,
  lowStorage: 2 ** 11,
  cloudPingRecent: 2 ** 13,
})

export function hasFlag(flags, flag) {
  return ((Number(flags) >>> 0) & (Number(flag) >>> 0)) !== 0
}

function bytesOf(value) {
  if (value instanceof Uint8Array) return value
  if (value instanceof DataView) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
  if (value instanceof ArrayBuffer) return new Uint8Array(value)
  if (ArrayBuffer.isView(value)) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
  return Uint8Array.from(value || [])
}

function reader(bytes) {
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
}

function commonFrame(bytes, view, frameType) {
  return {
    frameType,
    sequence: view.getUint16(6, true),
    monotonicMilliseconds: view.getUint32(8, true),
    flags: view.getUint32(12, true),
  }
}

export function decodeState(value) {
  const bytes = bytesOf(value)
  if (bytes.length !== LIVE_FRAME_SIZE) return null
  const view = reader(bytes)
  return {
    ...commonFrame(bytes, view, 1),
    kind: "state",
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
    cruiseState: bytes[42],
    borderState: bytes[43],
    alertStatus: bytes[44],
    conditionalChillReason: bytes[45],
    drivingProfile: bytes[46],
    longitudinalProfile: bytes[47],
    laneChangeState: bytes[48],
    laneChangeDirection: bytes[49],
    longControlState: bytes[50],
    modelSource: bytes[51],
    borderColor: { red: bytes[52], green: bytes[53], blue: bytes[54], alpha: bytes[55] },
    alertID: view.getUint32(56, true),
    metadataRevision: view.getUint32(60, true),
    isMetric: hasFlag(view.getUint32(12, true), LIVE_FLAGS.metric),
  }
}

export function decodeHealth(value) {
  const bytes = bytesOf(value)
  if (bytes.length !== LIVE_FRAME_SIZE) return null
  const view = reader(bytes)
  return {
    ...commonFrame(bytes, view, 2),
    kind: "health",
    cpuPercent: bytes[16],
    gpuPercent: bytes[17],
    memoryPercent: bytes[18],
    freeStoragePercent: bytes[19],
    cpuTempC: view.getInt16(20, true) / 10,
    gpuTempC: view.getInt16(22, true) / 10,
    memoryTempC: view.getInt16(24, true) / 10,
    maxTempC: view.getInt16(26, true) / 10,
    intakeTempC: view.getInt16(28, true) / 10,
    thermalStatus: bytes[30],
    fanSpeedPercent: bytes[31],
    totalPowerDrawW: view.getUint16(32, true) / 100,
    somPowerDrawW: view.getUint16(34, true) / 100,
    batteryReserveWh: view.getUint16(36, true) / 10,
    screenBrightnessPercent: bytes[38],
    rawNetworkType: bytes[39],
    networkStrength: bytes[40],
    uptimeSeconds: view.getUint32(42, true),
  }
}

export function decodeFrame(value, notificationSequence = null) {
  const bytes = bytesOf(value)
  if (bytes.length !== LIVE_FRAME_SIZE) return null
  const view = reader(bytes)
  if (bytes[0] !== 0x53 || bytes[1] !== 0x50 || bytes[2] !== LIVE_PROTOCOL_VERSION) return null
  if (view.getUint16(4, true) !== LIVE_FRAME_SIZE) return null
  if (notificationSequence !== null && view.getUint16(6, true) !== notificationSequence) return null
  if (bytes[3] === 1) return decodeState(bytes)
  if (bytes[3] === 2) return decodeHealth(bytes)
  return null
}

export class LiveReassembler {
  constructor() { this.reset() }

  reset() {
    this.sequence = null
    this.parts = Array(LIVE_FRAGMENT_COUNT).fill(null)
  }

  consume(value) {
    const bytes = bytesOf(value)
    if (bytes.length === LIVE_FRAME_SIZE) return decodeFrame(bytes)
    if (bytes.length !== LIVE_NOTIFICATION_SIZE || bytes[0] !== LIVE_PROTOCOL_VERSION) return null

    const view = reader(bytes)
    const sequence = view.getUint16(1, true)
    const index = bytes[3] & 0x0f
    const count = (bytes[3] >>> 4) & 0x0f
    if (count !== LIVE_FRAGMENT_COUNT || index >= LIVE_FRAGMENT_COUNT) return null

    if (this.sequence !== sequence) {
      this.reset()
      this.sequence = sequence
    }
    if (this.parts[index] === null) this.parts[index] = bytes.slice(4, 20)
    if (this.parts.some((part) => part === null)) return null

    const frame = new Uint8Array(LIVE_FRAME_SIZE)
    this.parts.forEach((part, partIndex) => frame.set(part, partIndex * 16))
    this.reset()
    return decodeFrame(frame, sequence)
  }
}

export class LiveSessionStats {
  constructor() { this.reset() }

  reset() {
    this.observedDrivingSeconds = 0
    this.lateralActiveSeconds = 0
    this.longitudinalActiveSeconds = 0
    this.stoppedSeconds = 0
    this.lastMonotonicMilliseconds = null
  }

  get lateralPercent() {
    return this.observedDrivingSeconds > 0 ? this.lateralActiveSeconds / this.observedDrivingSeconds * 100 : null
  }

  get longitudinalPercent() {
    return this.observedDrivingSeconds > 0 ? this.longitudinalActiveSeconds / this.observedDrivingSeconds * 100 : null
  }

  consume(frame) {
    const previous = this.lastMonotonicMilliseconds
    this.lastMonotonicMilliseconds = frame.monotonicMilliseconds
    if (previous === null) return
    if (frame.monotonicMilliseconds < previous) {
      this.reset()
      this.lastMonotonicMilliseconds = frame.monotonicMilliseconds
      return
    }
    const elapsed = Math.min((frame.monotonicMilliseconds - previous) / 1000, 1)
    if (elapsed <= 0) return
    if (hasFlag(frame.flags, LIVE_FLAGS.started) && hasFlag(frame.flags, LIVE_FLAGS.standstill)) {
      this.stoppedSeconds += elapsed
    } else {
      this.stoppedSeconds = 0
    }
    if (!hasFlag(frame.flags, LIVE_FLAGS.telemetryValid) || !hasFlag(frame.flags, LIVE_FLAGS.started) || hasFlag(frame.flags, LIVE_FLAGS.standstill)) return
    this.observedDrivingSeconds += elapsed
    if (hasFlag(frame.flags, LIVE_FLAGS.lateralActive)) this.lateralActiveSeconds += elapsed
    if (hasFlag(frame.flags, LIVE_FLAGS.longitudinalActive)) this.longitudinalActiveSeconds += elapsed
  }

  snapshot() {
    return {
      observedDrivingSeconds: this.observedDrivingSeconds,
      lateralActiveSeconds: this.lateralActiveSeconds,
      longitudinalActiveSeconds: this.longitudinalActiveSeconds,
      stoppedSeconds: this.stoppedSeconds,
      lateralPercent: this.lateralPercent,
      longitudinalPercent: this.longitudinalPercent,
    }
  }
}
