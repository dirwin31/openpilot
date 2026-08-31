import io
import json
import queue
import threading
import time

import numpy as np
import pytest

from jeepney import DBusAddress, new_method_call
from jeepney.low_level import HeaderFields, MessageType

from openpilot.starpilot.system.bluetooth.audio import BluetoothAudioSink
from openpilot.starpilot.system.bluetooth.bluez import AdapterUnavailableError, BlueZClient, BlueZError, PairingAgent
from openpilot.starpilot.system.bluetooth.companion import (
  ADVERTISEMENT_IFACE, COMPANION_ADVERTISEMENT_PATH, COMPANION_APP_PATH, COMPANION_COMMAND_PATH, COMPANION_PROTOCOL_VERSION,
  COMPANION_RESPONSE_PATH, COMPANION_SERVICE_PATH, COMPANION_SERVICE_UUID, COMPANION_STATUS_PATH, GATT_CHARACTERISTIC_IFACE,
  OBJECT_MANAGER_IFACE, PROPERTIES_IFACE, CompanionGattApplication, CompanionProtocol,
)
from openpilot.starpilot.system.bluetooth.daemon import BluetoothController
from openpilot.starpilot.system.bluetooth.protocol import (A2DP_SINK_UUID, HID_UUID, BluetoothClient, BluetoothDevice, BluetoothStatus,
                                                           device_capabilities, show_pairing_device)
from openpilot.system import hardware
from openpilot.system.ui.lib.bluetooth_manager import BluetoothManager, companion_setup_visible


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

  def respond(self, prompt_id, accepted, value):
    self.responses.append((prompt_id, accepted, value))
    return prompt_id == "prompt"


