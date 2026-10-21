# Spotify and phone audio

Galaxy → Spotify and Phone Audio supports two mutually exclusive music sources:
Spotify Connect (Soloist) and a paired phone's Bluetooth A2DP audio. Both use the
private PulseAudio output, either Android Auto's PCM media channel or an existing
BlueALSA Bluetooth output. Phone mode needs neither Soloist nor a Spotify API key.
The feature's existing enable flag starts `spotifyd` for either source; with the phone source it
never launches Soloist, and switching to it stops a running Soloist.

## Phone setup

1. While parked, enable Bluetooth and pair the phone from Galaxy's Bluetooth page.
2. Choose **Phone over Bluetooth**, then select the paired phone.
3. Choose **Android Auto** as the output and connect projection to the head unit,
   or choose a different paired Bluetooth speaker/head unit.
4. Turn on **Phone Audio**. Select the comma as the phone's media audio output,
   then play music. Use the phone and car for playback and volume.

Changing the source stops the previous player. Pairing, source/device selection,
and output changes retain Galaxy's existing session and parked/offroad checks.
Phone calls, microphone forwarding, head-unit metadata and steering-wheel track
controls are not implemented. AVRCP pause is attempted when Android Auto loses
media focus permanently; even if the phone cannot be paused, forwarding relinquishes
focus. The comma's warning speaker and global ALSA default are not changed.

## Companion apps

Pairing a phone in the Android or iPhone companion app also sets it up for phone
audio. The app asks the comma to open a short pairing window (Galaxy's **Let a
phone pair** button does the same). Only one phone can claim it, only phones
(Bluetooth device class) are accepted, and a confirmation code must be approved.
Afterward the phone appears as the music source if none was chosen yet. Android
bonds from the app; on iPhone the app asks you to tap the comma in
Settings → Bluetooth. The window refuses to open while Android Auto, another
pairing, or a search holds the adapter, and adapter visibility returns to what it
was before. The companion's own key pairing never creates a Bluetooth bond.

Installing Soloist is only allowed from the local network, the Galaxy hotspot or
the tunnel. Over the companion's Bluetooth link the page says so and sends nothing.
Phone audio itself needs no install and works over Bluetooth. The Android app and
the iPhone app each carry their own copy of the Galaxy pages, so they show this
page only after they are updated.

## Runtime behavior

The checkout bundles BlueALSA 5.0.0's daemon and matching PCM/CTL plugins for
phone reception and Bluetooth output. `phone_profile.py` uses temporary `/run`
overrides; the system's BlueALSA 4.1.1 installation stays intact. Phone capture
uses `hwcompat "silence"` for continuity across input interruptions. Private ALSA
configs select the matching plugins and explicitly preserve software volume.
See [the bundle documentation](bluealsa/README.md) for build inputs, tests, and
the old-commit rollback contract (restart music/openpilot or reboot after checkout).

Only the selected paired phone is captured. The existing trusted pairing policy
still determines which phones may establish Bluetooth connections; this feature
does not alter trust or erase bonds. `arecord` opens an ALSA plug PCM for that
phone, converting to signed 16-bit 48 kHz stereo. A separate Python worker writes
to the private server using AGNOS's `libpulse-simple.so.0`, with bounded buffers.
The supervisor retries disconnects without repeatedly restarting Bluetooth.
The versioned BlueALSA bundle and private PulseAudio server are included with the checkout.

## Validation

Automated tests cover source migration/validation, authenticated settings, parked
selection, no-Soloist startup, radio-off, disconnect/retry, temporary-profile
restoration, selected-phone AVRCP targeting, and clearing audio on source changes.
Run the Soloist, Galaxy Spotify setup, and Android Auto media-audio Python tests,
plus `node openpilot/starpilot/galaxy/tests/test_spotify.mjs`.

Device acceptance remains necessary: test Android/iPhone pairing and audio,
44.1/48 kHz tracks, radio focus loss/resume, phone disconnect/reconnect, Bluetooth
off/on, source switching, service restart and reboot. Test wireless Android Auto
and Bluetooth output separately for radio coexistence, latency and dropouts.

## Now playing on the car screen

