import asyncio
import os
import sys
import time

for p in ("/data/python_packages", "/usr/local/venv/lib/python3.12/site-packages"):
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

# Add starpilot third_party for vendored bleak + dbus-fast
_third_party = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "third_party")
if os.path.isdir(_third_party) and _third_party not in sys.path:
    sys.path.insert(0, _third_party)

try:
    import bleak
except ImportError:
    bleak = None

from openpilot.common.params import Params
from starpilot.system.uniden_r4 import discover_uniden_device, DEFAULTS, get_param, set_param
from starpilot.system.uniden_protocol import parse_alerts, parse_telemetry

# Use uniden_shm for cross-process shared memory
from starpilot.system.uniden_shm import set_shm_param, get_shm_param


async def _resolve_device(mac: str):
    """Look up a known BlueZ device by MAC and return a BLEDevice with the D-Bus path,
    so BleakClient does not need an active BLE scan."""
    try:
        from dbus_fast import Message, MessageType, unpack_variants
        from dbus_fast.aio import MessageBus
        from dbus_fast.constants import BusType
        from bleak.backends.device import BLEDevice

        bus = MessageBus(bus_type=BusType.SYSTEM)
        await bus.connect()
        try:
            reply = await bus.call(
                Message(
                    destination="org.bluez",
                    path="/",
                    interface="org.freedesktop.DBus.ObjectManager",
                    member="GetManagedObjects",
                )
            )
            if reply.message_type == MessageType.ERROR:
                return None

            for obj_path, interfaces in unpack_variants(reply.body[0]).items():
                if "org.bluez.Device1" in interfaces:
                    props = interfaces["org.bluez.Device1"]
                    addr = (props.get("Address") or "").upper()
                    name = props.get("Alias", "")
                    if addr == mac.upper():
                        return BLEDevice(addr, name, {"path": obj_path, "props": props})
            return None
        finally:
            bus.disconnect()
    except Exception:
        return None


ALERT_UUID = "6eb675ab-8bd1-1b9a-7444-621e52ec6823"
TEL_UUID   = "6c290d2e-1c03-aca1-ab48-a9b908bae79e"
RESP_UUID  = "5987b4ef-3bfa-76a8-e642-92933c31434f"
SET1_UUID  = "2d86686a-53dc-25b3-0c4a-f0e10c8dee20"
WRITE_UUID = "2c86686a-53dc-25b3-0c4a-f0e10c8dee20"

# Maximum time (seconds) to attempt connecting when first going onroad
ONROAD_CONNECT_WINDOW_SEC = 180.0  # 3 minutes

def clear_active_alert():
    set_shm_param("UnidenRadarAlertActive", False)
    set_shm_param("UnidenRadarAlertBand", "")
    set_shm_param("UnidenRadarAlertStrength", 0)
    set_shm_param("UnidenRadarAlertDescription", "")

def update_heartbeat():
    set_shm_param("UnidenRadarHeartbeat", time.monotonic())

