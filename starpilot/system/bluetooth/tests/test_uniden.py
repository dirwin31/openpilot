import threading
from types import SimpleNamespace

import pytest

from openpilot.starpilot.system.bluetooth.uniden import (
  ALERT_UUID, CONFIG_KEY, FIRMWARE_UUID, GATT_IFACE, STATE_KEY, TELEMETRY_UUID, WRITE_UUID,
  UnidenService, normalize_config, parse_alerts, setting_command,
)
from openpilot.starpilot.controls.lib.uniden_slowdown import UnidenSlowdown

ADDRESS = "AA:BB:CC:DD:EE:01"
PACKET = b"1,00,KA,3,33,33.7850,R,1&0&0&0"


class Params:
  def __init__(self, values=None):
    self.values = values or {}

  def get(self, key):
    return self.values.get(key)

  def get_bool(self, key):
    return bool(self.get(key))

  def put(self, key, value):
    self.values[key] = value

  put_nonblocking = put


def config(**extra):
  return normalize_config({"enabled": True, "address": ADDRESS, "auto_slowdown": True, **extra})


def test_packet_snapshot_clear_muting_and_multiple_alerts():
  assert parse_alerts(b"0&0&0&0") == []
  alerts = parse_alerts(PACKET + b"&1,01,K POP,7,50,24.15,F,2")
  assert alerts[0] == {"band": "KA", "strength": 3, "muted": False, "direction": "Rear", "frequency": "33.7850", "id": "00"}
  assert alerts[1]["muted"] and alerts[1]["band"] == "K POP"


@pytest.mark.parametrize("packet", [b"", b"garbage", b"1,00,KA,9,33,33.7,R,1", b"1,00,UNKNOWN,3,33,33.7,R,1",
                                     b"1,00,KA,3,33,33.7,R,0", b"2,00,KA,3,33,33.7,R,1", b"\xff", b"0&"])
def test_invalid_packets_do_not_become_alerts(packet):
  with pytest.raises((ValueError, UnicodeError)):
    parse_alerts(packet)


@pytest.mark.parametrize("extra", [{"address": "bad"}, {"enabled": "true"}, {"auto_slowdown": 1}, {"bands": []},
                                    {"bands": ["POP"]}, {"min_strength": True}, {"min_strength": 9}, {"unknown": 1}])
def test_config_validation(extra):
  with pytest.raises(ValueError):
    config(**extra)


def make_slowdown(**extra):
  params = Params({CONFIG_KEY: config(**extra)})
  memory = Params({STATE_KEY: {"connected": True, "address": ADDRESS, "observed_at": 100, "alerts": parse_alerts(PACKET)}})
  slowdown = UnidenSlowdown(params, memory)
  return params, memory, slowdown


def update(slowdown, **extra):
  return slowdown.update(**{"enabled": True, "longitudinal": True, "gas_pressed": False, "brake_pressed": False,
                            "cruise": 35, "posted_limit": 25, "source": "Map Data", "now": 101, **extra})


def test_slowdown_never_raises_target_and_gas_override_latches_until_clear():
  _, memory, slowdown = make_slowdown()
  assert update(slowdown) == 25
  assert update(slowdown, cruise=20) == 20
  assert update(slowdown, gas_pressed=True) == 0
  assert update(slowdown) == 0
  memory.values[STATE_KEY]["alerts"] = []
  assert update(slowdown) == 0
  memory.values[STATE_KEY]["alerts"] = parse_alerts(PACKET)
  assert update(slowdown) == 25


@pytest.mark.parametrize("changes", [{"observed_at": 0}, {"observed_at": 105}, {"observed_at": float("nan")},
                                      {"observed_at": None}, {"observed_at": 97}, {"connected": False},
                                      {"address": "other"}, {"alerts": [{"band": "KA"}]}])
def test_missing_stale_future_wrong_device_or_invalid_state_cannot_slow(changes):
  _, memory, slowdown = make_slowdown()
  memory.values[STATE_KEY].update(changes)
  assert update(slowdown) == 0


@pytest.mark.parametrize("changes", [{"enabled": False}, {"longitudinal": False}, {"brake_pressed": True},
                                      {"posted_limit": 0}, {"posted_limit": float("nan")}, {"posted_limit": 100},
                                      {"source": "None"}, {"cruise": 0}])
def test_inactive_control_and_invalid_limits_cannot_slow(changes):
  _, _, slowdown = make_slowdown()
  assert update(slowdown, **changes) == 0


