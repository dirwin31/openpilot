import json
import shutil
import subprocess

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
LIVE_LINK_SOURCE = REPO_ROOT / "starpilot/system/the_galaxy/assets/components/tools/live_link_standalone.js"

NODE_HARNESS = r"""
import fs from "node:fs"
import vm from "node:vm"

class FakeClassList {
  constructor() { this.values = new Set() }
  toggle(name, enabled) {
    if (enabled) this.values.add(name)
    else this.values.delete(name)
  }
  contains(name) { return this.values.has(name) }
}

class FakeElement {
  constructor() {
    this.className = ""
    this.classList = new FakeClassList()
    this.hidden = false
    this.textContent = ""
    this.disabled = false
    this.listeners = new Map()
    this.properties = new Map()
    this.style = { setProperty: (name, value) => this.properties.set(name, value) }
  }
  addEventListener(name, callback) { this.listeners.set(name, callback) }
  removeEventListener(name) { this.listeners.delete(name) }
}

const dom = new Map()
const navigator = { standalone: false }
const context = {
  console,
  TextEncoder,
  TextDecoder,
  Uint8Array,
  DataView,
  ArrayBuffer,
  JSON,
  Math,
  Number,
  Date,
  Promise,
  URL,
  Response,
  performance: { now: () => 1000 },
  document: {
    baseURI: "https://galaxy.firestar.link/device/live",
    body: { dataset: { secureLiveUrl: "" } },
    getElementById(id) {
      if (!dom.has(id)) dom.set(id, new FakeElement())
      return dom.get(id)
    },
  },
  navigator,
  setTimeout: () => 1,
  clearTimeout: () => {},
  setInterval: () => 1,
  clearInterval: () => {},
}
context.window = {
  isSecureContext: true,
  navigator,
  matchMedia: () => ({ matches: false }),
  addEventListener: () => {},
}
context.globalThis = context
vm.createContext(context)

const source = fs.readFileSync(process.argv[1], "utf8")
vm.runInContext(`${source}\nglobalThis.__liveTest = { reassemble, decodeFrame, acceptDecodedFrame, connectionErrorMessage, state }`, context)

const frame = new context.Uint8Array(64)
const view = new context.DataView(frame.buffer)
frame[0] = "S".charCodeAt(0)
frame[1] = "P".charCodeAt(0)
view.setUint8(2, 1)
view.setUint8(3, 1)
view.setUint16(4, 64, true)
view.setUint16(6, 513, true)
view.setUint32(8, 123456, true)
const flags = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 8) | (1 << 9) | (1 << 10) |
  (1 << 11) | (1 << 12) | (1 << 13) | (1 << 24) | (1 << 25)
view.setUint32(12, flags >>> 0, true)
view.setInt16(16, 2025, true)
view.setUint16(18, 2350, true)
view.setUint16(30, 264, true)
view.setInt16(32, -175, true)
view.setUint16(34, 992, true)
view.setUint16(36, 2010, true)
view.setInt16(38, 112, true)
view.setUint16(40, 1720, true)
view.setUint8(43, 2)
view.setUint8(45, 1)
frame.set([22, 127, 64, 255], 52)
view.setUint32(56, 99, true)
view.setUint32(60, 100, true)

let complete = null
for (const index of [2, 0, 3, 1]) {
  const fragment = new context.Uint8Array(20)
  fragment[0] = 1
  fragment[1] = 513 & 0xff
  fragment[2] = 513 >> 8
  fragment[3] = (4 << 4) | index
  fragment.set(frame.slice(index * 16, (index + 1) * 16), 4)
  complete = context.__liveTest.reassemble(fragment) || complete
}

const decoded = context.__liveTest.decodeFrame(complete.frame, complete.sequence)
context.__liveTest.state.phase = "connected"
context.__liveTest.acceptDecodedFrame(decoded)

process.stdout.write(JSON.stringify({
  sequence: decoded.sequence,
  vehicleSpeed: decoded.vehicleSpeed,
  setSpeed: decoded.setSpeed,
  leadDistance: decoded.leadDistance,
  speedLimit: decoded.speedLimit,
  speedLimitOffset: decoded.speedLimitOffset,
  curveTargetSpeed: decoded.curveTargetSpeed,
  vehicleText: dom.get("vehicleSpeed").textContent,
  setSpeedText: dom.get("setSpeed").textContent,
  status: dom.get("driveStatusLabel").textContent,
  statusDetail: dom.get("driveStatusDetail").textContent,
  engagement: dom.get("engagementValue").textContent,
  lead: dom.get("leadDistance").textContent,
  relativeSpeed: dom.get("leadRelativeSpeed").textContent,
  slc: dom.get("slcValue").textContent,
  slcDetail: dom.get("slcDetail").textContent,
  curve: dom.get("curveValue").textContent,
  border: dom.get("livePanel").properties.get("--drive-border"),
  chooserError: context.__liveTest.connectionErrorMessage({ name: "NotFoundError" }, "chooser"),
  permissionError: context.__liveTest.connectionErrorMessage({ name: "NotAllowedError" }, "chooser"),
  authorizationError: context.__liveTest.connectionErrorMessage({ name: "NetworkError" }, "authorization"),
}))
"""

