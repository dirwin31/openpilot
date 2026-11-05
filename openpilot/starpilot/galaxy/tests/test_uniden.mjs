import assert from 'node:assert/strict'
import { test } from 'node:test'
import { compile } from '../web/vendor/vue/vue.esm-browser.js'
import { UnidenPanel, SWATCHES } from '../web/js/uniden.js'

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
  assert.ok(ONROAD_LAYOUT_TEMPLATE.includes(`:save-value="(index, value) => setUnidenDisplay('hide_when_no_alert'`))
  for (const field of ['hide_quiet_ride', 'hide_mute_memory'])
    assert.ok(!ONROAD_LAYOUT_TEMPLATE.includes(`setUnidenDisplay('${field}'`), 'which alerts show is a detector setting now')
  for (const key of ['hide_filtered', 'hide_below_limit', 'hide_q_ride', 'hide_mute_memory'])
    assert.ok(UnidenPanel.template.includes(`v-model="config.${key}"`), key)
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

test('one warning for every band: colors and on/off only, with a scenario preview beside the settings', () => {
  assert.ok(!UnidenPanel.template.includes('border_styles'), 'no per-band style picker')
  assert.deepEqual(Object.values(UnidenPanel.data().sections), ['Preferences', 'Colors', 'Rules', 'Lockouts'])
  assert.equal(UnidenPanel.data().panel, 'alerts', 'opens on Preferences')
  for (const hook of ['gx-layout__savebar', 'gx-layout__workspace', 'gx-layout__preview-card', 'aria-label="Preview scenario"', '<edge-preview'])
    assert.ok(UnidenPanel.template.includes(hook), hook)
  assert.deepEqual(Object.keys(SWATCHES), ['amber', 'coral', 'magenta', 'ice', 'lime', 'white', 'ultraviolet'])
  const state = { config: { border_bands: ['KA', 'LASER'] } }
  UnidenPanel.methods.toggleBand.call(state, 'border_bands', 'K')
  UnidenPanel.methods.toggleBand.call(state, 'border_bands', 'KA')
  assert.deepEqual(state.config.border_bands, ['LASER', 'K'])
})

test('saved lockouts make learning progress stand out', () => {
  const { computed } = UnidenPanel
  const learning = [{ id: 'a', band: 'K', frequency: 24.15, count: 2, need: 3, misses: 1 }, { id: 'b', band: 'KA', frequency: 34.7, count: 1, need: 5, misses: 0 }]
  const panel = { saved: { auto_learn: true, learn_bands: ['X', 'K'] }, learning }
  panel.learningOn = computed.learningOn.call(panel)
  const rows = computed.learningRows.call(panel)
  assert.deepEqual(rows.map(r => [r.learnable, r.percent, r.left]), [[true, 67, 1], [false, 20, 4]])
  assert.deepEqual(rows.map(r => r.stage), ['Locks out next pass', 'Just started'], 'a stage, not a pass count')
  assert.equal(computed.learningTitle.call(panel), 'Learning 2 sources')
  assert.equal(computed.learningTitle.call({ ...panel, learning: [] }), 'Learning: watching for repeat sources')
  assert.equal(computed.learningTitle.call({ ...panel, learningOn: false }), 'Learning is off')
})

test('edge preview follows the device rules and only lists old colors while they are chosen', async () => {
  const { EdgePreview, EdgeMotion, chevronCount, edgeAlpha } = await import('../web/js/uniden-preview.js')
  const { EDGE_PARTS, LEGACY_SWATCHES } = await import('../web/js/uniden.js')
  assert.equal(typeof compile(EdgePreview.template, { decodeEntities: value => value }), 'function')
  assert.deepEqual([1, 2, 3, 6, 7, 8].map(chevronCount), [1, 1, 2, 3, 4, 4])
  assert.equal(edgeAlpha({ glow: false }, 'K', 1), 0.85)
  assert.equal(edgeAlpha({ glow: false }, 'LASER', 1), 1)
  assert.ok(edgeAlpha({ glow: true }, 'K', 8) > edgeAlpha({ glow: true }, 'K', 1))
  assert.deepEqual(Object.keys(EDGE_PARTS), ['line', 'dashes', 'glow', 'veil', 'chevrons'])
  const motion = new EdgeMotion()
  motion.update({ band: 'K', strength: 3 }, 10, false)
  assert.equal(motion.pulses[0][1], 2)
  motion.update({ band: 'K', strength: 4 }, 11, false)
  assert.equal(motion.boost(11.1), 0)
  motion.update({ band: 'K', strength: 5 }, 12, false)
  assert.ok(motion.boost(12.15) > 0.9)
  motion.update({ band: 'LASER', strength: 5 }, 13, false)
  assert.equal(motion.pulses.at(-1)[1], 3)
  motion.update(null, 14, false)
  assert.equal(motion.fading(14.25)[1], 0.5)
  const { swatchesFor } = UnidenPanel.methods
  assert.ok(!('purple' in swatchesFor.call({ config: { border_colors: { K: 'amber', KA: 'coral', LASER: 'white' } } }, 'K')))
  const mixed = { config: { border_colors: { K: 'purple', KA: 'white', LASER: 'yellow' } } }
  assert.equal(swatchesFor.call(mixed, 'KA').purple, LEGACY_SWATCHES.purple, 'every band lists the same colors')
  assert.deepEqual(Object.keys(swatchesFor.call(mixed, 'KA')), Object.keys(swatchesFor.call(mixed, 'K')))
})

