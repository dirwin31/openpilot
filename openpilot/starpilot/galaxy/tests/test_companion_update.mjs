import assert from 'node:assert/strict'
import { createRenderer, nextTick } from '../web/vendor/vue/vue.esm-browser.js'
import { GalaxyPage } from '../web/js/galaxy.js'

const storage = new Map()
globalThis.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) }
globalThis.window = { StarPilotApp: { version: () => '1.1.4', uplinkState: () => '{}' } }
globalThis.document = { activeElement: null }
const status = { protocol: 1, enabled: false, connected: false, active: false, appAvailable: true, updateAvailable: false,
  bytes: 0, shippedVersion: '1.1.5', version: null, state: 'disabled' }
globalThis.fetch = async url => ({ ok: true, json: async () => url.includes('/api/companion/status') ? status : { paired: false } })

const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null,
  addEventListener() {}, removeEventListener() {} })
const renderer = createRenderer({
  createElement: type => node(type), createText: text => node('text', text), createComment: text => node('comment', text),
  setText: (item, text) => { item.text = text }, setElementText: (item, text) => { item.text = text; item.children = [] },
  patchProp: (item, key, before, after) => { item.props[key] = after },
  insert: (item, parent, anchor = null) => {
    if (item.parent) item.parent.children.splice(item.parent.children.indexOf(item), 1)
    item.parent = parent
    const index = parent.children.indexOf(anchor)
    parent.children.splice(index < 0 ? parent.children.length : index, 0, item)
  },
  remove: item => { const index = item.parent?.children.indexOf(item); if (index >= 0) item.parent.children.splice(index, 1) },
  parentNode: item => item.parent,
  nextSibling: item => item.parent?.children[item.parent.children.indexOf(item) + 1] ?? null,
})
const all = item => [item, ...item.children.flatMap(all)]
const text = item => [item.text, ...item.children.map(text)].join(' ')
const banner = host => all(host).find(item => item.type === 'div' && text(item).includes('Android app update available') && item.props.role === 'status')
async function mount() {
  const host = node('host')
  const app = renderer.createApp({ ...GalaxyPage,
    // The real companion child still polls and emits metadata to the real page.
    components: { ...GalaxyPage.components, GalaxyHotspot: { template: '<div />' }, InstallApp: { props: ['updateAvailable'], template: '<div class="install-state">{{ updateAvailable }}</div>' } },
  }, { mode: 'local', unauthorized() { assert.fail('Unexpected authentication failure') } })
  app.config.compilerOptions.decodeEntities = raw => raw.replaceAll('&amp;', '&')
  const vm = app.mount(host)
  await new Promise(resolve => setImmediate(resolve))
  await nextTick()
  return { host, app, vm }
}

const mounted = await mount()
try {
  assert.equal(banner(mounted.host), undefined)
  assert.doesNotMatch(text(mounted.host), /Download Android APK|Android offline app version|Update app/)
  assert.ok(!all(mounted.host).some(item => String(item.props.href || '').includes('app.apk')))
} finally { mounted.app.unmount() }

// iPhone users never mount or poll the Android companion section.
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { userAgent: 'iPhone' } })
let companionMounts = 0
const iosHost = node('host')
const iosApp = renderer.createApp({ ...GalaxyPage, components: { ...GalaxyPage.components,
  GalaxyHotspot: { template: '<div />' }, InstallApp: { template: '<div class="web-install">Install as web app</div>' },
  GalaxyCompanion: { mounted() { companionMounts++ }, template: '<div>Android companion</div>' },
} }, { mode: 'local', unauthorized() { assert.fail('Unexpected authentication failure') } })
iosApp.config.compilerOptions.decodeEntities = raw => raw.replaceAll('&amp;', '&')
const iosVm = iosApp.mount(iosHost)
await new Promise(resolve => setImmediate(resolve))
await nextTick()
assert.equal(iosVm.ios, true)
assert.equal(companionMounts, 0)
assert.doesNotMatch(text(iosHost), /Android companion/)
assert.match(text(iosHost), /Install as web app/)
iosApp.unmount()

console.log('No bundled Android download/update UI; iPhone and web installation preserved')
