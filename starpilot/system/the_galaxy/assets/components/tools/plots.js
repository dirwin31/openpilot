import { html, reactive } from "/assets/vendor/arrow-core.js"

const state = reactive({
  loading: true,
  error: "",
  paused: false,
  showAdvancedTerms: false,
  live: null,
  samples: [],
  overrides: { driveId: 0, isOnroad: false, episodes: [] },
  helpOpen: false,
})

// Below this |gap| the model and driver agree closely enough to call it "about the same".
const DISAGREEMENT_DEADBAND = 0.05

let initialized = false
let pollHandle = null
let lastTimestamp = 0
let visibilityListenerAttached = false

const MAX_POINTS = 240
const POLL_INTERVAL_MS = 750
const SVG_WIDTH = 1000
const SVG_HEIGHT = 260
const ADVANCED_TERMS_KEY = "plotsShowAdvancedTerms"
const QUALITY_WINDOW_SECONDS = 30
const QUALITY_MIN_SAMPLES = 8

const LATERAL_QUALITY_CONFIG = {
  desiredKey: "desiredLateralAccel",
  actualKey: "actualLateralAccel",
  minSpeedMps: 0.5,
  minDemand: 0.008,
  allowLowDemandFallback: true,
  fallbackMinSpeedMps: 0.5,
  fallbackMinPeakDemand: 0.01,
  great: 0.15,
  good: 0.30,
  fair: 0.50,
}

const LONGITUDINAL_QUALITY_CONFIG = {
  desiredKey: "desiredLongitudinalAccel",
  actualKey: "actualLongitudinalAccel",
  minSpeedMps: 0.0,
  minDemand: 0.05,
  allowLowDemandFallback: true,
  fallbackMinSpeedMps: 1.5,
  fallbackMinPeakDemand: 0.04,
  applyPersistenceRules: true,
  warnError: 0.50,
  severeError: 0.90,
  great: 0.32,
  good: 0.52,
  fair: 0.78,
}

function isPlotsRouteActive() {
  return window.location.pathname === "/plots"
}

function toNumber(value, fallback = 0) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function formatValue(value, digits = 2) {
  return toNumber(value).toFixed(digits)
}

function formatAge(seconds) {
  const value = Math.max(0, toNumber(seconds))
  if (value < 1) return `${Math.round(value * 1000)} ms`
  return `${value.toFixed(1)} s`
}

function formatSourceLabel(kind, source) {
  const normalizedKind = String(kind || "").toLowerCase()
  const normalizedSource = String(source || "").toLowerCase()

  if (normalizedKind === "lateral") {
    if (normalizedSource === "torquestate") return "Steering controller output"
    if (normalizedSource === "curvature") return "Path model estimate"
  }

  if (normalizedKind === "longitudinal") {
    if (normalizedSource.includes("atarget")) return "Planner target acceleration + measured acceleration"
    if (normalizedSource.includes("pid sum")) return "PID term sum + measured acceleration"
    if (normalizedSource.includes("caroutput")) return "Final accel command + measured acceleration"
    if (normalizedSource.includes("livelocationkalman")) return "Control output + calibrated acceleration"
    if (normalizedSource === "controlsstate") return "Planner target output"
  }

  if (normalizedKind === "lateralterms") {
    if (normalizedSource === "torquestate") return "Steering torque controller"
    if (normalizedSource === "pidstate") return "Steering angle PID controller"
  }

  if (normalizedKind === "longitudinalterms") {
    if (normalizedSource === "controlsstate") return "Longitudinal controller terms"
  }

  return "Live control signal"
}

function percentile(sortedValues, percentileValue) {
  const values = Array.isArray(sortedValues) ? sortedValues : []
  if (!values.length) return 0
  const p = Math.max(0, Math.min(1, Number(percentileValue)))
  const index = (values.length - 1) * p
  const lower = Math.floor(index)
  const upper = Math.ceil(index)
  if (lower === upper) return values[lower]
  const weight = index - lower
  return values[lower] * (1 - weight) + values[upper] * weight
}

function formatQualityLabel(label) {
  const text = String(label || "").trim()
  return text || "N/A"
}

function signalMagnitude(sample, config) {
  const desired = Math.abs(toNumber(sample?.[config.desiredKey], 0))
  const actual = Math.abs(toNumber(sample?.[config.actualKey], 0))
  return Math.max(desired, actual)
}

