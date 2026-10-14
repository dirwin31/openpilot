# Jetlink test diagnostics

Logging starts automatically when modeld attaches Jetlink. After a test, download
Galaxy's **Logs & Diagnostics → Android Auto** log bundle. The ZIP includes
Android Auto sessions, renderer logs, system context, and a `jetlink/` folder with
Jetlink owner and processing logs, including their retained rotations. Download
soon after the test so the relevant history has not rotated away.

`/data/log/jetlink-runtime.jsonl` records runtime state and handover changes,
plus a sample every 10 seconds while steady. Each row includes wall time (Unix
seconds), monotonic time, boot/process/session identity, remote output age, model
identity when remote execution is selected, and whether Android Auto is enabled.
Use the Android Auto session logs to determine whether it was actually connected
or streaming; the enabled flag alone does not establish that.

`processing` contains cumulative successful model-run count, total and maximum
run duration in milliseconds, the count exceeding the 50 ms frame budget, and
the latest camera frame-drop ratio. Differences between samples give interval
average processing time and slow-run count. Run duration covers the joined local
and remote processing call, not just inference on the remote device. The runtime
log retains three files of up to approximately 2 MiB each, written on the existing
background runtime publisher rather than the model frame thread.

`/data/log/jetlink-owner.log` records USB ownership, setup and cleanup failures.
The normal drive logs also contain upstream Jetlink telemetry, detailed slow-frame
timing and handover events. Keep the drive route and note the approximate time of
any freeze, disconnect or fallback, plus the cable arrangement and remote device.
This change adds diagnostics only; it does not change USB ownership or inference
fallback behavior.
