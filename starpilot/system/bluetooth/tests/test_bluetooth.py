import io
import json
import queue
import threading
import time

import numpy as np
import pytest

from jeepney import DBusAddress, new_method_call

from openpilot.starpilot.system.bluetooth.audio import BluetoothAudioSink
from openpilot.starpilot.system.bluetooth.bluez import BlueZClient, PairingAgent
from openpilot.starpilot.system.bluetooth.companion import (
  ADVERTISEMENT_IFACE, COMPANION_ADVERTISEMENT_PATH, COMPANION_APP_PATH, COMPANION_COMMAND_PATH, COMPANION_PROTOCOL_VERSION,
  COMPANION_RESPONSE_PATH, COMPANION_SERVICE_PATH, COMPANION_SERVICE_UUID, COMPANION_STATUS_PATH, GATT_CHARACTERISTIC_IFACE,
  OBJECT_MANAGER_IFACE, PROPERTIES_IFACE, CompanionGattApplication, CompanionProtocol,
)
from openpilot.starpilot.system.bluetooth.daemon import BluetoothController
from openpilot.starpilot.system.bluetooth.protocol import (A2DP_SINK_UUID, HID_UUID, BluetoothClient, BluetoothDevice, BluetoothStatus,
                                                           device_capabilities, show_pairing_device)
from openpilot.system import hardware
from openpilot.system.ui.lib.bluetooth_manager import BluetoothManager


class FakeParams:
  def __init__(self, **values):
    self.values = values

  def get_bool(self, key):
    return bool(self.values.get(key, False))

  def get(self, key, encoding=None, **_kwargs):
    value = self.values.get(key)
    return value.decode(encoding) if encoding and isinstance(value, bytes) else value

  def put_bool(self, key, value):
    self.values[key] = value

  def put(self, key, value):
    self.values[key] = value

  def remove(self, key):
    self.values.pop(key, None)


class TypedJsonFakeParams(FakeParams):
  def get(self, key, encoding=None, **kwargs):
    value = super().get(key, encoding=encoding, **kwargs)
    if key == "BluetoothCompanionDevices" and isinstance(value, str):
      return json.loads(value)
    return value

  def put(self, key, value):
    if key == "BluetoothCompanionDevices" and not isinstance(value, list):
      raise TypeError("Type mismatch while writing param BluetoothCompanionDevices")
    super().put(key, value)


class FakeAgent:
  def __init__(self):
    self.responses = []

  def set_auto_accept_incoming(self, _enabled):
    pass

  def respond(self, prompt_id, accepted, value):
    self.responses.append((prompt_id, accepted, value))
    return prompt_id == "prompt"


class FakeBlueZ:
  def __init__(self):
    self.agent = FakeAgent()
    self.powered = False
    self.discoverable = False
    self.discovering = False
    self.closed = False
    self.actions = []
    self.pairing_mode_error = None
    self.router = object()
    self.device = {
      "path": "/fake/device",
      "address": "00:11:22:33:44:55",
      "name": "Speaker",
      "paired": True,
      "trusted": True,
      "connected": False,
      "audio": True,
      "controller": False,
    }

  def close(self):
    self.closed = True

  def set_powered(self, powered):
    self.powered = powered

  def set_discoverable(self, discoverable):
    self.discoverable = discoverable

  def status(self):
    return {"powered": self.powered, "discovering": self.discovering, "devices": [dict(self.device)], "prompt": None}

  def start_discovery(self):
    self.discovering = True

  def stop_discovery(self):
    self.discovering = False
    self.actions.append(("stop_scan", ""))

  def device_for_address(self, _address):
    return dict(self.device)

  def pair(self, address, _device_path=None):
    self.actions.append(("pair", address))

  def connect(self, address):
    self.actions.append(("connect", address))
    self.device["connected"] = True

  def disconnect(self, address):
    self.actions.append(("disconnect", address))
    self.device["connected"] = False

  def remove(self, address):
    self.actions.append(("remove", address))

  def _call(self, *args):
    self.actions.append(("call", *args))

  def adapter(self):
    return "/org/bluez/hci0", {}

  def set_pairing_mode(self, enabled):
    self.actions.append(("pairing_mode", enabled))
    if self.pairing_mode_error is not None:
      raise self.pairing_mode_error

  def paired_device_for_path(self, path):
    if path != "/org/bluez/hci0/dev_phone" or not self.device["paired"]:
      return None
    return {**self.device, "path": path}

  def set_device_property(self, address, name, signature, value):
    self.actions.append(("property", address, name, signature, value))
    if name == "Trusted":
      self.device["trusted"] = bool(value)


