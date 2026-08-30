import threading
import time
import uuid

from typing import Any

from jeepney import DBusAddress, MatchRule, new_error, new_method_call, new_method_return
from jeepney.io.threading import DBusRouter, open_dbus_connection
from jeepney.low_level import HeaderFields, MessageType
from jeepney.wrappers import Properties

from openpilot.starpilot.system.bluetooth.protocol import device_capabilities, show_pairing_device


BLUEZ = "org.bluez"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"
AGENT_IFACE = "org.bluez.Agent1"
AGENT_PATH = "/link/firestar/starpilot/agent"


def unwrap_variant(value: Any) -> Any:
  if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
    return unwrap_variant(value[1])
  if isinstance(value, dict):
    return {key: unwrap_variant(item) for key, item in value.items()}
  if isinstance(value, list):
    return [unwrap_variant(item) for item in value]
  return value


class PairingAgent:
  def __init__(self):
    self._condition = threading.Condition()
    self._prompt: dict[str, Any] | None = None
    self._response: tuple[bool, str] | None = None

  @property
  def prompt(self) -> dict[str, Any] | None:
    with self._condition:
      return dict(self._prompt) if self._prompt is not None else None

  def clear(self) -> None:
    with self._condition:
      self._prompt = None
      self._response = None
      self._condition.notify_all()

  def display(self, kind: str, device_path: str, value: str) -> None:
    with self._condition:
      self._prompt = {"id": uuid.uuid4().hex, "kind": kind, "device_path": device_path, "value": value, "display_only": True}

  def request(self, kind: str, device_path: str, value: str = "", timeout: float = 60.0) -> tuple[bool, str]:
    prompt_id = uuid.uuid4().hex
    with self._condition:
      self._response = None
      self._prompt = {"id": prompt_id, "kind": kind, "device_path": device_path, "value": value, "display_only": False}
      deadline = time.monotonic() + timeout
      while self._response is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          self._prompt = None
          return False, ""
        self._condition.wait(remaining)
      response = self._response
      self._response = None
      self._prompt = None
      return response

  def respond(self, prompt_id: str, accepted: bool, value: str = "") -> bool:
    with self._condition:
      if self._prompt is None or self._prompt.get("id") != prompt_id or self._prompt.get("display_only"):
        return False
      self._response = accepted, value
      self._condition.notify_all()
      return True


