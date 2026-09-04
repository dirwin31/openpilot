import json
import os
import subprocess
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
    """Dynamically discover any paired or connected Uniden R-series detector (R4@*, R8@*, etc.)"""
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

    # If a configured MAC was saved, make sure it actually exists in BlueZ paired devices
    if configured_mac:
        if configured_mac in paired_devices:
            return configured_mac
        else:
            # Device was forgotten from Bluetooth! Clear stale param
            set_param("UnidenR4Mac", "")

    # Fallback: Check if any known BlueZ device looks like a Uniden detector
    for mac, name in paired_devices.items():
        if any(k in name.upper() for k in ["R4@", "R8@", "R9@", "UNIDEN"]):
            set_param("UnidenR4Mac", mac)
            return mac

    return ""

def scan_and_pair_uniden():
    """Runs a 5-second BLE scan, finds any advertising Uniden detector, pairs and trusts it via BlueZ."""
    try:
        # Start discovery for 5 seconds
        subprocess.run(['bluetoothctl', '--timeout', '5', 'scan', 'le'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out = subprocess.check_output(['bluetoothctl', 'devices'], stderr=subprocess.DEVNULL, timeout=3).decode()
        for line in out.splitlines():
            if any(k in line.upper() for k in ["R4@", "R8@", "R9@", "UNIDEN"]):
                parts = line.split()
                if len(parts) >= 2:
                    mac = parts[1].strip()
                    # Pair and trust
                    subprocess.run(['bluetoothctl', 'pair', mac], check=False, timeout=6)
                    subprocess.run(['bluetoothctl', 'trust', mac], check=False, timeout=3)
                    set_param("UnidenR4Mac", mac)
                    set_shm_param("UnidenManualConnectTrigger", True)
                    return {"status": "ok", "mac": mac, "name": " ".join(parts[2:]), "message": f"Successfully paired & trusted {mac}!"}
        return {"status": "error", "message": "No Uniden radar detector found advertising nearby. Ensure Bluetooth is ON on the detector."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

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

def trigger_action(action):
    if action == "connect":
        # Signal the background daemon to initiate connection immediately (even offroad)
        set_shm_param("UnidenManualConnectTrigger", True)
        mac = discover_uniden_device()
        if mac:
            try:
                subprocess.run(['bluetoothctl', 'connect', mac], check=False, timeout=5)
            except Exception:
                pass
            return {"status": "ok", "message": f"Connecting to {mac}..."}
        return {"status": "ok", "message": "Scanning and connecting to nearby Uniden detector..."}

    elif action == "pair":
        return scan_and_pair_uniden()

    elif action == "forget":
        mac = get_param("UnidenR4Mac", "") or discover_uniden_device()
        if mac:
            try:
                subprocess.run(['bluetoothctl', 'disconnect', mac], check=False, timeout=3)
                subprocess.run(['bluetoothctl', 'remove', mac], check=False, timeout=3)
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
                subprocess.run(['bluetoothctl', 'disconnect', mac], check=False, timeout=5)
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
            
    return {"status": "error", "message": "Unknown action"}
