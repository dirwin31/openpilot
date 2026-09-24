# Wireless Android Auto

The comma presents itself to the car as an Android phone and projects wireless
Android Auto onto the car's screen. The car shows either a car-sized StarPilot
UI (with touch) or a mirror of the comma's own screen. Nothing here touches
vehicle control.

**Status:** working on a 2026 Honda Civic (Alps Alpine `8A501-T20-A1` head unit,
Android Auto protocol 4.1). The car negotiates 1280×720; the comma streams
hardware H.264 at 27–30 fps with about 70 ms frame age (p95). Other cars are
untested.

## Requirements

- A car whose head unit supports **wireless** Android Auto. Wired-only head units will not work.
- A comma 3X/four with Bluetooth enabled (`android_autod` runs whenever Bluetooth is on).
- A Mac or Linux computer, once, to extract the phone identity:
  - `jadx` (`brew install jadx`, which also installs OpenJDK)
  - Android SDK `apksigner` (from `build-tools/<version>/`)
  - Python with `cryptography`
- The Android Auto app **17.6.663454-release** as an XAPK or APK from an APK mirror.
  Make sure you get the app itself; some mirrors' big download buttons hand you their store installer instead.

## 1. Extract the phone identity

The car only accepts a phone that presents Google's Android Auto phone
certificate. It is embedded in the Android Auto app, so each user extracts it
from their own copy. **Never commit or share the output.**

```bash
mkdir -p .cache/android_auto && cd .cache/android_auto   # .cache/ is git-ignored
unzip -o ~/Downloads/android-auto-17-6-663454-release.xapk com.google.android.projection.gearhead.apk -d xapk
export JAVA_HOME=/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home   # macOS/Homebrew
$ANDROID_SDK/build-tools/<version>/apksigner verify --print-certs xapk/com.google.android.projection.gearhead.apk | grep "SHA-256"
```

The signer digest must be Google's:
`1ca8dcc0bed3cbd872d2cb791200c0292ca9975768a82d676b8b424fb65b5295`.
If it isn't, delete the file; it is not the real app.

Then run the importer from the repository root:

```bash
python tools/android_auto/import_identity.py \
  --apk .cache/android_auto/xapk/com.google.android.projection.gearhead.apk \
  --apksigner $ANDROID_SDK/build-tools/<version>/apksigner
```

It writes `phone-cert.pem`, `phone-key.pem`, `root-cert.pem` and
`provenance.json` to `.cache/android_auto/identity/` and checks that the key
matches the certificate and that the certificate chains to the root.

### If the importer refuses a genuine APK

The importer pins one exact APK hash and expects apksigner's `Signer #1` output
format. Mirrors often serve a different build of the same version, signed only
with a v3 signature (`V3.0 Signer: …`), and the importer rejects it. If you
verified the Google signer digest above, extract the identity directly:

```bash
APK=.cache/android_auto/xapk/com.google.android.projection.gearhead.apk
OUT=$(mktemp -d)
for c in jcf jch; do
  jadx --no-res --no-replace-consts --log-level error --single-class defpackage.$c \
       --single-class-output $OUT/$c.java $APK
done
python - "$OUT" <<'EOF'
import json, os, sys
from pathlib import Path
sys.path.insert(0, "tools/android_auto")
import import_identity as imp
src = Path(sys.argv[1])
files, meta = imp.recover((src / "jcf.java").read_text(), (src / "jch.java").read_text())
out = Path(".cache/android_auto/identity")
out.mkdir(mode=0o700)
for name, content in files.items():
  with os.fdopen(os.open(out / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
    f.write(content)
print(json.dumps({k: meta[k] for k in ("subject", "expires", "key_matches")}, indent=2))
EOF
rm -rf "$OUT"
```

The class names `jcf`/`jch` are specific to 17.6.663454; other versions need the importer updated.

The 17.6.663454 certificate **expires 2026-12-23**. From 14 days before that,
Android Auto status shows a warning; re-import from a newer app version before
it expires.

## 2. Install the identity on the comma