function computeMatchQuality(samples, config) {
  const safeSamples = Array.isArray(samples) ? samples : []
  if (safeSamples.length < 2) {
    return { label: "N/A", value: null, detail: "Waiting for data" }
  }

  const latestTs = toNumber(safeSamples[safeSamples.length - 1]?.timestamp, 0)
  const cutoffTs = latestTs > 0 ? latestTs - QUALITY_WINDOW_SECONDS : 0
  const recentSamples = safeSamples.filter((sample) => toNumber(sample?.timestamp, 0) >= cutoffTs)

  const eligibleSignalSamples = recentSamples.filter((sample) => {
    const speed = Math.abs(toNumber(sample?.speed, 0))
    const signal = signalMagnitude(sample, config)

    const speedOk = config.minSpeedMps <= 0 ? true : speed >= config.minSpeedMps
    const demandOk = config.minDemand <= 0 ? true : signal >= config.minDemand
    return speedOk && demandOk
  })

  let eligibleSamples = eligibleSignalSamples
  let usedLowDemandFallback = false
  const allowLowDemandFallback = config.allowLowDemandFallback !== false
  if (allowLowDemandFallback && eligibleSamples.length < QUALITY_MIN_SAMPLES && recentSamples.length >= QUALITY_MIN_SAMPLES) {
    const totalSpeed = recentSamples.reduce((sum, sample) => sum + Math.abs(toNumber(sample?.speed, 0)), 0)
    const avgSpeed = recentSamples.length > 0 ? (totalSpeed / recentSamples.length) : 0
    const peakDemand = recentSamples.reduce((peak, sample) => Math.max(peak, signalMagnitude(sample, config)), 0)

    const fallbackMinSpeed = Math.max(0, toNumber(config.fallbackMinSpeedMps, 0))
    const fallbackMinPeakDemand = Math.max(0, toNumber(config.fallbackMinPeakDemand, 0))
    if (avgSpeed >= fallbackMinSpeed && peakDemand >= fallbackMinPeakDemand) {
      eligibleSamples = recentSamples
      usedLowDemandFallback = true
    }
  }

  if (eligibleSamples.length < QUALITY_MIN_SAMPLES) {
    if (allowLowDemandFallback) {
      return {
        label: "N/A",
        value: null,
        detail: `Need ${QUALITY_MIN_SAMPLES} samples (${eligibleSamples.length} eligible / ${recentSamples.length} total)`,
      }
    }

    return {
      label: "N/A",
      value: null,
      detail: `Need ${QUALITY_MIN_SAMPLES} signal samples (${eligibleSamples.length} signal / ${recentSamples.length} total)`,
    }
  }

  const errors = eligibleSamples
    .map((sample) => Math.abs(toNumber(sample?.[config.desiredKey], 0) - toNumber(sample?.[config.actualKey], 0)))
    .sort((a, b) => a - b)

  if (!errors.length) {
    return { label: "N/A", value: null, detail: "No eligible samples" }
  }

  const p50 = percentile(errors, 0.50)
  const p90 = percentile(errors, 0.90)
  const robustError = (0.7 * p50) + (0.3 * p90)

  const sampleSummary = `${eligibleSamples.length} samples / ${QUALITY_WINDOW_SECONDS}s${usedLowDemandFallback ? ", low-demand fallback" : ""}`

  if (config.applyPersistenceRules) {
    const warnThreshold = Math.max(config.warnError || 0.35, config.good || 0.35)
    const severeThreshold = Math.max(config.severeError || 0.70, config.fair || 0.55)
    const warnFrac = errors.filter((value) => value > warnThreshold).length / errors.length
    const severeFrac = errors.filter((value) => value > severeThreshold).length / errors.length

    let label = "Poor"
    if (robustError <= config.great && warnFrac <= 0.18 && severeFrac <= 0.05) label = "Great"
    else if (robustError <= config.good && warnFrac <= 0.34 && severeFrac <= 0.12) label = "Good"
    else if (robustError <= config.fair && warnFrac <= 0.55 && severeFrac <= 0.24) label = "Fair"

    const warnPct = Math.round(warnFrac * 100)
    const severePct = Math.round(severeFrac * 100)
    return {
      label,
      value: robustError,
      detail: `${sampleSummary}, ${warnPct}% > ${formatValue(warnThreshold)} and ${severePct}% > ${formatValue(severeThreshold)}`,
    }
  }

  let label = "Poor"
  if (robustError <= config.great) label = "Great"
  else if (robustError <= config.good) label = "Good"
  else if (robustError <= config.fair) label = "Fair"

  return {
    label,
    value: robustError,
    detail: sampleSummary,
  }
}

function qualityToneClass(label) {
  const normalized = String(label || "").toLowerCase()
  if (normalized === "great") return "qualityTone-great"
  if (normalized === "good") return "qualityTone-good"
  if (normalized === "fair") return "qualityTone-fair"
  if (normalized === "poor") return "qualityTone-poor"
  return "qualityTone-na"
}

function stopPolling() {
  if (!pollHandle) return
  clearTimeout(pollHandle)
  pollHandle = null
}

