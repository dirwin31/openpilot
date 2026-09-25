import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"
import { compile } from "../assets/vendor/vue/vue.esm-browser.js"

const source = readFileSync(new URL("../assets/mobile/js/components/AndroidAutoCarScreenPanel.js", import.meta.url), "utf8")
const settingsSource = readFileSync(new URL("../assets/mobile/js/views/Settings.js", import.meta.url), "utf8")
const api = {}
const notifications = []
const panel = new Function("api", "showSnackbar",
  source.replace(/^import .*\n/gm, "").replace("export const AndroidAutoCarScreenPanel =", "return"))(
  api, (...args) => notifications.push(args))

function instance() {
  const state = { ...panel.data() }
  for (const [name, method] of Object.entries(panel.methods)) state[name] = method.bind(state)
  return state
}

async function withoutRetryDelay(action) {
  const originalSetTimeout = globalThis.setTimeout
  globalThis.setTimeout = (callback) => { callback(); return 0 }
  try {
    await action()
  } finally {
    globalThis.setTimeout = originalSetTimeout
  }
}

test("a transient enable race retries and loads the layout", async () => {
  const state = instance()
  let requests = 0
  api.getCarScreen = async () => {
    requests++
    if (requests === 1) throw new Error("Enable Android Auto first")
    return { settings: { onroad_view: "split", map_side: "right", camera: true } }
  }

  await withoutRetryDelay(() => state.load())

  assert.equal(requests, 2)
  assert.equal(state.loading, false)
  assert.equal(state.error, "")
  assert.equal(state.settings.onroad_view, "split")
})

test("a persistent failure stops loading and exposes a retryable error", async () => {
  const state = instance()
  let requests = 0
  api.getCarScreen = async () => { requests++; throw new Error("Connection lost") }

  await withoutRetryDelay(() => state.load())

  assert.equal(requests, 3)
  assert.equal(state.loading, false)
  assert.equal(state.settings, null)
  assert.equal(state.error, "Connection lost")
  assert.deepEqual(notifications.at(-1), ["Connection lost", "error"])
})

test("the panel template compiles with loading, error, and retry states", () => {
  globalThis.document = { createElement: () => ({
    set innerHTML(value) {
      this.textContent = value
      this.children = [{ getAttribute: () => value.match(/foo="(.*)">/s)?.[1] }]
    },
  }) }
  assert.equal(typeof compile(panel.template), "function")
})

test("the Android Auto master toggle has its own section below the vehicle settings", () => {
  assert.match(settingsSource, /s\.name === "Vehicle" && p\.key === "AndroidAutoEnabled"/)
  const settingsTree = settingsSource.indexOf("<SettingTree")
  const androidAutoSection = settingsSource.indexOf('title="Android Auto"')
  assert.ok(settingsTree >= 0 && androidAutoSection > settingsTree)
  assert.match(settingsSource.slice(settingsTree, androidAutoSection), /<\/div>\s*<GalaxySection/)
  assert.match(settingsSource.slice(androidAutoSection), /:param="androidAutoParam\(activeSection\)"/)
})

test("blind-spot controls convert the displayed unit back to metres per second", () => {
  const state = instance()
  state.isMetric = false
  state.speedFactor = panel.computed.speedFactor.call(state)
  let change
  state.update = (value) => { change = value }

  state.updateBlindSpotSpeed({ target: { value: "30" } })

  assert.ok(Math.abs(change.blind_spot_min_speed_ms - 13.4112) < 0.001)
  assert.match(panel.template, /Show Blind Spot Monitors/)
  assert.match(panel.template, /Blind Spot Minimum Speed/)
})
