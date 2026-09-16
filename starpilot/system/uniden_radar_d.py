import asyncio
import os
import subprocess
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
from starpilot.system.uniden_r4 import _uniden_name_match, DEFAULTS, get_param, set_param
from starpilot.system.uniden_protocol import parse_alerts, parse_telemetry

# Use uniden_shm for cross-process shared memory
from starpilot.system.uniden_shm import set_shm_param, get_shm_param
from starpilot.system.bluetooth.protocol import BluetoothClient


def _select_detector(status, configured_mac):
    """Pick the bonded detector out of bluetooth_managerd status: the saved MAC first,
    else any paired, uniden-looking device."""
    detectors = [device for device in status.devices if device.uniden or _uniden_name_match(device.name)]
    configured = (configured_mac or "").upper()
    if configured:
        match = next((device for device in detectors if device.address.upper() == configured), None)
        if match is not None:
            return match
    return next((device for device in detectors if device.paired), None)


async def _manager_status(client):
    return await asyncio.to_thread(client.status)


async def _manager_call(client, name, *args):
    return await asyncio.to_thread(getattr(client, name), *args)


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
    manager = BluetoothClient()
    last_alert_time = 0.0
    last_sound_time = 0.0
    last_sound_tier = 0
    was_onroad = False
    onroad_start_time = 0.0
    manual_window_start = 0.0
    last_error = ""

    while True:
        client = None
        address = ""
        try:
            update_heartbeat()

            # 1. Respect StarPilot global Bluetooth toggle
            if not params.get_bool("BluetoothEnabled"):
                clear_active_alert()
                set_shm_param("UnidenRadarConnected", False)
                await asyncio.sleep(4.0)
                continue

            # 2. Respect Uniden specific toggle
            if not get_param("UnidenR4Enabled", True):
                clear_active_alert()
                set_shm_param("UnidenRadarConnected", False)
                await asyncio.sleep(4.0)
                continue

            # 3. Check manual connect trigger from Galaxy WebUI
            if get_shm_param("UnidenManualConnectTrigger", False):
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
            should_connect = is_manual_active or is_onroad_active

            # bluetooth_managerd is the single owner of the adapter: it handles
            # discovery, pairing and the device link. This process only consumes the
            # established link, so it never competes with the phone companion for the
            # radio (no independent scan / connect / bluetoothctl calls).
            status = await _manager_status(manager)
            if not status.available:
                clear_active_alert()
                set_shm_param("UnidenRadarConnected", False)
                await asyncio.sleep(4.0)
                continue

            detector = _select_detector(status, get_param("UnidenR4Mac", ""))
            if detector is None:
                if not should_connect:
                    clear_active_alert()
                    set_shm_param("UnidenRadarConnected", False)
                await asyncio.sleep(3.0)
                continue

            address = detector.address
            if (get_param("UnidenR4Mac", "") or "").upper() != address.upper():
                set_param("UnidenR4Mac", address)

            if not should_connect:
                if detector.connected:
                    try:
                        await _manager_call(manager, "disconnect", address)
                    except Exception:
                        pass
                clear_active_alert()
                set_shm_param("UnidenRadarConnected", False)
                await asyncio.sleep(2.0)
                continue

            if not detector.connected or not detector.services_resolved:
                if not status.concurrent_roles and status.companion_connected:
                    # The adapter cannot hold the phone (peripheral) and the detector
                    # (central) at once; keep the phone link and retry later.
                    clear_active_alert()
                    set_shm_param("UnidenRadarConnected", False)
                    await asyncio.sleep(3.0)
                    continue
                if not last_error:
                    print(f"[uniden_radar_d] Asking bluetooth_managerd to connect {address}...")
                try:
                    await _manager_call(manager, "connect", address)
                except Exception as error:
                    message = str(error) or repr(error)
                    if message != last_error:
                        print(f"[uniden_radar_d] Connection attempt failed: {message}")
                    last_error = message
                    set_shm_param("UnidenRadarConnected", False)
                    await asyncio.sleep(2.0)
                    continue
                status = await _manager_status(manager)
                detector = _select_detector(status, address)
                if detector is None or not detector.connected or not detector.services_resolved:
                    await asyncio.sleep(1.0)
                    continue

            if bleak is None:
                await asyncio.sleep(3.0)
                continue
            last_error = ""

            # Resolve the BlueZ device path so bleak attaches to the link the manager
            # established instead of initiating its own connection or scan.
            device = await _resolve_device(address)
            if device is None:
                await asyncio.sleep(1.0)
                continue
            client = bleak.BleakClient(device, timeout=12.0)
            await client.connect()
            print(f"[uniden_radar_d] Consuming detector {address} (link owned by bluetooth_managerd)")
            set_shm_param("UnidenRadarConnected", True)
            update_heartbeat()

            def on_alert_received(sender, data):
                nonlocal last_alert_time, last_sound_time, last_sound_tier
                try:
                    update_heartbeat()
                    alerts = parse_alerts(data)
                    slowdown_bands_str = get_param("UnidenAutoSlowdownBands", "KA,K,LASER,MRCD,POP")
                    allowed_bands = [b.strip().upper() for b in slowdown_bands_str.split(",") if b.strip()]

                    # Check for active, relevant radar threats
                    threats = [a for a in alerts if a.band.upper() in allowed_bands and (a.strength or 0) > 0]
                    if threats:
                        primary = max(threats, key=lambda a: a.strength or 0)
                        now = time.monotonic()
                        last_alert_time = now
                        set_shm_param("UnidenRadarAlertActive", True)
                        set_shm_param("UnidenRadarAlertBand", primary.band)
                        set_shm_param("UnidenRadarAlertStrength", primary.strength or 0)
                        set_shm_param("UnidenRadarAlertDescription", primary.description or primary.band)

                        # Audio alert sound handling
                        strength_val = primary.strength or 0
                        tier = 0
                        sound_param = None
                        if 1 <= strength_val <= 2:
                            tier = 1
                            sound_param = "UnidenSoundSignal1_2"
                        elif 3 <= strength_val <= 5:
                            tier = 2
                            sound_param = "UnidenSoundSignal3_5"
                        elif strength_val >= 6:
                            tier = 3
                            sound_param = "UnidenSoundSignal6_8"

                        if tier > 0 and sound_param:
                            sound_choice = get_param(sound_param, DEFAULTS.get(sound_param, "disabled"))
                            if sound_choice and sound_choice.lower() not in ("disabled", "none", "off"):
                                # Escalation trigger or repeat interval debounce (4.0s)
                                tier_escalated = tier > last_sound_tier
                                repeat_due = (now - last_sound_time) >= 4.0
                                if tier_escalated or repeat_due:
                                    last_sound_time = now
                                    last_sound_tier = tier
                                    sound_file = f"/data/openpilot/selfdrive/assets/sounds/{sound_choice}"
                                    if os.path.exists(sound_file):
                                        try:
                                            subprocess.Popen(["aplay", "-D", "plughw:0,0", "-q", sound_file])
                                        except Exception as err:
                                            print(f"[uniden_radar_d] Error playing alert audio: {err}")
                    else:
                        if time.monotonic() - last_alert_time > 1.5:
                            clear_active_alert()
                            last_sound_tier = 0
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
