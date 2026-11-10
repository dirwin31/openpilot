# Heatdeath

`./heatdeath` exports the joined replay fixture used during StarPilot Controls CPU optimization. It runs actual Card, SelfdriveD, Controls, RadarD and planner processes, feeds recorded CAN and peer messages at their recorded cadence, and captures bounded timing and native-safety evidence. It changes only owned benchmark processes' affinity. It does not change production scheduling, IRQ affinity, CPU governors, online cores, installed preferences or source files.

This export retains the existing **active-long Ioniq 6 steady-state fixture** and also accepts **active-long Honda Civic 2022** recordings, whose checkpoint restores only the already-initialized car interface. It is a CPU-placement benchmark, not a general vehicle replay test. It explicitly restores the recorded remembered cruise speed and post-ECU-handoff initialization phase; it never restores an armed latch, command, native permission or healthy receipt. Discovery, physical ECU handoff and startup performance are excluded. Native permission must be earned by fresh replay inputs.

## Requirements

- A parked, offroad Linux ARM64 comma bench device with the normal compiled StarPilot dependencies and development compiler installed. Build the checkout normally before preparation. Execution requires root for the production realtime setup and private namespaces.
- A clean source checkout. A bundle is pinned to that exact revision and source hashes. Prepare/build a separate bundle when comparing a different revision.
- Local full rlogs, including startup CarParams, from a compatible Ioniq 6 or Honda Civic 2022 active-long drive. Recordings and compiled binaries are not bundled in Git.
- An explicit JSON preference snapshot, mapping parameter names to base64 bytes. Provide the actual intended benchmark settings, including `OpenpilotEnabledToggle=1` and `IsOffroad=0` for the **private** Params namespace. Production readiness/CarParams keys cannot be supplied. No physical device preferences are changed.
- Explicit source clock context: the source boot-minus-monotonic offset and whether `deviceMotion` timestamps use monotonic or boottime. Do not infer this from your current device clock.

The root command selects `/usr/local/venv/bin/python3` when installed; override with `HEATDEATH_PYTHON`. Run it directly, outside `./dev`, which can enable simulation environment variables. `./heatdeath --help` works without native dependencies or root.

## Prepare and build

Use a 60-second window with two clean, stable cruise-speed samples immediately before its start. Times below are examples; choose the timestamps from your recording. Include startup and any adjacent rlogs needed to cover the window.

```sh
./heatdeath prepare \
  --rlog /data/bench/startup/rlog.zst \
  --rlog /data/bench/window/rlog.zst \
  --start-mono-ns 220000000000 --duration 60 \
  --source-boot-minus-mono-ns 0 --device-motion-clock monotonic \
  --params-json /data/bench/params.json \
  --output /data/bench/heatdeath-bundle

sudo ./heatdeath build --bundle /data/bench/heatdeath-bundle
```

Use zero boot offset only when verified appropriate for the recording. The bundle retains input hashes, actual CarParams, source/helper hashes and the explicit checkpoint. Build compiles a virtual transport around current production pandad and its safety test library. USB/SPI implementations are not linked; it cannot send commands to the vehicle. Build output remains in the external bundle, with a receipt and logs.

## Compare CPU placements

First capture the production placement baseline, then change only the actor placements being compared. Run outputs must be new directories outside the checkout and bundle.

```sh
sudo ./heatdeath run --bundle /data/bench/heatdeath-bundle \
  --output /data/bench/baseline-1

sudo ./heatdeath run --bundle /data/bench/heatdeath-bundle \
  --placement '{"card":[4],"controlsd":[6],"selfdrived":[5],"radard":[3,5],"plannerd":[3,5],"peer":[1],"observer":[0,1]}' \
  --native-core2 --reserve-core 6 --reserve-ms 8 --reserve-period-ms 50 \
  --output /data/bench/placement-1

./heatdeath report --output /data/bench/placement-1 --skip-seconds 10
```

Actor names in `--placement` are optional: omitted actors retain their original fixture/production placement. Priority remains the current production policy. `--native-core2` changes only the virtual native endpoint's placement after validating its actual startup thread state; it does not move physical pandad. CPU choices must already be online and allowed. No cores are enabled automatically.

The reserve is an **optional synthetic CFS CPU workload**, with measured dose and missed-pulse reporting; `--reserve-nice` sets its nice level. It is not inference, soundd or Android Auto. A clean baseline and a loaded run answer different questions. Use repeated baseline/candidate/candidate/baseline runs with identical inputs, reserve dose, peer/native placement, thermals and power state to compare distributions. Do not infer a scheduler improvement from one favorable sample.

## Interpret results

`report` shows phase call rate, wall-time p50/p95/p99/max, thread CPU p95, largest start gap, rolling lag flags, native receipt and active-authority observations. Raw binary capture, hashes, CPU/scheduler records, source-feed lateness, child logs and timing rings stay in the run directory. Raw observations can also be decoded using the bundled driver's `decode-observations` command.

A completed command is not a headroom or steering pass. Check native RX/TX admission, sustained actual active output, source-feed completeness/lateness, source ages, lag episodes and scheduling records together. The fixture refuses unsupported/mixed CarParams and stale native/authority evidence; do not disable those checks to obtain prettier timings. Timing rings that overflow are rejected by the report.

Recorded model and driver-monitoring messages are inputs. Actual model inference, camera decoding, audio and Android Auto are not launched by this harness. This benchmark cannot alone establish real co-load headroom, ECU acceptance, physical firmware-heartbeat equivalence, or road behavior. The bounded observer and virtual endpoint themselves consume CPU and RAM; include their cost.