class BlueZClient:
  def __init__(self):
    self.router = DBusRouter(open_dbus_connection(bus="SYSTEM"))
    self.agent = PairingAgent()
    self._agent_filter = self.router.filter(MatchRule(type="method_call", interface=AGENT_IFACE, path=AGENT_PATH), bufsize=20)
    self._agent_queue = self._agent_filter.__enter__()
    self._agent_thread = threading.Thread(target=self._agent_loop, daemon=True)
    self._agent_thread.start()
    self._register_agent()

  def close(self) -> None:
    try:
      self._call("/org/bluez", AGENT_MANAGER_IFACE, "UnregisterAgent", "o", (AGENT_PATH,))
    except Exception:
      pass
    self._agent_filter.__exit__(None, None, None)
    self.router.close()

  def _call(self, path: str, interface: str, member: str, signature: str | None = None, body: tuple = (), timeout: float = 15.0):
    address = DBusAddress(path, bus_name=BLUEZ, interface=interface)
    message = new_method_call(address, member, signature, body) if signature is not None else new_method_call(address, member)
    reply = self.router.send_and_get_reply(message, timeout=timeout)
    if reply.header.message_type == MessageType.error:
      error_name = reply.header.fields.get(HeaderFields.error_name, "org.bluez.Error.Failed")
      detail = reply.body[0] if reply.body else error_name
      raise RuntimeError(str(detail))
    return reply.body

  def _register_agent(self) -> None:
    self._call("/org/bluez", AGENT_MANAGER_IFACE, "RegisterAgent", "os", (AGENT_PATH, "KeyboardDisplay"))
    self._call("/org/bluez", AGENT_MANAGER_IFACE, "RequestDefaultAgent", "o", (AGENT_PATH,))

  def _agent_loop(self) -> None:
    while True:
      message = self._agent_queue.get()
      member = message.header.fields.get(HeaderFields.member, "")
      try:
        response_signature = None
        response_body: tuple = ()
        device_path = str(message.body[0]) if message.body else ""
        if member == "Release":
          self.agent.clear()
        elif member == "RequestPinCode":
          accepted, value = self.agent.request("pin", device_path)
          if not accepted:
            raise PermissionError
          response_signature, response_body = "s", (value,)
        elif member == "DisplayPinCode":
          self.agent.display("display_pin", device_path, str(message.body[1]))
        elif member == "RequestPasskey":
          accepted, value = self.agent.request("passkey", device_path)
          if not accepted:
            raise PermissionError
          response_signature, response_body = "u", (int(value),)
        elif member == "DisplayPasskey":
          self.agent.display("display_passkey", device_path, f"{int(message.body[1]):06d}")
        elif member == "RequestConfirmation":
          accepted, _ = self.agent.request("confirmation", device_path, f"{int(message.body[1]):06d}")
          if not accepted:
            raise PermissionError
        elif member in ("RequestAuthorization", "AuthorizeService"):
          accepted, _ = self.agent.request("authorization", device_path)
          if not accepted:
            raise PermissionError
        elif member == "Cancel":
          self.agent.clear()
        else:
          raise RuntimeError(f"Unsupported pairing request: {member}")
        self.router.send(new_method_return(message, response_signature, response_body))
      except PermissionError:
        self.router.send(new_error(message, "org.bluez.Error.Rejected", "s", ("Pairing rejected",)))
      except Exception as error:
        self.router.send(new_error(message, "org.bluez.Error.Canceled", "s", (str(error),)))

  def managed_objects(self) -> dict[str, dict[str, dict[str, Any]]]:
    body = self._call("/", OBJECT_MANAGER, "GetManagedObjects")
    return unwrap_variant(body[0]) if body else {}

  def adapter(self, objects: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    objects = self.managed_objects() if objects is None else objects
    for path, interfaces in objects.items():
      if ADAPTER_IFACE in interfaces:
        return path, interfaces[ADAPTER_IFACE]
    raise RuntimeError("Bluetooth adapter is not available")

  def devices(self, objects: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    objects = self.managed_objects() if objects is None else objects
    devices = []
    for path, interfaces in objects.items():
      if DEVICE_IFACE not in interfaces:
        continue
      props = interfaces[DEVICE_IFACE]
      uuids = [str(value).lower() for value in props.get("UUIDs", [])]
      audio, controller = device_capabilities(uuids, int(props.get("Class", 0)), str(props.get("Icon", "")))
      device = {
        "path": path,
        "address": str(props.get("Address", "")),
        "name": str(props.get("Alias") or props.get("Name") or props.get("Address") or "Unknown device"),
        "paired": bool(props.get("Paired", False)),
        "trusted": bool(props.get("Trusted", False)),
        "connected": bool(props.get("Connected", False)),
        "blocked": bool(props.get("Blocked", False)),
        "rssi": int(props["RSSI"]) if "RSSI" in props else None,
        "uuids": uuids,
        "audio": audio,
        "controller": controller,
      }
      if show_pairing_device(device["address"], device["name"], device["paired"], device["trusted"], device["connected"],
                             device["blocked"], audio, controller):
        devices.append(device)
    return sorted(devices, key=lambda device: (not device["connected"], not device["paired"], -(device["rssi"] or -127), device["name"].lower()))

  def status(self) -> dict[str, Any]:
    objects = self.managed_objects()
    _, adapter = self.adapter(objects)
    prompt = self.agent.prompt
    if prompt is not None:
      device_path = str(prompt.get("device_path", ""))
      interfaces = objects.get(device_path, {})
      props = interfaces.get(DEVICE_IFACE, {})
      if props:
        prompt["address"] = str(props.get("Address", ""))
        prompt["name"] = str(props.get("Alias") or props.get("Name") or props.get("Address") or "Bluetooth device")
    return {
      "powered": bool(adapter.get("Powered", False)),
      "discovering": bool(adapter.get("Discovering", False)),
      "devices": self.devices(objects),
      "prompt": prompt,
    }

  def set_adapter_property(self, name: str, signature: str, value: Any) -> None:
    path, _ = self.adapter()
    address = DBusAddress(path, bus_name=BLUEZ, interface=ADAPTER_IFACE)
    reply = self.router.send_and_get_reply(Properties(address).set(name, signature, value), timeout=10.0)
    if reply.header.message_type == MessageType.error:
      raise RuntimeError(str(reply.body[0] if reply.body else f"Unable to set adapter {name}"))

  def set_powered(self, powered: bool) -> None:
    self.set_adapter_property("Powered", "b", powered)

  def set_pairing_mode(self, enabled: bool) -> None:
    # Pairing windows are timed by bluetooth_managerd instead of BlueZ so all UIs
    # report the same deadline.
    if enabled:
      self.set_adapter_property("PairableTimeout", "u", 0)
      self.set_adapter_property("DiscoverableTimeout", "u", 0)
      self.set_adapter_property("Pairable", "b", True)
      try:
        self.set_adapter_property("Discoverable", "b", True)
      except Exception:
        self.set_adapter_property("Pairable", "b", False)
        raise
    else:
      error: Exception | None = None
      for name in ("Discoverable", "Pairable"):
        try:
          self.set_adapter_property(name, "b", False)
        except Exception as current_error:
          error = error or current_error
      if error is not None:
        raise error

  def start_discovery(self) -> None:
    path, _ = self.adapter()
    self._call(path, ADAPTER_IFACE, "StartDiscovery")

  def stop_discovery(self) -> None:
    path, props = self.adapter()
    if props.get("Discovering", False):
      self._call(path, ADAPTER_IFACE, "StopDiscovery")

  def device_for_address(self, address: str) -> dict[str, Any]:
    normalized = address.upper()
    for device in self.devices():
      if device["address"].upper() == normalized:
        return device
    raise RuntimeError(f"Bluetooth device {address} was not found")

  def paired_device_for_path(self, path: str) -> dict[str, Any] | None:
    interfaces = self.managed_objects().get(path, {})
    props = interfaces.get(DEVICE_IFACE)
    if props is None or not props.get("Paired", False):
      return None
    return {
      "path": path,
      "address": str(props.get("Address", "")),
      "name": str(props.get("Alias") or props.get("Name") or props.get("Address") or "Bluetooth device"),
      "paired": True,
      "trusted": bool(props.get("Trusted", False)),
      "connected": bool(props.get("Connected", False)),
    }

  def set_device_property(self, address: str, name: str, signature: str, value: Any) -> None:
    device = self.device_for_address(address)
    dbus_address = DBusAddress(device["path"], bus_name=BLUEZ, interface=DEVICE_IFACE)
    reply = self.router.send_and_get_reply(Properties(dbus_address).set(name, signature, value), timeout=10.0)
    if reply.header.message_type == MessageType.error:
      raise RuntimeError(str(reply.body[0] if reply.body else f"Unable to set {name}"))

  def pair(self, address: str) -> None:
    device = self.device_for_address(address)
    self._call(device["path"], DEVICE_IFACE, "Pair", timeout=90.0)
    self.set_device_property(address, "Trusted", "b", True)
    self.agent.clear()

  def connect(self, address: str) -> None:
    device = self.device_for_address(address)
    self._call(device["path"], DEVICE_IFACE, "Connect", timeout=30.0)

  def disconnect(self, address: str) -> None:
    device = self.device_for_address(address)
    self._call(device["path"], DEVICE_IFACE, "Disconnect", timeout=15.0)

  def remove(self, address: str) -> None:
    adapter_path, _ = self.adapter()
    device = self.device_for_address(address)
    self._call(adapter_path, ADAPTER_IFACE, "RemoveDevice", "o", (device["path"],))