```bash
cd .cache/android_auto/identity
COPYFILE_DISABLE=1 tar -cf - phone-cert.pem phone-key.pem root-cert.pem provenance.json | \
  ssh comma@<comma-ip> 'umask 077 && mkdir -p /data/android_auto/identity && \
    tar -xf - -C /data/android_auto/identity && chmod 700 /data/android_auto /data/android_auto/identity && \
    chmod 600 /data/android_auto/identity/*'
```

`COPYFILE_DISABLE=1` stops macOS adding `._*` metadata files. The key must be
owned by `comma` and not readable by anyone else, or the identity is rejected.

Check that it loads:

```bash
ssh comma@<comma-ip> 'cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python -c "
from openpilot.starpilot.system.android_auto.identity import load_identity
i = load_identity(); print(i.expires, i.days_left, \"days\")"'
```

## 3. Pair the car

Bluetooth pairing and scanning only work while the comma is **offroad**, but
the car's screen only works with the car on, which normally puts the comma
onroad. Force it offroad while parked:

1. Car on, parked. On the comma: **Settings → System**, set to **Offroad**.
2. **Settings → Bluetooth → android auto → pair a new car.**
3. On the car: Bluetooth / phone settings → add a new device. Pick the comma in the comma's list and confirm the code on both screens.
4. **android auto → choose car**, and pick the car. It is marked "(android auto)" if it advertises wireless Android Auto.

## 4. Start projection

**Settings → Bluetooth → android auto → start.**

The comma connects over Bluetooth, the car sends its Wi-Fi hotspot details, the
comma joins that hotspot (it leaves any other Wi-Fi; cellular stays up), and
projection starts over TCP. The status line reads
`projecting / car layout / <fps> fps` once video is flowing. Getting there
usually takes 30–60 s. The service retries on its own with backoff until you
press **stop**.

In the same menu:

- **mirror comma screen / use car layout** switches what the car shows, from the next session.
- **show last error** shows why the last attempt failed.

**Touch.** In the car layout, car-screen touches control the StarPilot UI, but
only while offroad; onroad they are ignored by design. That includes a comma
forced onroad with the **Onroad** switch in Settings → System, which the car
screen can then not undo; set it back to **Auto** on the comma. Mirror view has
no touch.

## Configuration

`/data/android_auto/config.json`, written when you choose a car. Edit with the
service idle; changes apply from the next session.

| Key | Default | Meaning |
|---|---|---|
| `view` | `"car"` | `"car"`: car-sized StarPilot UI; `"mirror"`: copy of the comma screen |
| `encoder` | `"auto"` | `"auto"`: hardware H.264, falling back to libx264; `"hardware"` / `"software"` to force |
| `fps` | `0` | `0` = automatic (30 hardware, 15 software); otherwise a cap, 5–30 |
| `bitrate_kbps` | `6000` | 1000–12000 |
| `verify_head_unit` | `true` | verify the car's certificate against `root-cert.pem` |
| `rfcomm_channel` | `0` | `0` = discover over SDP; set only to work around a broken SDP record |
| `phone_class` | `true` | present as a phone (HFP gateway, smartphone Class of Device) while pairing/projecting |
| `wifi_interface` | `"wlan0"` | interface used to join the car's hotspot |
| `device_name` | `"StarPilot"` | name shown to the car |

## Logs

Everything is under `/data/android_auto/logs/` on the comma:

- `session-YYYYMMDD-HHMMSS.jsonl`: one file per **start**, covering every retry until **stop**. One JSON object per line, UTC timestamps. The newest 20 are kept.
- `car_ui.log`: output of the car-layout renderer for the current session (startup, crashes).

Readable timeline of the latest session, without the hands-free chatter:

```bash
ssh comma@<comma-ip> 'cat $(ls -t /data/android_auto/logs/session-*.jsonl | head -1)' | python3 -c '
import json, sys
for line in sys.stdin:
    r = json.loads(line); t = r.pop("t")[11:19]; e = r.pop("event")
    if not e.startswith("hfp"):
        print(t, e, json.dumps(r)[:240])'
```