class FakeCompanion:
  def __init__(self, _router, _call, authorize, protocol):
    self.authorize = authorize
    self.protocol = protocol
    self.started = ""
    self.closed = False

  def start(self, adapter_path):
    self.started = adapter_path

  def close(self):
    self.closed = True


class FakeRadio:
  available = True
  ready = True

  def __init__(self):
    self.starts = 0
    self.stops = 0

  def start(self):
    self.starts += 1

  def stop(self):
    self.stops += 1


class BlockingStopRadio(FakeRadio):
  def __init__(self):
    super().__init__()
    self.stop_started = threading.Event()
    self.allow_stop = threading.Event()

  def stop(self):
    self.stops += 1
    self.stop_started.set()
    self.allow_stop.wait()


class BlockingPowerClient:
  def __init__(self):
    self.power_entered = threading.Event()
    self.allow_power = threading.Event()
    self.power_finished = threading.Event()
    self.status_calls = 0

  def set_power(self, _enabled):
    self.power_entered.set()
    self.allow_power.wait()
    self.power_finished.set()

  def status(self):
    self.status_calls += 1
    return BluetoothStatus()


class FakeProcess:
  def __init__(self):
    self.stdin = io.BytesIO()
    self.stopped = False

  def poll(self):
    return 0 if self.stopped else None

  def terminate(self):
    self.stopped = True

  def wait(self, timeout=None):
    return 0

  def kill(self):
    self.stopped = True


def test_protocol_round_trip_and_capabilities():
  audio, controller = device_capabilities([A2DP_SINK_UUID, HID_UUID])
  assert audio and controller
  status = BluetoothStatus.from_dict({
    "available": True,
    "enabled": True,
    "devices": [{"address": "00:11:22:33:44:55", "name": "Combo", "uuids": [A2DP_SINK_UUID, HID_UUID], "audio": True, "controller": True}],
  })
  assert status.devices == (BluetoothDevice("00:11:22:33:44:55", "Combo", uuids=(A2DP_SINK_UUID, HID_UUID), audio=True, controller=True),)


def test_companion_protocol_is_read_only_and_versioned():
  params = FakeParams(IsOffroad=True, Version="0.10", GitBranch="Dom")
  protocol = CompanionProtocol(params, clock=lambda: 1234.9)

  status = json.loads(protocol.status_bytes())
  assert status == {
    "branch": "Dom",
    "device": "StarPilot",
    "onroad": False,
    "protocol_version": COMPANION_PROTOCOL_VERSION,
    "version": "0.10",
  }
  assert json.loads(protocol.handle(b'{"id":"one","op":"ping"}')) == {
    "data": {"time": 1234}, "id": "one", "ok": True, "op": "ping",
  }
  rejected = json.loads(protocol.handle(b'{"id":"two","op":"set_speed"}'))
  assert not rejected["ok"] and "Unsupported" in rejected["error"]


def test_companion_gatt_contract_requires_authenticated_characteristics():
  app = object.__new__(CompanionGattApplication)
  objects = app.managed_objects()

  assert objects[COMPANION_SERVICE_PATH]["org.bluez.GattService1"]["UUID"] == ("s", COMPANION_SERVICE_UUID)
  assert objects[COMPANION_STATUS_PATH]["org.bluez.GattCharacteristic1"]["Flags"] == ("as", ["encrypt-authenticated-read"])
  assert objects[COMPANION_COMMAND_PATH]["org.bluez.GattCharacteristic1"]["Flags"] == ("as", ["encrypt-authenticated-write"])
  assert objects[COMPANION_RESPONSE_PATH]["org.bluez.GattCharacteristic1"]["Flags"] == ("as", ["encrypt-authenticated-read"])