SETUP_HARNESS = r"""
import fs from "node:fs"
import vm from "node:vm"

class FakeElement {
  constructor() {
    this.className = ""
    this.hidden = false
    this.textContent = ""
    this.disabled = false
    this.listeners = new Map()
    this.classList = { toggle: () => {} }
    this.style = { setProperty: () => {} }
  }
  addEventListener(name, callback) { this.listeners.set(name, callback) }
  removeEventListener(name) { this.listeners.delete(name) }
}

const mode = process.argv[2]
const insecure = mode === "insecure"
const dom = new Map()
const navigator = {
  userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 Version/26.0 Mobile/15E148 Safari/604.1",
  platform: "iPhone",
  maxTouchPoints: 5,
  standalone: false,
}
if (mode === "ready") {
  navigator.bluetooth = {
    getAvailability: async () => true,
    getDevices: async () => [],
  }
}

const context = {
  console,
  TextEncoder,
  TextDecoder,
  Uint8Array,
  DataView,
  ArrayBuffer,
  JSON,
  Math,
  Number,
  Date,
  Promise,
  URL,
  Response,
  performance: { now: () => 1000 },
  document: {
    baseURI: "https://galaxy.firestar.link/device/live",
    body: { dataset: { secureLiveUrl: insecure ? "https://galaxy.firestar.link/AbCdEf0123456789" : "" } },
    getElementById(id) {
      if (!dom.has(id)) dom.set(id, new FakeElement())
      return dom.get(id)
    },
  },
  navigator,
  setTimeout,
  clearTimeout,
  setInterval: () => 1,
  clearInterval: () => {},
}
context.window = {
  isSecureContext: !insecure,
  navigator,
  location: { reload: () => {} },
  matchMedia: () => ({ matches: false }),
  addEventListener: () => {},
}
context.globalThis = context
vm.createContext(context)

const source = fs.readFileSync(process.argv[1], "utf8")
vm.runInContext(source, context)
await new Promise((resolve) => setTimeout(resolve, 0))

const result = insecure ? {
  title: dom.get("setupTitle").textContent,
  message: dom.get("setupMessage").textContent,
  linkText: dom.get("secureAppLink").textContent,
  linkHref: dom.get("secureAppLink").href,
  linkHidden: dom.get("secureAppLink").hidden,
} : {
  title: dom.get("setupTitle").textContent,
  message: dom.get("setupMessage").textContent,
  installHidden: dom.get("beacioInstallLink").hidden,
  connectHidden: dom.get("connectButton").hidden,
  beacioClass: dom.get("beacioStep").className,
  permissionClass: dom.get("permissionStep").className,
}
process.stdout.write(JSON.stringify(result))
"""

CONNECTION_HARNESS = r"""
import fs from "node:fs"
import vm from "node:vm"

class FakeElement {
  constructor() {
    this.className = ""
    this.hidden = false
    this.textContent = ""
    this.disabled = false
    this.listeners = new Map()
    this.classList = { toggle: () => {} }
    this.style = { setProperty: () => {} }
  }
  addEventListener(name, callback) { this.listeners.set(name, callback) }
  removeEventListener(name) { this.listeners.delete(name) }
}

const mode = process.argv[2]
const dom = new Map()
let chooserCalls = 0
let gattConnects = 0
const bluetoothDevice = {
  name: "StarPilot",
  listeners: new Map(),
  addEventListener(name, callback) { this.listeners.set(name, callback) },
  removeEventListener(name) { this.listeners.delete(name) },
  gatt: {
    connected: mode === "granted",
    async connect() {
      gattConnects += 1
      throw Object.assign(new Error("test connection stop"), { name: "NetworkError" })
    },
  },
}
const navigator = {
  userAgent: "Mozilla/5.0 Safari/605.1.15",
  standalone: false,
  bluetooth: {
    getAvailability: async () => true,
    getDevices: async () => mode === "granted" ? [bluetoothDevice] : [],
    requestDevice: async () => {
      chooserCalls += 1
      return bluetoothDevice
    },
  },
}
const context = {
  console,
  TextEncoder,
  TextDecoder,
  Uint8Array,
  DataView,
  ArrayBuffer,
  JSON,
  Math,
  Number,
  Date,
  Promise,
  URL,
  Response,
  performance: { now: () => 1000 },
  document: {
    baseURI: "https://galaxy.firestar.link/device/live",
    body: { dataset: { secureLiveUrl: "" } },
    getElementById(id) {
      if (!dom.has(id)) dom.set(id, new FakeElement())
      return dom.get(id)
    },
  },
  navigator,
  setTimeout,
  clearTimeout,
  setInterval: () => 1,
  clearInterval: () => {},
}
context.window = {
  isSecureContext: true,
  navigator,
  location: { reload: () => {} },
  matchMedia: () => ({ matches: false }),
  addEventListener: () => {},
}
context.globalThis = context
vm.createContext(context)

const source = fs.readFileSync(process.argv[1], "utf8")
vm.runInContext(source, context)
await new Promise((resolve) => setTimeout(resolve, 0))
await dom.get("connectButton").listeners.get("click")()

process.stdout.write(JSON.stringify({ chooserCalls, gattConnects }))
"""


