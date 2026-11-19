"""Accessory authentication with a software key (MFi auth v3: P-256, raw r||s signature)."""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed, decode_dss_signature
from cryptography.hazmat.primitives.serialization import pkcs7

PRIVATE_DIR = Path("/data/carplay-private")
MAX_FILE_BYTES = 16 * 1024
P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


class MfiIdentity:
  def __init__(self, key: ec.EllipticCurvePrivateKey, certificate: bytes):
    self._key = key
    self.certificate = certificate

  @classmethod
  def load(cls, directory: Path = PRIVATE_DIR) -> MfiIdentity:
    return cls.from_bytes(_read(directory / "identity.pk8"), _read(directory / "certificate.p7b"))

  @classmethod
  def from_bytes(cls, key_bytes: bytes, certificate: bytes) -> MfiIdentity:
    if not 0 < len(key_bytes) <= MAX_FILE_BYTES or not 0 < len(certificate) <= MAX_FILE_BYTES:
      raise ValueError("Invalid accessory identity size")
    key = serialization.load_der_private_key(key_bytes, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
      raise ValueError("Accessory key must be a P-256 EC key")
    certs = pkcs7.load_der_pkcs7_certificates(certificate)
    if len(certs) != 1:
      raise ValueError("Expected one accessory certificate")
    public = certs[0].public_key()
    if not isinstance(public, ec.EllipticCurvePublicKey) or public.public_numbers() != key.public_key().public_numbers():
      raise ValueError("Accessory key does not match its certificate")
    return cls(key, certificate)

  def sign(self, challenge: bytes) -> bytes:
    if len(challenge) != 32:
      raise ValueError("MFi v3 expects a 32-byte challenge digest")
    der = self._key.sign(challenge, ec.ECDSA(Prehashed(hashes.SHA256())))
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _read(path: Path) -> bytes:
  if os.path.getsize(path) > MAX_FILE_BYTES:
    raise ValueError(f"{path.name} is too large")
  data = path.read_bytes()
  if not data:
    raise ValueError(f"{path.name} is empty")
  return data
