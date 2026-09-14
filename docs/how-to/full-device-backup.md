# Full StarPilot device backup

Open Galaxy at `/#/system`, then **Backup & Restore → Full Backup**.
The section is ordered **Settings Profiles**, **Toggles Backup**, then **Full Backup**.
The Full Backup section lists excluded data alongside its download and restore controls.
Download the ZIP to a phone or computer before changing forks. After reinstalling
StarPilot on the same device, restore the ZIP from that section. Once it succeeds,
choose **Download Models and Reboot** or **Reboot Without Downloading**. There is
no Later option. The first choice downloads missing saved models through Model
Manager, verifies they are installed, then requests reboot. The second skips this
download step and requests reboot immediately. Existing models are kept. Keep the
vehicle parked with ignition off; Galaxy temporarily disconnects during reboot.
A download failure does not reboot: use **Finish Restore and Reboot** to retry or
choose the other path. Reopening the page reconnects to the server's download job.
Avoid editing settings or running other model downloads/FLM analysis during restore.

Model files are deliberately excluded to keep the ZIP small. The archive records
installed model identifiers, versions, and standard/eGPU variants. Downloading
requires internet access and continued availability of the saved versions.
Manually installed or removed models may need manual reinstallation. Stock models
are supplied by the installed fork. Choosing not to download does not change the
restored model selection or the user's normal automatic-download preferences;
normal model-manager fallback/startup behavior still applies.

This backs up StarPilot data, not the operating system or installed fork. Matching
files are replaced; extra existing model and workspace files remain. Settings in
the audited backup policy are restored to their saved values or cleared if absent
from the archive. Excluded settings are never written or cleared.

## Data audit (September 2026)

The policy is an explicit allowlist in
`starpilot/system/the_galaxy/device_backup_keys.json`, checked against the current
Params registry at runtime. New Params are excluded until reviewed. `PERSISTENT`
alone is not sufficient: it also describes credentials, runtime state, and device
identity. `DONT_LOG` alone is not sufficient either: several credentials lack that
flag, while FLM baselines and personality profiles have it.

Included:

- Reviewed driving, display, model selection, and other preference Params.
- Calibration and learned tuning, FLM active overrides/profile/baseline/trial state,
  longitudinal personality profiles, and Safe Mode's settings snapshot.
- Installed-model inventory (no model binaries or download URLs), saved and active themes.
- `/data/galaxy/flm` workspace, including saved tunes, baselines, and reports.
  FLM's current-state snapshots use its fixed `TRIAL_PARAM_SPECS` tuning keys.
- Reviewed dashboard/driving statistics. These and FLM reports can contain route
  names, timestamps, vehicle details, and usage history; archives are not anonymous.
- Saved profile slots, sanitized to the same Params allowlist. Automatic backup
  directories include only allowlisted raw Params files; arbitrary older archive
  files and unknown profile documents are excluded.

Excluded from both export and restore:

- Galaxy `glxyauth` (password hash), `glxysession` (session token), and `glxyslug`
  (routing identifier). These reside directly under `/data/galaxy`, which is not a
  backup root. Their filenames are also blocked inside every backed-up root.
- `GalaxyPaired`, `GalaxyUploadPending`, `StarPilotApiToken`, and
  `StarPilotDongleId`.
- Other dongle identifiers and hardware serial, API/account caches, SSH authorized
  keys and username/access enablement, API keys for maps/weather/AssistNow, SecOC
  keys, and notification webhook/ntfy endpoints.
- Sentry push private key and subscriptions; system Wi-Fi/Bluetooth bonds and
  Tailscale identity/configuration are outside the backup roots.
- Navigation destinations/history and last GPS/location filter state.
- Runtime driving flags, update/build metadata, stale process/queue state, and
  account/registration/terms state.
- Model files, model catalog caches, driving recordings, offline maps, OS image,
  installed code, and existing full archives. Free-form text or manually placed files in theme/FLM folders
  are not content-redacted; do not put secrets there.

Galaxy credentials are ordinary files and could technically be copied, but an old
session can be stale or revoked and should not be revived by a settings restore.
The restore leaves current Galaxy pairing and other excluded credentials untouched.
If reinstalling removed them, pair again and re-enter the required service keys.

The allowlist is enforced inside the archive service for export and import,
including older version-1 archives. New backups use version 2. Model binaries in
older archives are skipped; old archives without an inventory cannot recreate a
model download list. Raw automatic backups and slot JSON documents
are filtered separately to prevent credentials from being carried through nested
backups. Archives downloaded before this policy change are not retroactively
sanitized; create a new backup to replace any such copy.

Checks cover settings/file round trips, model inventory without binaries, typed Params, invalid paths/checksums, symlink targets,
rollback when parked state changes, credential exclusion in Params and nested
profiles, preservation of current credentials when importing old archives, and the
inline UI's pre-restore cancellation, two completion choices, parked guards, error
reporting, progress polling, and multipart upload. Download tests cover missing and
already-installed variants, unavailable versions, offline failures, driving-state
changes, and timeouts; failed downloads never request reboot.
