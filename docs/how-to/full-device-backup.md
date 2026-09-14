# Full StarPilot device backup

Open Galaxy at `/#/system`, then **Backup & Restore → Full Backup**.
The section is ordered **Settings Profiles**, **Toggles Backup**, then **Full Backup**.
The Full Backup section lists excluded data alongside its download and restore controls.
Download the ZIP to a phone or computer before changing forks. After reinstalling
StarPilot on the same device, restore the ZIP from that section. Once it succeeds,
choose **Download Models and Reboot** or **Reboot Without Downloading**. There is
no Later option. The first choice downloads each missing saved model the catalog
still offers through Model Manager, verifies it is installed, then requests reboot.
Models the catalog no longer offers, and models that need an undetected external
GPU, are skipped and listed rather than blocking the others. The second choice asks
for confirmation (**Back** returns to the choice), then skips this download step
and requests reboot. Existing models are kept. Keep the
vehicle parked with ignition off; Galaxy temporarily disconnects during reboot.
A download failure does not reboot: the remaining models are still attempted, then
use **Finish Restore and Reboot** to retry or choose the other path. Reopening the page reconnects to the server's download job.
Avoid editing settings or running other model downloads/FLM analysis during restore.

Model files are deliberately excluded to keep the ZIP small. The archive records
installed model identifiers, versions, and standard/eGPU variants. Downloading
requires internet access. Model Manager always serves the catalog's current
version, so a newer version of a saved model is downloaded; saved versions are
informational. The catalog is refreshed before downloading, including on fresh installs where
Galaxy initially shows only its built-in model. An unavailable catalog produces a
retryable error instead of silently skipping every saved model. Manually installed or removed models may need manual
reinstallation. Stock models
are supplied by the installed fork. Choosing not to download does not change the
restored model selection or the user's normal automatic-download preferences;
normal model-manager fallback/startup behavior still applies.

This backs up StarPilot data, not the operating system or installed fork. Matching
files are replaced; extra existing model and workspace files remain. Settings in
the audited backup policy are restored to their saved values or cleared if absent
from the archive, but only within the scope recorded by that backup. New settings
added afterward keep their current values. Legacy archives without a recorded
scope preserve absent settings. Every value is converted to the current Params type before
anything is written; a setting whose type changed, or a driving-personality value
that fails the same validation toggle restores use, keeps its current value and is
listed after restore instead of failing the whole restore. Excluded settings are
never written or cleared. The upload is streamed straight to `/data` rather than
through RAM-backed `/tmp`. Uploads (including chunked bodies) are limited to 8 GiB
and checked against available storage before and during receipt. Restore reserves
space for staged data, the actual existing files that need rollback copies, an
atomic replacement, and a margin. Profile JSON uses the existing 2 MB profile limit.
Recovery metadata and copies are written before live changes. A failed rollback
keeps them and reports the recovery directory rather than silently discarding them.
An interrupted restore is flagged on the next server start; power-loss recovery
is not automatic and still needs device validation.

## Data audit (September 2026)

The policy in `starpilot/system/the_galaxy/device_backup_keys.json` lists every
persistent Param as either `include` or `exclude`. A test fails when a new
persistent Param is added to `common/params_keys.h` without being classified, so new
settings are never silently left out. At runtime the include list is intersected
with the current Params registry. `PERSISTENT`
alone is not sufficient: it also describes credentials, runtime state, and device
identity. `DONT_LOG` alone is not sufficient either: several credentials lack that
flag, while FLM baselines and personality profiles have it.

Included:

- Reviewed driving, display, model selection, and other preference Params.
- Calibration and learned tuning, FLM active overrides/profile/baseline/trial state,
  longitudinal personality profiles, and Safe Mode's settings snapshot.
- Installed-model inventory (no model binaries or download URLs) and saved themes
  in `/data/themes`. The active theme folder is not archived: it consists of symbolic
  links that the theme manager rebuilds from the restored theme settings on boot.
  Symbolic links are never followed or archived in any backup root.
- `/data/galaxy/flm` workspace, including saved tunes, baselines, reports, and
  `progress.json`. Despite its name, that file stores persistent per-vehicle
  tuning progression (such as reaching Cleanup Pass). Runtime job status is
  `/tmp/galaxy_flm_status.json`, outside all backup roots. FLM snapshots use
  the fixed `TRIAL_PARAM_SPECS` tuning keys.
- Reviewed dashboard/driving statistics. These and FLM reports can contain route
  names, timestamps, vehicle details, and usage history; archives are not anonymous.
- Saved profile slots, sanitized to the same Params allowlist. Automatic (`*_auto`)
  and user-named toggle backup directories (created from the device's Create Toggle
  Backup) include only allowlisted raw Params files; unfinished `*_in_progress`
  copies, arbitrary older archive files, and unknown profile documents are excluded.

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
including older version-1 and version-2 archives. New backups use version 3, with
Params types and the captured key scope. Older archives have no type metadata,
so their values receive best-effort current-type validation. Model binaries in
older archives are skipped; old archives without an inventory cannot recreate a
model download list. Raw toggle backups and slot JSON documents
are filtered separately to prevent credentials from being carried through nested
backups. Archives downloaded before this policy change are not retroactively
sanitized; create a new backup to replace any such copy.

Checks cover settings/file round trips, model inventory without binaries, typed
Params, settings whose type changed, personality validation, invalid
paths/checksums, symlink destinations, skipped linked theme assets, the exact
backup roots, user-named toggle backups, the complete include/exclude
classification, rollback when parked state changes, credential exclusion in Params
and nested profiles, preservation of current credentials when importing old
archives, and the inline UI's pre-restore cancellation, two completion choices with
a confirmed no-download reboot, parked guards, error reporting, progress polling,
and raw-body upload. Download tests cover missing and already-installed variants,
newer catalog versions, skipped unavailable and GPU-only models, catalog refresh,
failed downloads that continue to the next model, driving-state changes, and
timeouts; only an owned download request can be cancelled, and failed downloads never
request reboot. Galaxy reserves the model workflow during restore downloads so
other browser download/refresh/delete operations cannot interfere. The completion
summary, including skipped models/settings, remains visible after server restart.