def test_companion_gatt_exports_serializable_bluez_objects():
  app = object.__new__(CompanionGattApplication)

  object_manager = new_method_call(
    DBusAddress(COMPANION_APP_PATH, bus_name="org.bluez", interface=OBJECT_MANAGER_IFACE), "GetManagedObjects",
  )
  object_manager.header.serial = 1
  assert len(app._dispatch(object_manager).serialise(2)) > 0

  advertisement = new_method_call(
    DBusAddress(COMPANION_ADVERTISEMENT_PATH, bus_name="org.bluez", interface=PROPERTIES_IFACE),
    "GetAll", "s", (ADVERTISEMENT_IFACE,),
  )
  advertisement.header.serial = 1
  response = app._dispatch(advertisement)
  assert response.body[0]["ServiceUUIDs"] == ("as", [COMPANION_SERVICE_UUID])
  assert len(response.serialise(2)) > 0


def test_companion_gatt_close_is_idempotent():
  class FakeFilter:
    def __init__(self):
      self.messages = queue.Queue()
      self.exit_count = 0

    def __enter__(self):
      return self.messages

    def __exit__(self, *_args):
      self.exit_count += 1

  class FakeRouter:
    def __init__(self):
      self.message_filter = FakeFilter()

    def filter(self, *_args, **_kwargs):
      return self.message_filter

    def send(self, _message):
      pass

  router = FakeRouter()
  app = CompanionGattApplication(router, lambda *_args: (), lambda _path: False)

  app.close()
  app.close()

  assert router.message_filter.exit_count == 1
  assert not app._thread.is_alive()


def test_companion_gatt_rejects_unbonded_access_and_scopes_responses_by_phone():
  protocol = CompanionProtocol(FakeParams(IsOffroad=True))
  app = object.__new__(CompanionGattApplication)
  app.protocol = protocol
  app._authorize = lambda path: path == "/phone/one"
  app._responses = {}

  def message(path, member, signature, body):
    request = new_method_call(DBusAddress(path, bus_name="org.bluez", interface=GATT_CHARACTERISTIC_IFACE), member, signature, body)
    request.header.serial = 1
    return request

  unbonded = message(COMPANION_STATUS_PATH, "ReadValue", "a{sv}", ({"device": ("o", "/phone/two")},))
  with pytest.raises(PermissionError, match="bonded"):
    app._dispatch(unbonded)

  write = message(COMPANION_COMMAND_PATH, "WriteValue", "aya{sv}", (
    b'{"id":"one","op":"ping"}', {"device": ("o", "/phone/one")},
  ))
  app._dispatch(write)
  read = message(COMPANION_RESPONSE_PATH, "ReadValue", "a{sv}", ({"device": ("o", "/phone/one")},))
  response = json.loads(app._dispatch(read).body[0])
  assert response["id"] == "one" and response["ok"]
  assert "/phone/two" not in app._responses


def test_pairing_list_filters_anonymous_and_irrelevant_advertisements():
  assert not show_pairing_device("00:11:22:33:44:55", "00:11:22:33:44:55", False, False, False, False, False, False)
  assert not show_pairing_device("00:11:22:33:44:55", "Nearby sensor", False, False, False, False, False, False)
  assert show_pairing_device("00:11:22:33:44:55", "Media Remote", False, False, False, False, False, True)
  assert show_pairing_device("00:11:22:33:44:55", "Media Remote", False, False, False, False, False, True, True)
  assert not show_pairing_device("00:11:22:33:44:55", "Nearby sensor", False, False, False, False, False, False, True)
  assert show_pairing_device("00:11:22:33:44:55", "Known device", True, True, False, False, False, False)


def test_desktop_fake_bluetooth_is_stateful_and_interactive(monkeypatch, tmp_path):
  monkeypatch.setenv("SP_ALLOW_DESKTOP_FAKE_BLUETOOTH", "1")
  monkeypatch.setenv("SIMULATION", "1")
  monkeypatch.setenv("NOBOARD", "1")
  client = BluetoothClient(socket_path=str(tmp_path / "bluetooth.sock"))

  initial = client.status()
  speaker, controller = initial.devices[:2]
  assert initial.available and initial.enabled and speaker.connected

  client.start_scan()
  assert client.status().discovering

  client.pair(controller.address)
  client.connect(controller.address)
  paired_controller = next(device for device in client.status().devices if device.address == controller.address)
  assert paired_controller.paired and paired_controller.trusted and paired_controller.connected

  client.select_audio(speaker.address)
  assert client.status().selected_audio == speaker.address
  assert client.test_audio(speaker.address) == 3.0

  client.start_companion_pairing()
  companion = client.status()
  assert companion.companion_enabled and companion.companion_pairing
  assert 115 <= companion.companion_pairing_remaining <= 120
  client.stop_companion_pairing()
  assert not client.status().companion_pairing

  client.forget(controller.address)
  forgotten_controller = next(device for device in client.status().devices if device.address == controller.address)
  assert not forgotten_controller.paired and not forgotten_controller.connected

  client.set_power(False)
  disabled = client.status()
  assert not disabled.enabled and not disabled.powered and not disabled.discovering
  assert disabled.selected_audio == speaker.address
  with pytest.raises(RuntimeError, match="Enable Bluetooth"):
    client.start_scan()
  client.set_power(True)
  enabled = client.status()
  assert enabled.enabled and enabled.selected_audio == speaker.address


