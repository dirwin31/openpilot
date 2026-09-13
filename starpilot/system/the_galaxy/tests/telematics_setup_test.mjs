import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'

const assets = new URL('../assets/mobile/js/', import.meta.url)
const browser = (await readFile(new URL('browser.js', assets), 'utf8')).replace(/^export /gm, '')
const setup = (await readFile(new URL('offline.js', assets), 'utf8')).replace(/^import .*$/gm, '').replace(/^export /gm, '')
const remote = 'https://galaxy.firestar.link/1234567890abcdef/mobile/'
async function run({ url = remote, ios = false, snapshot = true, registerFails = false, identity = 'comma', savedIdentity = null } = {}) {
  const stored = new Map(savedIdentity ? [['galaxy-telematics-page:/1234567890abcdef/assets/mobile/telematics.html', savedIdentity]] : [])
  const calls = []
  let unregistered = false
  const registration = { active: { state: 'activated', addEventListener() {}, removeEventListener() {}, postMessage(_message, ports) { ports[0].postMessage({ ready: snapshot }); ports[0].close() } } }
  const context = vm.createContext({
    URL, Promise, Error, AbortSignal, MessageChannel, setTimeout, clearTimeout,
    window: { location: new URL(url), isSecureContext: url.startsWith('https:') },
    navigator: {
      userAgent: ios ? 'iPhone' : 'Android Chrome',
      serviceWorker: {
        async register(url, options) { calls.push({ url, options }); if (registerFails) throw new Error('Offline'); return registration },
        async getRegistration() { return registration },
        async getRegistrations() { return [{ scope: 'https://galaxy.firestar.link/1234567890abcdef/assets/mobile/', unregister: async () => { unregistered = true } }] },
      },
    },
    reactive: value => value,
    api: { getDeviceStatus: async () => { if (registerFails) throw new Error('Offline'); return { telematicsDeviceId: identity } } },
    localStorage: { getItem: key => stored.get(key), setItem: (key, value) => stored.set(key, value) },
  })
  vm.runInContext(browser + '\n' + setup, context)
  await vm.runInContext('prepareOffline()', context)
  return { context, calls, stored, unregistered, state: vm.runInContext('offlineState.state', context) }
}
for (const url of ['http://192.168.1.4:8082/', 'https://192.168.1.4:8443/']) {
  assert.equal((await run({ url })).calls.length, 0, 'Device pages must not register offline workers')
}
assert.equal((await run({ ios: true })).calls.length, 0, 'Keep the iOS gate')
const ready = await run()
assert.equal(ready.state, 'ready')
assert.equal(ready.calls[0].url, 'https://galaxy.firestar.link/1234567890abcdef/service-worker.js')
assert.equal(ready.calls[0].options.scope, '/1234567890abcdef', 'Scope includes the slashless PWA launch URL')
assert.equal(ready.unregistered, true, 'Migrate the old narrower worker only after verifying the new snapshot')
assert.equal(ready.stored.get('galaxy-telematics-page:/1234567890abcdef/assets/mobile/telematics.html'), 'comma')
assert.equal((await run({ snapshot: false })).state, 'error')
assert.equal((await run({ identity: null })).state, 'error')
assert.equal((await run({ registerFails: true, savedIdentity: 'comma' })).state, 'ready', 'Saved apps stay ready when registration cannot reach the network')
assert.equal(vm.runInContext('galaxyRoute("https://galaxy.firestar.link/1234567890abcdef")', ready.context), remote + '#/telematics')
assert.equal(vm.runInContext('galaxyRoute("https://galaxy.firestar.link/1234567890abcdef", "/bluetooth/phone")', ready.context), remote + '#/bluetooth/phone')
for (const url of ['https://galaxy.firestar.link.evil.example/1234567890abcdef', 'https://galaxy.firestar.link/', 'http://galaxy.link/1234567890abcdef']) {
  assert.equal(vm.runInContext(`galaxyRoute(${JSON.stringify(url)})`, ready.context), '')
}
console.log('Origin selection, slug links, automatic saving, migration, iOS blocking, and offline restoration passed')
