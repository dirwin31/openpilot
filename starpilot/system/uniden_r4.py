import json
import os
import subprocess
import threading
import time
try:
    from openpilot.starpilot.system.uniden_shm import set_shm_param, get_shm_param
except ImportError:
    from starpilot.system.uniden_shm import set_shm_param, get_shm_param


PARAMS_PATH = '/data/params/d'

DEFAULTS = {
    "UnidenR4Enabled": True,
    "UnidenR4Mac": "",                  # Dynamically discovered or configured
    "UnidenR4Mode": "all_threat",       # all_threat, highway, city, advanced
    "UnidenR4AutoMute": True,
    "UnidenR4QuietRideSpeed": 35,       # mph
    "UnidenR4Volume": 5,                # 0-8
    "UnidenR4Brightness": "auto",       # auto, bright, dim, dimmer, dark, brightest, off
    "UnidenR4KBand": True,
    "UnidenR4KaBand": True,
    "UnidenR4Laser": True,
    "UnidenR4MRCD": True,
    "UnidenR4POP": False,
    "UnidenR4MuteMemory": True,
    "UnidenR4AlertVolume": 5,
    "UnidenAutoSlowdown": True,
    "UnidenAutoSlowdownBands": "KA,K,LASER,MRCD,POP",
    "UnidenSlowdownOffset1_2": 14,
    "UnidenSlowdownOffset3_5": 9,
    "UnidenSlowdownOffset6_8": 5,
    "UnidenSoundSignal1_2": "prompt.wav",
    "UnidenSoundSignal3_5": "warning_soft.wav",
    "UnidenSoundSignal6_8": "warning_immediate.wav",
}

# Mapping for Uniden R-series BLE command protocol (SETC IDs verified via Android R/TACH trace)
BRIGHTNESS_CMDS = {
    "auto": "BTreqSETC:102=0",
    "dark": "BTreqSETC:102=1",
    "dimmer": "BTreqSETC:102=2",
    "dim": "BTreqSETC:102=3",
    "bright": "BTreqSETC:102=4",
    "brightest": "BTreqSETC:102=5",
    "off": "BTreqSETC:102=6",
}

MODE_CMDS = {
    "all_threat": "BTreqSETC:100=1",
    "highway": "BTreqSETC:100=2",
    "city": "BTreqSETC:100=3",
    "advanced": "BTreqSETC:100=4",
}

WRITE_CHAR_UUID = "2c86686a-53dc-25b3-0c4a-f0e10c8dee20"

# ---------------------------------------------------------------------------
# Pairing (SMP bond) via native BlueZ D-Bus - no bluetoothctl, no agent shell,
# works headless so users only need to press "Scan & Pair" in The Galaxy UI.
# ---------------------------------------------------------------------------
PAIRING_MODE_HINT = (
    "Put your Uniden detector (R4 / R8 / R9) in pairing mode now - it only advertises while pairing! "
    "The detector display should show PAIRING / Accept Pairing. Keep it within one "
    "meter of this device."
)

PAIRING_SCAN_WINDOW_SEC = 45.0
PAIRING_BOND_TIMEOUT_SEC = 150.0  # Pair (90s) + detector connect
PAIRING_NAME_KEYS = ("R4@", "R5@", "R7@", "R8@", "R8W@", "R9@", "R1@", "R3@", "UNIDEN")

ACTIVE_PAIRING_STATES = ("searching", "pairing", "verifying")


def _set_pair_state(state, message):
    set_shm_param("UnidenPairState", state)
    if message:
        set_shm_param("UnidenPairMessage", message)


def _uniden_name_match(name):
    if not name:
        return False
    upper = str(name).upper()
    return any(key in upper for key in PAIRING_NAME_KEYS)


def _find_detector(status):
    detectors = [device for device in status.devices if device.uniden or _uniden_name_match(device.name)]
    # Already bonded (e.g. reconnect scenario) - no need to pair again.
    return next((device for device in detectors if device.paired), None) or next(iter(detectors), None)


def _device_status(client, address):
    status = client.status()
    device = next((device for device in status.devices if device.address.upper() == address.upper()), None)
    return status, device