test('leaving with unsaved changes asks first, like the theme maker', async () => {
  const { requestLeave, cancelLeave, discardAndLeave } = UnidenPanel.methods
  const left = []
  const panel = { dirty: false, busy: false, leaveAction: null, leaveModal: false, $nextTick() {}, cancelLeave, cancel() { this.dirty = false } }
  requestLeave.call(panel, () => left.push('clean'))
  assert.deepEqual(left, ['clean'])
  panel.dirty = true
  requestLeave.call(panel, () => left.push('dirty'), 'modal')
  assert.deepEqual(left, ['clean']) && assert.equal(panel.leaveModal, true)
  assert.equal(UnidenPanel.computed.leaveInline.call(panel), false, 'menu navigation shows the dialog')
  discardAndLeave.call(panel)
  assert.deepEqual(left, ['clean', 'dirty'])
  assert.equal(panel.leaveAction, null)
  const { PREVIEW_SCENARIOS, previewScenario } = await import('../web/js/uniden-preview.js')
  assert.ok(Object.keys(PREVIEW_SCENARIOS).length >= 2 && previewScenario('laser').alert.band === 'LASER')
  assert.equal(previewScenario('missing').script, true, 'unknown scenarios fall back to the drive-by')
})

test('picking a color shows that band in the preview', () => {
  const { pickColor } = UnidenPanel.methods
  const panel = { config: { border_colors: { K: 'amber', KA: 'coral', LASER: 'coral' } }, scenario: 'drive', replay: 0 }
  pickColor.call(panel, 'LASER', 'white')
  assert.equal(panel.config.border_colors.LASER, 'white')
  assert.equal(panel.scenario, 'laser')
  pickColor.call(panel, 'LASER', 'lime')
  assert.deepEqual([panel.scenario, panel.replay], ['laser', 1], 'already showing laser: replay it')
  pickColor.call(panel, 'KA', 'magenta')
  assert.equal(panel.scenario, 'ka-strong')
  panel.scenario = 'drive'
  pickColor.call(panel, 'K', 'ice')
  assert.deepEqual([panel.scenario, panel.replay], ['drive', 2], 'the drive-by is a K pass')
})

test('turning on Ka or laser learning asks first; X, K and turning off do not', () => {
  const { requestLearnBand, confirmLearnBand, toggleBand } = UnidenPanel.methods
  const panel = { config: { learn_bands: ['X', 'K'] }, confirmLearn: null, toggleBand }
  requestLearnBand.call(panel, 'KA')
  assert.deepEqual([panel.confirmLearn, panel.config.learn_bands], ['KA', ['X', 'K']], 'waits for the dialog')
  confirmLearnBand.call(panel)
  assert.deepEqual([panel.confirmLearn, panel.config.learn_bands], [null, ['X', 'K', 'KA']])
  requestLearnBand.call(panel, 'LASER')
  panel.confirmLearn = null   // Keep it off
  assert.ok(!panel.config.learn_bands.includes('LASER'))
  requestLearnBand.call(panel, 'KA')
  assert.ok(!panel.config.learn_bands.includes('KA') && panel.confirmLearn === null, 'turning off is immediate')
  requestLearnBand.call(panel, 'X')
  assert.ok(!panel.config.learn_bands.includes('X'))
})
