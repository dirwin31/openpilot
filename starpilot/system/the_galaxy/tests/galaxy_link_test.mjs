import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
const source = await readFile(new URL('../assets/mobile/js/galaxy-link.js', import.meta.url), 'utf8')
const { galaxyDestination, openGalaxyBluetooth } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
const target = 'https://galaxy.firestar.link/1234567890abcdef/mobile/#/telematics'
const login = 'https://galaxy.firestar.link/1234567890abcdef#/telematics'
const calls = []
function mock(...responses) {
  calls.length = 0
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    const response = responses.shift()
    if (response instanceof Error) throw response
    assert.ok(response, 'Unexpected extra request')
    return response
  }
}
mock(new Response('<main id="galaxy-app"></main><script src="/assets/mobile/js/app.js"></script>'))
assert.equal(await galaxyDestination(target), target)
assert.equal(calls[0].options.credentials, 'include')
assert.equal(calls[0].options.mode, 'cors')
assert.equal(calls[0].options.method, 'GET')
assert.equal(calls[0].url, target.split('#')[0])
for (const status of [401, 403, 404]) {
  mock(new Response('Sign in', { status }))
  assert.equal(await galaxyDestination(target), login)
}
mock(new Response('<form>Galaxy password</form>'))
assert.equal(await galaxyDestination(target), login)
mock({ type: 'opaqueredirect', status: 0, ok: false })
assert.equal(await galaxyDestination(target), login)
mock(new TypeError('CORS'), { type: 'opaque', status: 0 })
assert.equal(await galaxyDestination(target), login)
assert.equal(calls[1].options.mode, 'no-cors')
assert.equal(calls[1].url, login.split('#')[0])
mock(new TypeError('Network'), new TypeError('Network'))
await assert.rejects(galaxyDestination(target), /unreachable/)
mock(new Response('Unavailable', { status: 503 }))
await assert.rejects(galaxyDestination(target), /unavailable/)
let navigated
globalThis.window = { location: { assign: value => { navigated = value } } }
mock(new Response('Sign in', { status: 401 }))
await openGalaxyBluetooth(target.replace('#/telematics', '#/bluetooth/phone'))
assert.equal(navigated, login.replace('#/telematics', '#/bluetooth/phone'))
mock()
await assert.rejects(galaxyDestination('https://example.com/1234567890abcdef/mobile/#/telematics'), /unavailable/)
assert.equal(calls.length, 0)
console.log('Galaxy authenticated navigation, login fallback, CORS, outage, and destination validation passed')