class FakeBlueZ:
  def __init__(self):
    self.agent = FakeAgent()
    self.powered = False
    self.discovering = False
    self.closed = False
    self.actions = []
    self.pairing_mode_error = None
    self.pair_error = None
    self.router = object()
    self.device = {
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

  def status(self):
    return {"powered": self.powered, "discovering": self.discovering, "devices": [dict(self.device)], "prompt": None}

  def start_discovery(self):
    self.discovering = True

  def stop_discovery(self):
    self.discovering = False

  def device_for_address(self, _address):
    return dict(self.device)

  def pair(self, address):
    self.actions.append(("pair", address))
    if self.pair_error is not None:
      raise self.pair_error

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
    self.rearms = 0

  def rearm_advertisement(self):
    self.rearms += 1

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


def test_companion_rearm_serializes_with_close():
  app = object.__new__(CompanionGattApplication)
  app._close_lock = threading.Lock()
  app._closed = False
  app._registered = True
  app._adapter_path = "/org/bluez/hci0"
  unregister_started = threading.Event()
  allow_unregister = threading.Event()

  def bluez_call(_path, _interface, member, *_args):
    if member == "UnregisterAdvertisement":
      unregister_started.set()
      allow_unregister.wait(1.0)

  app._bluez_call = bluez_call
  worker = threading.Thread(target=app.rearm_advertisement, daemon=True)
  worker.start()
  assert unregister_started.wait(1.0)

  acquired = app._close_lock.acquire(timeout=0.05)
  if acquired:
    app._close_lock.release()
  allow_unregister.set()
  worker.join(timeout=1.0)

  assert not acquired
  assert not worker.is_alive()


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
  with pytest.raises(RuntimeError, match="Enable Bluetooth"):
    client.start_scan()
  client.set_power(True)
  assert client.status().enabled


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


def test_pairing_agent_auto_accept_handler():
  agent = PairingAgent()
  assert not agent.should_auto_accept("/device")  # no handler installed
  agent.auto_accept_handler = lambda path: path == "/phone"
  assert agent.should_auto_accept("/phone")
  assert not agent.should_auto_accept("/speaker")
  agent.auto_accept_handler = lambda path: (_ for _ in ()).throw(RuntimeError("boom"))
  assert not agent.should_auto_accept("/phone")


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
  assert clients[0].actions == [("pairing_mode", False)]
  params.values["IsOffroad"] = False
  with pytest.raises(RuntimeError, match="offroad"):
    controller.handle({"command": "start_scan"})
  controller.handle({"command": "connect", "address": "00:11:22:33:44:55"})
  assert clients[0].actions[-1] == ("connect", "00:11:22:33:44:55")
  params.values["IsOffroad"] = True
  controller.handle({"command": "set_power", "enabled": False})
  assert not params.get_bool("BluetoothEnabled") and radio.stops == 1 and clients[0].closed


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


def test_companion_bond_auto_accepts_and_registers_without_prompt():
  params = TypedJsonFakeParams(IsOffroad=True, BluetoothEnabled=True, BluetoothCompanionEnabled=False)
  client = FakeBlueZ()
  client.device.update({"name": "Phone", "audio": False, "trusted": False})
  controller = BluetoothController(params, lambda: client, FakeRadio(),
                                   companion_factory=lambda *args: FakeCompanion(*args))
  controller.handle({"command": "start_companion_pairing"})

  # The agent handler the controller installed accepts the phone bond silently.
  assert client.agent.auto_accept_handler("/org/bluez/hci0/dev_phone")

  # A device-initiated pair still uses the normal prompt, never auto-accept.
  controller._pairing_address = "AA:BB:CC:DD:EE:FF"
  assert not client.agent.auto_accept_handler("/org/bluez/hci0/dev_other")
  controller._pairing_address = ""

  controller._maintain_pending_companions()
  assert params.get("BluetoothCompanionDevices") == ["00:11:22:33:44:55"]
  assert ("property", "00:11:22:33:44:55", "Trusted", "b", True) in client.actions
  status = controller.status()
  assert status["companion_devices"] == ["00:11:22:33:44:55"]
  assert not status["companion_pairing"]
  assert client.actions[-1] == ("pairing_mode", False)


def test_phone_pair_forget_repair_and_reconnect_workflow():
  class WorkflowBlueZ(FakeBlueZ):
    def __init__(self):
      super().__init__()
      self.present = False
      self.device.update({"name": "iPhone", "paired": False, "trusted": False, "connected": False,
                          "audio": False, "controller": False})

    def status(self):
      devices = [dict(self.device)] if self.present else []
      return {"powered": self.powered, "discovering": self.discovering, "devices": devices, "prompt": None}

    def device_for_address(self, address):
      if not self.present or address.upper() != self.device["address"].upper():
        raise RuntimeError(f"Bluetooth device {address} was not found")
      return dict(self.device)

    def paired_device_for_path(self, path):
      if not self.present:
        return None
      return super().paired_device_for_path(path)

    def remove(self, address):
      self.device_for_address(address)
      self.actions.append(("remove", address))
      self.present = False
      self.device.update({"paired": False, "trusted": False, "connected": False})

    def bond_phone(self, connected=True):
      self.present = True
      self.device.update({"paired": True, "trusted": False, "connected": connected})

  params = TypedJsonFakeParams(
    IsOffroad=True,
    BluetoothEnabled=True,
    BluetoothCompanionEnabled=False,
    BluetoothCompanionDevices=[],
  )
  client = WorkflowBlueZ()
  companions = []
  controller = BluetoothController(
    params, lambda: client, FakeRadio(),
    companion_factory=lambda *args: companions.append(FakeCompanion(*args)) or companions[-1],
  )

  initial = BluetoothStatus.from_dict(controller.status())
  assert companion_setup_visible(initial)
  assert initial.companion_devices == () and initial.devices == ()

  controller.handle({"command": "start_companion_pairing"})
  pairing = BluetoothStatus.from_dict(controller.status())
  assert pairing.companion_pairing and companion_setup_visible(pairing)

  client.bond_phone()
  assert client.agent.auto_accept_handler("/org/bluez/hci0/dev_phone")
  controller._maintain_pending_companions()
  paired = BluetoothStatus.from_dict(controller.status())
  assert paired.companion_devices == (client.device["address"],)
  assert paired.companion_connected and not paired.companion_pairing
  assert not companion_setup_visible(paired)

  client.device["connected"] = False
  disconnected = BluetoothStatus.from_dict(controller.status())
  assert not disconnected.companion_connected
  assert not companion_setup_visible(disconnected)
  assert controller._companion is companions[0] and not companions[0].closed
  assert client.agent.auto_accept_handler("/org/bluez/hci0/dev_phone")

  # The phone is the BLE central, so its reconnect changes BlueZ state while
  # the comma keeps the advertisement/service alive.
  client.device["connected"] = True
  assert BluetoothStatus.from_dict(controller.status()).companion_connected

  controller.handle({"command": "forget", "address": client.device["address"]})
  forgotten = BluetoothStatus.from_dict(controller.status())
  assert params.get("BluetoothCompanionDevices") == []
  assert not params.get_bool("BluetoothCompanionEnabled")
  assert forgotten.companion_devices == () and forgotten.devices == ()
  assert not forgotten.companion_connected and companion_setup_visible(forgotten)
  assert companions[0].closed and controller._companion is None
  assert not companions[0].authorize("/org/bluez/hci0/dev_phone")

  controller.handle({"command": "start_companion_pairing"})
  assert len(companions) == 2 and not companions[1].closed
  client.bond_phone()
  assert client.agent.auto_accept_handler("/org/bluez/hci0/dev_phone")
  controller._maintain_pending_companions()
  repaired = BluetoothStatus.from_dict(controller.status())
  assert repaired.companion_devices == (client.device["address"],)
  assert repaired.companion_connected and not companion_setup_visible(repaired)


def test_phone_is_not_saved_until_bluez_trust_succeeds():
  class RetryTrustBlueZ(FakeBlueZ):
    fail_trust = True

    def set_device_property(self, address, name, signature, value):
      if name == "Trusted" and self.fail_trust:
        raise RuntimeError("unable to trust")
      super().set_device_property(address, name, signature, value)

  params = TypedJsonFakeParams(
    IsOffroad=True,
    BluetoothEnabled=True,
    BluetoothCompanionEnabled=False,
    BluetoothCompanionDevices=[],
  )
  client = RetryTrustBlueZ()
  client.device.update({"name": "iPhone", "audio": False, "trusted": False})
  controller = BluetoothController(params, lambda: client, FakeRadio(), companion_factory=lambda *args: FakeCompanion(*args))
  controller.handle({"command": "start_companion_pairing"})
  assert client.agent.auto_accept_handler("/org/bluez/hci0/dev_phone")

  controller._maintain_pending_companions()
  assert params.get("BluetoothCompanionDevices") == []
  assert controller.status()["companion_pairing"]
  assert "/org/bluez/hci0/dev_phone" in controller._pending_companion_paths

  client.fail_trust = False
  controller._maintain_pending_companions()
  assert params.get("BluetoothCompanionDevices") == [client.device["address"]]
  assert not controller.status()["companion_pairing"]


def test_failed_bluez_forget_keeps_phone_authorization_and_service_state():
  class FailingRemoveBlueZ(FakeBlueZ):
    def remove(self, _address):
      raise RuntimeError("remove failed")

  address = "00:11:22:33:44:55"
  params = TypedJsonFakeParams(
    IsOffroad=True,
    BluetoothEnabled=True,
    BluetoothCompanionEnabled=True,
    BluetoothCompanionDevices=[address],
  )
  client = FailingRemoveBlueZ()
  companions = []
  controller = BluetoothController(
    params, lambda: client, FakeRadio(),
    companion_factory=lambda *args: companions.append(FakeCompanion(*args)) or companions[-1],
  )
  controller.status()

  with pytest.raises(RuntimeError, match="remove failed"):
    controller.handle({"command": "forget", "address": address})

  assert params.get("BluetoothCompanionDevices") == [address]
  assert params.get_bool("BluetoothCompanionEnabled")
  assert controller._companion is companions[0] and not companions[0].closed


def test_saved_phone_service_returns_after_bluetooth_power_cycle():
  address = "00:11:22:33:44:55"
  params = TypedJsonFakeParams(
    IsOffroad=True,
    BluetoothEnabled=True,
    BluetoothCompanionEnabled=True,
    BluetoothCompanionDevices=[address],
  )
  clients = []
  companions = []

  def client_factory():
    client = FakeBlueZ()
    client.device.update({"name": "iPhone", "audio": False, "controller": False, "connected": False})
    clients.append(client)
    return client

  controller = BluetoothController(
    params, client_factory, FakeRadio(),
    companion_factory=lambda *args: companions.append(FakeCompanion(*args)) or companions[-1],
  )
  before = BluetoothStatus.from_dict(controller.status())
  assert not companion_setup_visible(before)
  assert companions[0].started == "/org/bluez/hci0"

  controller.handle({"command": "set_power", "enabled": False})
  assert companions[0].closed
  assert params.get("BluetoothCompanionDevices") == [address]
  assert params.get_bool("BluetoothCompanionEnabled")

  controller.handle({"command": "set_power", "enabled": True})
  after = BluetoothStatus.from_dict(controller.status())
  assert len(clients) == 2 and len(companions) == 2
  assert companions[1].started == "/org/bluez/hci0" and not companions[1].closed
  assert after.companion_devices == (address,)
  assert not companion_setup_visible(after)


def test_reconnect_maintenance_connects_audio_and_controllers_but_not_phone_centrals():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  client = FakeBlueZ()
  controller = BluetoothController(params, lambda: client, FakeRadio())
  status = controller.status()

  controller._maintain_reconnects(status, time.monotonic())
  assert client.actions[-1] == ("connect", client.device["address"])

  client.actions.clear()
  client.device.update({"connected": False, "audio": False, "controller": False})
  phone_status = controller.status()
  controller._maintain_reconnects(phone_status, controller._last_reconnect + 15.0)
  assert not any(action[0] == "connect" for action in client.actions)


def test_connect_to_phone_reports_actionable_hint_and_rearms_companion():
  address = "00:11:22:33:44:55"
  params = TypedJsonFakeParams(
    IsOffroad=True,
    BluetoothEnabled=True,
    BluetoothCompanionEnabled=True,
    BluetoothCompanionDevices=[address],
  )

  class ProfileUnavailableBlueZ(FakeBlueZ):
    def __init__(self):
      super().__init__()
      self.device.update({"name": "iPhone", "audio": False, "controller": False, "trusted": False})

    def connect(self, address):
      self.actions.append(("connect", address))
      raise RuntimeError("br-connection-profile-unavailable")

  client = ProfileUnavailableBlueZ()
  companions = []
  controller = BluetoothController(params, lambda: client, FakeRadio(),
                                   companion_factory=lambda *args: companions.append(FakeCompanion(*args)) or companions[-1])

  with pytest.raises(RuntimeError, match="connect from the phone"):
    controller.handle({"command": "connect", "address": address})

  # The bond is kept trusted and the service stays advertised so the phone, which
  # is the BLE central, can link back on its own.
  assert ("property", address, "Trusted", "b", True) in client.actions
  assert companions and companions[-1].started == "/org/bluez/hci0" and not companions[-1].closed


def test_connect_failure_on_audio_device_keeps_the_bluez_error():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)

  class ProfileUnavailableBlueZ(FakeBlueZ):
    def connect(self, address):
      self.actions.append(("connect", address))
      raise RuntimeError("br-connection-profile-unavailable")

  client = ProfileUnavailableBlueZ()
  controller = BluetoothController(params, lambda: client, FakeRadio())

  with pytest.raises(RuntimeError, match="br-connection-profile-unavailable"):
    controller.handle({"command": "connect", "address": client.device["address"]})


