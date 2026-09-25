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

- A car whose head unit supports **wireless** Android Auto. (A wired USB mode exists but is
  experimental and untested in a car; see step 3.)
- A comma 3X/four with Bluetooth enabled (`android_autod` runs whenever Bluetooth is on).
- The **Android Auto** app as an XAPK, APK or APKM file, downloaded in a phone or
  computer browser from an APK mirror. `17.6.663454-release` is known to work.
  Make sure you get the app itself; some mirrors' big download buttons hand you
  their own store installer instead.

## 1. Install the phone identity

The car only accepts a phone that presents Google's Android Auto phone
certificate and its key. They are embedded in the Android Auto app, so each user
extracts them from their own copy. The comma does this itself:

1. Open The Galaxy and go to **Vehicle Controls → Android Auto Identity**.
2. Tap **Install from File** and pick the Android Auto file you downloaded.

The comma finds the certificate and the encrypted key in the app's code,
decrypts the key, and accepts the result only if the key matches the
certificate, the certificate is issued by Google's Automotive Link root (pinned
by fingerprint), and both are currently valid. It then installs the identity to
`/data/android_auto/identity/` (readable only by the `comma` user) and deletes
the uploaded file. A modified or wrong file is rejected and changes nothing.
This takes a few seconds after the upload.

**Or have the comma download it from a link** does the same from a direct link
to the file, e.g. in your own cloud storage. Mirror pages that need a browser
(Cloudflare checks, download buttons built by JavaScript) will not work there.

The card shows the certificate's expiry date. Nothing needs restarting: the
Android Auto service reads the identity each time a session starts.

**Never commit or share the identity.** It is Google's key.

### Renewing

The 17.6.663454 certificate **expires 2026-12-23**. From 14 days before, The
Galaxy card and the device's Android Auto status warn; after that Android Auto
stops connecting. Download a newer Android Auto version and use **Renew from
File** on the same card. The previous identity is kept in
`/data/android_auto/identity.previous/`.

If a future app version stores its key differently, the import says so and
leaves the current identity in place; use a version that works.

### On a computer instead

For testing with the Desktop Head Unit, or to install by hand, the same
extraction runs on a computer with Python and `cryptography`:

```bash
python tools/android_auto/import_identity.py --apk ~/Downloads/android-auto.xapk
# writes .cache/android_auto/identity/ (git-ignored)
cd .cache/android_auto/identity
COPYFILE_DISABLE=1 tar -cf - phone-cert.pem phone-key.pem root-cert.pem provenance.json | \
  ssh comma@<comma-ip> 'umask 077 && mkdir -p /data/android_auto/identity && \
    tar -xf - -C /data/android_auto/identity && chmod 700 /data/android_auto /data/android_auto/identity && \
    chmod 600 /data/android_auto/identity/*'
```

`COPYFILE_DISABLE=1` stops macOS adding `._*` metadata files.

## 2. Pair the car

Bluetooth pairing and scanning only work while the comma is **offroad**, but
the car's screen only works with the car on, which normally puts the comma
onroad. Force it offroad while parked:

1. Car on, parked. On the comma: **Settings → System**, set to **Offroad**.
2. **Settings → Bluetooth → android auto → pair a new car.**
3. On the car: Bluetooth / phone settings → add a new device. Pick the comma in the comma's list and confirm the code on both screens.
4. **android auto → choose car**, and pick the car. It is marked "(android auto)" if it advertises wireless Android Auto.

## 3. Start projection

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
- **use wired (usb) / use wireless** (while stopped) switches to projecting over a USB cable from the
  car to the comma's USB-C port, with no pairing or Wi-Fi. Experimental: not yet tested in a car.

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
| `connection` | `"wireless"` | `"wireless"`: Bluetooth + the car's Wi-Fi; `"wired"`: USB (experimental) |
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
- `car_ui.log`: output of the car-layout renderer for the current session (startup, crashes, and `render_stats` every 10 seconds while drawing).

`render_stats` reports produced FPS and average per-frame `update_ms`, `map_ms`,
`draw_ms`, and `readback_ms`. These are CPU wall times; GPU work submitted during
drawing can finish during readback and be counted there. Compare these with the
session's sent `fps`, `encode_ms`, `frame_age_p95_ms`, and receiver `pending` count
to distinguish rendering delays from encoding or delivery delays. The Bluetooth
panel shows sent FPS over five seconds, including startup time in that window.
While the car shows its own screen, frame production pauses and the renderer stays
loaded until the session ends.

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
| **pair a new car** missing, **scan for devices** does nothing | The comma is onroad. Use the **Offroad** switch (step 2). |
| Car-screen touches do nothing | The comma is onroad (maybe forced **Onroad**). Set Settings → System to **Auto**. |
| `Android Auto identity missing` / `expired` / `is unusable` | Install or renew it in The Galaxy → Vehicle Controls → Android Auto Identity (step 1). |
| The Galaxy says *does not contain the Android Auto identity* | The file is not the Android Auto app (often a mirror's store installer). Download the app itself. |
| The Galaxy says *stores its key differently* | That app version is not supported yet; use 17.6.663454 or another version that works. |
| `waiting for car: wifi_start: head unit did not answer` | The car did not start Wi-Fi. The comma asks it to after 5 s; occasional misses retry on their own. If it never succeeds, delete the comma on the car and pair again: the car can remember an earlier failure. |
| Car says the device is not compatible / connect a phone with the latest OS | The comma dropped the connection during setup. Check `attempt_failed` in the log. |
| Car shows Android Auto briefly, then its own screen | Look at `video_focus`: repeated `focus 1` then `focus 2` about 3 s later means the car is not getting decodable video. |
| `projecting: timed out` or `Video acknowledgement older than 1.5 s` | The Wi-Fi link to the car stalled. The service reconnects automatically. |
| Status shows `mirror (car view failed: …)` | The car layout failed to start and the view fell back to mirror; see `car_ui.log`. |
| `hfp_closed … Connection reset by peer` about every second | The car keeps dropping the hands-free link. Known; it has not blocked projection. |

## Testing without the car

Google's **Desktop Head Unit** shows the comma's projection on a computer, no car
needed: see [android-auto-desktop-head-unit.md](android-auto-desktop-head-unit.md).
It does not test Bluetooth pairing, the Wi-Fi handoff or car-specific behavior.

## Known limitations

- Video and touch only: no audio, microphone, calls or navigation data are projected.
- In the car, sessions so far have lasted about a minute before a reconnect (the service reconnects
  on its own); with the Desktop Head Unit they run for 15+ minutes.
- Tested on one car (2026 Honda Civic).
- The identity comes from the Android Auto app and expires with it; renew it from a newer app version in The Galaxy.