def test_desktop_fake_bluetooth_cannot_activate_on_device(monkeypatch, tmp_path):
  monkeypatch.setenv("SP_ALLOW_DESKTOP_FAKE_BLUETOOTH", "1")
  monkeypatch.setenv("SIMULATION", "1")
  monkeypatch.setenv("NOBOARD", "1")
  monkeypatch.setattr(hardware, "PC", False)
  client = BluetoothClient(socket_path=str(tmp_path / "bluetooth.sock"))

  assert client._get_desktop_fake() is None
  assert client._desktop_fake is None


def test_pairing_agent_accept_reject_and_timeout():
  agent = PairingAgent()
  agent.set_auto_accept_incoming(True)
  assert agent.request("confirmation", "/incoming", "123456") == (True, "")
  agent.set_auto_accept_incoming(False)
  result = []
  worker = threading.Thread(target=lambda: result.append(agent.request("confirmation", "/device", "123456", timeout=1.0)))
  worker.start()
  deadline = time.monotonic() + 1.0
  while agent.prompt is None and time.monotonic() < deadline:
    time.sleep(0.01)
  assert agent.prompt is not None
  assert agent.respond(agent.prompt["id"], True)
  worker.join(timeout=1.0)
  assert result == [(True, "")]
  assert agent.request("pin", "/device", timeout=0.01) == (False, "")


def test_bluez_disconnect_waits_for_confirmed_state(monkeypatch):
  client = object.__new__(BlueZClient)
  states = iter((True, True, False))
  calls = []
  client.device_for_address = lambda _address: {"path": "/phone", "connected": next(states)}
  client._call = lambda *args, **kwargs: calls.append((args, kwargs))
  monkeypatch.setattr("openpilot.starpilot.system.bluetooth.bluez.time.sleep", lambda _delay: None)

  client.disconnect("00:11:22:33:44:55")

  assert calls[0][0][:3] == ("/phone", "org.bluez.Device1", "Disconnect")


def test_bluez_disconnect_accepts_removed_device_as_disconnected():
  client = object.__new__(BlueZClient)
  calls = 0

  def device_for_address(_address):
    nonlocal calls
    calls += 1
    if calls == 1:
      return {"path": "/phone", "connected": True}
    raise RuntimeError("Bluetooth device was not found")

  client.device_for_address = device_for_address
  client._call = lambda *_args, **_kwargs: None

  client.disconnect("00:11:22:33:44:55")


def test_disabled_status_does_not_start_radio_or_bluez():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=False)
  radio = FakeRadio()
  created = []
  controller = BluetoothController(params, lambda: created.append(FakeBlueZ()) or created[-1], radio)
  status = controller.status()
  assert status["available"] and not status["enabled"] and not status["powered"]
  assert radio.starts == 0 and created == []


def test_enabled_initialization_registers_bluetooth_agent_without_ui_poll():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  radio = FakeRadio()
  created = []
  controller = BluetoothController(params, lambda: created.append(FakeBlueZ()) or created[-1], radio)

  controller.initialize()

  assert radio.starts == 1 and len(created) == 1
  assert created[0].powered


