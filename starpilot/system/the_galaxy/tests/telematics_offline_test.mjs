import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'

const assets = new URL('../assets/', import.meta.url)
const source = await readFile(new URL('mobile/telematics-worker.js', assets), 'utf8')
for (const prefix of ['', '/device123']) {
  const origin = `https://galaxy.test${prefix}`
  const handlers = {}
  const entries = new Map()
  const cache = {
    async addAll(requests) {
      for (const request of requests) {
        const relative = new URL(request.url).pathname.replace(`${prefix}/assets/`, '')
        const body = await readFile(new URL(relative, assets))
        entries.set(request.url, new Response(body))
      }
    },
    async match(request) { return entries.get(request.url.split('?')[0])?.clone() },
  }
  const context = vm.createContext({
    URL, Request, Set,
    self: {
      registration: { scope: `${origin}/assets/mobile/` },
      location: { href: `${origin}/assets/mobile/telematics-worker.js` },
      addEventListener: (type, callback) => { handlers[type] = callback },
      skipWaiting: async () => {}, clients: { claim: async () => {} },
    },
    caches: { open: async () => cache, keys: async () => [], delete: async () => {} },
    fetch: async () => { throw new Error('Offline') },
  })
  vm.runInContext(source, context)
  let pending
  handlers.install({ waitUntil: promise => { pending = promise } })
  await pending
  handlers.activate({ waitUntil: promise => { pending = promise } })
  await pending
  for (const url of entries.keys()) {
    let response
    handlers.fetch({ request: new Request(url), respondWith: promise => { response = promise } })
    assert.ok((await response).ok, url)
  }
  // Every static JS import in the cached snapshot must also be available offline.
  for (const [url, response] of entries) {
    if (!url.endsWith('.js')) continue
    const text = await response.clone().text()
    for (const match of text.matchAll(/(?:from\s*|import\s*)["']([^"']+)["']/g)) {
      const dependency = match[1] === 'vue' ? `${origin}/assets/vendor/vue/vue.esm-browser.js` : new URL(match[1], url).href
      assert.ok(entries.has(dependency), `Missing dependency ${dependency}`)
    }
  }
  let font
  handlers.fetch({ request: new Request(`${origin}/assets/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2?version=1`), respondWith: p => { font = p } })
  assert.ok((await font).ok)
  for (const path of ['/api/telematics/status', '/api/telematics/stream', '/', '/assets/mobile/index.html', '/assets/mobile/js/app.js']) {
    handlers.fetch({ request: new Request(`${origin}${path}`), respondWith: () => assert.fail(`Unexpected cache interception: ${path}`) })
  }
  handlers.fetch({ request: new Request(`${origin}/assets/mobile/telematics.html`, { method: 'POST' }), respondWith: () => assert.fail('POST intercepted') })
}
console.log('Telematics offline snapshot, module closure, tunnel paths, fonts, and network-only API checks passed')
