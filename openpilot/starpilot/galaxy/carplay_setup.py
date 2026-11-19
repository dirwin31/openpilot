"""Galaxy setup owner for the CarPlay key read from a user's own CarLink APK.

The HTTP server must authenticate the caller before invoking this owner.
"""
from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from openpilot.starpilot.system.carplay import key_import


class SetupRejected(RuntimeError):
  pass


class CarPlaySetup:
  def __init__(self, *, session_valid: Callable[[tuple], bool], import_job: key_import.ImportJob | None = None,
               key_status: Callable[[], dict] = key_import.key_status):
    self.session_valid = session_valid
    self.job = import_job or key_import.ImportJob()
    self.key_status = key_status
    self._upload_lock = threading.Lock()

  def _require_session(self, session: tuple) -> None:
    if not session or not self.session_valid(session):
      raise SetupRejected('Galaxy session expired; sign in again')

  def status(self, session: tuple) -> dict:
    self._require_session(session)
    key = self.key_status()
    return {
      'key': {name: key[name] for name in ('installed', 'expires', 'imported', 'app_version', 'message', 'error') if name in key},
      'import': self.job.status(),
      'maxUploadBytes': key_import.MAX_FILE_BYTES,
      'releasesUrl': key_import.RELEASES_URL,
    }

  def upload(self, session: tuple, source: BinaryIO, length: int) -> dict:
    self._require_session(session)
    if type(length) is not int or not 0 < length <= key_import.MAX_FILE_BYTES:
      raise SetupRejected('Invalid CarLink package size')
    if not self._upload_lock.acquire(blocking=False):
      raise SetupRejected('Another CarLink upload is running')
    path: Path | None = None
    started = False
    try:
      if self.job.busy():
        raise SetupRejected('A CarPlay key import is running')
      work_dir = self.job.work_dir
      work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
      fd, name = tempfile.mkstemp(prefix='galaxy-upload-', suffix='.apk', dir=work_dir)
      path = Path(name)
      with os.fdopen(fd, 'wb') as out:
        remaining = length
        while remaining:
          self._require_session(session)
          chunk = source.read(min(1024 * 1024, remaining))
          if not chunk:
            raise SetupRejected('CarLink upload ended early')
          if len(chunk) > remaining:
            raise SetupRejected('CarLink upload exceeds declared size')
          out.write(chunk)
          remaining -= len(chunk)
        out.flush()
        os.fsync(out.fileno())
      self._require_session(session)
      self.job.start(path=path, enabled=lambda: self.session_valid(session))
      started = True
      return self.status(session)
    finally:
      if path is not None and not started:
        path.unlink(missing_ok=True)
      self._upload_lock.release()

  def remove(self, session: tuple) -> dict:
    self._require_session(session)
    with self._upload_lock:
      self._require_session(session)
      if self.job.busy():
        raise SetupRejected('A CarPlay key import is running')
      key_import.remove_key()
    return self.status(session)