def test_standalone_live_link_reassembles_decodes_and_renders_protocol_frame():
  node = shutil.which("node")
  if node is None:
    pytest.skip("node is required for the Live Link browser protocol test")

  result = subprocess.run(
    [node, "--input-type=module", "-e", NODE_HARNESS, str(LIVE_LINK_SOURCE)],
    check=True,
    capture_output=True,
    text=True,
    timeout=30,
  )
  rendered = json.loads(result.stdout)

  assert rendered == {
    "sequence": 513,
    "vehicleSpeed": 20.25,
    "setSpeed": 23.5,
    "leadDistance": 26.4,
    "speedLimit": 20.1,
    "speedLimitOffset": 1.12,
    "curveTargetSpeed": 17.2,
    "vehicleText": "45",
    "setSpeedText": "53",
    "status": "Conditional Chill",
    "statusDetail": "Active — Lead vehicle",
    "engagement": "Engaged",
    "lead": "87 ft",
    "relativeSpeed": "Closing 3.9 mph",
    "slc": "45",
    "slcDetail": "Offset +2.5 mph",
    "curve": "38",
    "border": "rgba(22, 127, 64, 1.000)",
    "chooserError": "No StarPilot device was selected. On the comma, open Galaxy → Bluetooth → Pair a Phone, then try again and choose StarPilot.",
    "permissionError": (
      "Bluetooth access was denied. In Safari, allow beacio on this website. "
      + "Also verify Settings → Privacy & Security → Bluetooth → beacio is on."
    ),
    "authorizationError": (
      "The comma did not authorize this phone. While parked, leave Galaxy → Bluetooth → Pair a Phone open on the comma, "
      + "then reconnect here. StarPilot authorizes the phone automatically during that window."
    ),
  }


@pytest.mark.parametrize(("mode", "expected"), [
  ("insecure", {
    "title": "Set up Live Link",
    "message": "Use any step when you need it. Keep Pair a Phone open on the comma when you connect.",
    "linkText": "Open secure Galaxy",
    "linkHref": "https://galaxy.firestar.link/AbCdEf0123456789",
    "linkHidden": False,
  }),
  ("missing", {
    "title": "Set up Live Link",
    "message": "Use any step when you need it. Keep Pair a Phone open on the comma when you connect.",
    "installHidden": False,
    "connectHidden": False,
    "beacioClass": "setupStep setupStepPending",
    "permissionClass": "setupStep setupStepCurrent",
  }),
  ("ready", {
    "title": "Set up Live Link",
    "message": "Use any step when you need it. Keep Pair a Phone open on the comma when you connect.",
    "installHidden": False,
    "connectHidden": False,
    "beacioClass": "setupStep setupStepComplete",
    "permissionClass": "setupStep setupStepComplete",
  }),
])
def test_ios_setup_guidance_tracks_beacio_availability(mode, expected):
  node = shutil.which("node")
  if node is None:
    pytest.skip("node is required for the Live Link browser setup test")

  result = subprocess.run(
    [node, "--input-type=module", "-e", SETUP_HARNESS, str(LIVE_LINK_SOURCE), mode],
    check=True,
    capture_output=True,
    text=True,
    timeout=30,
  )
  assert json.loads(result.stdout) == expected


@pytest.mark.parametrize(("mode", "expected_chooser_calls"), [("granted", 0), ("new", 1)])
def test_connect_reuses_a_granted_starpilot_before_opening_the_picker(mode, expected_chooser_calls):
  node = shutil.which("node")
  if node is None:
    pytest.skip("node is required for the Live Link Bluetooth connection test")

  result = subprocess.run(
    [node, "--input-type=module", "-e", CONNECTION_HARNESS, str(LIVE_LINK_SOURCE), mode],
    check=True,
    capture_output=True,
    text=True,
    timeout=30,
  )
  assert json.loads(result.stdout) == {"chooserCalls": expected_chooser_calls, "gattConnects": 1}
