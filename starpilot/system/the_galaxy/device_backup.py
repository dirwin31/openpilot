"""Portable same-device backups. Never extract archive paths into live data."""
import base64
import hashlib
import json
import os
import io
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

FORMAT = "starpilot-device-backup"
VERSION = 2
# Explicitly reviewed settings. New registry entries are excluded until audited.
BACKUP_KEYS = frozenset(json.loads(Path(__file__).with_name("device_backup_keys.json").read_text()))
AUTH_FILES = {"glxyauth", "glxysession", "glxyslug", "sentry_vapid_private.pem", "sentry_push_subscriptions.json"}


def eligible_keys(keys):
  return set(keys) & BACKUP_KEYS


def allowed_file(name, keys):
  parts = PurePosixPath(name).parts
  if any(part in AUTH_FILES for part in parts):
    return False
  if parts[0] != "profiles":
    return True
  # Only known slot documents or raw Params files from automatic backups.
  return (len(parts) == 2 and parts[1] in {".params-profile-a.json", ".params-profile-b.json"}) or (
    len(parts) == 3 and parts[1].endswith("_auto") and parts[2] in keys
  )


def sanitized_profile(content, keys):
  payload = json.loads(content)
  if payload.get("format") != "starpilot-params-profile" or not isinstance(payload.get("settings"), dict):
    raise ValueError("Invalid saved settings profile")
  payload = {key: value for key, value in payload.items() if key in {"format", "version", "slot", "createdAt", "settings"}}
  payload["settings"] = {key: value for key, value in payload["settings"].items() if key in keys}
  payload["settingsCount"] = len(payload["settings"])
  return json.dumps(payload).encode("utf-8")


def saved_models(catalog):
  return [{"key": entry["value"], "version": entry.get("version", ""),
           "standard": bool(entry.get("installed") and not entry.get("builtin")),
           "lab": bool(entry.get("modelLabArtifactInstalled"))}
          for entry in catalog if (entry.get("installed") and not entry.get("builtin")) or entry.get("modelLabArtifactInstalled")]


def validate_models(models):
  if not isinstance(models, list) or len(models) > 2000:
    raise ValueError("Invalid saved model inventory")
  result = []
  seen = set()
  for model in models:
    if not isinstance(model, dict) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", str(model.get("key", ""))):
      raise ValueError("Invalid saved model identifier")
    if model["key"] in seen or type(model.get("standard")) is not bool or type(model.get("lab")) is not bool:
      raise ValueError("Invalid saved model inventory")
    seen.add(model["key"])
    result.append({"key": model["key"], "version": str(model.get("version", ""))[:200],
                   "standard": model["standard"], "lab": model["lab"]})
  return result


def read_param(params, key):
  # Native Params.get() returns typed values, not the serialized bytes in the store.
  if hasattr(params, "get_param_path"):
    try:
      return Path(params.get_param_path(key)).read_bytes()
    except FileNotFoundError:
      return None
  return params.get(key)


def write_param(params, key, value):
  if value is None:
    params.remove(key)
    return
  if hasattr(params, "get_type"):
    kind = int(params.get_type(key))
    if kind != 6:  # BYTES
      text = value.decode("utf-8")
      value = {0: lambda: text, 1: lambda: text == "1", 2: lambda: int(text),
               3: lambda: float(text), 4: lambda: datetime.fromisoformat(text), 5: lambda: json.loads(text)}[kind]()
  params.put(key, value)


def create_backup(destination, roots, params, keys, models=()):
  keys = eligible_keys(keys)
  manifest = {"format": FORMAT, "version": VERSION, "params": {}, "files": {}, "models": validate_models(list(models))}
  for key in sorted(keys):
    value = read_param(params, key)
    if value is not None:
      manifest["params"][key] = base64.b64encode(value).decode("ascii")
  with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
    for label, root in roots.items():
      if label == "models":
        continue
      root = Path(root)
      if root.is_symlink():
        raise ValueError(f"Cannot back up symbolic link: {root}")
      if not root.exists():
        continue
      for path in sorted(root.rglob("*")):
        if path.is_symlink():
          raise ValueError(f"Cannot back up symbolic link: {path}")
        if not path.is_file():
          continue
        name = f"{label}/{path.relative_to(root).as_posix()}"
        if not allowed_file(name, keys):
          continue
        profile = label == "profiles" and len(PurePosixPath(name).parts) == 2
        source_file = io.BytesIO(sanitized_profile(path.read_bytes(), keys)) if profile else path.open("rb")
        digest = hashlib.sha256()
        size = 0
        with source_file as source, archive.open(name, "w", force_zip64=True) as output:
          while chunk := source.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        manifest["files"][name] = {"size": size, "sha256": digest.hexdigest()}
    archive.writestr("manifest.json", json.dumps(manifest))


