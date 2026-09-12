"""Uniden R-series GATT access through StarPilot's existing BlueZ connection owner.

SETC mappings are from inauner/StarPilot 57f68dbd, not a vendor specification.
A successful write is transport success only; never treat it as setting readback.
"""
import re
import threading
import time

CONFIG_KEY = "UnidenConfig"
STATE_KEY = "UnidenState"
SLOWDOWN_KEY = "UnidenSlowdownStatus"
ALERT_UUID = "6eb675ab-8bd1-1b9a-7444-621e52ec6823"
TELEMETRY_UUID = "6c290d2e-1c03-aca1-ab48-a9b908bae79e"
WRITE_UUID = "2c86686a-53dc-25b3-0c4a-f0e10c8dee20"
FIRMWARE_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
GATT_IFACE = "org.bluez.GattCharacteristic1"
BANDS = ("X", "K", "KA", "LASER", "MRCD", "MRCT", "RT3", "RT4", "K POP", "KA POP")
DEFAULT_CONFIG = {"enabled": False, "address": "", "auto_slowdown": False, "bands": ["KA", "LASER"],
                  "min_strength": 2, "ignore_muted": True}
# label, SETC ID, allowed values. These are deliberately not presented as current device values.
SETTINGS = {
  "mode": ("Sensitivity mode", 100, {"all_threat": 1, "highway": 2, "city": 3, "advanced": 4}),
  "volume": ("Volume", 101, list(range(9))),
  "brightness": ("Brightness", 102, {"auto": 0, "dark": 1, "dimmer": 2, "dim": 3, "bright": 4, "brightest": 5, "off": 6}),
  "auto_mute": ("Auto mute", 103, [0, 1]),
  "mute_memory": ("Mute memory", 104, [0, 1]),
  "quiet_ride": ("Quiet Ride speed (mph)", 105, list(range(0, 91, 5))),
  "k_band": ("K band", 110, [0, 1]),
  "ka_band": ("Ka band", 111, [0, 1]),
  "laser": ("Laser detection", 112, [0, 1]),
  "mrcd": ("MRCD", 113, [0, 1]),
  "pop": ("POP", 114, [0, 1]),
  "alert_volume": ("Alert volume", 115, list(range(9))),
}