@pytest.mark.parametrize("changes", [{"enabled": False}, {"auto_slowdown": False}, {"bands": ["K"]}, {"min_strength": 4}])
def test_slowdown_configuration_is_respected(changes):
  _, _, slowdown = make_slowdown(**changes)
  assert update(slowdown) == 0


def test_muted_alert_filter():
  params, memory, slowdown = make_slowdown()
  memory.values[STATE_KEY]["alerts"][0]["muted"] = True
  assert update(slowdown) == 0
  params.values[CONFIG_KEY]["ignore_muted"] = False
  assert update(slowdown) == 25


class Client:
  def __init__(self):
    self.device = {"path": "/org/bluez/hci0/dev_A", "address": ADDRESS, "name": "R8W@123", "uniden": True,
                   "paired": True, "connected": True, "services_resolved": True}
    self.values = {ALERT_UUID: PACKET, FIRMWARE_UUID: b"R8W/142", TELEMETRY_UUID: b"12.1&0&W,0,193,C&0&12&D&D", WRITE_UUID: b""}
    self.calls = []
    self.fail_write = False

  def device_for_address(self, address):
    assert address == ADDRESS
    return self.device

  def managed_objects(self):
    return {self.device["path"] + "/service99/" + uuid: {GATT_IFACE: {"UUID": uuid}} for uuid in self.values}

  def _call(self, path, interface, method, signature, body, **kwargs):
    self.calls.append((path, method, body))
    if method == "WriteValue":
      if self.fail_write:
        raise RuntimeError("Rejected write")
      return ()
    return (self.values[path.rsplit("/", 1)[1]],)


def make_service():
  client = Client()
  lock = threading.RLock()
  controller = SimpleNamespace(params=Params({CONFIG_KEY: config(), "BluetoothEnabled": True}),
                               _client=lambda: client, _connection_lock=lambda _: lock, _offroad=lambda: True)
  memory = Params()
  service = UnidenService(controller, memory)
  return service, client, memory


def test_monitor_reads_snapshot_each_tick_and_clears_on_disconnect_or_invalid_data():
  service, client, memory = make_service()
  service.tick()
  first = memory.get(STATE_KEY)
  assert first["connected"] and first["alerts"] and first["firmware"] == "R8W/142"
  service.tick()
  assert memory.get(STATE_KEY)["observed_at"] >= first["observed_at"]
  client.values[ALERT_UUID] = b"0&0&0&0"
  service.tick()
  assert memory.get(STATE_KEY)["alerts"] == []
  client.values[ALERT_UUID] = b"broken"
  service.tick()
  assert not memory.get(STATE_KEY)["connected"]
  client.values[ALERT_UUID] = PACKET
  client.device["connected"] = False
  service.tick()
  assert not memory.get(STATE_KEY)["connected"] and memory.get(STATE_KEY)["alerts"] == []


def test_setting_transport_is_scoped_and_never_claims_confirmation():
  service, client, memory = make_service()
  service.apply_setting("volume", 5)
  path, method, body = client.calls[-1]
  assert path.endswith(WRITE_UUID) and "/service99/" in path
  assert method == "WriteValue" and body == (b"BTreqSETC:101=5", {"type": ("s", "command")})
  assert memory.get(STATE_KEY)["last_command"]["status"] == "sent_unconfirmed"
  client.fail_write = True
  with pytest.raises(RuntimeError, match="Rejected"):
    service.apply_setting("volume", 4)
  assert memory.get(STATE_KEY)["last_command"]["value"] == 5
  service.controller._offroad = lambda: False
  with pytest.raises(RuntimeError, match="offroad"):
    service.apply_setting("volume", 4)


def test_no_hardcoded_gatt_fallback_and_disable_clears_alerts():
  service, client, memory = make_service()
  del client.values[WRITE_UUID]
  with pytest.raises(RuntimeError, match="characteristic"):
    service.apply_setting("volume", 5)
  service.tick()
  assert not memory.get(STATE_KEY)["can_write"]
  service.configure(config(enabled=False))
  assert not memory.get(STATE_KEY)["connected"]
  service.tick()
  assert memory.get(STATE_KEY)["alerts"] == []


@pytest.mark.parametrize("key,value", [("volume", 9), ("mode", "unknown"), ("volume", True), ("unknown", 0), ("brightness", "bogus")])
def test_setting_commands_reject_invalid_values(key, value):
  with pytest.raises(ValueError):
    setting_command(key, value)


def test_corrupt_runtime_json_cannot_crash_or_activate_slowdown(monkeypatch):
  _, memory, slowdown = make_slowdown()

  def corrupt(_key):
    raise ValueError("Invalid JSON")

  monkeypatch.setattr(memory, "get", corrupt)
  assert update(slowdown) == 0