Events worth knowing:

| Event | Meaning |
|---|---|
| `stage` | progress: `connecting_bluetooth` → `discovering` → `rfcomm` → `wifi_start` → `connecting_tcp` → `authenticating` → `negotiating` → `streaming` |
| `bootstrap_version` | car make/model/head unit and its wireless protocol version |
| `bootstrap_credentials` / `wifi_joined` | car hotspot SSID and the comma's address on it (the key is never logged) |
| `version` | projection protocol the car asked for and the comma's reply (e.g. `4.1` → `6.1`) |
| `head_unit_verified` | the car's certificate subject |
| `video_setup` | negotiated resolution and frame rate |
| `video_focus` | `focus 1` = car shows projection, `focus 2` = car shows its own screen |
| `stats` | every 30 s: fps, frames sent/acked, encode time, frame age, touch events |
| `attempt_failed` | why an attempt failed and at which stage; a retry follows |
| `session_ended` | the car ended projection |

The service's live status (the same data the settings menu shows):

```bash
ssh comma@<comma-ip> 'cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python -c "
from openpilot.starpilot.system.android_auto.protocol import AndroidAutoClient
import json; print(json.dumps(AndroidAutoClient().status(), indent=2, default=str))"'
```

After editing Android Auto code on the comma, restart the service; the manager starts it again:

```bash
ssh comma@<comma-ip> 'pkill -TERM -f "^starpilot.system.android_auto.daemon$"'
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| **pair a new car** missing, **scan for devices** does nothing | The comma is onroad. Use the **Offroad** switch (step 3). |
| Car-screen touches do nothing | The comma is onroad (maybe forced **Onroad**). Set Settings → System to **Auto**. |
| `Android Auto identity missing` / `is unusable` | Step 2; check owner `comma` and mode `600` on `phone-key.pem`. |
| `waiting for car: wifi_start: head unit did not answer` | The car did not start Wi-Fi. The comma asks it to after 5 s; occasional misses retry on their own. If it never succeeds, delete the comma on the car and pair again: the car can remember an earlier failure. |
| Car says the device is not compatible / connect a phone with the latest OS | The comma dropped the connection during setup. Check `attempt_failed` in the log. |
| Car shows Android Auto briefly, then its own screen | Look at `video_focus`: repeated `focus 1` then `focus 2` about 3 s later means the car is not getting decodable video. |
| `projecting: timed out` or `Video acknowledgement older than 1.5 s` | The Wi-Fi link to the car stalled. The service reconnects automatically. |
| Status shows `mirror (car view failed: …)` | The car layout failed to start and the view fell back to mirror; see `car_ui.log`. |
| `hfp_closed … Connection reset by peer` about every second | The car keeps dropping the hands-free link. Known; it has not blocked projection. |

## Testing without the car

Google's **Desktop Head Unit** (Android SDK → `extras/google/auto/desktop-head-unit`)
accepts the same identity. It negotiates 800×480 and protocol 1.7, so it does
not exercise everything a real car does.

- **Protocol only, on the computer:**
  `python tools/android_auto/dhu_test.py --dhu <path>/desktop-head-unit --identity .cache/android_auto/identity`
- **The comma's real pipeline** (car layout, hardware encoder, touch), with the DHU on the computer:

  ```bash
  # comma: stop Android Auto in settings first
  cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python tools/android_auto/dhu_device.py --view car
  # computer
  ssh -N -L 5288:127.0.0.1:5288 comma@<comma-ip>
  cd <sdk>/extras/google/auto && ./desktop-head-unit --adb=127.0.0.1:5288
  ```

  Use the venv Python on the comma; with the system `python3` the car layout
  cannot find `pyray` and falls back to mirror. The DHU console accepts
  `focus video toggle` (take the screen away and back), `tap x y` and
  `screenshot <file>`.

## Known limitations

- Video and touch only: no audio, microphone, calls or navigation data are projected.
- Test sessions so far have lasted about a minute before a reconnect.
- Tested on one car (2026 Honda Civic).
- The identity comes from one app version and expires with it.
