import assert from 'node:assert/strict'
import { test } from 'node:test'
import { compile } from '../web/vendor/vue/vue.esm-browser.js'
import { UnidenPanel } from '../web/js/uniden.js'

test('detector panel compiles and only offers bonded R4/R8 names', () => {
  assert.equal(typeof compile(UnidenPanel.template), 'function')
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
  const position = { transparency: 8, hide_when_no_alert: true }
  const history = []
  const context = { editable: true, selectedWidget: { kind: 'uniden_alerts' }, selectedPosition: position,
    state: { profile: 'large', selected: 'uniden_alerts', draft: { position } },
    finishColorEdit() {}, recordChange(before) { history.push(before) } }
  const edit = (field, value) => OnroadLayoutPage.methods.setUnidenDisplay.call(context, field, value)
  edit('transparency', 65)
  edit('hide_when_no_alert', false)
  assert.deepEqual(position, { transparency: 65, hide_when_no_alert: false })
  assert.equal(history[0].position.transparency, 8)
  assert.equal(history[1].position.hide_when_no_alert, true)
  edit('transparency', 101)
  context.editable = false
  edit('hide_when_no_alert', true)
  assert.equal(history.length, 2)
  assert.ok(!UnidenPanel.template.includes('uniden-transparency'))
  assert.ok(ONROAD_LAYOUT_TEMPLATE.includes(":save-value=\"(index, value) => setUnidenDisplay('hide_when_no_alert'"))
})
