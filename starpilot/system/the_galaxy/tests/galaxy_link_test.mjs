import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
const source = await readFile(new URL('../assets/mobile/js/galaxy-link.js', import.meta.url), 'utf8')
const { galaxyDestination, openGalaxyBluetooth, galaxySignInURL } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
let navigated
let requests = 0
globalThis.fetch = async () => { requests++; throw new TypeError('CORS blocked') }
globalThis.window = { location: { assign: value => { navigated = value } } }
for (const route of ['/telematics', '/bluetooth/phone']) {
  const target = `https://galaxy.firestar.link/1234567890abcdef/mobile/#${route}`
  assert.equal(await galaxyDestination(target), target)
  await openGalaxyBluetooth(target)
  assert.equal(navigated, target, 'Open the requested page directly, without a reachability check')
  assert.equal(galaxySignInURL(target), 'https://galaxy.firestar.link/1234567890abcdef')
}
assert.equal(requests, 0)
await assert.rejects(galaxyDestination('https://example.com/1234567890abcdef/mobile/#/telematics'), /unavailable/)
assert.equal(galaxySignInURL(''), '')
console.log('Direct Bluetooth navigation, sign-in links, and destination validation passed')