def test_connect_failure_on_unregistered_ble_device_keeps_the_bluez_error():
  params = TypedJsonFakeParams(
    IsOffroad=True,
    BluetoothEnabled=True,
    BluetoothCompanionDevices=[],
  )

  class ProfileUnavailableBlueZ(FakeBlueZ):
    def __init__(self):
      super().__init__()
      self.device.update({"name": "BLE Sensor", "audio": False, "controller": False})

    def connect(self, address):
      self.actions.append(("connect", address))
      raise RuntimeError("br-connection-profile-unavailable")

  client = ProfileUnavailableBlueZ()
  controller = BluetoothController(params, lambda: client, FakeRadio())

  with pytest.raises(RuntimeError, match="br-connection-profile-unavailable"):
    controller.handle({"command": "connect", "address": client.device["address"]})


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
  params = FakeParams(
    IsOffroad=False,
    BluetoothEnabled=True,
    BluetoothCompanionEnabled=True,
    BluetoothCompanionDevices=json.dumps(["00:11:22:33:44:55"]),
  )
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


def test_startup_closes_a_pairing_window_leaked_by_a_previous_daemon():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  client = FakeBlueZ()
  controller = BluetoothController(params, lambda: client, FakeRadio())

  controller._client()

  assert ("pairing_mode", False) in client.actions
  assert controller.status()["companion_pairing"] is False


