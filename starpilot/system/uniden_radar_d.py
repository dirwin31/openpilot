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


def _select_detector(objects, configured_mac: str):
    """Pick the bonded detector out of BlueZ's managed objects: the saved MAC first,
    else any paired device named like a Uniden R-series detector. Devices that were
    only seen in a scan are never picked - connecting to them would race Scan & Pair."""
    paired = [(path, interfaces["org.bluez.Device1"]) for path, interfaces in objects.items()
              if interfaces.get("org.bluez.Device1", {}).get("Paired")]
    configured = configured_mac.upper()
    if configured:
        match = next(((path, props) for path, props in paired if (props.get("Address") or "").upper() == configured), None)
        if match is not None:
            return match
    return next(((path, props) for path, props in paired
                 if _uniden_name_match(props.get("Name") or props.get("Alias"))), None)


async def _find_detector():
    """Look up the bonded detector in BlueZ and return a BLEDevice with its D-Bus path,
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
            objects = unpack_variants(reply.body[0])
        finally:
            bus.disconnect()
    except Exception:
        return None

    configured_mac = get_param("UnidenR4Mac", "")
    match = _select_detector(objects, configured_mac)
    if match is None:
        # Never clear the saved MAC here: BlueZ may just not be up yet.
        return None
    path, props = match
    address = (props.get("Address") or "").upper()
    if address != configured_mac.upper():
        set_param("UnidenR4Mac", address)
    return BLEDevice(address, props.get("Alias", ""), {"path": path, "props": props})


ALERT_UUID = "6eb675ab-8bd1-1b9a-7444-621e52ec6823"
TEL_UUID   = "6c290d2e-1c03-aca1-ab48-a9b908bae79e"
RESP_UUID  = "5987b4ef-3bfa-76a8-e642-92933c31434f"
SET1_UUID  = "2d86686a-53dc-25b3-0c4a-f0e10c8dee20"
WRITE_UUID = "2c86686a-53dc-25b3-0c4a-f0e10c8dee20"

# A bonded detector does not show up in scans - it reconnects by advertising to the
# comma, so a BlueZ connection attempt has to be pending when it does. Keep one pending
# almost continuously, onroad or offroad, so it reconnects whichever powers on first.
CONNECT_TIMEOUT_SEC = 20.0
RETRY_DELAY_SEC = 1.0
FAST_FAIL_SEC = 3.0         # an attempt that failed this quickly was refused, not timed out
FAST_FAIL_DELAY_SEC = 5.0   # adapter off / BlueZ busy - don't spin

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
    last_sound_time = 0.0
    last_sound_tier = 0
    last_error = ""

    while True:
        client = None
        attempt_start = None
        delay = 2.0
        try:
            update_heartbeat()

            # 1. Respect StarPilot global Bluetooth toggle
            # 2. Respect Uniden specific toggle
            if not params.get_bool("BluetoothEnabled") or not get_param("UnidenR4Enabled", True):
                delay = 4.0
                continue

            # 3. The Galaxy Connect button resumes auto-connect after a manual Disconnect
            if get_shm_param("UnidenManualConnectTrigger", False):
                set_shm_param("UnidenManualConnectTrigger", False)
                set_shm_param("UnidenAutoConnectPaused", False)
                print("[uniden_radar_d] Manual connection requested by user (Galaxy UI)!")
            if get_shm_param("UnidenAutoConnectPaused", False):
                continue

            device = await _find_detector() if bleak is not None else None
            if device is None:
                delay = 3.0
                continue

            if not last_error:
                print(f"[uniden_radar_d] Waiting for {device.address} to come in range...")
            client = bleak.BleakClient(device, timeout=CONNECT_TIMEOUT_SEC)
            attempt_start = time.monotonic()
            await client.connect()
            attempt_start = None
            last_error = ""
            print(f"[uniden_radar_d] Connected to {device.address}!")
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
            while client.is_connected and not get_shm_param("UnidenAutoConnectPaused", False):
                update_heartbeat()
                if time.monotonic() - last_alert_time > 2.0:
                    clear_active_alert()
                await asyncio.sleep(0.5)
            print("[uniden_radar_d] Detector disconnected, waiting for it to reconnect...")
            delay = RETRY_DELAY_SEC

        except Exception as e:
            error = str(e) or repr(e)
            # Only log a new error - a powered-off detector fails every attempt.
            if error != last_error:
                print(f"[uniden_radar_d] Connection dropped or error: {error}")
            last_error = error
            refused = attempt_start is not None and time.monotonic() - attempt_start < FAST_FAIL_SEC
            delay = FAST_FAIL_DELAY_SEC if refused else RETRY_DELAY_SEC
        finally:
            clear_active_alert()
            set_shm_param("UnidenRadarConnected", False)
            if client is not None:
                try:
                    if client.is_connected:
                        await client.disconnect()
                except Exception:
                    pass
            await asyncio.sleep(delay)

def main():
    asyncio.run(run_uniden_daemon())

if __name__ == "__main__":
    main()
