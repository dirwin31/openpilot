import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'
const source = (await readFile(new URL('../assets/mobile/js/telematics-install.js', import.meta.url), 'utf8')).replace(/^import .*$/gm, '').replace(/^export /gm, '')
const base = 'https://galaxy.firestar.link/1234567890abcdef/'
function setup(path, standalone = false, saved = true) {
  let destination, saves = 0
  const listeners = {}
  const context = vm.createContext({
    URL, URLSearchParams, reactive: value => value,
    window: {
      location: { ...Object.fromEntries(['pathname', 'search'].map(key => [key, new URL(path, base)[key]])), assign: url => { destination = url } },
      matchMedia: () => ({ matches: standalone }),
      addEventListener: (type, fn) => { listeners[type] = fn },
    },
    galaxyAppBase: () => new URL(base),
    offlineState: { state: 'idle' },
    prepareOffline: async () => { saves++; context.offlineState.state = saved ? 'ready' : 'error' },
  })
  vm.runInContext(source, context)
  return { context, listeners, state: vm.runInContext('telematicsInstall', context), destination: () => destination, saves: () => saves }
}
const browser = setup('mobile/')
assert.equal(browser.saves(), 0, 'No automatic offline saving on ordinary page visits')
assert.deepEqual(Object.keys(browser.listeners), [], 'Do not intercept the main Galaxy install prompt')
await vm.runInContext('setupTelematicsOffline()', browser.context)
assert.equal(browser.destination(), `${base}assets/mobile/telematics-setup.html#/bluetooth/phone`)
const failed = setup('mobile/', false, false)
await vm.runInContext('setupTelematicsOffline()', failed.context)
assert.equal(failed.destination(), `${base}assets/mobile/telematics-setup.html#/bluetooth/phone`, 'Always leave the Galaxy manifest before starting offline saving')
assert.equal(failed.saves(), 0, 'Saving belongs to the separate installer')
const installer = setup('assets/mobile/telematics-setup.html')
let prompted = 0, prevented = 0
installer.listeners.beforeinstallprompt({ preventDefault() { prevented++ }, prompt() { prompted++ }, userChoice: Promise.resolve({ outcome: 'accepted' }) })
await vm.runInContext('installTelematics()', installer.context)
assert.equal(prompted, 0)
installer.context.offlineState.state = 'ready'
await vm.runInContext('installTelematics()', installer.context)
assert.equal(prompted, 1)
assert.equal(prevented, 1)
assert.equal(installer.state.installed, false, 'Accepted prompt is not proof installation completed')
installer.listeners.appinstalled()
assert.equal(installer.state.installed, true)
const noPrompt = setup('assets/mobile/telematics-setup.html')
noPrompt.context.offlineState.state = 'ready'
await vm.runInContext('installTelematics()', noPrompt.context)
assert.equal(noPrompt.state.manualHelp, true)
assert.match(noPrompt.state.message, /Chrome has not offered/)
const galaxyWindow = setup('assets/mobile/telematics-setup.html', true)
galaxyWindow.context.offlineState.state = 'ready'
await vm.runInContext('installTelematics()', galaxyWindow.context)
assert.match(galaxyWindow.state.message, /Open this setup page in Chrome/)
assert.equal(setup('assets/mobile/telematics.html?app=telematics', true).state.appWindow, true)
assert.equal(setup('assets/mobile/telematics.html?app=telematics').state.appWindow, false)
assert.equal(setup('mobile/?app=telematics', true).state.appWindow, false)
console.log('Explicit offline setup, separate install prompt, install verification, and app-window detection passed')