def test_companion_start_preserves_an_active_pairing_window():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True, BluetoothCompanionDevices=[])
  client = FakeBlueZ()
  controller = BluetoothController(params, lambda: client, FakeRadio(), companion_factory=FakeCompanion)
  controller._bluez = client
  controller._companion_pairing_deadline = time.monotonic() + 30.0

  controller._start_companion(client)

  assert not any(action == ("pairing_mode", False) for action in client.actions)
  assert controller.status()["companion_pairing"] is True


def test_pairing_error_clears_on_the_next_user_action():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  client = FakeBlueZ()
  client.pair_error = RuntimeError("Authentication Failed")
  controller = BluetoothController(params, lambda: client, FakeRadio())

  controller.handle({"command": "pair", "address": client.device["address"]})
  for _ in range(200):
    if not controller._pairing_address:
      break
    time.sleep(0.01)
  assert controller.status()["error"] == "Authentication Failed"

  controller.handle({"command": "start_scan"})
  assert controller.status()["error"] == ""


def test_concurrent_pair_requests_claim_the_slot_exactly_once():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  client = FakeBlueZ()
  release = threading.Event()
  started = threading.Event()

  def blocking_pair(address):
    client.actions.append(("pair", address))
    started.set()
    release.wait(5.0)

  client.pair = blocking_pair
  controller = BluetoothController(params, lambda: client, FakeRadio())

  controller.handle({"command": "pair", "address": client.device["address"]})
  assert started.wait(5.0)
  with pytest.raises(RuntimeError, match="already pairing"):
    controller.handle({"command": "pair", "address": "AA:BB:CC:DD:EE:FF"})

  release.set()
  for _ in range(200):
    if not controller._pairing_address:
      break
    time.sleep(0.01)
  assert [action for action in client.actions if action[0] == "pair"] == [("pair", client.device["address"])]