def restore_backup(source, roots, params, keys, workdir, check_parked, models_out=None):
  """Validate fully, then replace files with rollback on application errors."""
  keys = eligible_keys(keys)
  with tempfile.TemporaryDirectory(prefix="restore-", dir=workdir) as temporary:
    stage = Path(temporary)
    with zipfile.ZipFile(source) as archive:
      entries = archive.infolist()
      names = [entry.filename for entry in entries]
      if len(names) != len(set(names)) or archive.getinfo("manifest.json").file_size > 16 * 1024 * 1024:
        raise ValueError("Invalid backup manifest")
      manifest = json.loads(archive.read("manifest.json"))
      if manifest.get("format") != FORMAT or manifest.get("version") not in (1, VERSION):
        raise ValueError("Unsupported device backup")
      models = validate_models(manifest.get("models", []))
      files = manifest["files"]
      if not isinstance(files, dict) or not isinstance(manifest.get("params"), dict):
        raise ValueError("Invalid backup contents")
      if set(names) != set(files) | {"manifest.json"}:
        raise ValueError("Backup file list does not match manifest")
      values = {}
      for key, value in manifest["params"].items():
        if key in keys:
          values[key] = base64.b64decode(value, validate=True)
      total = sum(entry.file_size for entry in entries)
      if total * 2 + 256 * 1024 * 1024 > shutil.disk_usage(workdir).free:
        raise ValueError("Not enough free space to safely restore this backup")
      targets = []
      for name, metadata in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 2 or relative.parts[0] not in set(roots) | {"models"}:
          raise ValueError("Invalid backup file path")
        if relative.parts[0] == "models" or not allowed_file(name, keys):
          continue
        target = Path(roots[relative.parts[0]]).joinpath(*relative.parts[1:])
        if any(parent.is_symlink() for parent in (target, *target.parents)):
          raise ValueError("Restore destination contains a symbolic link")
        staged = stage / "new" / name
        staged.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with archive.open(name) as input_file, staged.open("wb") as output:
          while chunk := input_file.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
        if staged.stat().st_size != metadata["size"] or digest.hexdigest() != metadata["sha256"]:
          raise ValueError("Backup checksum failed")
        if relative.parts[0] == "profiles" and len(relative.parts) == 2:
          staged.write_bytes(sanitized_profile(staged.read_bytes(), keys))
        targets.append((name, staged, target))
    check_parked()
    previous = {key: read_param(params, key) for key in keys}
    applied = []
    try:
      for name, staged, target in targets:
        check_parked()
        old = stage / "old" / name
        old.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        if existed:
          shutil.copy2(target, old)
        applied.append((target, old, existed))
        # Destinations such as active themes may be on another filesystem.
        descriptor, pending_name = tempfile.mkstemp(prefix=".device-restore-", dir=target.parent)
        pending = Path(pending_name)
        try:
          with os.fdopen(descriptor, "wb") as output, staged.open("rb") as source_file:
            shutil.copyfileobj(source_file, output)
          os.replace(pending, target)
        finally:
          pending.unlink(missing_ok=True)
      check_parked()
      for key in keys:
        write_param(params, key, values.get(key))
    except Exception:
      for key, value in previous.items():
        write_param(params, key, value)
      for target, old, existed in reversed(applied):
        if existed:
          shutil.copy2(old, target)
        else:
          target.unlink(missing_ok=True)
      raise
    if models_out is not None:
      models_out.extend(models)
    return len(values), len(targets)
