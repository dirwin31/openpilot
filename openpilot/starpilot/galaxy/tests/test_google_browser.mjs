import assert from 'node:assert/strict'
import { GoogleBrowserPanel } from '../web/js/google-browser.js'

globalThis.document = { hidden: false }
function panel() {
  const instance = { ...GoogleBrowserPanel.data(), alive: true, inputChain: Promise.resolve(), pendingInputs: 0,
    email: 'user@example.com', remember: false, parked: true, available: true, $emit() {} }
  for (const [name, method] of Object.entries(GoogleBrowserPanel.methods)) instance[name] = method.bind(instance)
  Object.defineProperty(instance, 'active', { get: () => GoogleBrowserPanel.computed.active.call(instance) })
  return instance
}

// Closing while a frame is in flight must never restore an image or polling.
const closing = panel()
closing.browser = { id: 'one', state: 'signing_in' }
let respond
closing.request = (payload) => payload.operation === 'frame' ? new Promise(resolve => { respond = resolve }) : Promise.resolve({})
const poll = closing.poll()
await closing.stop()
respond({ id: 'one', state: 'signing_in', frame: 'private-frame', origin: 'https://accounts.google.com' })
await poll
assert.equal(closing.frame, '')
assert.equal(closing.active, false)
assert.equal(closing.timer, undefined)

// Inputs preserve order, and queued input cannot enter a replacement session.
const typing = panel()
typing.browser = { id: 'one', state: 'signing_in' }
const sent = []
typing.request = async (payload) => { sent.push(payload) }
typing.input({ kind: 'text', text: 'a' })
typing.input({ kind: 'key', key: 'Enter' })
await typing.inputChain
assert.deepEqual(sent.map(x => x.input), [{ kind: 'text', text: 'a' }, { kind: 'key', key: 'Enter' }])
typing.input({ kind: 'text', text: 'old secret' })
typing.browser = { id: 'two', state: 'signing_in' }
await typing.inputChain
assert.equal(sent.length, 2)

// Mobile composition is sent once after completion; local text is cleared.
const event = { target: { value: '日本語' }, isComposing: true }
typing.type(event)
assert.equal(event.target.value, '日本語')
event.isComposing = false
typing.type(event)
assert.equal(event.target.value, '')
await typing.inputChain
assert.deepEqual(sent.at(-1).input, { kind: 'text', text: '日本語' })

// Auth loss clears the frame and stops retries.
const expired = panel()
expired.browser = { id: 'old', state: 'signing_in' }
expired.frame = 'private-frame'
expired.request = async () => { throw Object.assign(new Error('Sign in to Galaxy'), { status: 401 }) }
await expired.poll()
assert.equal(expired.frame, '')
assert.equal(expired.active, false)

// Leaving the page during startup sends a stop once the session ID arrives.
const leaving = panel()
const requests = []
leaving.request = async (payload) => {
  requests.push(payload)
  if (payload.operation === 'start') { leaving.alive = false; return { id: 'created', state: 'starting' } }
  return {}
}
await leaving.start()
assert.deepEqual(requests.map(x => x.operation), ['start', 'stop'])
console.log('Google browser: frame cancellation, ordered input, mobile composition, auth loss and startup cleanup passed')