def test_pairing_reports_success_when_only_the_audio_selection_fails():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  client = FakeBlueZ()
  client.device_for_address = lambda _address: (_ for _ in ()).throw(RuntimeError("device vanished"))
  controller = BluetoothController(params, lambda: client, FakeRadio())

  controller.handle({"command": "pair", "address": client.device["address"]})
  for _ in range(200):
    if not controller._pairing_address:
      break
    time.sleep(0.01)

  assert controller._pairing_error == ""
  assert ("pair", client.device["address"]) in client.actions


def test_bluez_pair_recovers_a_bond_left_untrusted_by_an_earlier_attempt():
  client = object.__new__(BlueZClient)
  client.agent = PairingAgent()
  properties = []
  client.device_for_address = lambda _address: {"path": "/phone", "paired": True}
  client.set_device_property = lambda *args: properties.append(args)

  def call(*_args, **_kwargs):
    raise BlueZError("org.bluez.Error.AlreadyExists", "Already Exists")

  client._call = call
  client.pair("00:11:22:33:44:55")

  assert properties == [("00:11:22:33:44:55", "Trusted", "b", True)]


def test_bluez_pair_preserves_a_genuine_failure():
  client = object.__new__(BlueZClient)
  client.agent = PairingAgent()
  client.device_for_address = lambda _address: {"path": "/phone", "paired": False}
  client.set_device_property = lambda *_args: pytest.fail("must not trust an unpaired device")

  def call(*_args, **_kwargs):
    raise BlueZError("org.bluez.Error.AuthenticationFailed", "Authentication Failed")

  client._call = call
  with pytest.raises(BlueZError, match="Authentication Failed"):
    client.pair("00:11:22:33:44:55")


def test_bluez_pair_rejects_already_exists_without_a_real_bond():
  client = object.__new__(BlueZClient)
  client.agent = PairingAgent()
  client.device_for_address = lambda _address: {"path": "/phone", "paired": False}
  client.set_device_property = lambda *_args: pytest.fail("must not trust an unpaired device")

  def call(*_args, **_kwargs):
    raise BlueZError("org.bluez.Error.AlreadyExists", "Already Exists")

  client._call = call
  with pytest.raises(BlueZError, match="Already Exists"):
    client.pair("00:11:22:33:44:55")


@pytest.mark.parametrize(("error_name", "is_bluez_error"), [
  ("org.bluez.Error.NotReady", True),
  ("org.freedesktop.DBus.Error.ServiceUnknown", False),
])
def test_bluez_call_distinguishes_bluez_and_bus_errors(error_name, is_bluez_error):
  class Reply:
    body = ("service unavailable",)
    header = type("Header", (), {
      "message_type": MessageType.error,
      "fields": {HeaderFields.error_name: error_name},
    })()

  class Router:
    def send_and_get_reply(self, *_args, **_kwargs):
      return Reply()

  client = object.__new__(BlueZClient)
  client.router = Router()
  with pytest.raises(RuntimeError, match="service unavailable") as exc_info:
    client._call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")

  assert isinstance(exc_info.value, BlueZError) is is_bluez_error