function pushSample(payload) {
  const timestamp = toNumber(payload.timestamp, 0)
  if (!timestamp || timestamp <= 0 || timestamp === lastTimestamp) return

  lastTimestamp = timestamp
  state.samples.push({
    timestamp,
    speed: toNumber(payload.speed),
    controlsActive: !!payload.controlsActive,
    longitudinalControlActive: !!payload.longitudinalControlActive,
    desiredLateralAccel: toNumber(payload.desiredLateralAccel),
    actualLateralAccel: toNumber(payload.actualLateralAccel),
    desiredLongitudinalAccel: toNumber(payload.desiredLongitudinalAccel),
    actualLongitudinalAccel: toNumber(payload.actualLongitudinalAccel),
    lateralP: toNumber(payload.lateralP),
    lateralI: toNumber(payload.lateralI),
    lateralD: toNumber(payload.lateralD),
    lateralF: toNumber(payload.lateralF),
    longitudinalUpAccelCmd: toNumber(payload.longitudinalUpAccelCmd),
    longitudinalUiAccelCmd: toNumber(payload.longitudinalUiAccelCmd),
    longitudinalUfAccelCmd: toNumber(payload.longitudinalUfAccelCmd),
    modelLateralAccel: toNumber(payload.modelLateralAccel),
    driverLateralAccel: toNumber(payload.driverLateralAccel),
    modelCurvature: toNumber(payload.modelCurvature),
    steeringPressed: !!payload.steeringPressed,
    steeringTorque: toNumber(payload.steeringTorque),
  })

  if (state.samples.length > MAX_POINTS) {
    state.samples.splice(0, state.samples.length - MAX_POINTS)
  }
}

async function fetchLiveData() {
  const response = await fetch("/api/plots/live")
  const payload = await response.json()

  if (!response.ok) {
    throw new Error(payload.error || response.statusText || "Failed to load live plot data")
  }

  state.live = payload
  state.error = ""
  state.loading = false

  if (!payload.stale) {
    pushSample(payload)
  }
}

async function fetchOverrideLog() {
  const response = await fetch("/api/plots/overrides")
  const payload = await response.json()

  if (!response.ok) {
    throw new Error(payload.error || response.statusText || "Failed to load override log")
  }

  state.overrides = {
    driveId: toNumber(payload.driveId),
    isOnroad: !!payload.isOnroad,
    episodes: Array.isArray(payload.episodes) ? payload.episodes : [],
  }
}

function ensurePolling() {
  if (pollHandle) return

  const poll = async () => {
    if (!isPlotsRouteActive()) {
      pollHandle = null
      return
    }

    if (document.visibilityState !== "visible") {
      pollHandle = setTimeout(poll, POLL_INTERVAL_MS)
      return
    }

    if (!state.paused) {
      try {
        await fetchLiveData()
      } catch (error) {
        state.error = error?.message || String(error)
        state.loading = false
      }

      // Refresh the last-drive override log alongside live data; failures here are non-fatal.
      fetchOverrideLog().catch(() => {})
    }

    pollHandle = setTimeout(poll, POLL_INTERVAL_MS)
  }

  pollHandle = setTimeout(poll, POLL_INTERVAL_MS)
}

function clearHistory() {
  state.samples = []
  lastTimestamp = 0
}

function togglePaused() {
  state.paused = !state.paused
  if (state.paused) {
    stopPolling()
    return
  }

  if (!isPlotsRouteActive()) return

  fetchLiveData().catch((error) => {
    state.error = error?.message || String(error)
  })
  ensurePolling()
}

function toggleAdvancedTerms() {
  state.showAdvancedTerms = !state.showAdvancedTerms
  try {
    localStorage.setItem(ADVANCED_TERMS_KEY, state.showAdvancedTerms ? "1" : "0")
  } catch (error) {
    console.warn("Failed to persist plots advanced terms preference", error)
  }
}

function toggleHelp() {
  state.helpOpen = !state.helpOpen
}

function yForValue(value, min, max) {
  const safeMin = toNumber(min, -1)
  const safeMax = toNumber(max, 1)
  const clamped = Math.max(safeMin, Math.min(safeMax, toNumber(value)))
  const span = Math.max(1e-6, safeMax - safeMin)
  return ((safeMax - clamped) / span) * SVG_HEIGHT
}

function computeRange(samples, desiredKey, actualKey) {
  let maxAbs = 0
  for (const sample of samples) {
    maxAbs = Math.max(
      maxAbs,
      Math.abs(toNumber(sample?.[desiredKey])),
      Math.abs(toNumber(sample?.[actualKey])),
    )
  }

  const halfSpan = Math.max(1.5, Math.ceil((maxAbs * 1.25) * 10) / 10)
  return { min: -halfSpan, max: halfSpan }
}

function buildPolyline(samples, key, min, max) {
  if (!samples.length) return ""

  const lastIndex = Math.max(1, samples.length - 1)
  return samples.map((sample, index) => {
    const x = (index / lastIndex) * SVG_WIDTH
    const y = yForValue(sample?.[key], min, max)
    return `${x.toFixed(2)},${y.toFixed(2)}`
  }).join(" ")
}

function computeRangeForKeys(samples, keys) {
  let maxAbs = 0
  for (const sample of samples) {
    for (const key of keys) {
      maxAbs = Math.max(maxAbs, Math.abs(toNumber(sample?.[key])))
    }
  }

  const halfSpan = Math.max(0.15, Math.ceil((maxAbs * 1.35) * 1000) / 1000)
  return { min: -halfSpan, max: halfSpan }
}

