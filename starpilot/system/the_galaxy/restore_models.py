"""Finish a settings restore through the existing model downloader."""
import time


def download_saved_models(models, *, catalog, queue, cancel, owns, busy, progress, check_parked, reboot, report,
                          refresh=None, canonical=str, cancelled=lambda: False, sleep=time.sleep, monotonic=time.monotonic, timeout=1800):
  """Only request catalog IDs; archives cannot supply download URLs or commands.

  Models the catalog no longer offers are skipped and reported. The downloader always serves the
  catalog's current version, so saved versions are informational. Reboot is requested only when
  every attempted download is verified installed.
  """
  check_parked()
  if busy():
    raise ValueError("Another model download is active. Wait for it to finish and retry.")
  if models and refresh is not None:
    # Even an unloaded Galaxy catalog contains its synthetic built-in model.
    report("Refreshing the model list...")
    try:
      refresh()
    except Exception as exc:
      report(f"Could not refresh model list: {exc}. Checking the cached catalog...")
  check_parked()
  entries = catalog()
  if models and not any(not model.get("builtin") or model.get("modelLabArtifactAvailable") for model in entries):
    raise ValueError("The downloadable model catalog is unavailable. Connect to the internet and retry, or reboot without downloading.")
  current = {model["value"]: model for model in entries}

  pending = []
  skipped = []
  for saved in models:
    key = canonical(saved["key"])
    model = current.get(key)
    if model is None:
      skipped.append(f"{key} (no longer offered)")
      continue
    for variant, installed in (("standard", "installed"), ("lab", "modelLabArtifactInstalled")):
      if not saved[variant] or model.get(installed):
        continue
      if variant == "standard" and model.get("requiresGpu") and not model.get("gpuAvailable"):
        skipped.append(f"{key} (needs a detected external GPU)")
      elif variant == "lab" and (not model.get("modelLabArtifactAvailable") or not model.get("modelLabEligible", True)):
        skipped.append(f"{key} eGPU variant (unavailable)")
      else:
        pending.append((key, variant, installed))

  failed = []
  for index, (key, variant, installed) in enumerate(pending):
    check_parked()
    if busy():
      raise ValueError("Another model download is active. Wait for it to finish and retry.")
    label = f"{key}{' (eGPU)' if variant == 'lab' else ''}"
    report(f"Downloading {index + 1}/{len(pending)}: {label}")
    try:
      token = queue(key, variant)
    except ValueError as exc:
      if busy():
        raise ValueError("Another model download started. Wait for it to finish and retry.") from exc
      failed.append(f"'{label}' ({exc})")
      continue
    deadline = monotonic() + timeout
    try:
      while owns(token):
        check_parked()
        if monotonic() >= deadline:
          raise ValueError(f"Timed out downloading '{label}'. Check Model Manager before retrying.")
        sleep(1)
    except BaseException:
      cancel(token)  # An ownership token prevents cancelling a newer user request.
      raise
    if cancelled():
      raise ValueError("Restore model downloads were cancelled. Retry or reboot without downloading.")
    model = next((entry for entry in catalog() if entry["value"] == key), {})
    if not model.get(installed):
      failed.append(f"'{label}' ({progress() or 'download did not complete'})")

  check_parked()
  skipped_note = f"Skipped {len(skipped)} unavailable model(s): {', '.join(skipped)}." if skipped else ""
  if failed:
    raise ValueError(" ".join(filter(None, [f"Could not download {', '.join(failed)}.", skipped_note,
                                            "Retry, or reboot without downloading."])))
  if busy():
    raise ValueError("Another download started. Wait for it to finish before rebooting.")
  reboot(f"{skipped_note} Install them from Model Manager after reboot." if skipped else "")