def test_reconnect_maintenance_attempts_one_device_per_pass():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  client = FakeBlueZ()
  controller = BluetoothController(params, lambda: client, FakeRadio())
  devices = [
    {**client.device, "address": "00:11:22:33:44:55", "connected": False},
    {**client.device, "address": "AA:BB:CC:DD:EE:FF", "connected": False},
    {**client.device, "address": "10:20:30:40:50:60", "connected": False},
  ]
  client.connect = lambda address: client.actions.append(("connect", address))

  status = {"devices": devices, "selected_audio": ""}
  controller._maintain_reconnects(status, time.monotonic())
  assert [action for action in client.actions if action[0] == "connect"] == [("connect", "00:11:22:33:44:55")]

  attempts = []
  for index in range(4):
    controller._maintain_reconnects(status, controller._last_reconnect + 15.0 * (index + 1))
    attempts.append(client.actions[-1][1])
  assert attempts == ["AA:BB:CC:DD:EE:FF", "10:20:30:40:50:60", "00:11:22:33:44:55", "AA:BB:CC:DD:EE:FF"]


def test_status_keeps_the_client_alive_when_bluez_reports_an_error():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  client = FakeBlueZ()
  client.status = lambda: (_ for _ in ()).throw(BlueZError("org.bluez.Error.NotReady", "Resource Not Ready"))
  controller = BluetoothController(params, lambda: client, FakeRadio())
  controller._client()

  result = controller.status()

  assert result["error"] == "Resource Not Ready"
  assert controller._bluez is client and not client.closed


def test_status_retries_companion_registration_when_adapter_appears():
  class RecoveringAdapterBlueZ(FakeBlueZ):
    def __init__(self):
      super().__init__()
      self.adapter_calls = 0

    def adapter(self):
      self.adapter_calls += 1
      if self.adapter_calls == 1:
        raise AdapterUnavailableError("Bluetooth adapter is not available")
      return super().adapter()

  params = FakeParams(IsOffroad=True, BluetoothEnabled=True, BluetoothCompanionEnabled=True,
                      BluetoothCompanionDevices=["00:11:22:33:44:55"])
  clients = [RecoveringAdapterBlueZ(), FakeBlueZ()]
  companions = []
  controller = BluetoothController(
    params, lambda: clients.pop(0), FakeRadio(),
    companion_factory=lambda *args: companions.append(FakeCompanion(*args)) or companions[-1],
  )

  assert controller.status()["error"] == "Bluetooth adapter is not available"
  assert companions[0].closed and controller._bluez is None

  assert controller.status()["error"] == ""
  assert controller._companion is companions[1]
  assert companions[1].started == "/org/bluez/hci0"


def test_status_resets_the_client_when_the_bus_itself_fails():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  client = FakeBlueZ()
  client.status = lambda: (_ for _ in ()).throw(ConnectionResetError("bus went away"))
  controller = BluetoothController(params, lambda: client, FakeRadio())
  controller._client()

  assert controller.status()["error"] == "bus went away"
  assert controller._bluez is None and client.closed


def test_companion_advertisement_rearms_when_a_saved_phone_disconnects():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True, BluetoothCompanionEnabled=True,
                      BluetoothCompanionDevices=["00:11:22:33:44:55"])
  client = FakeBlueZ()
  controller = BluetoothController(params, lambda: client, FakeRadio(), companion_factory=FakeCompanion)
  controller._client()
  companion = controller._companion

  connected = {"devices": [{"address": "00:11:22:33:44:55", "connected": True}]}
  disconnected = {"devices": [{"address": "00:11:22:33:44:55", "connected": False}]}
  controller._maintain_companion_advertisement(connected)
  assert companion.rearms == 0

  controller._maintain_companion_advertisement(disconnected)
  assert companion.rearms == 1

  controller._maintain_companion_advertisement(disconnected)
  assert companion.rearms == 1


def test_bluetooth_connected_param_tracks_transitions_only():
  params = FakeParams(IsOffroad=True, BluetoothEnabled=True)
  controller = BluetoothController(params, lambda: FakeBlueZ(), FakeRadio())

  controller._publish_connected(True)
  assert params.get_bool("BluetoothConnected") is True

  params.values["BluetoothConnected"] = "untouched"
  controller._publish_connected(True)
  assert params.values["BluetoothConnected"] == "untouched"

  controller._publish_connected(False)
  assert params.get_bool("BluetoothConnected") is False
