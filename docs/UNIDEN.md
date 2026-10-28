# Uniden detector link

Pair and select an R4/R4w or R8/R8w in **Galaxy → Bluetooth → Uniden radar detector**. Enable Bluetooth on both devices, put the detector in **BT Pairing**, then use the existing Nearby Devices search and Pair buttons. Close phone detector apps first: a detector already connected elsewhere may stop advertising. If authentication fails, re-enter pairing mode and retry through the same UI.

Setup requires Park. Finish any Android Auto pairing window before pairing the detector. Ordinary detector pairing uses the existing Bluetooth owner and its live pairing agent, including delegated pairing when Android Auto already owns the adapter. No bluetoothctl subprocess, radio reset, agent replacement, or visibility change is introduced by the detector monitor.

Choose the paired address and model, enable alerts, and save. The **Uniden alerts** widget is available in the comma 3X large-screen layout and Android Auto layout editor. A single threat uses a compact card. Multiple threats expand into up to four rows, ordered by laser priority then signal strength, with band, frequency, eight signal bars, and observed mute state for each. Additional threats show an overflow count. The layout editor reserves the maximum 620×260 footprint. R8 displays only the active direction for each detection, without dim inactive arrows; side illuminates both side arrows because the protocol does not distinguish left from right. R4 never displays direction. In the theme maker, select the **Uniden alerts** widget. **Background transparency** adjusts the card from solid (0%) to no background (100%); text, arrows and signal bars stay opaque. **Hide when no alert** defaults on and hides both quiet and disconnected cards. Turn it off to show **Detector ready** when quiet and a connection warning when disconnected. Disconnection or invalid reads always clear old alerts. These settings are saved with each layout, independently for Android Auto and the comma 3X. Other openpilot alerts hide the detector card.

Tap the card to send `BTreqMUTE:1` for the current alert. Only detector-reported mute state displays **Muted**. Successful GATT writes initially display **Mute sent • awaiting detector**. A gesture canceled by dragging, a changed detector or alert, expired data, or loss of connection sends no command. Failed commands are not replayed after reconnecting. This feature does not alter cruise speed or vehicle controls.

## Implementation

- `openpilot/starpilot/uniden/protocol.py`: bounded snapshot parser, R4/R8 model selection.
- `transport.py`: UUID-based GATT discovery scoped to the selected paired device; 2 Hz reads while visible, reconnection attempts no more often than every 15 seconds. Reads use the existing BlueZ connection and owner lock. During projection they execute inside Android Auto's existing lease; pairing/discovery and invalid leases block access. No scans or new pairing happen automatically.
- `service.py`: persistent `UnidenConfig`, default disabled, parked configuration, selected-device mute validation.
- `feed.py`: background display polling, three-second freshness limit, expiring single-shot mute requests.
- `ui/onroad_uniden.py`: shared large-screen card and touch handling. Preview renderers do not start Bluetooth activity.
- Authenticated `POST /api/bluetooth/uniden`: `status`, `configure`, or `mute`. The Galaxy source proof authenticates delegated Android Auto operations; monitoring and current-alert mute work onroad.

Rebuild the Params native library when deploying this branch: `UnidenConfig` is a new JSON key. No new runtime dependencies are required. The implementation uses the existing Jeepney/BlueZ stack, rather than adding bleak. Turning monitoring off leaves the saved Bluetooth bond intact; disconnect/forget remain in the Bluetooth device list. Disconnecting an enabled detector allows automatic reconnect; disable monitoring to stop retries.

## Compatibility and validation

This is an implementation ready for bench validation, not hardware-certified support. [Uniden lists R4/R4w and R8/R8w for R/TACH](https://uniden.com/pages/rtach-app), but that does not prove identical GATT formats across models or firmware. The [reference protocol](https://github.com/AegisX86/UnidenR8wlink/blob/main/PROTOCOL.md) verifies reads on one R8w and explicitly leaves writes and other models unverified. Unknown characteristics, malformed snapshots and mismatched model names fail closed. Model names must identify R4/R4w or R8/R8w, and the device must expose the expected alert characteristic.

Reference: AegisX86/UnidenR8wlink `PROTOCOL.md`, blob `a6a968cd59e70db3fbd74480024d9afde6d94f12`, MIT notice retained in `openpilot/starpilot/uniden/UPSTREAM-LICENSE`. Earlier local research: `origin/BTAndroid_Unidenv2` and `origin/InaunerUnidenClone`. Experimental SETC settings and slowdown logic from those branches are outside this integration.

Before vehicle use, bench-test each model/firmware with Android Auto active: pair while parked; verify quiet/Ka/K/laser snapshots and R8 front/side/rear behavior; press detector mute and compare reported state; test touchscreen mute against the detector; power-cycle the detector and confirm reconnect; start/cancel pairing and disable the radio while monitoring; verify no stale alerts or mute replay. Test native 3X and projected touch positioning on the target screen.

Focused tests: `PYTHONPATH=.:opendbc_repo pytest openpilot/starpilot/uniden/tests openpilot/starpilot/system/android_auto/tests/test_projection_layout.py`.

## Proposed crowdsourced reports bridge

Not implemented: make the Galaxy Android companion a [SABRE host](https://sabre.app/tech), discover a user-selected compatible plugin, handshake and fetch nearby reports using phone location. Validate request IDs, bounded batches, source IDs, coordinates and timestamps; deduplicate by source + alert ID and expire stale reports. Relay normalized reports to a new authenticated companion endpoint on comma using the existing companion transport. Display source, distance, age and confirmation count separately from measured detector alerts. The detector remains owned by comma under the existing Android Auto Bluetooth lease.

[Highway Radar documents](https://book.highwayradar.com/basic/crowdsourced) server-based fetches and SABRE plugins; it is not itself the crowdsourcing network. Feed availability depends on the chosen plugin/provider. SABRE compatibility does not grant Waze API access. Start with read-only reports; report/confirm/discard actions would be a separate explicit feature. Validate Android background execution and plugin compatibility on a phone before claiming support.

The Uniden card uses the same dark fill as the cruise cards and inherits the theme text color. Fill, purple outline, and text can be customized on the widget in the theme maker. Signal bars use the Galaxy update progress gradient from teal (#4DE8E8) to purple (#9D72FF), across the illuminated segments. Segment count indicates strength. Rows omit “Live” and show “Muted” only when reported by the detector. Band labels and R8 arrows retain band-specific alert colors.
