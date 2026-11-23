"""A short-lived, single-use Mapbox share opened on the comma's touchscreen."""
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time

SHARE = Path('/dev/shm/starpilot-mapbox-share')
PUBLIC = re.compile(r'pk\.[A-Za-z0-9_.-]{1,2045}')
_claim_lock = threading.Lock()


def offer(token, path=SHARE):
  if not isinstance(token, str) or not PUBLIC.fullmatch(token):
    return False
  fd, temporary = tempfile.mkstemp(dir=path.parent)
  try:
    with os.fdopen(fd, 'w') as stream:
      json.dump([time.monotonic() + 120, token], stream)
    os.replace(temporary, path)
  finally:
    Path(temporary).unlink(missing_ok=True)
  return True


def take(path=SHARE):
  if not path.exists():
    return None
  # Rename into our own private directory before opening. Only one request
  # can claim an offer, even when multiple paired phones ask concurrently.
  with tempfile.TemporaryDirectory(dir=path.parent) as temporary:
    claimed = Path(temporary) / 'offer'
    with _claim_lock:
      try:
        path.rename(claimed)
      except FileNotFoundError:
        return None
    info = claimed.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096 or info.st_uid != Path(temporary).stat().st_uid:
      return None
    if os.name == 'posix' and info.st_mode & 0o077:
      return None
    expires, token = json.loads(claimed.read_text())
    if 0 < expires - time.monotonic() <= 120 and isinstance(token, str) and PUBLIC.fullmatch(token):
      return token
  return None
