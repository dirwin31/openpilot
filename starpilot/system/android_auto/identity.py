"""Private phone identity and configuration locations, and identity validation.

The identity (phone certificate, its key, and the Google Automotive Link root used
to verify the head unit) is provisioned separately with
``tools/android_auto/import_identity.py`` and copied to ``IDENTITY_DIR``. It is
never committed, never logged, and the key must be readable only by its owner.
"""

from __future__ import annotations

import json
import os
import ssl
import stat
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("ANDROID_AUTO_DIR", "/data/android_auto"))
IDENTITY_DIR = DATA_DIR / "identity"
CONFIG_PATH = DATA_DIR / "config.json"
LOG_DIR = DATA_DIR / "logs"
CERT_NAME, KEY_NAME, ROOT_NAME = "phone-cert.pem", "phone-key.pem", "root-cert.pem"
EXPIRY_WARNING_DAYS = 14


@dataclass(frozen=True)
class Identity:
  cert: str
  key: str
  root: str | None
  expires: str
  days_left: int


class IdentityError(RuntimeError):
  pass


def _not_after(cert_path: Path) -> datetime | None:
  try:
    from cryptography import x509
    return x509.load_pem_x509_certificate(cert_path.read_bytes()).not_valid_after_utc
  except ImportError:
    pass
  try:
    decoded = ssl._ssl._test_decode_cert(str(cert_path))  # type: ignore[attr-defined]
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(decoded["notAfter"]), UTC)
  except Exception:
    return None


def load_identity(directory: Path | None = None, now: datetime | None = None) -> Identity:
  directory = directory or IDENTITY_DIR
  cert, key, root = directory / CERT_NAME, directory / KEY_NAME, directory / ROOT_NAME
  missing = [path.name for path in (cert, key) if not path.is_file()]
  if missing:
    raise IdentityError(f"Android Auto identity missing ({', '.join(missing)} in {directory}); see the setup guide")
  if stat.S_IMODE(key.stat().st_mode) & 0o077:
    raise IdentityError(f"{key} must not be readable by other users (chmod 600)")
  try:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    if root.is_file():
      context.load_verify_locations(cafile=str(root))
  except (ssl.SSLError, OSError) as error:
    raise IdentityError(f"Android Auto identity is unusable: {error}") from error
  expires = _not_after(cert)
  now = now or datetime.now(UTC)
  if expires is not None and expires <= now:
    raise IdentityError(f"Android Auto phone certificate expired on {expires.date()}; import a newer identity")
  days_left = (expires - now).days if expires is not None else -1
  return Identity(str(cert), str(key), str(root) if root.is_file() else None,
                  expires.isoformat() if expires is not None else "unknown", days_left)


DEFAULT_CONFIG = {
  "receiver_address": "",      # Bluetooth address of the paired head unit
  "receiver_name": "",
  "rfcomm_channel": 0,         # 0 = discover through SDP (normal); set only to work around a broken SDP record
  "verify_head_unit": True,    # verify the car's certificate against root-cert.pem when present
  "fps": 12,                   # source frame rate sent to the car
  "bitrate_kbps": 4000,
  "wifi_interface": "wlan0",
  "device_name": "StarPilot",
  "version_status": 0,         # WifiVersionResponse status (0 = success) for receivers that negotiate a version
  "phone_class": True,         # while pairing/projecting, present as a phone: HFP gateway + smartphone Class of Device
}


def load_config(path: Path | None = None) -> dict:
  path = path or CONFIG_PATH
  config = dict(DEFAULT_CONFIG)
  try:
    stored = json.loads(path.read_text())
    if isinstance(stored, dict):
      config.update({key: value for key, value in stored.items() if key in DEFAULT_CONFIG and isinstance(value, type(DEFAULT_CONFIG[key]))})
  except (OSError, ValueError):
    pass
  config["fps"] = max(5, min(30, int(config["fps"])))
  config["bitrate_kbps"] = max(1000, min(12000, int(config["bitrate_kbps"])))
  return config


def save_config(config: dict, path: Path | None = None) -> None:
  path = path or CONFIG_PATH
  path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
  data = json.dumps({key: config[key] for key in DEFAULT_CONFIG if key in config}, indent=2) + "\n"
  fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config-")
  try:
    with os.fdopen(fd, "w") as handle:
      handle.write(data)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
  except BaseException:
    try:
      os.unlink(temporary)
    except FileNotFoundError:
      pass
    raise


def expiry_warning(identity: Identity) -> str:
  if 0 <= identity.days_left <= EXPIRY_WARNING_DAYS:
    return f"Android Auto identity expires in {identity.days_left} days ({identity.expires[:10]})"
  return ""


def timestamp() -> str:
  return time.strftime("%Y%m%d-%H%M%S")