def _pairing_flow():
    """Pair through bluetooth_managerd - the adapter's single pairing agent and
    discovery owner - exactly like its Bluetooth screen does, so the detector and
    the phone companion never compete for BlueZ. uniden_radar_d then opens its
    session on the bonded detector."""
    from openpilot.starpilot.system.bluetooth.protocol import BluetoothClient

    client = BluetoothClient()
    if not client.status().enabled:
        _set_pair_state("failed", "Turn on Bluetooth on the comma first, then tap Scan & Pair again.")
        return

    _set_pair_state("searching", "Searching for your detector... " + PAIRING_MODE_HINT)
    client.start_scan()
    deadline = time.monotonic() + PAIRING_SCAN_WINDOW_SEC
    last_notice = time.monotonic()
    while True:
        status = client.status()
        target = _find_detector(status)
        now = time.monotonic()
        if target is not None or now >= deadline:
            break
        if not status.discovering:
            client.start_scan()  # bluetooth_managerd ends each scan after 20s
        if now - last_notice > 5.0:
            last_notice = now
            _set_pair_state("searching", f"Searching for your detector ({int(deadline - now)}s left)... {PAIRING_MODE_HINT}")
        time.sleep(1.0)

    if target is None:
        client.stop_scan()
        _set_pair_state("failed", "Couldn't find a Uniden detector. " + PAIRING_MODE_HINT + " Then tap Scan & Pair again.")
        return

    address, name = target.address, target.name or target.address
    just_paired = not target.paired
    if just_paired:
        _set_pair_state("verifying", f"Found {name}. Establishing secure bond (LTK exchange)...")
        client.pair(address)  # bluetooth_managerd pairs, trusts, then connects the detector
        pair_deadline = time.monotonic() + PAIRING_BOND_TIMEOUT_SEC
        while True:
            status, device = _device_status(client, address)
            if status.pairing_address.upper() != address.upper():
                break
            if time.monotonic() >= pair_deadline:
                raise RuntimeError("Operation timed out.")
            time.sleep(1.0)
        if device is None or not device.paired:
            raise RuntimeError(status.error or "bond did not complete (Paired=false)")
    else:
        client.stop_scan()
        _, device = _device_status(client, address)

    set_param("UnidenR4Mac", address)
    set_shm_param("UnidenManualConnectTrigger", True)

    # Do not claim success unless we can actually reach the detector -
    # a cached bond persists in BlueZ even when the R4 is powered off.
    connected = bool(device and device.connected)
    if not connected and not just_paired:
        try:
            client.connect(address)
            connected = True
        except Exception:
            connected = False

    if connected:
        _set_pair_state("success", f"Bonded & connected to {name}! Radar alerts are live.")
    else:
        # Bond established (new or from a previous pairing) but the detector
        # could not be reached right now. Keep the saved MAC - the BLE daemon
        # will connect automatically as soon as the R4 is powered on.
        _set_pair_state(
            "unreachable",
            f"{name} is bonded, but not reachable right now. "
            "Power the detector on (its display should light up) and it will connect automatically. "
            "If it still won't appear, put it in pairing mode and tap Scan & Pair again."
        )


def _pairing_worker():
    try:
        _pairing_flow()
    except Exception as e:
        err = str(e).strip() or repr(e)
        _set_pair_state("failed", f"Pairing failed: {err} Make sure the detector is in pairing mode and try again.")


def scan_and_pair_uniden():
    """Kick off headless SMP bonding in a background thread and return immediately,
    so the Galaxy UI can show progress while the user puts the detector in
    pairing mode. The BLE daemon picks the result up and connects."""
    current = get_shm_param("UnidenPairState", "idle")
    if current in ACTIVE_PAIRING_STATES:
        return {"status": current, "message": "Pairing already in progress - " + PAIRING_MODE_HINT}
    _set_pair_state("idle", PAIRING_MODE_HINT)
    threading.Thread(target=_pairing_worker, name="uniden-pairing", daemon=True).start()
    return {"status": "searching", "message": PAIRING_MODE_HINT}

def get_param(name, default):
    p = os.path.join(PARAMS_PATH, name)
    if os.path.exists(p):
        try:
            with open(p, 'r') as f:
                val = f.read().strip()
                if isinstance(default, bool):
                    return val == '1' or val.lower() == 'true'
                elif isinstance(default, int):
                    return int(val)
                return val
        except Exception:
            return default
    return default