def test_power_pair_audio_and_offroad_enforcement():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=False)
  radio = FakeRadio()
  clients = []
  controller = BluetoothController(params, lambda: clients.append(FakeBlueZ()) or clients[-1], radio)
  controller.handle({"command": "set_power", "enabled": True})
  assert params.get_bool("BluetoothEnabled") and radio.starts == 1 and clients[0].powered
  controller.handle({"command": "select_audio", "address": "00:11:22:33:44:55"})
  assert params.get("BluetoothAudioAddress") == "00:11:22:33:44:55"
  controller.handle({"command": "select_audio", "address": ""})
  assert params.get("BluetoothAudioAddress") is None
  assert clients[0].actions == []
  params.values["IsOffroad"] = False
  with pytest.raises(RuntimeError, match="offroad"):
    controller.handle({"command": "start_scan"})
  controller.handle({"command": "connect", "address": "00:11:22:33:44:55"})
  assert clients[0].actions[-1] == ("connect", "00:11:22:33:44:55")
  params.values["IsOffroad"] = True
  controller.handle({"command": "set_power", "enabled": False})
  assert not params.get_bool("BluetoothEnabled") and radio.stops == 1 and clients[0].closed


def test_power_off_preserves_saved_audio_selection():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=False, BluetoothAudioAddress="00:11:22:33:44:55")
  controller = BluetoothController(params, FakeBlueZ, FakeRadio())

  controller.handle({"command": "set_power", "enabled": True})
  controller.handle({"command": "set_power", "enabled": False})

  assert params.get("BluetoothAudioAddress") == "00:11:22:33:44:55"


def test_status_does_not_restart_radio_during_disable():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  radio = BlockingStopRadio()
  client = FakeBlueZ()
  controller = BluetoothController(params, lambda: client, radio)
  controller._bluez = client

  errors = []
  def disable():
    try:
      controller.handle({"command": "set_power", "enabled": False})
    except Exception as error:
      errors.append(error)

  status_started = threading.Event()
  status_done = threading.Event()
  status_result = []

  def read_status():
    status_started.set()
    status_result.append(controller.status())
    status_done.set()

  worker = threading.Thread(target=disable, daemon=True)
  worker.start()
  assert radio.stop_started.wait(timeout=1.0)

  status_worker = threading.Thread(target=read_status, daemon=True)
  status_worker.start()
  try:
    assert status_started.wait(timeout=1.0)
    assert not status_done.wait(timeout=0.1)
  finally:
    radio.allow_stop.set()

  worker.join(timeout=1.0)
  status_worker.join(timeout=1.0)

  assert not worker.is_alive()
  assert not status_worker.is_alive()
  assert errors == []
  assert radio.starts == 0
  assert radio.stops == 1
  status = status_result[0]
  assert not status["enabled"]
  assert not params.get_bool("BluetoothEnabled")


def test_status_poll_does_not_overlap_power_transition():
  client = BlockingPowerClient()
  manager = object.__new__(BluetoothManager)
  manager._client = client
  manager._lock = threading.Lock()
  manager._client_lock = threading.Lock()
  manager._status = BluetoothStatus()
  manager._active = True
  manager._exit = False
  manager._operation_error = ""
  manager._operations = {}
  manager._power_pending = False
  manager._audio_test_deadline = 0.0

  manager.set_power(True)
  assert client.power_entered.wait(timeout=1.0)
  poller = threading.Thread(target=manager._poll_status)
  poller.start()
  poller.join(timeout=1.0)

  client.allow_power.set()
  assert client.power_finished.wait(timeout=1.0)

  assert not poller.is_alive()
  assert client.status_calls == 0


def test_audio_uses_soundd_engage_alert_and_cleans_up():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  params_memory = FakeParams()
  client = FakeBlueZ()
  client.device["connected"] = True
  controller = BluetoothController(params, lambda: client, FakeRadio(), params_memory, sleep=lambda _delay: None)

  result = controller.handle({"command": "test_audio", "address": client.device["address"]})
  deadline = time.monotonic() + 1.0
  while params.get_bool("BluetoothAudioTestActive") and time.monotonic() < deadline:
    time.sleep(0.01)

  assert params.get("BluetoothAudioAddress") == client.device["address"]
  assert 2500 <= result["audio_test_delay_ms"] <= 3000
  assert params_memory.get("TestAlert") == "engage"
  assert not params.get_bool("BluetoothAudioTestActive")