async def run_uniden_daemon():
    print("[uniden_radar_d] Starting Uniden Radar Detector background monitor...")
    clear_active_alert()
    set_shm_param("UnidenRadarConnected", False)
    update_heartbeat()
    
    params = Params()
    last_alert_time = 0.0
    was_onroad = False
    onroad_start_time = 0.0
    manual_window_start = 0.0

    while True:
        client = None
        try:
            update_heartbeat()

            # 1. Respect StarPilot global Bluetooth toggle
            bt_enabled = params.get_bool("BluetoothEnabled")
            if not bt_enabled:
                clear_active_alert()
                set_shm_param("UnidenRadarConnected", False)
                await asyncio.sleep(4.0)
                continue

            # 2. Respect Uniden specific toggle
            enabled = get_param("UnidenR4Enabled", True)
            if not enabled:
                clear_active_alert()
                set_shm_param("UnidenRadarConnected", False)
                await asyncio.sleep(4.0)
                continue

            # 3. Check manual connect trigger from Galaxy WebUI
            manual_connect = get_shm_param("UnidenManualConnectTrigger", False)
            if manual_connect:
                set_shm_param("UnidenManualConnectTrigger", False)
                manual_window_start = time.monotonic()
                print("[uniden_radar_d] Manual connection requested by user (Galaxy UI)!")

            # 4. Check car onroad driving state
            is_onroad = bool(params.get_bool("IsOnroad"))
            if is_onroad and not was_onroad:
                onroad_start_time = time.monotonic()
                print("[uniden_radar_d] Car transitioned ONROAD! Starting 3-minute connection window...")
            elif not is_onroad:
                onroad_start_time = 0.0

            was_onroad = is_onroad

            # Connection criteria:
            # - Manual connect button pressed within last 60 seconds (window preserved across retries)
            # - Car is onroad (connects in initial 3-minute window or retries during drive)
            is_manual_active = manual_window_start > 0 and (time.monotonic() - manual_window_start < 60.0)
            is_onroad_active = is_onroad and (time.monotonic() - onroad_start_time <= ONROAD_CONNECT_WINDOW_SEC or is_onroad)

            if not is_manual_active and not is_onroad_active:
                clear_active_alert()
                set_shm_param("UnidenRadarConnected", False)
                await asyncio.sleep(2.0)
                continue

            mac = discover_uniden_device()
            if not mac or bleak is None:
                clear_active_alert()
                await asyncio.sleep(3.0)
                continue

            print(f"[uniden_radar_d] Attempting connection to {mac}...")
            # Resolve BlueZ device path and create BLEDevice so bleak doesn't
            # need an active scan (device is already known/bonded to BlueZ)
            device = await _resolve_device(mac)
            if device is None:
                print(f"[uniden_radar_d] Device {mac} not found in BlueZ, trying scan...")
                await asyncio.sleep(0.5)
                continue
            client = bleak.BleakClient(device, timeout=12.0)
            await client.connect()
            print(f"[uniden_radar_d] Connected to {mac}!")
            set_shm_param("UnidenRadarConnected", True)
            update_heartbeat()

            def on_alert_received(sender, data):
                nonlocal last_alert_time
                try:
                    update_heartbeat()
                    alerts = parse_alerts(data)
                    slowdown_bands_str = get_param("UnidenAutoSlowdownBands", "KA,K,LASER,MRCD,POP")
                    allowed_bands = [b.strip().upper() for b in slowdown_bands_str.split(",") if b.strip()]

                    # Check for active, relevant radar threats
                    threats = [a for a in alerts if a.band.upper() in allowed_bands and (a.strength or 0) > 0]
                    if threats:
                        primary = max(threats, key=lambda a: a.strength or 0)
                        last_alert_time = time.monotonic()
                        set_shm_param("UnidenRadarAlertActive", True)
                        set_shm_param("UnidenRadarAlertBand", primary.band)
                        set_shm_param("UnidenRadarAlertStrength", primary.strength or 0)
                        set_shm_param("UnidenRadarAlertDescription", primary.description or primary.band)
                    else:
                        if time.monotonic() - last_alert_time > 1.5:
                            clear_active_alert()
                except Exception as e:
                    print(f"[uniden_radar_d] Error parsing alert packet: {e}")

            def on_telemetry_received(sender, data):
                try:
                    update_heartbeat()
                    tel = parse_telemetry(data)
                    if tel.voltage is not None:
                        set_shm_param("UnidenRadarVoltage", float(tel.voltage))
                except Exception:
                    pass

            # Register notifications
            try:
                await client.start_notify(ALERT_UUID, on_alert_received)
                await client.start_notify(TEL_UUID, on_telemetry_received)
                await client.start_notify(RESP_UUID, lambda s, d: None)
                await client.start_notify(SET1_UUID, lambda s, d: None)
            except Exception as e:
                print(f"[uniden_radar_d] Notify registration warning: {e}")

            # Send init handshake
            try:
                await client.write_gatt_char(WRITE_UUID, b"BTreqGURL:", response=False)
                await asyncio.sleep(0.3)
                await client.write_gatt_char(WRITE_UUID, b"BTreqGWAP:", response=False)
            except Exception:
                pass

            # Monitor loop while connected
            while client.is_connected:
                update_heartbeat()
                if time.monotonic() - last_alert_time > 2.0:
                    clear_active_alert()
                await asyncio.sleep(0.5)

        except Exception as e:
            print(f"[uniden_radar_d] Connection dropped or error: {e}")
        finally:
            clear_active_alert()
            set_shm_param("UnidenRadarConnected", False)
            if client is not None:
                try:
                    if client.is_connected:
                        await client.disconnect()
                except Exception:
                    pass
            await asyncio.sleep(3.0)

def main():
    asyncio.run(run_uniden_daemon())

if __name__ == "__main__":
    main()
