import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
DECODER_PATH = REPO_ROOT / "starpilot/system/the_galaxy/assets/mobile/js/ble/live_frames.js"


def _live_module(monkeypatch):
  cereal = ModuleType("cereal")
  cereal.messaging = SimpleNamespace(SubMaster=object)
  cereal.log = SimpleNamespace()
  swaglog = ModuleType("openpilot.common.swaglog")
  swaglog.cloudlog = SimpleNamespace(exception=lambda *args, **kwargs: None)
  monkeypatch.setitem(sys.modules, "cereal", cereal)
  monkeypatch.setitem(sys.modules, "cereal.messaging", cereal.messaging)
  monkeypatch.setitem(sys.modules, "openpilot.common.swaglog", swaglog)
  # Test the working tree even when macOS native dependencies use a host mirror.
  name = "openpilot.starpilot.system.bluetooth.live"
  spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "starpilot/system/bluetooth/live.py")
  live = importlib.util.module_from_spec(spec)
  monkeypatch.setitem(sys.modules, name, live)
  spec.loader.exec_module(live)
  return live


@pytest.mark.skipif(shutil.which("node") is None, reason="no node.js runtime available")
def test_python_live_frame_golden_vectors_and_reassembly(monkeypatch, tmp_path):
  live = _live_module(monkeypatch)
  flags = int(
    live.LiveFlags.CONNECTED | live.LiveFlags.STARTED | live.LiveFlags.ENGAGED |
    live.LiveFlags.CRUISE_ENABLED | live.LiveFlags.LEAD_PRESENT | live.LiveFlags.LATERAL_ACTIVE |
    live.LiveFlags.LONGITUDINAL_ACTIVE | live.LiveFlags.TELEMETRY_VALID | live.LiveFlags.METRIC |
    live.LiveFlags.RED_LIGHT
  )
  state = live.LiveSnapshot(
    flags=flags,
    vehicle_speed=12.34,
    set_speed=27.78,
    acceleration=-1.25,
    target_acceleration=0.42,
    steering_angle=-12.3,
    desired_steering_angle=8.7,
    steering_torque=-2.1,
    lead_distance=45.6,
    lead_relative_speed=-3.25,
    lead_probability=0.987,
    speed_limit=22.22,
    speed_limit_offset=-1.5,
    curve_target_speed=18.75,
    cruise_state=2,
    border_state=6,
    alert_status=1,
    conditional_chill_reason=2,
    driving_profile=3,
    longitudinal_profile=1,
    lane_change_state=2,
    lane_change_direction=1,
    long_control_state=3,
    model_source=1,
    border_color=(10, 20, 30, 240),
    alert_id=0x89ABCDEF,
    metadata_revision=0x10203040,
  ).pack(65530, 0xF1234567)

  health_flags = int(
    live.HealthFlags.ONROAD | live.HealthFlags.WIFI_CONNECTED |
    live.HealthFlags.LOCAL_NON_METERED_LINK | live.HealthFlags.THERMAL_OK |
    live.HealthFlags.FAN_ACTIVE | live.HealthFlags.CLOUD_PINGED
  )
  health = live.HealthSnapshot(
    flags=health_flags,
    cpu_usage=87,
    gpu_usage=42,
    memory_usage=73,
    free_storage=9,
    cpu_temp=81.2,
    gpu_temp=70.4,
    memory_temp=65.5,
    max_temp=82.1,
    intake_temp=-4.5,
    thermal_status=2,
    fan_speed=91,
    power_draw=13.57,
    som_power_draw=8.24,
    battery_reserve_wh=321.4,
    screen_brightness=66,
    network_type=1,
    network_strength=4,
    uptime_s=1234567,
  ).pack(65531, 0xF1234999)

  newer = live.LiveSnapshot(vehicle_speed=3.21).pack(7, 9000)
  vectors = {
    "state": state.hex(),
    "stateFragments": [part.hex() for part in live.live_notification_fragments(state)],
    "health": health.hex(),
    "healthFragments": [part.hex() for part in live.live_notification_fragments(health)],
    "newerFragments": [part.hex() for part in live.live_notification_fragments(newer)],
  }
  decoder = tmp_path / "live_frames.mjs"
  decoder.write_text(DECODER_PATH.read_text(encoding="utf-8"), encoding="utf-8")
  harness = tmp_path / "check.mjs"
  harness.write_text(
    """
import assert from "node:assert/strict"
import { decodeFrame, hasFlag, LIVE_FLAGS, HEALTH_FLAGS, LiveReassembler } from "./live_frames.mjs"
const input = JSON.parse(process.env.LIVE_VECTORS)
const bytes = hex => Uint8Array.from(Buffer.from(hex, "hex"))

const state = decodeFrame(bytes(input.state))
assert.equal(state.kind, "state")
assert.equal(state.sequence, 65530)
assert.equal(state.monotonicMilliseconds, 0xF1234567)
assert.equal(state.flags >>> 0, 0xA200E027)
assert.equal(state.vehicleSpeed, 12.34)
assert.equal(state.setSpeed, 27.78)
assert.equal(state.acceleration, -1.25)
assert.equal(state.targetAcceleration, 0.42)
assert.equal(state.steeringAngle, -12.3)
assert.equal(state.desiredSteeringAngle, 8.7)
assert.equal(state.steeringTorque, -2.1)
assert.equal(state.leadDistance, 45.6)
assert.equal(state.leadRelativeSpeed, -3.25)
assert.equal(state.leadProbability, 0.987)
assert.equal(state.speedLimit, 22.22)
assert.equal(state.speedLimitOffset, -1.5)
assert.equal(state.curveTargetSpeed, 18.75)
assert.deepEqual(state.borderColor, { red: 10, green: 20, blue: 30, alpha: 240 })
assert.equal(state.alertID, 0x89ABCDEF)
assert.equal(state.metadataRevision, 0x10203040)
assert.equal(state.isMetric, true)
assert.equal(hasFlag(state.flags, LIVE_FLAGS.redLight), true)

const health = decodeFrame(bytes(input.health))
assert.equal(health.kind, "health")
assert.equal(health.sequence, 65531)
assert.equal(health.cpuPercent, 87)
assert.equal(health.memoryPercent, 73)
assert.equal(health.cpuTempC, 81.2)
assert.equal(health.intakeTempC, -4.5)
assert.equal(health.totalPowerDrawW, 13.57)
assert.equal(health.batteryReserveWh, 321.4)
assert.equal(health.uptimeSeconds, 1234567)
assert.equal(hasFlag(health.flags, HEALTH_FLAGS.directLanAllowed), true)

let reassembler = new LiveReassembler()
let assembled = null
for (const index of [2, 0, 3, 1]) assembled ||= reassembler.consume(bytes(input.stateFragments[index]))
assert.equal(assembled?.sequence, state.sequence)
assert.equal(assembled?.vehicleSpeed, state.vehicleSpeed)

reassembler = new LiveReassembler()
assert.equal(reassembler.consume(bytes(input.stateFragments[0])), null)
assert.equal(reassembler.consume(bytes(input.stateFragments[1])), null)
assembled = null
for (const fragment of input.newerFragments) assembled ||= reassembler.consume(bytes(fragment))
assert.equal(assembled?.sequence, 7)
assert.equal(assembled?.vehicleSpeed, 3.21)

reassembler = new LiveReassembler()
assert.equal(reassembler.consume(bytes(input.health))?.kind, "health")
const unknown = bytes(input.state)
unknown[3] = 99
assert.equal(decodeFrame(unknown), null)
assert.equal(new LiveReassembler().consume(unknown), null)
console.log("live frame decoder OK")
""",
    encoding="utf-8",
  )
  environment = {**__import__("os").environ, "LIVE_VECTORS": json.dumps(vectors)}
  result = subprocess.run([shutil.which("node"), str(harness)], capture_output=True, text=True, env=environment)
  assert result.returncode == 0, f"node failed:\n{result.stdout}\n{result.stderr}"