function latestSampleValue(key) {
  const latest = state.samples[state.samples.length - 1]
  if (latest) return latest[key]
  return toNumber(state.live?.[key])
}

function PlotCard(title, desiredKey, actualKey, sourceKind, sourceLabel, desiredLabel = "Target", actualLabel = "Measured") {
  const hasData = state.samples.length > 1
  const range = computeRange(state.samples, desiredKey, actualKey)
  const desiredPolyline = buildPolyline(state.samples, desiredKey, range.min, range.max)
  const actualPolyline = buildPolyline(state.samples, actualKey, range.min, range.max)
  const zeroY = yForValue(0, range.min, range.max)
  const topQuarterY = yForValue(range.max * 0.5, range.min, range.max)
  const bottomQuarterY = yForValue(range.min * 0.5, range.min, range.max)

  return html`
    <section class="plotCard plotChartCard">
      <div class="plotCardHeader">
        <h2>${title}</h2>
        <span class="plotSource">Source: ${formatSourceLabel(sourceKind, sourceLabel)}</span>
      </div>

      <div class="plotLegend">
        <span class="plotLegendItem"><i class="plotLegendLine desired"></i>${desiredLabel}: ${formatValue(latestSampleValue(desiredKey))}</span>
        <span class="plotLegendItem"><i class="plotLegendLine actual"></i>${actualLabel}: ${formatValue(latestSampleValue(actualKey))}</span>
      </div>

      <div class="plotSvgWrap">
        ${hasData ? html`
          <svg class="plotSvg" viewBox="0 0 ${SVG_WIDTH} ${SVG_HEIGHT}" preserveAspectRatio="none" role="img" aria-label="${title} live plot">
            <line class="plotGridLine" x1="0" y1="${topQuarterY}" x2="${SVG_WIDTH}" y2="${topQuarterY}"></line>
            <line class="plotZeroLine" x1="0" y1="${zeroY}" x2="${SVG_WIDTH}" y2="${zeroY}"></line>
            <line class="plotGridLine" x1="0" y1="${bottomQuarterY}" x2="${SVG_WIDTH}" y2="${bottomQuarterY}"></line>
            <polyline class="plotLine desired" points="${desiredPolyline}"></polyline>
            <polyline class="plotLine actual" points="${actualPolyline}"></polyline>
          </svg>
        ` : html`
          <div class="plotEmpty">Waiting for enough live samples...</div>
        `}
      </div>

      <div class="plotRangeRow">
        <span>${formatValue(range.max, 1)} m/s²</span>
        <span>0.0 m/s²</span>
        <span>${formatValue(range.min, 1)} m/s²</span>
      </div>
    </section>
  `
}

function LateralTermsPlotCard(sourceLabel) {
  const hasData = state.samples.length > 1
  const keys = ["lateralP", "lateralI", "lateralD", "lateralF"]
  const range = computeRangeForKeys(state.samples, keys)
  const zeroY = yForValue(0, range.min, range.max)
  const topQuarterY = yForValue(range.max * 0.5, range.min, range.max)
  const bottomQuarterY = yForValue(range.min * 0.5, range.min, range.max)

  return html`
    <section class="plotCard plotChartCard">
      <div class="plotCardHeader">
        <h2>Lateral Controller Terms</h2>
        <span class="plotSource">Source: ${formatSourceLabel("lateralTerms", sourceLabel)}</span>
      </div>

      <div class="plotLegend">
        <span class="plotLegendItem"><i class="plotLegendLine p"></i>P: ${formatValue(latestSampleValue("lateralP"), 3)}</span>
        <span class="plotLegendItem"><i class="plotLegendLine i"></i>I: ${formatValue(latestSampleValue("lateralI"), 3)}</span>
        <span class="plotLegendItem"><i class="plotLegendLine d"></i>D: ${formatValue(latestSampleValue("lateralD"), 3)}</span>
        <span class="plotLegendItem"><i class="plotLegendLine f"></i>F: ${formatValue(latestSampleValue("lateralF"), 3)}</span>
      </div>

      <div class="plotSvgWrap">
        ${hasData ? html`
          <svg class="plotSvg" viewBox="0 0 ${SVG_WIDTH} ${SVG_HEIGHT}" preserveAspectRatio="none" role="img" aria-label="Lateral controller terms live plot">
            <line class="plotGridLine" x1="0" y1="${topQuarterY}" x2="${SVG_WIDTH}" y2="${topQuarterY}"></line>
            <line class="plotZeroLine" x1="0" y1="${zeroY}" x2="${SVG_WIDTH}" y2="${zeroY}"></line>
            <line class="plotGridLine" x1="0" y1="${bottomQuarterY}" x2="${SVG_WIDTH}" y2="${bottomQuarterY}"></line>
            <polyline class="plotLine p" points="${buildPolyline(state.samples, "lateralP", range.min, range.max)}"></polyline>
            <polyline class="plotLine i" points="${buildPolyline(state.samples, "lateralI", range.min, range.max)}"></polyline>
            <polyline class="plotLine d" points="${buildPolyline(state.samples, "lateralD", range.min, range.max)}"></polyline>
            <polyline class="plotLine f" points="${buildPolyline(state.samples, "lateralF", range.min, range.max)}"></polyline>
          </svg>
        ` : html`
          <div class="plotEmpty">Waiting for enough live samples...</div>
        `}
      </div>

      <div class="plotRangeRow">
        <span>${formatValue(range.max, 3)}</span>
        <span>0.000</span>
        <span>${formatValue(range.min, 3)}</span>
      </div>
    </section>
  `
}