def test_audio_requires_connected_device_and_offroad():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  client = FakeBlueZ()
  controller = BluetoothController(params, lambda: client, FakeRadio(), FakeParams())

  with pytest.raises(RuntimeError, match="Connect"):
    controller.handle({"command": "test_audio", "address": client.device["address"]})
  params.values["IsOffroad"] = False
  with pytest.raises(RuntimeError, match="offroad"):
    controller.handle({"command": "test_audio", "address": client.device["address"]})


def test_scan_stops_after_timeout():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  client = FakeBlueZ()
  controller = BluetoothController(params, lambda: client, FakeRadio())
  controller.handle({"command": "start_scan"})
  assert client.discovering and controller._scan_deadline > time.monotonic()

  controller._maintain_scan(controller.status(), controller._scan_deadline)
  assert not client.discovering and controller._scan_deadline == 0.0


def test_pair_keeps_discovery_until_pair_starts():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  client = FakeBlueZ()
  controller = BluetoothController(params, lambda: client, FakeRadio())
  controller.handle({"command": "start_scan"})
  controller.handle({"command": "pair", "address": client.device["address"]})

  deadline = time.monotonic() + 1.0
  while not any(action[0] == "pair" for action in client.actions) and time.monotonic() < deadline:
    time.sleep(0.01)

  pair_index = client.actions.index(("pair", client.device["address"]))
  assert client.actions[-1] == ("stop_scan", "")
  assert pair_index < len(client.actions) - 1


def test_companion_pairing_window_and_bond_authorization():
  params = TypedJsonFakeParams(IsOffroad=True, BluetoothEnabled=True, BluetoothCompanionEnabled=False)
  client = FakeBlueZ()
  client.device.update({"name": "Phone", "audio": False, "trusted": False})
  companions = []

  def companion_factory(*args):
    companions.append(FakeCompanion(*args))
    return companions[-1]

  controller = BluetoothController(params, lambda: client, FakeRadio(), companion_factory=companion_factory)
  controller.handle({"command": "start_companion_pairing"})
  assert params.get_bool("BluetoothCompanionEnabled")
  assert companions[0].started == "/org/bluez/hci0"
  status = controller.status()
  assert status["companion_pairing"] and 115 <= status["companion_pairing_remaining"] <= 120
  assert client.actions[-1] == ("pairing_mode", True)

  assert companions[0].authorize("/org/bluez/hci0/dev_phone")
  assert params.get("BluetoothCompanionDevices") == ["00:11:22:33:44:55"]
  assert ("property", "00:11:22:33:44:55", "Trusted", "b", True) in client.actions
  status = controller.status()
  assert status["companion_enabled"]
  assert status["companion_devices"] == ["00:11:22:33:44:55"]
  assert not status["companion_pairing"]
  assert client.actions[-1] == ("pairing_mode", False)

  controller.handle({"command": "set_companion", "enabled": False})
  assert companions[0].closed and not params.get_bool("BluetoothCompanionEnabled")

  controller.handle({"command": "forget", "address": client.device["address"]})
  assert params.get("BluetoothCompanionDevices") == []


def test_saved_companion_reenables_service_when_daemon_starts():
  params = FakeParams(
    BluetoothCompanionEnabled=False,
    BluetoothCompanionDevices='["00:11:22:33:44:55"]',
  )

  BluetoothController(params, lambda: FakeBlueZ(), FakeRadio())

  assert params.get_bool("BluetoothCompanionEnabled")


def test_known_companion_does_not_close_pairing_window_for_another_phone():
  address = "00:11:22:33:44:55"
  params = FakeParams(
    IsOffroad=True,
    BluetoothEnabled=True,
    BluetoothCompanionEnabled=True,
    BluetoothCompanionDevices=json.dumps([address]),
  )
  client = FakeBlueZ()
  companions = []
  controller = BluetoothController(
    params, lambda: client, FakeRadio(),
    companion_factory=lambda *args: companions.append(FakeCompanion(*args)) or companions[-1],
  )

  controller.handle({"command": "start_companion_pairing"})
  assert companions[0].authorize("/org/bluez/hci0/dev_phone")
  assert controller.status()["companion_pairing"]
  assert client.actions[-1] == ("pairing_mode", True)


