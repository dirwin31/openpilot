# Uniden in Galaxy

Open **Tools → Bluetooth → Uniden** (`#/bluetooth/uniden`). This tab uses the comma's Bluetooth radio, so it does not require the browser's Web Bluetooth API.

## Pair and select a detector

1. Park the vehicle and enable Bluetooth on the comma.
2. Enable Bluetooth on the detector (BT/WiFi on some models), select **BT Pairing**, and press the Menu key.
3. Select **Search for Detectors**, identify the name and address, then select **Pair**.
4. Wait for **Connected · Services ready**, select the device under **Active detector**, enable monitoring, and save the configuration.
5. Check the monitor's live alerts and reported firmware before enabling auto slowdown.

Disconnect a phone app already using the detector if it is missing from discovery. Enter BT Pairing again and search. Saved detectors reconnect through the existing Bluetooth manager. Disconnect pauses retries for five minutes; Connect resumes immediately. Forget removes the saved BlueZ device and bond. Bluetooth power affects all devices, including phones and audio.

Discovery recognizes R4/R4W/R8/R8W/R9/R9W-style names and UNIDEN names. Actual access requires a paired connection and the expected GATT characteristics. A recognized name or completed service discovery does not prove protocol compatibility. R7 is not supported by this integration.

## Auto slowdown

Monitoring and automatic slowdown default to off. Enable **Auto slowdown to posted speed limit**, select alert bands and minimum strength, choose whether muted alerts count, and save while parked. Defaults are Ka and Laser, minimum strength 2/8, and ignoring muted alerts.

The feature adds a speed ceiling to the existing cruise planner. It uses the Speed Limit Controller's selected posted target without the configured offset. Enable Speed Limit Controller or Show Speed Limits to supply that target. Accuracy and persistence of that limit depend on the configured speed-limit sources and fallback policy; Uniden does not establish the road's speed limit. No valid source/target means no Uniden speed ceiling.

The ceiling requires enabled driving controls, openpilot longitudinal capability, active longitudinal control, no brake input, and a qualifying alert from the selected detector read within the last three seconds. Future timestamps, missing timestamps, disconnected devices, malformed packets, and mismatched addresses cannot activate it. The ceiling can only lower the cruise target; existing curve/lead/stop constraints and acceleration limits still apply.

Pressing gas (or an existing SLC speed override) suppresses the Uniden ceiling until the qualifying alert clears. Clearing the alert or losing fresh data releases this ceiling and lets the normal planner resume. Galaxy displays the last planner reason and target; stale feedback is shown as planner inactive. This is not a replacement for driver supervision or vehicle braking controls.

## Detector commands

The experimental settings section exposes the source branch's sensitivity mode, volume, brightness, auto mute, mute memory, Quiet Ride speed, K/Ka/Laser/MRCD/POP switches, alert volume, and mute/unmute commands. Choose one value and select **Send** while parked. Unsaved changes to the active detector/configuration disable commands until saved.

**These SETC IDs are not independently verified across models or firmware.** Values shown in the selectors are requested values, not current detector settings. There is no decoded settings readback or verified acknowledgment parser. A successful operation means BlueZ accepted the GATT write and is explicitly shown as **sent, unconfirmed**. Check the result on the detector. Failed writes are reported as errors and never stored as successful settings. Commands are never automatically replayed on reconnect.

The command characteristic is resolved by UUID beneath the selected device's object path. No fixed GATT handle fallback, arbitrary command input, or separate Bluetooth connection owner is used. The monitor reads current alert snapshots, including unchanged active alerts, at approximately 2 Hz; optional telemetry and firmware reads are diagnostic. Read failures clear the alert state. The integration does not import Waze or road-hazard slowdown.

## Fact-check sources

- [Source integration at commit 57f68dbd](https://github.com/inauner/StarPilot/blob/57f68dbd16fc1ab2f656b9ffde50872fc9ce22ae/starpilot/system/uniden_r4.py): origin of the experimental SETC mappings. Its pairing path uses unchecked exit codes; its setting path saves values even when a write fails. Those behaviors are not copied.
- [Source slowdown logic](https://github.com/inauner/StarPilot/blob/57f68dbd16fc1ab2f656b9ffde50872fc9ce22ae/starpilot/controls/lib/starpilot_vcruise.py): posted-limit ceiling and gas override. The original accepts a missing heartbeat; this integration rejects missing/stale data.
- [Uniden setup guide](https://support.uniden.com/support/solutions/articles/153000224590-r-tach-application-start-up-guide-r4w-r8w-r9w-and-non-w): detector-side Bluetooth and BT Pairing steps.
- [Uniden R/TACH compatibility](https://uniden.com/pages/rtach-app): R4, R4w, R8, R8w, R9, R9w; this list is app compatibility, not certification of StarPilot.
- [Uniden R8W firmware](https://www.uniden.info/download/index.cfm?s=r8w): 1.42 listed as latest on 2026-09-11, released 2026-09-03. The connected device's actual version is displayed separately.
- [Independent R8w protocol investigation](https://github.com/AegisX86/UnidenR8wlink/blob/main/PROTOCOL.md): GATT characteristic IDs and alert snapshot format. Its author limits hardware verification to one R8w/firmware combination and does not verify the settings mapping.

## Validation

Tests exercise packet parsing, stale/future/malformed state, selected-device isolation, gas override, cruise planner integration, settings validation/write failures, Galaxy APIs, actual Vue templates, pending edits, and offroad restrictions. Physical R8W 1.42 testing and cross-model compatibility remain unverified.

Persistent configuration uses `UnidenConfig`; runtime state uses `UnidenState` and `UnidenSlowdownStatus` in memory Params. The Params-only Linux AArch64 build regenerates `common/libcommon.a` and `common/params_pyx.so`. These artifacts must accompany the registry update when installed on a comma. No deployment or hardware configuration is performed by this change.