function LongitudinalTermsPlotCard(sourceLabel) {
  const hasData = state.samples.length > 1
  const keys = ["longitudinalUpAccelCmd", "longitudinalUiAccelCmd", "longitudinalUfAccelCmd"]
  const range = computeRangeForKeys(state.samples, keys)
  const zeroY = yForValue(0, range.min, range.max)
  const topQuarterY = yForValue(range.max * 0.5, range.min, range.max)
  const bottomQuarterY = yForValue(range.min * 0.5, range.min, range.max)

  return html`
    <section class="plotCard plotChartCard">
      <div class="plotCardHeader">
        <h2>Longitudinal Accel Cmd Terms</h2>
        <span class="plotSource">Source: ${formatSourceLabel("longitudinalTerms", sourceLabel)}</span>
      </div>

      <div class="plotLegend">
        <span class="plotLegendItem"><i class="plotLegendLine p"></i>Up: ${formatValue(latestSampleValue("longitudinalUpAccelCmd"), 3)}</span>
        <span class="plotLegendItem"><i class="plotLegendLine i"></i>Ui: ${formatValue(latestSampleValue("longitudinalUiAccelCmd"), 3)}</span>
        <span class="plotLegendItem"><i class="plotLegendLine f"></i>Uf: ${formatValue(latestSampleValue("longitudinalUfAccelCmd"), 3)}</span>
      </div>

      <div class="plotSvgWrap">
        ${hasData ? html`
          <svg class="plotSvg" viewBox="0 0 ${SVG_WIDTH} ${SVG_HEIGHT}" preserveAspectRatio="none" role="img" aria-label="Longitudinal accel cmd terms live plot">
            <line class="plotGridLine" x1="0" y1="${topQuarterY}" x2="${SVG_WIDTH}" y2="${topQuarterY}"></line>
            <line class="plotZeroLine" x1="0" y1="${zeroY}" x2="${SVG_WIDTH}" y2="${zeroY}"></line>
            <line class="plotGridLine" x1="0" y1="${bottomQuarterY}" x2="${SVG_WIDTH}" y2="${bottomQuarterY}"></line>
            <polyline class="plotLine p" points="${buildPolyline(state.samples, "longitudinalUpAccelCmd", range.min, range.max)}"></polyline>
            <polyline class="plotLine i" points="${buildPolyline(state.samples, "longitudinalUiAccelCmd", range.min, range.max)}"></polyline>
            <polyline class="plotLine f" points="${buildPolyline(state.samples, "longitudinalUfAccelCmd", range.min, range.max)}"></polyline>
          </svg>
        ` : html`
          <div class="plotEmpty">Waiting for enough live samples...</div>
        `}
      </div>

      <div class="plotRangeRow">
        <span>${formatValue(range.max, 3)}</span>
        <span>0.000</span>
        <span>${formatValue(range.min, 3)}</span>
      </div>
    </section>
  `
}

function buildOverrideBands(samples) {
  // Returned as CSS percentages for an HTML overlay (not SVG <rect>): ArrowJS can't build
  // sub-templates in the SVG namespace, so the shaded bands live as divs over the chart.
  const runs = []
  if (samples.length < 2) return runs

  const lastIndex = Math.max(1, samples.length - 1)
  let runStart = -1
  for (let i = 0; i < samples.length; i++) {
    const pressed = !!samples[i]?.steeringPressed
    if (pressed && runStart < 0) {
      runStart = i
    } else if (!pressed && runStart >= 0) {
      runs.push({ startIndex: runStart, endIndex: i - 1 })
      runStart = -1
    }
  }
  if (runStart >= 0) runs.push({ startIndex: runStart, endIndex: samples.length - 1 })

  return runs.map((run) => {
    const leftPct = (run.startIndex / lastIndex) * 100
    const rightPct = (run.endIndex / lastIndex) * 100
    return { leftPct, widthPct: Math.max(0.5, rightPct - leftPct) }
  })
}

// Signed gap relative to the turn direction: >0 = driver steering tighter than the model asked,
// <0 = wider. Raw (driver - model) is direction-ambiguous (both negative in a right turn), so
// project onto the turn direction. Mirrors backend _directional_gap.
function directionalGap(modelAccel, driverAccel) {
  const model = toNumber(modelAccel)
  const driver = toNumber(driverAccel)
  const ref = Math.abs(model) > 1e-6 ? model : driver
  const refSign = ref >= 0 ? 1 : -1
  return refSign * (driver - model)
}