@pytest.mark.parametrize("command_request,companion_enabled", [
  ({"command": "set_companion", "enabled": True}, False),
  ({"command": "start_companion_pairing"}, True),
  ({"command": "stop_companion_pairing"}, True),
])
def test_companion_commands_cannot_power_disabled_bluetooth(command_request, companion_enabled):
  params = FakeParams(IsOffroad=True, BluetoothEnabled=False, BluetoothCompanionEnabled=companion_enabled)
  client = FakeBlueZ()
  radio = FakeRadio()
  factory_calls = []

  def client_factory():
    factory_calls.append(True)
    return client

  controller = BluetoothController(params, client_factory, radio)

  with pytest.raises(RuntimeError, match="Enable Bluetooth"):
    controller.handle(command_request)

  assert factory_calls == []
  assert radio.starts == 0
  assert controller._bluez is None
  assert not client.powered


def test_companion_pairing_is_offroad_only_and_rejects_unbonded_phone():
  params = FakeParams(IsOffroad=False, BluetoothEnabled=True, BluetoothCompanionEnabled=True)
  client = FakeBlueZ()
  client.device["paired"] = False
  companions = []
  controller = BluetoothController(
    params, lambda: client, FakeRadio(),
    companion_factory=lambda *args: companions.append(FakeCompanion(*args)) or companions[-1],
  )

  with pytest.raises(RuntimeError, match="offroad"):
    controller.handle({"command": "start_companion_pairing"})
  controller.status()
  assert not companions[0].authorize("/org/bluez/hci0/dev_phone")


def test_companion_disable_does_not_fail_open_when_pairing_close_fails():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True, BluetoothCompanionEnabled=False)
  client = FakeBlueZ()
  companions = []
  controller = BluetoothController(
    params, lambda: client, FakeRadio(),
    companion_factory=lambda *args: companions.append(FakeCompanion(*args)) or companions[-1],
  )
  controller.handle({"command": "set_companion", "enabled": True})
  controller.handle({"command": "start_companion_pairing"})

  client.pairing_mode_error = RuntimeError("adapter busy")
  with pytest.raises(RuntimeError, match="adapter busy"):
    controller.handle({"command": "set_companion", "enabled": False})
  assert params.get_bool("BluetoothCompanionEnabled")
  assert controller._companion is companions[0] and not companions[0].closed
  assert controller.status()["companion_pairing"]

  client.pairing_mode_error = None
  controller.handle({"command": "set_companion", "enabled": False})
  assert not params.get_bool("BluetoothCompanionEnabled")
  assert companions[0].closed


def test_audio_queue_is_nonblocking_and_falls_back():
  params = FakeParams(BluetoothEnabled=True, BluetoothAudioAddress="00:11:22:33:44:55")
  process = FakeProcess()
  sink = BluetoothAudioSink(params, popen_factory=lambda *_args, **_kwargs: process, start_thread=False)
  sink._aplay = "/usr/bin/aplay"
  sink._thread = threading.Thread(target=sink._run, daemon=True)
  sink._thread.start()
  samples = np.array([-1.0, 0.0, 1.0], dtype=np.float32)
  deadline = time.monotonic() + 1.0
  while not sink._address and time.monotonic() < deadline:
    time.sleep(0.01)
  assert not sink.submit(samples)
  deadline = time.monotonic() + 1.0
  while not sink.healthy and time.monotonic() < deadline:
    time.sleep(0.01)
  assert sink.healthy
  assert len(process.stdin.getvalue()) == 12
  assert sink.submit(samples)
  process.stopped = True
  assert not sink.healthy
  sink.close()


def test_full_audio_queue_immediately_restores_local_output():
  params = FakeParams(BluetoothEnabled=True, BluetoothAudioAddress="00:11:22:33:44:55")
  process = FakeProcess()
  sink = BluetoothAudioSink(params, start_thread=False)
  sink._aplay = "/usr/bin/aplay"
  sink._address = "00:11:22:33:44:55"
  sink._process = process
  sink._healthy = True
  sink._last_write = time.monotonic()
  samples = np.zeros(3, dtype=np.float32)

  assert sink.submit(samples)
  assert sink.submit(samples)
  assert sink.submit(samples)
  assert not sink.submit(samples)
  assert not sink.healthy


def test_audio_address_decodes_device_params_bytes():
  params = FakeParams(BluetoothEnabled=True, BluetoothAudioAddress=b"00:11:22:33:44:55")
  sink = BluetoothAudioSink(params, start_thread=False)
  assert sink.desired_address() == "00:11:22:33:44:55"
