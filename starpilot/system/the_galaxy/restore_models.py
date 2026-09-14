"""Finish a settings restore through the existing model downloader."""
import time


def download_saved_models(models, *, catalog, queue, busy, progress, check_parked, reboot, report,
                          sleep=time.sleep, monotonic=time.monotonic, timeout=1800):
  """Only request catalog IDs; archives cannot supply download URLs or commands."""
  check_parked()
  current = {model["value"]: model for model in catalog()}
  pending = []
  for saved in models:
    key = saved["key"]
    model = current.get(key)
    if model is None:
      raise ValueError(f"Saved model '{key}' is no longer in the catalog. Reboot without downloading or install it manually.")
    if saved.get("version") and model.get("version") != saved["version"]:
      raise ValueError(f"The saved version of '{key}' is no longer in the catalog. Reboot without downloading or install it manually.")
    for variant, flag, installed in (("standard", "standard", "installed"), ("lab", "lab", "modelLabArtifactInstalled")):
      if not saved[flag] or model.get(installed):
        continue
      if variant == "lab" and not model.get("modelLabArtifactAvailable"):
        raise ValueError(f"The saved eGPU variant of '{key}' is unavailable.")
      pending.append((key, variant, installed))

  for index, (key, variant, installed) in enumerate(pending):
    check_parked()
    if busy():
      raise ValueError("Another model download is active. Wait for it to finish and retry.")
    report(f"Downloading {index + 1}/{len(pending)}: {key}{' (eGPU)' if variant == 'lab' else ''}")
    queue(key, variant)
    deadline = monotonic() + timeout
    while True:
      check_parked()
      if not busy():
        model = next((entry for entry in catalog() if entry["value"] == key), {})
        if not model.get(installed):
          raise ValueError(f"Could not download '{key}': {progress() or 'download did not complete'}. Retry or reboot without downloading.")
        break
      if monotonic() >= deadline:
        raise ValueError(f"Timed out downloading '{key}'. Check Model Manager before retrying.")
      sleep(1)
  check_parked()
  if busy():
    raise ValueError("Another download started. Wait for it to finish before rebooting.")
  reboot()