// Expects an already direction-relative gap (see directionalGap / backend gapMean).
function describeGap(directionRelativeGap) {
  const value = toNumber(directionRelativeGap)
  if (Math.abs(value) < DISAGREEMENT_DEADBAND) {
    return { word: "about the same", tone: "great", magnitude: Math.abs(value) }
  }
  return {
    word: value > 0 ? "tighter" : "wider",
    tone: Math.abs(value) < 0.3 ? "good" : (Math.abs(value) < 0.5 ? "fair" : "poor"),
    magnitude: Math.abs(value),
  }
}

function computeLateralDisagreement(samples) {
  const config = LATERAL_QUALITY_CONFIG
  const safeSamples = Array.isArray(samples) ? samples : []
  if (!safeSamples.length) {
    return { label: "N/A", detail: "Waiting for data", value: null }
  }

  const latestTs = toNumber(safeSamples[safeSamples.length - 1]?.timestamp, 0)
  const cutoffTs = latestTs > 0 ? latestTs - QUALITY_WINDOW_SECONDS : 0
  const eligible = safeSamples.filter((sample) => {
    if (!sample?.steeringPressed) return false
    if (toNumber(sample?.timestamp, 0) < cutoffTs) return false
    const speed = Math.abs(toNumber(sample?.speed, 0))
    const demand = Math.max(Math.abs(toNumber(sample?.modelLateralAccel)), Math.abs(toNumber(sample?.driverLateralAccel)))
    const speedOk = config.minSpeedMps <= 0 ? true : speed >= config.minSpeedMps
    const demandOk = config.minDemand <= 0 ? true : demand >= config.minDemand
    return speedOk && demandOk
  })

  if (eligible.length < QUALITY_MIN_SAMPLES) {
    return { label: "N/A", value: null, detail: `Need ${QUALITY_MIN_SAMPLES} override samples (${eligible.length} so far)` }
  }

  const gaps = eligible
    .map((sample) => directionalGap(sample?.modelLateralAccel, sample?.driverLateralAccel))
    .sort((a, b) => a - b)
  const medianGap = percentile(gaps, 0.5)
  const described = describeGap(medianGap)

  return {
    label: described.word,
    tone: described.tone,
    value: medianGap,
    magnitude: described.magnitude,
    detail: `${eligible.length} override samples / ${QUALITY_WINDOW_SECONDS}s`,
  }
}

function ModelVsDriverLateralCard() {
  const hasData = state.samples.length > 1
  const keys = ["modelLateralAccel", "driverLateralAccel"]
  const range = computeRange(state.samples, keys[0], keys[1])
  const zeroY = yForValue(0, range.min, range.max)
  const topQuarterY = yForValue(range.max * 0.5, range.min, range.max)
  const bottomQuarterY = yForValue(range.min * 0.5, range.min, range.max)
  const bands = buildOverrideBands(state.samples)
  const disagreement = computeLateralDisagreement(state.samples)

  return html`
    <section class="plotCard plotChartCard">
      <div class="plotCardHeader">
        <h2>Model Command vs You (Lateral)</h2>
        <span class="plotSource">Source: Model path request vs your steering</span>
      </div>

      <div class="plotLegend">
        <span class="plotLegendItem"><i class="plotLegendLine model"></i>Model command: ${formatValue(latestSampleValue("modelLateralAccel"))}</span>
        <span class="plotLegendItem"><i class="plotLegendLine driver"></i>You (measured): ${formatValue(latestSampleValue("driverLateralAccel"))}</span>
        <span class="plotLegendItem"><i class="plotLegendBand"></i>Override active</span>
      </div>

      <div class="plotSvgWrap plotOverlayWrap">
        ${hasData ? html`
          ${bands.map((band) => html`<div class="plotOverrideBand" style="left:${band.leftPct.toFixed(2)}%;width:${band.widthPct.toFixed(2)}%"></div>`)}
          <svg class="plotSvg" viewBox="0 0 ${SVG_WIDTH} ${SVG_HEIGHT}" preserveAspectRatio="none" role="img" aria-label="Model command vs driver lateral plot">
            <line class="plotGridLine" x1="0" y1="${topQuarterY}" x2="${SVG_WIDTH}" y2="${topQuarterY}"></line>
            <line class="plotZeroLine" x1="0" y1="${zeroY}" x2="${SVG_WIDTH}" y2="${zeroY}"></line>
            <line class="plotGridLine" x1="0" y1="${bottomQuarterY}" x2="${SVG_WIDTH}" y2="${bottomQuarterY}"></line>
            <polyline class="plotLine model" points="${buildPolyline(state.samples, "modelLateralAccel", range.min, range.max)}"></polyline>
            <polyline class="plotLine driver" points="${buildPolyline(state.samples, "driverLateralAccel", range.min, range.max)}"></polyline>
          </svg>
        ` : html`
          <div class="plotEmpty">Waiting for enough live samples...</div>
        `}
      </div>

      <div class="plotRangeRow">
        <span>${formatValue(range.max, 1)} m/s²</span>
        <span>0.0 m/s²</span>
        <span>${formatValue(range.min, 1)} m/s²</span>
      </div>

      <div class="qualitySummaryRow">
        <p class="qualitySentence">
          ${disagreement.value === null
            ? html`When you override, model vs you is <span class="qualityTone qualityTone-na">N/A</span>`
            : html`When you override, you steer <span class="qualityTone ${qualityToneClass(disagreement.tone)}">${disagreement.label}</span>${disagreement.label === "about the same" ? "" : html` than the model by ${formatValue(disagreement.magnitude)} m/s²`}`}
        </p>
        <p class="qualityDetail">${disagreement.detail}</p>
      </div>

      ${HowToReadThis()}
    </section>
  `
}

