# Android Auto reports in Bugsink

`bugsink_report.py` sends Android Auto drive, failure and pairing reports to Bugsink
(`bugsink.firestar.link`) through the crash-reporting SDK that `android_autod` already runs
with (`openpilot/system/sentry.py`, `capture_report()`).

## What is sent

| Report  | When | Bugsink issue (fingerprint) |
|---|---|---|
| Drive   | Once per drive: every session finished since the last report | Connection type + head unit (`android-auto-drive`, `wireless`, `Alpine HU-1`) |
| Failure | Each new kind of failure; at most once a day per kind, 5 a day per device | Outcome + stage + error type (`android-auto-failure`, `failed`, `joining_wifi`, `TimeoutError`) |
| Pairing | Each pairing attempt | Result: `paired`, `expired`, `cancelled`, `select_failed`, `interrupted`, `reopened` |

- **Drive reports**: each connection stage with seconds since the session started, time to
  first frame, streamed time, the resolution the car chose, the car's fps, the target fps and
  encoder, delivered fps (average, minimum, 10th percentile, weighted by 30 s samples across the
  drive), encode time, frame age, worst Wi-Fi signal, stalls, encoder recoveries, car and head unit.
- **Failures**: the session record plus the session's compatibility report as text.
- **Pairing**: each step with its timing (opened, pair requested, status changes, prompt
  answered, result). Steps are journaled to `/data/android_auto/logs/pairing.jsonl` (64 KB cap).
- **Tags**: `aa.report`, `aa.outcome`, `aa.transport`, `aa.head_unit`, `aa.car`, `aa.resolution`,
  `aa.fps`, `aa.encoder`, `aa.stage`, `aa.error_kind`, `aa.pairing`, plus the device, branch,
  commit and dongle ID crash reports already carry.
- **Timing**: checked every minute; sent only while offroad and nothing is projecting. State
  (`/data/android_auto/bugsink_report.json`) is saved only for reports the SDK accepted, so the
  rest are retried.
- **Privacy**: Bluetooth MAC and IP addresses are scrubbed from error text; the car's Bluetooth
  and display names are left out. Wi-Fi credentials never appear in session logs.
- **No opt-out yet**: every device with Android Auto enabled reports, like crash reports.
  AAComma had an on-by-default "share diagnostics" switch; adding one needs a new Params key and
  `./build --params 4`.

## How well this fits Bugsink

**Good fit**
- Failures: one issue per kind of failure, events from every device that hits it, tags showing
  which head units and cars are affected.
- Pairing: one issue per outcome, each event holding the step timeline.

**Poor fit, to fix before shipping**
1. **Mixed in with crashes.** The reports reuse the SELFDRIVE DSN (project 1, firestar's crash
   project). Bugsink caps the events it keeps per project, so a report per drive per device can
   push real crash events out, a cost to firestar, not just clutter.
2. **Stale drive issue titles.** The drive message includes outcome and fps, but Bugsink names an
   issue from its first event, so the title keeps the first drive's numbers. It should be fixed,
   e.g. `Android Auto drives: Alpine HU-1 (wireless)`, with the numbers in tags and contexts.
3. **Bugsink is not a metrics tool.** fps per drive is readable only one event at a time: no
   fps-over-time chart, no cross-device average, no "head units below 25 fps" view. Drive issues
   never really resolve; they sit open or get muted.

## Recommended next steps

- Keep failures and pairing in Bugsink, ideally in their own "Android Auto" project. That needs
  a new project and DSN on `bugsink.firestar.link` (firestar creates it) or a self-hosted Bugsink.
- Move drive reports and fps to the Influx telemetry worker Stardust already reports to
  (`upstreamtel.didesigns.fyi`), where fps per drive, resolution and time to first frame can be
  charted and filtered, as `telemetry.didesigns.fyi` did on AAComma.
- Fix the drive titles either way.

## Tests

```sh
./dev python -m pytest -q -o addopts= openpilot/starpilot/system/android_auto/tests/test_bugsink_report.py \
  "openpilot/system/tests/test_sentry.py::test_report_carries_fingerprint_tags_and_contexts_without_leaking_scope"
```

As of 2026-10-16, six `test_native_apport_keeps_local_copy_and_queues_header_stack_qlog` cases in
`test_sentry.py` and `test_usb_accessory.py::test_supervisor_wired_session_end_to_end` fail on
an untouched Stardust HEAD too; they are not caused by this change. Not yet run on a device or
against the live Bugsink server.
