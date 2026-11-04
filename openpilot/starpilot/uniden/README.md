# Uniden setup and frequency rules

Pair an R4/R8 in Galaxy → Bluetooth → Add a device → Search, then open the
**Uniden** tab to select it and choose **Use this detector**. The model is
inferred from the paired device name. The Uniden page (`#/bluetooth/uniden`)
shows connection status, **Change detector**, and frequency rules. Settings require the
selected detector to remain paired; edits require Park.

The settings page controls the integration, automatic mute, and hiding matching
alerts. Add up to 32 frequency rules with a label, X/K/KA band, frequency in GHz,
and tolerance in MHz. Each rule can be disabled or removed. For a 34.313 GHz head
unit signal, use KA / 34.313 / 0 MHz to start. A tolerance of 2 MHz would match
34.311–34.315 GHz. Rules apply everywhere and cannot distinguish different
transmitters on the same frequency. They are stored in `UnidenConfig` on comma,
not in the detector's mute memory. Existing configurations gain disabled filter
defaults and an empty rule list.

## Mute delivery

While the large driving UI is active, the feed polls every 100 ms after the
previous exchange completes when integration is enabled (one second when off).
It continues even if the widget is disabled or covered by an openpilot alert.
Leaving the driving UI expires the feed after one second without a heartbeat;
this is not an always-running offroad detector service.

Both local Bluetooth and the StarPort owner receive the rules. The transport
matches the fresh detector snapshot and sends `BTreqMUTE:1` in that same exchange,
without another HTTP request or waiting for a rendered frame. Automatic mute
requires every active alert to match an enabled rule. Mixed/unmatched alerts,
including Laser and photo radar, prevent automatic mute. Manual tap-to-mute
retains its existing fresh-snapshot checks. Hidden rows remain in the raw
snapshot and mute identity.

The detector exposes a general mute command, not a per-signal mute. The code
never marks a signal muted merely because a write succeeded. Unacknowledged
automatic writes retry at most every 500 ms; a cleared or changed alert set is
eligible immediately. Bluetooth/session/pairing admission still applies.

100 ms is the polling delay, not a measured end-to-end latency guarantee.
Bluetooth reads and detector firmware add latency, and an initial beep may
occur before the alert is readable. Hardware validation should check both R4
and R8, including Android Auto: first-beep duration, confirmation of mute,
repeated alerts after a clear, and an unmatched signal appearing alongside a
filtered one. StarPilot does not send an unmute command when a new signal
appears; the detector's handling of that transition needs hardware validation.

## Lockouts, Quiet Ride and warnings

All of this lives on the comma. Nothing is written to route logs, there is no cereal message, and nothing is logged
through cloudlog. Lockouts and learned sources are kept in `/data/starpilot/uniden/locks.json`.

- **Hold to lock out.** Hold a live alert on the driving screen for about a second. It is locked out where you are, for
  X, K, Ka or Laser, within 300 m. If the car just passed the source, the lockout is placed at the pass point instead.
  A tap still mutes. Locked-out alerts follow *Auto-mute saved frequencies* and *Hide filtered alerts*. Remove them under
  Uniden → Saved lockouts; this works while driving.
- **Learned lockouts** (off by default). The R8's front → side → rear arrow flip, or an R4's strength peak, marks a fixed
  source at the position of the pass. The same source passed on separate trips at least 10 minutes apart becomes a lockout
  (3 passes for X and K, 5 for Ka and Laser). Choose which bands may be learned. A signal more than two levels stronger
  than any seen at that place is not locked out. Each drive-by of a known place where the signal never appears counts as a
  miss; a source is learned only if misses are at most half its passes, and a learned lockout whose misses outnumber its
  passes is removed. A source that really does repeat at one place, such as a regular patrol spot, can still be learned, so
  review Saved lockouts now and then. Unseen learned lockouts expire after 90 days.