function HowToReadThis() {
  // Driven by reactive state (not native <details>) so it survives the live re-render on each poll.
  return html`
    <div class="plotHelp">
      <button class="plotHelpToggle" @click="${toggleHelp}">
        <span class="plotHelpCaret ${state.helpOpen ? "open" : ""}">▸</span> How to read this
      </button>
      ${state.helpOpen ? html`
      <div class="plotHelpBody">
        <p>
          <strong>Tighter</strong> = you steered more into the turn than the model asked;
          <strong>wider</strong> = less. One event means little — look for the same direction
          repeating across the Last Drive log below.
        </p>
        <p>
          <strong>First glance at the "Lateral Response" chart above</strong> — it shows whether the
          controller is actually <em>achieving</em> the model's target. That's what tells a tuning
          problem apart from a preference:
        </p>
        <ul class="plotHelpList">
          <li>
            <strong>Tracks well, but you still override</strong> → the tune is fine; you disagree with
            the model's line. Look at lane position (<code>LaneCenterOffset</code> /
            <code>CameraOffset</code>), or it's just preference.
          </li>
          <li>
            <strong>Tracks poorly (car lags / understeers) and you correct it</strong> → your override
            is compensating for a mistune. Use the patterns below.
          </li>
        </ul>
        <div class="plotHelpTable">
          <div class="plotHelpRow"><span>Always tighter, car under-responds in curves</span><span>↑ <code>SteerLatAccel</code> or <code>SteerKP</code></span></div>
          <div class="plotHelpRow"><span>Gap scales with how sharp the curve is</span><span><code>SteerRatio</code> or <code>SteerFriction</code></span></div>
          <div class="plotHelpRow"><span>Vague / sticky near center, wanders on straights</span><span><code>SteerFriction</code></span></div>
          <div class="plotHelpRow"><span>You correct just after the model moves (timing)</span><span><code>SteerDelay</code></span></div>
          <div class="plotHelpRow"><span>Constant one-side bias regardless of curve</span><span><code>SteerOffset</code></span></div>
        </div>
        <p class="plotHelpNote">
          Guidance only — this page never changes any values. Confirm a pattern repeats across
          conditions before you touch a knob, then tune in your own workflow.
        </p>
      </div>
      ` : ""}
    </div>
  `
}

function OverrideEventLog() {
  const episodes = Array.isArray(state.overrides?.episodes) ? state.overrides.episodes : []
  const ordered = episodes.slice().reverse()

  return html`
    <section class="plotCard overrideLogCard">
      <div class="plotCardHeader">
        <h2>Last Drive — Override Events (${episodes.length})</h2>
        <span class="plotSource">${state.overrides?.isOnroad ? "Recording this drive" : "Last completed drive"}</span>
      </div>

      ${ordered.length ? html`
        <div class="overrideTableWrap">
          <table class="overrideTable">
            <thead>
              <tr>
                <th>Duration</th>
                <th>Speed</th>
                <th>Turn</th>
                <th>Model wanted</th>
                <th>You did</th>
                <th>Gap</th>
              </tr>
            </thead>
            <tbody>
              ${ordered.map((episode) => {
                const gap = describeGap(episode.gapMean)
                return html`
                  <tr>
                    <td data-label="Duration">${formatValue(episode.durationS, 1)} s</td>
                    <td data-label="Speed">${formatValue(episode.avgSpeed, 1)} m/s</td>
                    <td data-label="Turn">${episode.direction === "left" ? "Left" : "Right"}</td>
                    <td data-label="Model wanted">${formatValue(episode.modelLatAccelMean)} m/s²</td>
                    <td data-label="You did">${formatValue(episode.driverLatAccelMean)} m/s²</td>
                    <td data-label="Gap" class="qualityTone ${qualityToneClass(gap.tone)}">
                      ${gap.word === "about the same" ? "≈ same" : `${formatValue(gap.magnitude)} ${gap.word}`}
                    </td>
                  </tr>
                `
              })}
            </tbody>
          </table>
        </div>
      ` : html`
        <div class="plotEmpty">No overrides recorded this drive yet.</div>
      `}
    </section>
  `
}

