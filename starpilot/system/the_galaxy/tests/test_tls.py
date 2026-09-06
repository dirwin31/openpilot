import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess
import time

from openpilot.starpilot.system.the_galaxy.tls import ensure_certificate


def _certificate_text(cert_path: Path) -> str:
  return subprocess.run(
    ["openssl", "x509", "-in", str(cert_path), "-noout", "-subject", "-dates", "-ext", "subjectAltName"],
    check=True, capture_output=True, text=True,
  ).stdout


def test_certificate_is_idempotent_private_and_has_expected_sans(tmp_path):
  cert_path, key_path = ensure_certificate(tmp_path, hostname="comma-test")
  first_mtimes = (cert_path.stat().st_mtime_ns, key_path.stat().st_mtime_ns)
  time.sleep(0.01)
  second_cert, second_key = ensure_certificate(tmp_path, hostname="different-host")

  assert (second_cert, second_key) == (cert_path, key_path)
  assert (cert_path.stat().st_mtime_ns, key_path.stat().st_mtime_ns) == first_mtimes
  assert os.stat(key_path).st_mode & 0o777 == 0o600
  certificate = _certificate_text(cert_path)
  assert "CN = StarPilot Galaxy" in certificate or "CN=StarPilot Galaxy" in certificate
  assert "DNS:localhost" in certificate
  assert "DNS:galaxy.local" in certificate
  assert "DNS:starpilot-comma-test.local" in certificate
  assert "IP Address:127.0.0.1" in certificate
  not_after = next(line.removeprefix("notAfter=") for line in certificate.splitlines() if line.startswith("notAfter="))
  expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
  assert expiry - datetime.now(UTC) > timedelta(days=9 * 365)