def normalize_config(value):
  if not isinstance(value, dict) or set(value) - DEFAULT_CONFIG.keys():
    raise ValueError("Invalid Uniden configuration")
  config = {**DEFAULT_CONFIG, **value}
  for key in ("enabled", "auto_slowdown", "ignore_muted"):
    if type(config[key]) is not bool:
      raise ValueError(f"{key} must be a boolean")
  address = config["address"]
  if not isinstance(address, str) or (address and not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", address)):
    raise ValueError("Invalid detector address")
  config["address"] = address.upper()
  bands = config["bands"]
  if not isinstance(bands, list) or not bands or any(band not in BANDS for band in bands):
    raise ValueError("Select at least one supported alert band")
  config["bands"] = list(dict.fromkeys(bands))
  if type(config["min_strength"]) is not int or not 1 <= config["min_strength"] <= 8:
    raise ValueError("Minimum signal strength must be 1–8")
  return config


def load_config(params):
  try:
    return normalize_config(params.get(CONFIG_KEY) or {})
  except (ValueError, TypeError):
    return normalize_config({})


def parse_alerts(payload):
  text = bytes(payload).decode("ascii").strip("\x00\r\n ")
  if not text or len(text) > 4096:
    raise ValueError("Empty or oversized Uniden alert packet")
  alerts = []
  for segment in text.split("&"):
    if segment == "0":
      continue
    fields = segment.split(",")
    if len(fields) < 8 or fields[0] != "1":
      raise ValueError("Malformed Uniden alert packet")
    band = fields[2].upper()
    strength = int(fields[3])
    if band not in BANDS or not 1 <= strength <= 8 or fields[7] not in ("1", "2", "3", "4", "5", "6"):
      raise ValueError("Unsupported Uniden alert values")
    alerts.append({"band": band, "strength": strength, "muted": fields[7] != "1",
                   "direction": {"F": "Front", "R": "Rear", "S": "Side"}.get(fields[6], "Unknown"),
                   "frequency": fields[5] if band != "LASER" else "", "id": fields[1]})
  return alerts


def setting_command(key, value):
  if key in ("mute", "unmute"):
    return f"BTreqMUTE:{1 if key == 'mute' else 0}"
  if key not in SETTINGS:
    raise ValueError("Unknown detector setting")
  _, command_id, choices = SETTINGS[key]
  if type(value) not in (int, str) or value not in choices:
    raise ValueError("Unsupported setting value")
  encoded = choices[value] if isinstance(choices, dict) else value
  return f"BTreqSETC:{command_id}={encoded}"


class UnidenService:
  def __init__(self, controller, memory):
    self.controller = controller
    self.params = controller.params
    self.memory = memory
    self.lock = threading.RLock()
    self.stop = threading.Event()
    self.state = {}
    self.last_command = None
    self._publish({"connected": False, "alerts": [], "error": "Waiting for detector"})

  def _publish(self, state):
    self.state = {**state, "last_command": self.last_command}
    self.memory.put(STATE_KEY, self.state)

  def configure(self, config):
    config = normalize_config(config)
    with self.lock:
      previous = load_config(self.params)
      if config["address"] and (config["address"] != previous["address"] or (config["enabled"] and not previous["enabled"])):
        device = self.controller._client().device_for_address(config["address"])
        if not device.get("uniden") or not device.get("paired"):
          raise ValueError("Select a paired Uniden detector")
      self.params.put(CONFIG_KEY, config)
      self._publish({"connected": False, "alerts": [], "error": "Configuration updated; waiting for fresh detector data"})
    return config

  def _device(self, config):
    if not config["enabled"] or not config["address"] or not self.params.get_bool("BluetoothEnabled"):
      raise RuntimeError("Enable Uniden and Bluetooth, and select a paired detector")
    client = self.controller._client()
    device = client.device_for_address(config["address"])
    if not all(device.get(key) for key in ("uniden", "paired", "connected", "services_resolved")):
      raise RuntimeError("Selected detector is not paired, connected, and service-ready")
    return client, device

  @staticmethod
  def _paths(client, device):
    prefix = device["path"] + "/"
    return {str(props["UUID"]).lower(): path
            for path, interfaces in client.managed_objects().items() if path.startswith(prefix)
            for props in [interfaces.get(GATT_IFACE, {})] if "UUID" in props}

  @staticmethod
  def _read(client, paths, uuid):
    if uuid not in paths:
      raise RuntimeError("Detector does not expose the required Uniden characteristic")
    return bytes(client._call(paths[uuid], GATT_IFACE, "ReadValue", "a{sv}", ({},), timeout=2.0)[0])

  def tick(self):
    with self.lock:
      config = load_config(self.params)
      try:
        client, device = self._device(config)
        # Reuse the connection lock so forget/disconnect cannot race GATT operations.
        with self.controller._connection_lock(config["address"]):
          paths = self._paths(client, device)
          alerts = parse_alerts(self._read(client, paths, ALERT_UUID))
          observed_at = time.monotonic()
          state = {"connected": True, "address": config["address"], "name": device["name"], "alerts": alerts,
                   "observed_at": observed_at, "can_write": WRITE_UUID in paths, "error": ""}
          # Optional telemetry failures never turn a valid alert read into fabricated data.
          for key, uuid in (("telemetry", TELEMETRY_UUID), ("firmware", FIRMWARE_UUID)):
            try:
              state[key] = self._read(client, paths, uuid).decode("ascii").strip("\x00")
            except Exception:
              state[key] = ""
          self._publish(state)
      except Exception as error:
        self._publish({"connected": False, "address": config["address"], "alerts": [], "error": str(error)})

  def apply_setting(self, key, value):
    command = setting_command(key, value)
    with self.lock:
      if not self.controller._offroad():
        raise RuntimeError("Detector settings can only be changed offroad")
      config = load_config(self.params)
      client, device = self._device(config)
      with self.controller._connection_lock(config["address"]):
        paths = self._paths(client, device)
        if WRITE_UUID not in paths:
          raise RuntimeError("Detector does not expose a command characteristic")
        client._call(paths[WRITE_UUID], GATT_IFACE, "WriteValue", "aya{sv}",
                     (command.encode("ascii"), {"type": ("s", "command")}), timeout=3.0)
      self.last_command = {"setting": key, "value": value, "status": "sent_unconfirmed", "at": time.monotonic()}
      self._publish(self.state)
      return self.last_command

  def run(self):
    while not self.stop.is_set():
      self.tick()
      self.stop.wait(0.5)

  def close(self):
    self.stop.set()
    with self.lock:
      self._publish({"connected": False, "alerts": [], "error": "Detector monitor stopped"})
