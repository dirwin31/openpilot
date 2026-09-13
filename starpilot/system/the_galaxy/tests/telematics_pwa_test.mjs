import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'

const assets = new URL('../assets/', import.meta.url)
const rootSource = await readFile(new URL('service-worker.js', assets), 'utf8')
const offlineSource = await readFile(new URL('mobile/telematics-worker.js', assets), 'utf8')
for (const prefix of ['', '/1234567890abcdef']) {
  const base = `https://galaxy.firestar.link${prefix}`
  const listeners = new Map()
  const entries = new Map()
  const cache = {
    async addAll(requests) {
      for (const request of requests) {
        const path = new URL(request.url).pathname.slice(`${prefix}/assets/`.length)
        entries.set(request.url, new Response(await readFile(new URL(path, assets))))
      }
    },
    async match(request) { return entries.get(typeof request === 'string' ? request : request.url)?.clone() },
  }
  let network = async () => { throw new Error('No internet') }
  const context = vm.createContext({
    URL, Request, Response, Set, Promise, AbortController, setTimeout, clearTimeout,
    self: {
      location: new URL(`${base}/service-worker.js`),
      registration: { scope: `${base || '/'}` },
      addEventListener(type, handler) {
        if (!listeners.has(type)) listeners.set(type, [])
        listeners.get(type).push(handler)
      },
      skipWaiting: async () => {}, clients: { claim: async () => {} },
    },
    caches: { open: async () => cache, keys: async () => [], delete: async () => {} },
    fetch: (...args) => network(...args),
    importScripts(path) {
      assert.equal(path, 'assets/mobile/telematics-worker.js')
      vm.runInContext(offlineSource, context)
    },
  })
  vm.runInContext(rootSource, context)
  const emit = async (type, event = {}) => {
    const pending = []
    for (const handler of listeners.get(type) || []) handler({ ...event, waitUntil: promise => pending.push(promise) })
    await Promise.all(pending)
  }
  await emit('install')
  await emit('activate')
  assert.ok(listeners.has('push') && listeners.has('notificationclick'), 'PWA must preserve notifications')
  let ready
  await emit('message', { data: { type: 'TELEMATICS_OFFLINE_STATUS' }, ports: [{ postMessage: data => { ready = data.ready } }] })
  assert.equal(ready, true)
  const fetchEvent = async (url, mode = 'navigate', method = 'GET') => {
    let response
    await emit('fetch', { request: { url, mode, method }, respondWith: promise => { response = promise } })
    return response
  }
  for (const path of ['', '/', '/mobile', '/mobile/', '/assets/mobile/index.html']) {
    const response = await fetchEvent(base + path)
    assert.equal(response.status, 302, `Cold PWA launch: ${path}`)
    assert.equal(response.headers.get('location'), `${base}/assets/mobile/telematics.html#/telematics`)
  }
  assert.ok((await fetchEvent(`${base}/assets/mobile/telematics.html`)).ok)
  for (const path of ['/api/device/status', '/api/telematics/stream', '/unrelated']) {
    assert.equal(await fetchEvent(base + path), undefined, `Do not cache ${path}`)
  }
  assert.equal(await fetchEvent(base, 'navigate', 'POST'), undefined)
  network = async () => new Response('Tunnel unavailable', { status: 503 })
  assert.equal((await fetchEvent(base)).status, 302)
  network = async () => new Response('Online Galaxy')
  assert.equal(await (await fetchEvent(base)).text(), 'Online Galaxy')
  const missing = entries.keys().next().value
  entries.delete(missing)
  await emit('message', { data: { type: 'TELEMATICS_OFFLINE_STATUS' }, ports: [{ postMessage: data => { ready = data.ready } }] })
  assert.equal(ready, false, 'Do not claim an incomplete snapshot is saved')
  await emit('message', { data: { type: 'TELEMATICS_OFFLINE_STATUS', repair: true }, ports: [{ postMessage: data => { ready = data.ready } }] })
  assert.equal(ready, true, 'Retry repairs evicted files without requiring a worker version change')
}
console.log('PWA cold launch, slug routing, offline fallback, snapshot verification, and push coexistence passed')
