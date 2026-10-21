# BlueALSA 5.0.0 for StarPilot music

This is a checkout-local Linux/ARM64 bundle of upstream BlueALSA **5.0.0**, commit
`1935d6dcb8975f2d7a51aaafe61538d157224623`. It replaces the abandoned 4.1.1 SBC
concealment experiment. There are **no custom decoder patches** in this bundle.

`bin/bluealsad`, `bin/bluealsactl`, `bin/bluealsa-aplay`, and both ALSA plugins in
`lib/` must be committed together with `VERSION`, `UPSTREAM_COMMIT`, and
`SHA256SUMS`. The feature validates checksums and ARM64 ELF headers before
activating the daemon. Build scratch files under `build/` are ignored.

## Audio integration

The phone profile helper temporarily selects `bluealsad` through the existing
`bluealsa.service`. Both phone capture and Spotify/Bluetooth output use explicit
paths to the bundled PCM/CTL plugins in their process-local ALSA configuration.
No global plugin search path, ALSA config, D-Bus policy, or system binary changes.
The D-Bus service remains `org.bluealsa`; the bundled plugins and tool use its
5.0 `Rate` property instead of 4.1's `Sampling`. No application code reads that
property directly. Existing diagnostics still query the same systemd unit.

Phone capture enables `hwcompat "silence"` to maintain capture across transport
inactivity and short input interruptions. It inserts silence; it does not
reconstruct missing music or eliminate interference. Playback leaves `hwcompat`
at its default so a disconnected output is not silently consumed. Both paths
explicitly set `softvol "true"` to preserve the previous software-volume mode
instead of adopting 5.0's new native-volume default. Initial device volume state
is separate from 4.1; the user's existing 4.1 state is never rewritten.

The build retains SBC reception, SBC/LDAC transmission, and oFono support. The
launcher explicitly enables SBC and LDAC. AAC, Opus, LHDC, and ASHA are not enabled
by this upgrade. `bluealsa-aplay` is built with adaptive resampling available,
but the `arecord` relay does not use it. The existing `Nice=-5` policy is retained;
no real-time scheduling priority is added over driving processes.

5.0 persistent state is compiled to `/data/starpilot/bluealsa5/lib/bluealsa`.
The launcher creates its parent before starting the daemon. This isolates it
from `/var/lib/bluealsa`, preserving 4.1 volume/mute state for rollback.

## Rollback contract

**Guaranteed rollback: check out the old commit, then reboot.** `/run` is cleared at
boot, so the stock 4.1.1 daemon returns with no further steps. Restarting
music/openpilot instead is best-effort: it works only if the old supervisor's
startup release runs (see below). Checking out files alone cannot replace a daemon
already running in memory.

* `/usr/bin/bluealsa`, system ALSA plugins, system service, and 4.1 persistent
  state are never overwritten.
* `90-starpilot-phone.conf` remains **byte-for-byte compatible with the old
  helper**. 5.0 selection is a separate `95-starpilot-bluealsa5.conf` in `/run`.
* The launcher is copied into `/run/systemd/system/` before activation. It is
  self-contained and remains available after Git removes the new checkout files.
* Checking out a pre-upgrade commit removes the tracked `VERSION` and bundle.
  On the old supervisor's normal startup, its existing release code removes
  `90-starpilot-phone.conf` and restarts the unit. The retained launcher sees the
  absent version marker, removes the 5.0 override and itself, reloads systemd,
  then execs the stock 4.1 daemon with the original source profile. Old capture
  and playback code generates configs using the system 4.1 plugins again.
* A reboot independently clears all `/run` overrides, including for much older
  commits without the phone-profile startup cleanup. No migration command is
  required for that rollback path.
* A failed activation removes the new override and restores the prior profile
  configuration before trying the system daemon again. Unknown overrides are
  refused rather than overwritten.

The integration test performs a real temporary Git checkout back to an older
commit, then runs the frozen pre-upgrade helper from `3434023608`. Service calls
are simulated so the test cannot change the host or a comma. It verifies the
resulting stock daemon command and removal of both temporary overrides/launcher.
This is local lifecycle verification, not an on-device audio acceptance test.

## Build and verification

Read `docs/how-to/laptop-device-build.md` first. This narrowly scoped bundle build
uses Linux/ARM64 Ubuntu 24.04 (AGNOS's userspace generation); it does not invoke
the full openpilot artifact build or deploy anything. The base image is pinned;
`BUILD_IMAGE`, `BUILD_PACKAGES.txt`, and `UPSTREAM_COMMIT` record build inputs.
Package versions are recorded, not guaranteed to remain available from apt.

```sh
git clone --branch v5.0.0 https://github.com/arkq/bluez-alsa.git /tmp/bluealsa-5
openpilot/starpilot/soloist/bluealsa/build.sh --setup
openpilot/starpilot/soloist/bluealsa/build.sh /tmp/bluealsa-5
.venv/bin/python -m pytest openpilot/starpilot/soloist/tests -q
```

The setup command downloads build dependencies. The build itself uses
`--network none --pull never`, exports only the pinned committed source, runs
upstream `make check`, builds matching daemon/plugins/tools, verifies ARM64 ELF
and dynamic dependencies in the build container, and writes checksums. It does
not build inside the mounted Git worktree, avoiding container ownership issues.

`UPSTREAM_TEST_RESULTS.txt` records the upstream suite (19 programs, zero skips).
This includes capture/playback PCM and each `hwcompat` mode. The local integration
suite covers bundle integrity, capture/playback config, profile ownership,
activation failure, Spotify output lifecycle, and old-commit rollback. The actual
packaged plugin was also loaded by `arecord` and `aplay` using generated StarPilot
configs in an isolated D-Bus session: it reached the expected absent-service error
without config or symbol errors. No physical device was used for these checks.

Device facts verified read-only on the comma (2026-10-21): `bluealsa.service` runs as
root with no `ProtectSystem`/`NoNewPrivileges`, so the launcher can write `/data`,
edit `/run/systemd/system` and `daemon-reload`; every bundled artifact resolves all
shared libraries (`ldd`, including LDAC and samplerate); the private v5 PCM plugin is
the one ALSA loads through the generated config (it overrides the system
`/etc/alsa/conf.d/20-bluealsa.conf` type). A v5 plugin talking to the stock 4.1
daemon fails (`Incorrect variant for 'Volume'`), which is why Spotify Bluetooth
output refuses to start (`audio_unavailable`) when v5 activation fails instead of
falling back to a mismatched pair. The v5 daemon itself has not yet been started on
the device.

Behavior changes to be aware of: while Spotify plays to a Bluetooth output the
daemon runs with the same `a2dp-sink` profile as phone reception, so the comma is
advertised as a music receiver; and a player restart releases and re-acquires the
profile, briefly restarting BlueALSA (and so Bluetooth music).

Remaining hardware acceptance after deployment: reconnect a phone, pause/resume,
exercise Android Auto and Bluetooth output, and confirm perceived dropout
behavior. Local tests do not establish audible smoothness or RF improvement.

Upstream references:
- https://github.com/arkq/bluez-alsa/blob/v5.0.0/NEWS.md
- https://github.com/arkq/bluez-alsa/blob/v5.0.0/doc/bluealsa-plugins.7.rst
- https://github.com/arkq/bluez-alsa/blob/v5.0.0/doc/bluealsad.8.rst
