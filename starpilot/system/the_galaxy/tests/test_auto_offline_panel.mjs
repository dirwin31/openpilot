import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { reactive, compile } from '../assets/vendor/vue/vue.esm-browser.js'
import * as helpers from '../assets/mobile/js/components/auto_offline_helpers.js'

const source = readFileSync(new URL('../assets/mobile/js/components/AndroidAutoOfflinePanel.js', import.meta.url), 'utf8')
const api = {}
const panel = new Function('api', 'showSnackbar', 'usePolling', 'GxNotice', 'getMapboxSearchContext', ...Object.keys(helpers),
  source.replace(/^import [\s\S]*? from ".*?"\n/gm, '').replace('export const AndroidAutoOfflinePanel =', 'return'))(
  api, () => {}, () => {}, {}, () => {}, ...Object.values(helpers))
function instance() {
  const state = reactive({ ...panel.data(), token: '', metric: false })
  for (const [name, method] of Object.entries(panel.methods)) state[name] = method.bind(state)
  state.showPoint = () => {}
  return state
}
function deferred() {
  let resolve, reject
  const promise = new Promise((a, b) => { resolve = a; reject = b })
  return { promise, resolve, reject }
}
const point = { latitude: 36, longitude: -115, name: 'Home' }
const presets = [{ radius_km: 10, max_zoom: 16, fits: true, tiles: 100, bytes: 1000 }]

test('raw map points complete sizing through Vue reactive state', async () => {
  api.estimateAutoOffline = async () => ({ presets })
  const state = instance()
  await state.chooseAreaPoint(point, false)
  assert.notEqual(state.areaPoint, point)
  assert.deepEqual(state.areaPresets, presets)
  assert.equal(state.areaLoading, false)
  assert.equal(state.areaError, '')
})

test('older estimates cannot replace the newest selection', async () => {
  const first = deferred(), second = deferred()
  api.estimateAutoOffline = ({ latitude }) => latitude === 36 ? first.promise : second.promise
  const state = instance()
  const a = state.chooseAreaPoint(point)
  const b = state.chooseAreaPoint({ ...point, latitude: 37 })
  second.resolve({ presets })
  await b
  first.resolve({ presets: [{ radius_km: 999 }] })
  await a
  assert.deepEqual(state.areaPresets, presets)
  assert.equal(state.areaPoint.latitude, 37)
})

test('failed or empty estimates stop loading and can be retried', async () => {
  const state = instance()
  api.estimateAutoOffline = async () => { throw new Error('Connection lost') }
  await state.chooseAreaPoint(point)
  assert.equal(state.areaLoading, false)
  assert.equal(state.areaError, 'Connection lost')
  api.estimateAutoOffline = async () => ({ presets: [] })
  await state.chooseAreaPoint(point)
  assert.match(state.areaError, /No area sizes/)
  api.estimateAutoOffline = async () => ({ presets })
  await state.chooseAreaPoint(state.areaPoint, false)
  assert.equal(state.areaError, '')
  assert.deepEqual(state.areaPresets, presets)
})

test('placing a marker can preserve the current map camera', () => {
  let flights = 0, markers = 0
  globalThis.window = { mapboxgl: { Marker: class {
    setLngLat() { return this }
    addTo() { markers++; return this }
    remove() {}
  } } }
  const state = { map: { flyTo() { flights++ } } }
  panel.methods.showPoint.call(state, point, false)
  assert.equal(flights, 0)
  assert.equal(markers, 1)
  panel.methods.showPoint.call(state, point, true)
  assert.equal(flights, 1)
})

test('save queues the selected size then refreshes download status', async () => {
  const state = instance()
  state.areaPoint = point
  let body, refreshed = false
  api.addAutoOfflineArea = async (value) => { body = value; return { id: 'home' } }
  state.refresh = async () => { refreshed = true }
  await state.saveArea(presets[0])
  assert.deepEqual(body, { ...point, radius_km: 10, max_zoom: 16 })
  assert.equal(refreshed, true)
  assert.equal(state.areaPoint, null)
  assert.equal(state.busy, '')
})

test('route sizing ignores stale requests and exposes errors', async () => {
  const state = instance()
  state.selectedRoute = { points: [[36, -115], [37, -115]] }
  const pending = deferred()
  api.estimateAutoOffline = () => pending.promise
  const request = state.estimateRoute()
  state.clearRoutes()
  pending.resolve({ fits: true })
  await request
  assert.equal(state.routeEstimate, null)
  api.estimateAutoOffline = async () => { throw new Error('Try again') }
  await state.estimateRoute()
  assert.equal(state.routeError, 'Try again')
})

test('panel template compiles with progress accessibility and retry controls', () => {
  // Vue uses a DOM element to decode entities when compiling browser templates.
  globalThis.document = { createElement: () => ({ set innerHTML(value) { this.textContent = value.replaceAll('&amp;', '&'); this.children = [{ getAttribute: () => value.match(/foo="(.*)">/s)?.[1].replaceAll('&quot;', '"').replaceAll('&amp;', '&') }] } }) }
  assert.equal(typeof compile(panel.template), 'function')
})


test('offline request timeout aborts the fetch and reports a retryable error', async () => {
  const source = readFileSync(new URL('../assets/mobile/js/api.js', import.meta.url), 'utf8')
  const request = new Function(source.slice(source.indexOf('async function parse'), source.indexOf('async function requestOk')) + '; return request')()
  const originalFetch = globalThis.fetch
  globalThis.fetch = (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(new Error('aborted')))
  })
  try {
    await assert.rejects(request('/estimate', { timeout: 5 }), /took too long.*try again/)
  } finally {
    globalThis.fetch = originalFetch
  }
})
