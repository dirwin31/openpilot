# Check a drive on the bench

Everything needed to explain a "TAKE CONTROL IMMEDIATELY", a lag spike or a slow Android Auto
connection is written to the drive's **rlog**. rlogs survive unplugging the comma; at most the last
few seconds of a drive are lost. The swaglog files in `/data/log` don't reliably survive: they are
uploaded and then deleted.

## 1. Copy the drive off the comma

Plug the comma in on the bench and find the route. Routes are listed by the time their first segment
was written:

```bash
ssh comma 'cd /data/media/0/realdata && ls -dt *--0 | head -3'
```

Copy its rlogs into one tar (`<route>` is the name without `--0`):

```bash
ssh comma 'cd /data/media/0/realdata && tar cf - <route>--*/rlog.zst' > docs/Rlogs/<route>-logs.tar
```

For Android Auto problems, also copy that drive's session log:

```bash
scp 'comma:/data/android_auto/logs/session-*' docs/Rlogs/   # or just the newest one
```

`docs/Rlogs/` is excluded from git.

## 2. Read the report

```bash
uv run --no-project --with pycapnp --with zstandard \
  python tools/profiling/drive_report.py docs/Rlogs/<route>-logs.tar \
  --aa docs/Rlogs/session-NNNNNN-*.jsonl
```

This needs no openpilot build and doesn't need the comma. A 30-minute drive takes a few seconds.

## What the report shows

- **Timeline:** the report includes these, with wall-clock times:
  - Non-routine alerts.
  - selfdrived `commIssue` entries, listing which inputs were invalid or late.
  - `*InputsInvalid` events from calibrationd, locationd, torqued and lagd.
  - mapd tile changes and per-minute activity.
  - Process restarts.

  In an `*InputsInvalid` event, `age_ms` is how old the input was, and `max_loop_gap_ms` (torqued and
  lagd) is the longest pause in that daemon's own loop:
  - A **large loop gap** means the daemon itself was starved of CPU.
  - A **small loop gap with a large `carState` age** means the publisher (card) was late.
- **Kernel/system journal trouble:** out-of-memory kills, thermal throttling, GPU (kgsl) faults, hung
  tasks.
- **Per segment:** speed, CPU per core, free memory, mapd CPU and the top processes for each minute.
  Cores 4 and 5 run card, controlsd and selfdrived. When they reach about 90%, inputs start arriving
  late. mapd's CPU depends on the offline map tile the car is in: dense city tiles cost several times
  more.
- **Android Auto:** connection attempts, failures, reconnects and why the session ended.
