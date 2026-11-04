import assert from 'node:assert/strict'
import { test } from 'node:test'
import { compile } from '../web/vendor/vue/vue.esm-browser.js'
import { UnidenPanel } from '../web/js/uniden.js'

test('detector panel compiles and only offers bonded R4/R8 names', () => {
  assert.equal(typeof compile(UnidenPanel.template, { decodeEntities: value => value }), 'function')
  const devices = [{ name: 'R8W@12', paired: true }, { name: 'Uniden R4', paired: true },
    { name: 'R8', paired: false }, { name: 'Phone', paired: true }, { name: 'R7', paired: true }]
  assert.deepEqual(UnidenPanel.computed.detectors.call({ devices }), devices.slice(0, 2))
})
test('setup cannot save onroad or while another save is pending', async () => {
  const state = { parked: false, busy: false, request: () => assert.fail('must not send') }
  await UnidenPanel.methods.save.call(state)
  state.parked = true; state.busy = true
  await UnidenPanel.methods.save.call(state)
})

test('theme maker edits Uniden display settings through draft history', async () => {
  const { OnroadLayoutPage } = await import('../web/js/onroad-layout.js')
  const { ONROAD_LAYOUT_TEMPLATE } = await import('../web/js/onroad-layout-template.js')
  assert.equal(typeof compile(ONROAD_LAYOUT_TEMPLATE, { decodeEntities: value => value }), 'function')
  const position = { transparency: 8, hide_when_no_alert: true, hide_quiet_ride: false, hide_mute_memory: false, scale: 100 }
  const history = []
  const context = { editable: true, selectedWidget: { kind: 'uniden_alerts' }, selectedPosition: position,
    state: { profile: 'large', selected: 'uniden_alerts', draft: { position } },
    finishColorEdit() {}, recordChange(before) { history.push(before) } }
  const edit = (field, value) => OnroadLayoutPage.methods.setUnidenDisplay.call(context, field, value)
  edit('transparency', 65)
  edit('hide_when_no_alert', false)
  edit('hide_quiet_ride', true)
  edit('hide_mute_memory', true)
  edit('scale', 130)
  assert.deepEqual(position, { transparency: 65, hide_when_no_alert: false, hide_quiet_ride: true, hide_mute_memory: true, scale: 130 })
  assert.equal(history[0].position.transparency, 8)
  assert.equal(history[1].position.hide_when_no_alert, true)
  edit('transparency', 101)
  edit('scale', 151)
  edit('scale', 49)
  edit('hide_quiet_ride', 1)
  context.editable = false
  edit('hide_when_no_alert', true)
  assert.equal(history.length, 5)
  assert.ok(!UnidenPanel.template.includes('uniden-transparency'))
  for (const field of ['hide_when_no_alert', 'hide_quiet_ride', 'hide_mute_memory'])
    assert.ok(ONROAD_LAYOUT_TEMPLATE.includes(`:save-value="(index, value) => setUnidenDisplay('${field}'`))
  assert.ok(ONROAD_LAYOUT_TEMPLATE.includes("setUnidenDisplay('scale'"))
})

test('saved setup only offers settings while matching a paired detector', () => {
  const devices = [{ name: 'R8', address: 'AA:BB:CC:DD:EE:FF', paired: true }]
  const state = { saved: { address: devices[0].address }, detectors: devices }
  assert.equal(UnidenPanel.computed.ready.call(state), true)
  state.detectors = []
  assert.equal(UnidenPanel.computed.ready.call(state), false)
  assert.equal(UnidenPanel.computed.dirty.call({ saved: { enabled: true }, config: { enabled: true } }), false)
  assert.equal(UnidenPanel.computed.dirty.call({ saved: { enabled: true }, config: { enabled: false } }), true)
})

test('selecting a paired detector infers model and enables integration', () => {
  const state = { selected: { name: 'Uniden R4W' }, config: { enabled: false, model: 'R8' } }
  UnidenPanel.methods.selectDetector.call(state)
  assert.deepEqual(state.config, { enabled: true, model: 'R4' })
})

test('reconnecting status stays stable and does not display transport errors', async () => {
  const { detectorStatus } = await import('../web/js/uniden.js')
  const failed = detectorStatus({ connected: false, connection_state: 'reconnecting', error: 'org.bluez.Error.Failed' })
  const waiting = detectorStatus({ connected: false, connection_state: 'reconnecting', error: 'Waiting to reconnect detector' })
  assert.deepEqual(failed, waiting)
  assert.equal(failed.label, 'Reconnecting')
  assert.equal(detectorStatus({ connected: true }).label, 'Connected')
  assert.equal(detectorStatus({ connection_state: 'radio_off' }).label, 'Bluetooth off')
  assert.equal(detectorStatus({ connection_state: 'detector_pairing_required' }).label, 'Check pairing')
})

test('rule summary explains exact matching and MHz tolerance in GHz', () => {
  assert.equal(UnidenPanel.methods.ruleRange({ frequency: 34.313, tolerance_mhz: 0 }), 'Matches 34.313 GHz exactly')
  assert.equal(UnidenPanel.methods.ruleRange({ frequency: 34.313, tolerance_mhz: 2 }), 'Matches 34.3110–34.3150 GHz')
})
