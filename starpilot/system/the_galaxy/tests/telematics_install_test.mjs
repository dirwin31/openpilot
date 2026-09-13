import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'
const source = (await readFile(new URL('../assets/mobile/js/telematics-install.js', import.meta.url), 'utf8')).replace(/^import .*$/gm, '').replace(/^export /gm, '')
const base = 'https://galaxy.firestar.link/1234567890abcdef/'
const storage = new Map()
function setup(path = 'mobile/', standalone = false, saved = true) {
  let saves = 0
  const context = vm.createContext({
    URL, URLSearchParams, reactive: value => value,
    window: {
      location: new URL(path, base),
      matchMedia: () => ({ matches: standalone }),
    },
    localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    galaxyAppBase: () => new URL(base),
    offlineState: { state: 'idle' },
    prepareOffline: async () => { saves++; context.offlineState.state = saved ? 'ready' : 'error' },
  })
  vm.runInContext(source, context)
  return { context, state: vm.runInContext('telematicsOffline', context), saves: () => saves }
}
const phone = setup()
assert.equal(phone.saves(), 0)
await vm.runInContext('saveTelematicsOffline()', phone.context)
assert.equal(phone.context.offlineState.state, 'ready')
assert.equal(phone.context.window.location.href, base + 'mobile/', 'Saving does not leave the Phone page')
assert.equal(vm.runInContext('cachedTelematicsURL()', phone.context), `${base}assets/mobile/telematics.html#/telematics`)
vm.runInContext('dismissOfflineBanner()', phone.context)
assert.equal(setup().state.dismissed, true, 'Dismissal survives reopening')
vm.runInContext('showOfflineBanner()', phone.context)
assert.equal(setup().state.dismissed, false)
const failure = setup('mobile/', false, false)
await vm.runInContext('saveTelematicsOffline()', failure.context)
assert.equal(failure.context.offlineState.state, 'error')
assert.equal(failure.state.dismissed, false)
assert.equal(vm.runInContext('isTelematicsAppWindow()', setup('assets/mobile/telematics.html?app=telematics', true).context), true)
assert.equal(vm.runInContext('isTelematicsAppWindow()', setup('assets/mobile/telematics.html?app=telematics').context), false)
console.log('Explicit offline saving, persistent dismissal, dashboard shortcut URL, and app detection passed')
