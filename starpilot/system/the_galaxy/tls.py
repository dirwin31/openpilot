"""Optional HTTPS listener for Galaxy's secure-context browser features."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import ipaddress
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading

from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import PC
from openpilot.system.hardware.hw import Paths
from openpilot.starpilot.system.the_galaxy.bonjour import _service_hostname


GALAXY_TLS_PORT = 8443
CERTIFICATE_VALID_DAYS = 3650


def _get_galaxy_dir() -> Path:
  if override := os.getenv("SP_GALAXY_DIR"):
    return Path(override)
  return Path(Paths.comma_home()) / "starpilot" / "data" / "galaxy" if PC else Path("/data/galaxy")


def _certificate_names(hostname: str | None = None) -> tuple[str, ...]:
  identifier = (hostname or socket.gethostname()).split(".", 1)[0]
  return "localhost", "galaxy.local", _service_hostname(identifier)


def _generate_with_cryptography(cert_path: Path, key_path: Path, hostname: str | None) -> None:
  from cryptography import x509
  from cryptography.hazmat.primitives import hashes, serialization
  from cryptography.hazmat.primitives.asymmetric import rsa
  from cryptography.x509.oid import NameOID

  key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
  subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "StarPilot Galaxy")])
  now = datetime.now(UTC)
  certificate = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - timedelta(minutes=5))
    .not_valid_after(now + timedelta(days=CERTIFICATE_VALID_DAYS))
    .add_extension(
      x509.SubjectAlternativeName([
        *(x509.DNSName(name) for name in _certificate_names(hostname)),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
      ]),
      critical=False,
    )
    .sign(key, hashes.SHA256())
  )
  key_path.write_bytes(key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
  ))
  cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def _generate_with_openssl(cert_path: Path, key_path: Path, hostname: str | None) -> None:
  dns_names = _certificate_names(hostname)
  config = "\n".join([
    "[req]",
    "distinguished_name = subject",
    "x509_extensions = extensions",
    "prompt = no",
    "[subject]",
    "CN = StarPilot Galaxy",
    "[extensions]",
    "subjectAltName = @alt_names",
    "[alt_names]",
    *(f"DNS.{index} = {name}" for index, name in enumerate(dns_names, start=1)),
    "IP.1 = 127.0.0.1",
    "",
  ])
  config_path = cert_path.parent / ".galaxy-openssl.cnf"
  try:
    config_path.write_text(config, encoding="utf-8")
    subprocess.run([
      "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
      "-days", str(CERTIFICATE_VALID_DAYS), "-config", str(config_path),
      "-keyout", str(key_path), "-out", str(cert_path),
    ], check=True, capture_output=True, timeout=30)
  finally:
    try:
      config_path.unlink()
    except FileNotFoundError:
      pass


def ensure_certificate(directory: Path | str | None = None, *, hostname: str | None = None) -> tuple[Path, Path]:
  """Create Galaxy's long-lived self-signed certificate once and return it."""
  tls_dir = Path(directory) if directory is not None else _get_galaxy_dir() / "tls"
  cert_path = tls_dir / "galaxy.crt"
  key_path = tls_dir / "galaxy.key"
  if cert_path.is_file() and key_path.is_file():
    key_path.chmod(0o600)
    return cert_path, key_path

  tls_dir.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(prefix=".galaxy-tls-", dir=tls_dir) as temporary:
    temporary_dir = Path(temporary)
    temporary_cert = temporary_dir / cert_path.name
    temporary_key = temporary_dir / key_path.name
    try:
      _generate_with_cryptography(temporary_cert, temporary_key, hostname)
    except (ImportError, ModuleNotFoundError):
      _generate_with_openssl(temporary_cert, temporary_key, hostname)
    temporary_key.chmod(0o600)
    os.replace(temporary_key, key_path)
    os.replace(temporary_cert, cert_path)
  key_path.chmod(0o600)
  return cert_path, key_path


def serve_tls(app, host: str = "0.0.0.0", port: int = GALAXY_TLS_PORT):
  """Serve the Galaxy app over TLS in a daemon thread."""
  from werkzeug.serving import make_server

  cert_path, key_path = ensure_certificate()
  server = make_server(host, port, app, threaded=True, ssl_context=(str(cert_path), str(key_path)))
  thread = threading.Thread(target=server.serve_forever, name="galaxy-tls", daemon=True)
  thread.start()
  cloudlog.info(f"Galaxy HTTPS listening on {host}:{port}")
  return server