Android Auto layouts include a **Now playing** card (Galaxy → Android Auto layout) that shows the title,
artist and progress of Spotify or of the phone's Bluetooth player, with a background opacity setting. The
supervisor publishes the snapshot in its status file; the car screen reads it once a second only while the
card is enabled, and the card stays hidden when nothing is playing or an alert is up. Phone metadata comes
from the phone's Bluetooth media player (BlueZ `MediaPlayer1`), so it depends on the phone sharing it.

## Spotify Connect on hotspots and cellular

Enable Spotify and pair once from Spotify's device picker on a shared local network
(home Wi-Fi, a phone hotspot, or the comma's Galaxy hotspot). Both local discovery
and device-to-device traffic must be allowed. A hotspot-hosting phone may restrict
this traffic; if initial pairing fails, use another shared Wi-Fi network. Wireless
Android Auto by itself is not a promise of local discovery or an internet uplink.

Soloist keeps its device identity and Connect session in `state/` across restarts,
output changes, network handoffs and cache clearing. After pairing, keep both devices
online and use the same Spotify account. On the phone, disable Spotify's **Local
device visibility** under Settings → Apps and devices to allow devices on other
networks. On iPhone also grant Spotify **Local Network** access for local pairing.
The comma needs its own usable internet connection (cellular or tethered internet
is fine); the local Galaxy hotspot alone does not supply one.

The supervisor has no Wi-Fi, unmetered-network or ignition requirement, and it never
restarts Soloist because the network changed. Soloist follows network changes itself:
it binds one mDNS socket on `0.0.0.0:5353` and answers on interfaces that appear after
it starts, because the device's avahi-daemon joins the mDNS group on every interface.
Checked on the device 2026-10-20 with network namespaces: a running Soloist answered
`_spotify-connect._tcp` queries on a link created after launch within ten seconds.

A disconnected selected Bluetooth speaker no longer prevents Soloist from starting.
It uses a private silent sink so the device can be paired and remain online; Galaxy
still shows the pairing state and adds that audio is waiting for the speaker. The
supervisor checks the selected speaker every twenty seconds in the background, so that
periodic check never blocks Galaxy commands. After two checks in a row agree that availability
changed, it uses the vendored `pactl` to load the speaker (or silent) sink into the running audio
server and move Soloist's stream onto it, so Soloist keeps its session, any pairing in progress,
and playback. Soloist plays to the server's default sink rather than a named one, so later streams
follow too. Only if `pactl` is missing or the switch fails does it restart the audio/player with
the saved session.
It never takes over playback from another Spotify device automatically.

The local API is polled for authentication state every fifteen seconds. A response
missing for forty-five seconds reconnects that socket; two minutes without a working
API restarts Soloist without clearing the account. A build that never answers at all
counts as a failed start, so repeated silence still rolls back to the previous build.
Loss of a previously observed login gets the same two-minute grace period, or ten
minutes if it was playing just before (cached music can outlast a coverage gap); an
initial pairing can wait indefinitely. A live API/login is not proof of internet reachability. If discovery still
fails, switch Spotify off/on in Galaxy to refresh it without resetting the account.

These behaviors cannot override phone hotspot isolation, Spotify app permissions,
cloud availability, expired Soloist builds, or a sleeping/powered-off comma.
The optional control WebSocket stays on loopback; exposing it or the Galaxy tunnel
is not how Spotify Connect discovers a device.

References checked 2026-10-20:
- https://developer.spotify.com/documentation/soloist/concepts/authentication
- https://developer.spotify.com/documentation/soloist/tutorials/getting-started
- https://developer.spotify.com/documentation/soloist/reference/websocket-api
- https://support.spotify.com/us/article/spotify-connect/

Hardware acceptance (not covered by unit tests): pair from Android and iPhone on
home Wi-Fi and each phone's hotspot; pair through the Galaxy hotspot with a separate
comma internet uplink; move a paired comma from Wi-Fi to cellular and IPv6 tethering;
control it from a phone on a different network with Local device visibility off;
restart the comma and confirm the saved account returns; connect/disconnect the
selected Bluetooth speaker; verify availability after a tunnel/coverage outage.
Confirm another Spotify player is never activated or interrupted by background recovery.
