"""The few BlueZ calls phone projection needs: paired phones, and a server profile for Android Auto."""
from __future__ import annotations

import socket
import threading
from collections.abc import Callable

BLUEZ = "org.bluez"
DEVICE_IFACE = "org.bluez.Device1"
ADAPTER_IFACE = "org.bluez.Adapter1"
PROFILE_MANAGER_IFACE = "org.bluez.ProfileManager1"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
IAP2_UUID = "00000000-deca-fade-deca-deafdecacafe"
AA_WIRELESS_UUID = "4de17a00-52cb-11e6-bdf4-0800200c9a66"
HFP_AG_UUID = "0000111f-0000-1000-8000-00805f9b34fb"
HSP_AG_UUID = "00001112-0000-1000-8000-00805f9b34fb"
AA_PROFILE_PATH = "/link/firestar/starpilot/projection/android_auto"
AA_RFCOMM_CHANNEL = 8


def _unwrap(value):
  if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
    return _unwrap(value[1])
  if isinstance(value, dict):
    return {k: _unwrap(v) for k, v in value.items()}
  if isinstance(value, list):
    return [_unwrap(v) for v in value]
  return value


def describe(path: str, props: dict) -> dict:
  return {"path": path, "address": str(props.get("Address", "")).upper(),
          "name": str(props.get("Alias") or props.get("Name") or props.get("Address", "")),
          "paired": bool(props.get("Paired")), "connected": bool(props.get("Connected")),
          "uuids": [str(u).lower() for u in props.get("UUIDs", [])]}


def pick_iphone(devices: list[dict], preferred: str = "") -> dict | None:
  """The chosen iPhone, else a connected paired iPhone, else any paired iPhone."""
  phones = [d for d in devices if d["paired"] and IAP2_UUID in d["uuids"]]
  if preferred:
    phones = [d for d in phones if d["address"] == preferred.upper()]
  phones.sort(key=lambda d: not d["connected"])
  return phones[0] if phones else None


def android_candidates(devices: list[dict], preferred: str = "") -> list[dict]:
  """Paired phones that are not iPhones; Android Auto starts from the phone once it sees our service."""
  phones = [d for d in devices if d["paired"] and IAP2_UUID not in d["uuids"] and
            (HFP_AG_UUID in d["uuids"] or HSP_AG_UUID in d["uuids"])]
  if preferred:
    phones = [d for d in phones if d["address"] == preferred.upper()]
  phones.sort(key=lambda d: not d["connected"])
  return phones


class Bluez:
  def __init__(self):
    from jeepney.io.threading import DBusRouter, open_dbus_connection
    self.router = DBusRouter(open_dbus_connection(bus="SYSTEM", enable_fds=True))
    self.lock = threading.RLock()
    self.profile_filter = None
    self.adapter = ""
    self.on_android_connection: Callable[[str, socket.socket], None] | None = None

  def _call(self, path: str, interface: str, member: str, signature: str | None = None, body: tuple = (), timeout: float = 10.0):
    from jeepney import DBusAddress, new_method_call
    from jeepney.low_level import HeaderFields, MessageType
    address = DBusAddress(path, bus_name=BLUEZ, interface=interface)
    message = new_method_call(address, member, signature, body) if signature else new_method_call(address, member)
    with self.lock:
      reply = self.router.send_and_get_reply(message, timeout=timeout)
    if reply.header.message_type == MessageType.error:
      raise RuntimeError(f"{reply.header.fields.get(HeaderFields.error_name, 'org.bluez.Error')}: {reply.body[0] if reply.body else ''}")
    return reply.body

  def devices(self) -> list[dict]:
    objects = _unwrap(self._call("/", OBJECT_MANAGER, "GetManagedObjects")[0])
    return [describe(path, ifaces[DEVICE_IFACE]) for path, ifaces in objects.items() if DEVICE_IFACE in ifaces]

  def adapter_address(self) -> str:
    """The comma's Bluetooth address; looked up once, it does not change while projectiond runs."""
    if self.adapter:
      return self.adapter
    objects = _unwrap(self._call("/", OBJECT_MANAGER, "GetManagedObjects")[0])
    for ifaces in objects.values():
      if ADAPTER_IFACE in ifaces and (address := str(ifaces[ADAPTER_IFACE].get("Address", "")).upper()):
        self.adapter = address
        return address
    raise RuntimeError("Bluetooth adapter is not available")

  def connect(self, path: str) -> None:
    """Bring up the phone's Bluetooth link so it sees our Android Auto service."""
    self._call(path, DEVICE_IFACE, "Connect", timeout=20.0)

  def register_android_auto(self, on_connection: Callable[[str, socket.socket], None]) -> None:
    """Publish the Android Auto wireless service so a paired phone can open its setup channel to us."""
    from jeepney import MatchRule
    self.on_android_connection = on_connection
    if self.profile_filter is None:
      self.profile_filter = self.router.filter(MatchRule(type="method_call", path=AA_PROFILE_PATH), bufsize=8)
      self.profile_queue = self.profile_filter.__enter__()
      threading.Thread(target=self._profile_loop, name="projection-aa-profile", daemon=True).start()
    options = {"Name": ("s", "Android Auto Wireless"), "Role": ("s", "server"), "Channel": ("q", AA_RFCOMM_CHANNEL),
               "RequireAuthentication": ("b", True), "RequireAuthorization": ("b", False), "AutoConnect": ("b", False)}
    try:
      self._call("/org/bluez", PROFILE_MANAGER_IFACE, "RegisterProfile", "osa{sv}", (AA_PROFILE_PATH, AA_WIRELESS_UUID, options))
    except RuntimeError as error:
      if "AlreadyExists" not in str(error):
        raise

  def unregister_android_auto(self) -> None:
    try:
      self._call("/org/bluez", PROFILE_MANAGER_IFACE, "UnregisterProfile", "o", (AA_PROFILE_PATH,))
    except RuntimeError:
      pass

  def _profile_loop(self) -> None:
    from jeepney import new_error, new_method_return
    from jeepney.low_level import HeaderFields
    while True:
      message = self.profile_queue.get()
      if message is None:
        return
      member = message.header.fields.get(HeaderFields.member, "")
      try:
        if member == "NewConnection":
          device_path, descriptor = str(message.body[0]), message.body[1]
          sock = descriptor.to_socket()
          address = device_path.rsplit("/", 1)[-1].replace("dev_", "").replace("_", ":").upper()
          if self.on_android_connection is not None:
            threading.Thread(target=self.on_android_connection, args=(address, sock), name="projection-aa-wireless",
                             daemon=True).start()
          else:
            sock.close()
        self.router.send(new_method_return(message))
      except Exception as error:
        try:
          self.router.send(new_error(message, "org.bluez.Error.Rejected", "s", (str(error),)))
        except Exception:
          pass

  def close(self) -> None:
    self.unregister_android_auto()
    if self.profile_filter is not None:
      try:
        self.profile_queue.put_nowait(None)
      except Exception:
        pass
      self.profile_filter.__exit__(None, None, None)
    self.router.close()