async function initialize() {
  try {
    state.showAdvancedTerms = localStorage.getItem(ADVANCED_TERMS_KEY) === "1"
  } catch (error) {
    state.showAdvancedTerms = false
  }

  try {
    await fetchLiveData()
  } catch (error) {
    state.error = error?.message || String(error)
    state.loading = false
  } finally {
    ensurePolling()
  }

  fetchOverrideLog().catch(() => {})

  if (!visibilityListenerAttached) {
    visibilityListenerAttached = true
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") {
        stopPolling()
        return
      }

      if (isPlotsRouteActive() && !state.paused) {
        fetchLiveData().catch((error) => {
          state.error = error?.message || String(error)
          state.loading = false
        })
        ensurePolling()
      }
    })
  }
}

export function LivePlots() {
  if (!initialized) {
    initialized = true
    initialize()
  }
  if (!state.paused) {
    ensurePolling()
  }

  return html`
    <div class="plotsPage">
      <h1>Plots</h1>

      <section class="plotCard plotStatusCard">
        <p class="plotDescription">
          Live comparison view for tuning diagnostics. These scores are a quick health check, not a final verdict. Short spikes from bumps, lane changes, traffic transitions, and manual inputs can temporarily lower a score.
        </p>
        <p class="qualityUserGuidance">
          Work in progress: use this as trend guidance over time. If you see a brief "Poor," keep driving and look for repeated behavior across multiple situations before changing tune values.
        </p>

        <div class="plotStatusGrid">
          <p><strong>Onroad:</strong> ${state.live?.isOnroad ? "Yes" : "No"}</p>
          <p><strong>Sample Age:</strong> ${formatAge(state.live?.sampleAgeSeconds)}</p>
          <p><strong>Vehicle Speed:</strong> ${formatValue(state.live?.speed)} m/s</p>
          <p><strong>Samples:</strong> ${state.samples.length}</p>
        </div>

        ${() => {
          const lateralQuality = computeMatchQuality(state.samples, LATERAL_QUALITY_CONFIG)
          const longQuality = computeMatchQuality(state.samples, LONGITUDINAL_QUALITY_CONFIG)

          return html`
            <div class="qualitySummaryGrid">
              <div class="qualitySummaryRow">
                <p class="qualitySentence">
                  Your lateral tuning is
                  <span class="qualityTone ${qualityToneClass(lateralQuality.label)}">${formatQualityLabel(lateralQuality.label)}</span>
                </p>
                <p class="qualityDetail">
                  ${lateralQuality.value === null ? lateralQuality.detail : `${formatValue(lateralQuality.value)} m/s² error (${lateralQuality.detail})`}
                </p>
              </div>
              <div class="qualitySummaryRow">
                <p class="qualitySentence">
                  Your longitudinal tuning is
                  <span class="qualityTone ${qualityToneClass(longQuality.label)}">${formatQualityLabel(longQuality.label)}</span>
                </p>
                <p class="qualityDetail">
                  ${longQuality.value === null ? longQuality.detail : `${formatValue(longQuality.value)} m/s² error (${longQuality.detail})`}
                </p>
              </div>
            </div>
          `
        }}

        <div class="plotActions">
          <button class="plotButton" @click="${togglePaused}">
            ${state.paused ? "Resume Live" : "Pause Live"}
          </button>
          <button class="plotButton" @click="${clearHistory}">
            Clear History
          </button>
        </div>

        ${state.error ? html`<p class="plotError"><strong>Error:</strong> ${state.error}</p>` : ""}
        ${state.live?.lastError ? html`<p class="plotError"><strong>Source Error:</strong> ${state.live.lastError}</p>` : ""}
        <p class="qualityMethodNote">
          Match rating uses a 30-second rolling window. Strong steering or accel moments are preferred, but gentler windows can still earn a rating so normal driving does not sit at N/A. Longitudinal also checks how much of the window stays above error limits so brief spikes are less likely to mark "Poor."
        </p>
      </section>

      <div class="plotCharts">
        ${PlotCard(
          "Lateral Response (m/s²)",
          "desiredLateralAccel",
          "actualLateralAccel",
          "lateral",
          state.live?.lateralSource,
          "Target",
          "Measured",
        )}
        ${PlotCard(
          "Longitudinal Response (m/s²)",
          "desiredLongitudinalAccel",
          "actualLongitudinalAccel",
          "longitudinal",
          state.live?.longitudinalSource,
          "Target",
          "Measured",
        )}
      </div>

      <div class="plotCharts">
        ${ModelVsDriverLateralCard()}
      </div>

      ${OverrideEventLog()}

      <div class="plotAdvancedRow">
        <button class="plotButton" @click="${toggleAdvancedTerms}">
          ${state.showAdvancedTerms ? "Hide Advanced Controller Terms" : "Show Advanced Controller Terms"}
        </button>
      </div>

      ${state.showAdvancedTerms ? html`
        <div class="plotCharts">
          ${LateralTermsPlotCard(state.live?.lateralTermsSource)}
          ${LongitudinalTermsPlotCard(state.live?.longitudinalTermsSource)}
        </div>
      ` : ""}

      ${state.loading ? html`<p>Loading live data...</p>` : ""}
    </div>
  `
}