Runs stop if the physical device leaves offroad/disengaged state and have a 180-second supervisor bound. Cleanup targets only actors carrying that run's unique token and only the journaled private message namespace. A per-device lock prevents concurrent Heatdeath runs/builds. It does not coordinate other developers' unrelated bench harnesses; keep the test device exclusively assigned while measuring. Interrupt normally with Ctrl-C; do not delete another active run's files.

## Worked example: Honda Civic 2022 on a parked comma

This is the sequence used on a comma mici for route `000000ab--33dc3445cf`. The device checkout stays untouched: the tool runs from a copy outside it.

1. Copy the tool next to the bench data and snapshot the car's settings into a private file. `IsOffroad` is forced to `0` only inside that file.

   ```sh
   mkdir -p /data/bench/hd-tool && cp tools/heatdeath/*.py tools/heatdeath/*.cc /data/bench/hd-tool/
   cd /data/openpilot && source /usr/local/venv/bin/activate
   PYTHONPATH=/data/openpilot:/data/openpilot/openpilot python3 - <<'PY'
   import base64, json, os
   from openpilot.common.params import Params
   skip = {'CarParams', 'CarParamsCache', 'CarParamsPersistent', 'CarParamsPrevRoute', 'ControlsReady', 'FirmwareQueryDone', 'PandaSignatures', 'PandaDongleId'}
   out = {k: base64.b64encode(open('/data/params/d/' + k, 'rb').read()).decode() for k in os.listdir('/data/params/d') if k not in skip and not k.startswith('.')}
   out['IsOffroad'] = base64.b64encode(b'0').decode()
   fd = os.open('/data/bench/params.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
   os.write(fd, json.dumps(out).encode())
   PY
   ```

2. Pick a window that starts disengaged with cruise available and a remembered set speed, and engages a few seconds later. Keep it under 60 seconds, or a timing ring overflows and `report` refuses the run. Include the route's first rlog for CarParams.

   ```sh
   R=/data/media/0/realdata/000000ab--33dc3445cf
   PYTHONPATH=/data/openpilot:/data/openpilot/openpilot python3 -B /data/bench/hd-tool/heatdeath.py prepare --repo /data/openpilot \
     --rlog $R--0/rlog.zst --rlog $R--10/rlog.zst --rlog $R--11/rlog.zst \
     --start-mono-ns 665000000000 --duration 58 \
     --source-boot-minus-mono-ns 0 --device-motion-clock monotonic \
     --params-json /data/bench/params.json --output /data/bench/civic58
   ```

3. Offroad, the device parks cores 4-7 and uses the `ondemand` governor. Heatdeath does not change that, so apply the onroad CPU settings for the run and put power-save back afterwards.

   ```sh
   for i in 4 5 6 7; do echo 1 | sudo tee /sys/devices/system/cpu/cpu$i/online; done
   for n in 0 4; do
     echo performance | sudo tee /sys/devices/system/cpu/cpufreq/policy$n/scaling_governor
     echo 1689600 | sudo tee /sys/devices/system/cpu/cpufreq/policy$n/scaling_max_freq
   done
   # ... build and run ...
   echo ondemand | sudo tee /sys/devices/system/cpu/cpufreq/policy0/scaling_governor
   for i in 4 5 6 7; do echo 0 | sudo tee /sys/devices/system/cpu/cpu$i/online; done
   ```

4. Build and run as root. Root does not own the checkout, so trust it for these commands only.

   ```sh
   hd() { sudo env -i PATH=$PATH HOME=/root GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/data/openpilot \
     /usr/local/venv/bin/python3 -B /data/bench/hd-tool/heatdeath.py "$@"; }
   hd build --bundle /data/bench/civic58
   hd run --bundle /data/bench/civic58 --output /data/bench/base1
   hd run --bundle /data/bench/civic58 --output /data/bench/core6 \
     --reserve-core 6 --reserve-ms 2 --reserve-period-ms 10 --reserve-nice -5
   hd report --output /data/bench/core6 --skip-seconds 20
   ```

## Music-chain probe

`audio_probe.py` answers what Heatdeath alone cannot: whether music survives the driving load. One run starts a private copy of the shipped audio server (same binary, flags, resampler and nice level; your live one is untouched), feeds it a constant tone through the phone relay's client call, and reads the pipe with StarPort's own `PipeSource` on core 6. Alongside it runs one Heatdeath replay and, with `--road`, stand-in loads for the other road processes, each with its real scheduling class, cores and average CPU from route `000000ab` segments 11-19.

```sh
cp tools/heatdeath/audio_probe.py /data/bench/hd-tool/
# with the onroad CPU settings applied as above:
sudo env -i PATH=$PATH HOME=/root /usr/local/venv/bin/python3 -B /data/bench/hd-tool/audio_probe.py run \
  --repo /data/openpilot --bundle /data/bench/civic58 --output /data/bench/p-road --road --config new
```

It prints and saves `probe.json`:

- `silenceMs` / `silenceEvents`: tone samples that came out silent, i.e. audible dropouts.
- `latenessMs`: how far delivery to the reader falls behind real time, which is what the head unit's queue has to absorb.
- `overruns`, `pipeQueuedMaxMs`: sound discarded by, or waiting in, the pipe.
- `coreBusyPct`: compare with the road to judge the stand-in loads.
- `controls`, `lagFlags`: Heatdeath's controls timing from the same run.

`--config old` uses the priorities from before the audio server was raised to nice -5. `--rate 48000` skips resampling, as phone passthrough does. `--control` pauses the tone source for 0.6 s; run it once and expect several hundred milliseconds of silence, otherwise a zero from the other runs means nothing.

Limits: the stand-in loads match average occupancy, not each process's burst pattern. The head unit, Wi-Fi, video encoding and Spotify's decoder are not in the loop, and Spotify's stream is assumed to be 44.1 kHz.