def set_param(name, value):
    os.makedirs(PARAMS_PATH, exist_ok=True)
    p = os.path.join(PARAMS_PATH, name)
    try:
        with open(p, 'w') as f:
            if isinstance(value, bool):
                f.write('1' if value else '0')
            else:
                f.write(str(value))
        # Keep SHM mirror in sync for high-frequency control loops
        set_shm_param(name, value)
        return True
    except Exception as e:
        print(f"Failed to write param {name}: {e}")
        return False

def discover_uniden_device():
    """Dynamically discover any paired or connected Uniden R-series detector (R4@*, R8@*, R9@*, etc.)"""
    configured_mac = get_param("UnidenR4Mac", "")

    # Check what devices BlueZ actually has paired/known
    paired_devices = {}
    try:
        out = subprocess.check_output(['bluetoothctl', 'devices'], stderr=subprocess.DEVNULL, timeout=2).decode()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].lower() == "device":
                mac = parts[1].strip()
                name = " ".join(parts[2:]) if len(parts) > 2 else ""
                paired_devices[mac] = name
    except Exception:
        pass

    # Keep the saved selection through transient BlueZ/adapter failures. It is only
    # cleared by an explicit forget, never by a failed query.
    if configured_mac:
        return configured_mac

    # Fallback: Check if any known BlueZ device looks like a Uniden detector
    for mac, name in paired_devices.items():
        if _uniden_name_match(name):
            set_param("UnidenR4Mac", mac)
            return mac

    return ""

def get_char_write_path(mac=None):
    """Dynamically resolve the BlueZ D-Bus object path for the detector's command characteristic."""
    if not mac:
        mac = discover_uniden_device()
    if not mac:
        return None
    dev_path = f"/org/bluez/hci0/dev_{mac.replace(':', '_')}"
    
    # 1. First attempt dynamic lookup via BlueZ D-Bus tree matching WRITE_CHAR_UUID
    try:
        out = subprocess.check_output(['busctl', 'tree', '--full', 'org.bluez'], text=True, stderr=subprocess.DEVNULL, timeout=2)
        for line in out.splitlines():
            path = line.strip().split()[-1]
            if dev_path in path and "/char" in path:
                try:
                    uuid_out = subprocess.check_output(['busctl', 'get-property', 'org.bluez', path, 'org.bluez.GattCharacteristic1', 'UUID'], text=True, stderr=subprocess.DEVNULL, timeout=1)
                    if WRITE_CHAR_UUID.lower() in uuid_out.lower():
                        return path
                except Exception:
                    pass
    except Exception:
        pass

    # 2. Stable fallback default path for Uniden BLE GATT profile
    return f"{dev_path}/service0028/char0029"

def send_ble_command(cmd_str):
    """Send GATT command via dynamic D-Bus path with exit code check."""
    char_write = get_char_write_path()
    if not char_write:
        return False
    try:
        byte_list = [str(b) for b in cmd_str.encode('utf-8')]
        count_str = str(len(byte_list))
        cmd = ['busctl', 'call', 'org.bluez', char_write, 'org.bluez.GattCharacteristic1', 'WriteValue', 'aya{sv}', count_str] + byte_list + ['0']
        res = subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        return res.returncode == 0
    except Exception as e:
        print(f"BLE send error ({cmd_str}): {e}")
        return False

def get_all_settings():
    settings = {}
    for k, v in DEFAULTS.items():
        settings[k] = get_param(k, v)
    if not settings.get("UnidenR4Mac"):
        settings["UnidenR4Mac"] = discover_uniden_device()
    return settings

