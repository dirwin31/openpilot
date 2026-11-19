"""Install the CarPlay accessory key from the user's own copy of the CarLink app.

Each user downloads the CarLink release APK (https://github.com/F-e-n-y-x/CarLink/releases)
and uploads it to Galaxy; the comma extracts the key on device. No APK, certificate or key
is distributed with this code. The key goes into ``mfi.PRIVATE_DIR`` and is only accepted
when ``MfiIdentity.load`` can use it.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import zipfile
import zlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from openpilot.starpilot.system.carplay import mfi

RELEASES_URL = "https://github.com/F-e-n-y-x/CarLink/releases/latest"
KEY_NAME = "identity.pk8"
CERT_NAME = "certificate.p7b"
PROVENANCE_NAME = "provenance.json"
MAX_FILE_BYTES = 128 * 1024 * 1024
EXTRACTOR_AVAILABLE = False  # flip once extract_key reads the key
IMPORT_DIR = Path("/data/starpilot/carplay-import")


class KeyImportError(RuntimeError):
  """A rejected package. ``code`` lets Galaxy explain the problem without parsing the message."""

  def __init__(self, message: str, code: str = "FAILED"):
    super().__init__(message)
    self.code = code


# ------------------------------------------------------------------ extraction

def extract_key(path: Path, *, progress: Callable[[str], None] = lambda stage: None) -> tuple[dict[str, bytes], dict]:
  """Return ({KEY_NAME: DER PKCS#8, CERT_NAME: DER PKCS#7}, metadata) from a CarLink APK.

  Reports stages "reading", "extracting" and "verifying" through ``progress``; Galaxy shows them as checks.
  """
  progress("reading")
  try:
    with zipfile.ZipFile(path) as archive:
      if not any(name.endswith(".dex") for name in archive.namelist()):
        raise KeyImportError("No app code found; choose the CarLink APK from its GitHub release", "NO_CODE")
  except zipfile.BadZipFile as error:
    raise KeyImportError("Not an APK file", "NOT_PACKAGE") from error
  raise KeyImportError("Reading the key from CarLink is not available in this build yet", "NOT_AVAILABLE")


# ------------------------------------------------------------------ install

def _certificate(directory: Path):
  from cryptography.hazmat.primitives.serialization import pkcs7
  return pkcs7.load_der_pkcs7_certificates((directory / CERT_NAME).read_bytes())[0]


def _not_after(cert):
  return cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after.replace(tzinfo=UTC)


def install_key(files: dict[str, bytes], metadata: dict, directory: Path | None = None) -> None:
  """Atomically replace the key directory, keeping the previous one alongside it."""
  directory = directory or mfi.PRIVATE_DIR
  if set(files) != {KEY_NAME, CERT_NAME}:
    raise KeyImportError("The extracted key is incomplete", "FAILED")
  directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
  staging = Path(tempfile.mkdtemp(prefix=".carplay-", dir=directory.parent))
  try:
    os.chmod(staging, 0o700)
    for name, content in {**files, PROVENANCE_NAME: (json.dumps(metadata, indent=2) + "\n").encode()}.items():
      with os.fdopen(os.open(staging / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
      mfi.MfiIdentity.load(staging)  # CarPlay must be able to use it before it goes live
    except (OSError, ValueError) as error:
      raise KeyImportError(f"The extracted key does not work: {error}", "INVALID") from error
    previous = directory.with_name(directory.name + ".previous")
    if directory.exists():
      shutil.rmtree(previous, ignore_errors=True)
      os.replace(directory, previous)
    os.replace(staging, directory)
  except BaseException:
    shutil.rmtree(staging, ignore_errors=True)
    raise


def remove_key(directory: Path | None = None) -> None:
  directory = directory or mfi.PRIVATE_DIR
  shutil.rmtree(directory, ignore_errors=True)
  shutil.rmtree(directory.with_name(directory.name + ".previous"), ignore_errors=True)


def key_status(directory: Path | None = None) -> dict:
  """Whether a usable key is installed. Never returns key or certificate material."""
  directory = directory or mfi.PRIVATE_DIR
  if not (directory / KEY_NAME).exists() and not (directory / CERT_NAME).exists():
    return {"installed": False, "message": "No CarPlay key installed"}
  try:
    mfi.MfiIdentity.load(directory)
  except (OSError, ValueError) as error:
    return {"installed": False, "error": str(error)[:200], "message": "The CarPlay key cannot be used; reinstall it"}
  try:
    provenance = json.loads((directory / PROVENANCE_NAME).read_text())
  except (OSError, ValueError):
    provenance = {}
  status = {"installed": True, "imported": str(provenance.get("imported", "")), "app_version": str(provenance.get("app_version", ""))}
  try:
    status["expires"] = _not_after(_certificate(directory)).isoformat()
  except (OSError, ValueError, IndexError):
    pass
  return status


# ------------------------------------------------------------------ background import

class ImportJob:
  """One key import at a time, in a background thread, with pollable status."""

  def __init__(self, work_dir: Path | None = None, key_dir: Path | None = None,
               extract: Callable[..., tuple[dict[str, bytes], dict]] = extract_key):
    self.work_dir = work_dir or IMPORT_DIR
    self.key_dir = key_dir
    self.extract = extract
    self.lock = threading.Lock()
    self.thread: threading.Thread | None = None
    self.state: dict = {"state": "idle"}

  def status(self) -> dict:
    with self.lock:
      return dict(self.state)

  def _set(self, **values) -> None:
    with self.lock:
      self.state.update(values)

  def busy(self) -> bool:
    return self.thread is not None and self.thread.is_alive()

  def start(self, *, path: Path, enabled: Callable[[], bool] | None = None, app_version: str | None = None) -> None:
    with self.lock:
      if self.busy():
        raise KeyImportError("An import is already running")
      self.state = {"state": "running", "stage": "reading", "started": datetime.now(UTC).timestamp()}
      self.thread = threading.Thread(target=self._run, args=(path, enabled, app_version), name="carplay_key_import", daemon=True)
      self.thread.start()

  def _run(self, path: Path, enabled: Callable[[], bool] | None, app_version: str | None) -> None:
    def progress(stage: str) -> None:
      if enabled is not None and not enabled():
        raise KeyImportError("Import cancelled: Galaxy session ended", "CANCELLED")
      self._set(stage=stage)

    try:
      files, metadata = self.extract(path, progress=progress)
      metadata = {**metadata, "imported": datetime.now(UTC).isoformat(timespec="seconds")}
      if app_version and "app_version" not in metadata:
        metadata["app_version"] = app_version
      progress("installing")
      install_key(files, metadata, self.key_dir)
      self._set(state="done", stage="done", finished=datetime.now(UTC).timestamp(), message="CarPlay key installed")
    except KeyImportError as error:
      self._set(state="failed", finished=datetime.now(UTC).timestamp(), error=str(error), code=error.code)
    except (zipfile.BadZipFile, EOFError, zlib.error) as error:
      self._set(state="failed", finished=datetime.now(UTC).timestamp(), error=f"The package is damaged: {error}", code="CORRUPT")
    except Exception as error:
      self._set(state="failed", finished=datetime.now(UTC).timestamp(), error=f"{type(error).__name__}: {error}", code="FAILED")
    finally:
      try:
        path.unlink(missing_ok=True)
      except OSError:
        pass
