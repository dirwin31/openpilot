---
title: Rebasing Stardust onto the existing PR-Smaller-4 split
author: Danny
date: 2026-10-22
branch: integration/stardust-smaller4-20261022
touches:
  - openpilot/starpilot/galaxy/spotify_setup.py
  - openpilot/starpilot/galaxy/tests/test_spotify.mjs
  - openpilot/starpilot/galaxy/tests/test_spotify_setup.py
  - openpilot/starpilot/galaxy/web/css/galaxy.css
  - openpilot/starpilot/galaxy/web/js/spotify.js
  - openpilot/starpilot/soloist/backend.py
  - openpilot/starpilot/soloist/bluealsa/README.md
  - openpilot/starpilot/soloist/preferences.py
  - openpilot/starpilot/soloist/tests/test_bluealsa_upgrade.py
---

## Decided
Use backup/Stardust-Spotify-Companion-pre-split-20261019 as the replay boundary when rebasing the October 22 Stardust tip onto PR-Smaller-4. The split already contains the older features, and PR-Smaller-5 is an ancestor of PR-Smaller-4. Replay the 13 later commits, preserving upstream UI and troubleshooting links.
## Tried and ruled out
A plain rebase tries 121 old commits, causing duplicate feature conflicts and temporarily reintroducing logging removed later. The partial clone lacks object 4569f738be4f7dab405c63f8db8f23872c1be190 for the intermediate volume commit; the net diff from ddc9244f0b to the saved Stardust tip is available and preserves the final volume behavior.
## Watch out
Do not overwrite active PR-Smaller-4 worktree edits. Keep backup branches and a binary patch. Refresh the combined prebuilt inventory receipt. Soloist tests need local socket permission; Galaxy server tests additionally require the native Params library. No device build or deployment was performed.