- **Quiet Ride at the posted limit** (off by default). The comma sends mute while the car is at or under the posted limit plus
  a margin, for the chosen bands (X and K by default). The beep is muted and the alert stays on screen. It needs a fresh GPS fix
  and a valid limit; with either missing nothing is quieted. A new signal that appears at a strength of 5 or more is treated
  as sudden and is never quieted. Mute is sent only when every active alert qualifies.
- **Timed mute** (off by default). Each new X, K or Ka signal beeps normally for a set number of seconds (5 by default), then
  the comma sends mute. The alert stays on screen. Laser is never muted this way, and neither is a signal that appeared suddenly
  strong. If the strength reaches the chosen level (6 of 8 by default) the signal stays loud until it clears: the card says
  Strong signal and the frame lights up even though the detector is muted. *Unmute at that strength* additionally sends the
  detector's unmute (`BTreqMUTE:0`, from the Uniden app's constants and untested on hardware). It only undoes a mute the timer
  sent, never one the driver pressed, and tries at most three times per set of alerts. Try it parked before relying on it.
- **Full-screen warning.** A colored frame drawn on top of the engagement border (green, blue or grey stays visible around it) and any openpilot alert, for live Ka, K or Laser
  alerts that are not locked out, quiet or muted (Ka and Laser by default). Each band has its own color and style (solid, pulse, a chase
  that speeds up with signal strength, or corners). Pulse and chase hold still when reduce motion is on. Colors are limited to purple, violet, yellow and white, since openpilot already uses green, blue, cyan, pink, orange, red and grey.
- **Offline badge.** If the selected detector is not connected for 10 seconds while driving, a small DETECTOR OFFLINE badge
  replaces the hidden card. It also shows at the top of the view when the widget is off or an openpilot alert covers it.

Hardware validation still needed: whether `BTreqMUTE:0` actually unmutes an R4 or R8 and how quickly; whether a new signal after a mute (including Laser or a sudden Ka after a quiet mute)
sounds before the detector's own transition; the R4 strength-peak pinpoint on a real drive; and the R8 arrow flip on a
real fixed source.

## Checks

- `./dev pytest openpilot/starpilot/uniden/tests openpilot/starpilot/galaxy/tests/test_uniden_http.py -q`
- `node --test openpilot/starpilot/galaxy/tests/test_uniden.mjs`
- `node openpilot/starpilot/galaxy/tests/browser/uniden.cjs` (Playwright; supports
  `PLAYWRIGHT_MODULE` and `PLAYWRIGHT_EXECUTABLE_PATH` overrides).

## Research notes (2026-11-04)

- [Uniden's Ka Block explanation](https://uniden.com/blogs/articles/uniden-ka-block-update-r-series)
  documents a firmware-side Mute mode for a user-selected frequency range on
  R4/R4w/R8/R8w. It retains the visual alert without the tone. Where the installed
  firmware accepts the desired narrow range, this avoids waiting for Bluetooth
  mute delivery. This integration does not configure that setting; the writable
  settings format and the unit's supported range must first be verified.
- [R8 Companion's release notes](https://apps.apple.com/ca/app/r8-companion/id6461348120)
  acknowledge short sounds before a mute command arrives. Its alternative is
  zero detector volume with the app supplying alert tones. StarPilot does not
  currently supply replacement detector audio, so that workaround is not part
  of this implementation.
- The [R8w protocol reference](https://github.com/AegisX86/UnidenR8wlink/blob/main/PROTOCOL.md)
  reports full alert snapshots delivered on detection changes via BLE Notify.
  It documents mute/unmute commands but explicitly labels command writes
  untested on its reference hardware. It does not establish what a new
  simultaneous alert does after remote mute.
- [BlueZ GATT documentation](https://bluez.readthedocs.io/en/latest/gatt-api/)
  provides StartNotify/PropertiesChanged and AcquireNotify. A future persistent
  subscription inside the Bluetooth owner could process alerts without waiting
  for the polling interval. It must handle session expiry, owner handoff,
  disconnects, subscription cleanup, and fresh-snapshot ordering. The current
  code still uses ReadValue polling; no notification latency is claimed.