def update_settings(new_settings):
    for k, v in new_settings.items():
        if k in DEFAULTS:
            set_param(k, v)
            # Dispatch live BLE GATT commands directly to the Uniden R4
            if k == "UnidenR4Brightness":
                cmd = BRIGHTNESS_CMDS.get(str(v).lower())
                if cmd:
                    send_ble_command(cmd)
            elif k == "UnidenR4Volume":
                send_ble_command(f"BTreqSETC:101={v}")
            elif k == "UnidenR4Mode":
                cmd = MODE_CMDS.get(str(v).lower())
                if cmd:
                    send_ble_command(cmd)
            elif k == "UnidenR4AutoMute":
                send_ble_command(f"BTreqSETC:103={1 if v else 0}")
            elif k == "UnidenR4MuteMemory":
                send_ble_command(f"BTreqSETC:104={1 if v else 0}")
            elif k == "UnidenR4QuietRideSpeed":
                send_ble_command(f"BTreqSETC:105={v}")
            elif k == "UnidenR4KBand":
                send_ble_command(f"BTreqSETC:110={1 if v else 0}")
            elif k == "UnidenR4KaBand":
                send_ble_command(f"BTreqSETC:111={1 if v else 0}")
            elif k == "UnidenR4Laser":
                send_ble_command(f"BTreqSETC:112={1 if v else 0}")
            elif k == "UnidenR4MRCD":
                send_ble_command(f"BTreqSETC:113={1 if v else 0}")
            elif k == "UnidenR4POP":
                send_ble_command(f"BTreqSETC:114={1 if v else 0}")
            elif k == "UnidenR4AlertVolume":
                send_ble_command(f"BTreqSETC:115={v}")
    return get_all_settings()

def get_connection_status():
    mac = discover_uniden_device()
    is_connected_shm = get_shm_param("UnidenRadarConnected", False)
    status = {
        "mac": mac,
        "name": "Uniden Radar Detector",
        "connected": bool(is_connected_shm),
        "trusted": False,
        "rssi": None,
        "pairing_state": get_shm_param("UnidenPairState", "idle"),
        "pairing_message": get_shm_param("UnidenPairMessage", ""),
    }
    if not mac:
        return status

    try:
        out = subprocess.check_output(['bluetoothctl', 'info', mac], stderr=subprocess.DEVNULL, timeout=2).decode()
        if not status["connected"]:
            status["connected"] = "Connected: yes" in out
        status["trusted"] = "Trusted: yes" in out
        for line in out.splitlines():
            if "Name:" in line:
                status["name"] = line.split("Name:")[1].strip()
            if "RSSI:" in line:
                try:
                    status["rssi"] = int(line.split("(")[1].split(")")[0])
                except Exception:
                    pass
    except Exception:
        pass
    return status

def _bluetooth_client():
    try:
        from openpilot.starpilot.system.bluetooth.protocol import BluetoothClient
    except ImportError:
        from starpilot.system.bluetooth.protocol import BluetoothClient
    return BluetoothClient()


def trigger_action(action):
    if action == "connect":
        # Signal uniden_radar_d, which asks bluetooth_managerd (the adapter's single
        # owner) to establish the link without racing the phone companion.
        set_shm_param("UnidenManualConnectTrigger", True)
        mac = discover_uniden_device()
        if mac:
            return {"status": "ok", "message": f"Connecting to {mac}..."}
        return {"status": "ok", "message": "Looking for a bonded Uniden detector..."}

    elif action == "pair":
        return scan_and_pair_uniden()

    elif action == "forget":
        mac = get_param("UnidenR4Mac", "") or discover_uniden_device()
        if mac:
            try:
                _bluetooth_client().forget(mac)
            except Exception:
                pass
        set_param("UnidenR4Mac", "")
        set_shm_param("UnidenRadarConnected", False)
        set_shm_param("UnidenRadarAlertActive", False)
        return {"status": "ok", "message": f"Forgot detector {mac or ''}."}
        
    elif action == "disconnect":
        mac = discover_uniden_device()
        if mac:
            try:
                _bluetooth_client().disconnect(mac)
                return {"status": "ok", "message": f"Disconnected from {mac}."}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        return {"status": "ok", "message": "Disconnected."}
        
    elif action == "mute":
        try:
            ok = send_ble_command("BTreqMUTE:1")
            return {"status": "ok" if ok else "error", "message": "Mute command sent." if ok else "Failed to send mute command"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    elif action.startswith("play_sound:"):
        sound_name = action.split(":", 1)[1].strip()
        sound_path = f"/data/openpilot/selfdrive/assets/sounds/{sound_name}"
        if os.path.exists(sound_path) and sound_name.endswith(".wav"):
            try:
                subprocess.Popen(["aplay", "-D", "plughw:0,0", "-q", sound_path])
                return {"status": "ok", "message": f"Playing {sound_name}"}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        return {"status": "error", "message": f"Sound file {sound_name} not found"}
            
    return {"status": "error", "message": "Unknown action"}
