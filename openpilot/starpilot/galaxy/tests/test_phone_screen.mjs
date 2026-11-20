import assert from 'node:assert/strict'

globalThis.window = {}

const { connectSteps, validPhoneScreen } = await import('../web/js/phone-screen.js')

const progress = (status, kind) => connectSteps(status, kind).map(step => step.progress)
const on = (kind, state) => ({ enabled: true, [kind]: { state, message: '' } })

// Off, or the service not up yet: nothing is ticked, nothing is claimed as the current step.
assert.deepEqual(progress({ enabled: false, carplay: { state: 'active' } }, 'carplay'), ['todo', 'todo', 'todo', 'todo'])
assert.deepEqual(progress(on('carplay', 'starting'), 'carplay'), ['todo', 'todo', 'todo', 'todo'])

// CarPlay checks the key, then the hotspot, then pairing; each blocked state names the step still to do.
assert.deepEqual(progress(on('carplay', 'unavailable'), 'carplay'), ['current', 'todo', 'todo', 'todo'])
assert.deepEqual(progress(on('carplay', 'waiting_hotspot'), 'carplay'), ['done', 'current', 'todo', 'todo'])
assert.deepEqual(progress(on('carplay', 'waiting_phone'), 'carplay'), ['done', 'done', 'current', 'todo'])
assert.deepEqual(progress(on('carplay', 'retrying'), 'carplay'), ['done', 'done', 'done', 'current'])
assert.deepEqual(progress(on('carplay', 'connecting'), 'carplay'), ['done', 'done', 'done', 'active'])
assert.deepEqual(progress(on('carplay', 'active'), 'carplay'), ['done', 'done', 'done', 'done'])

// Android has no key; a paired phone waiting on wireless Android Auto is its own step.
assert.deepEqual(connectSteps(on('android', 'waiting_projection'), 'android').map(step => step.id), ['hotspot', 'pair', 'wireless', 'connect'])
assert.deepEqual(progress(on('android', 'waiting_phone'), 'android'), ['done', 'current', 'todo', 'todo'])
assert.deepEqual(progress(on('android', 'waiting_projection'), 'android'), ['done', 'done', 'current', 'todo'])

// Older daemons without per-phone states still validate and show plain steps.
const base = { enabled: true, side: 'right', audio: 'phone', state: 'waiting', phone: '', message: '', kind: '' }
assert.equal(validPhoneScreen(base), true)
assert.deepEqual(progress(base, 'carplay'), ['todo', 'todo', 'todo', 'todo'])
console.log('phone screen steps ok')
