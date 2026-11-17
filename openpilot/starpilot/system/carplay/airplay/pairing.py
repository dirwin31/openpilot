"""AirPlay pair-setup (PIN 3939), pair-verify and MFi auth-setup, ported from DiPlay."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.asymmetric import x25519

from openpilot.starpilot.system.carplay.airplay.crypto import (Srp6aServer, ed25519_verify, hkdf, nonce_label, open_, seal,
                                                               tlv_decode, tlv_encode, x25519_pair)

T_METHOD, T_IDENTIFIER, T_SALT, T_PUBLIC_KEY, T_PROOF, T_ENCRYPTED, T_STATE, T_ERROR, T_SIGNATURE = 0, 1, 2, 3, 4, 5, 6, 7, 10
ERROR_AUTH = 2


def error(state: int) -> bytes:
  return tlv_encode([(T_STATE, bytes([state])), (T_ERROR, bytes([ERROR_AUTH]))])


class PairingStore:
  def __init__(self, path: Path):
    self.path = path
    self.lock = threading.Lock()

  def _load(self) -> dict:
    try:
      return json.loads(self.path.read_text())
    except (OSError, ValueError):
      return {}

  def get(self, controller_id: str) -> bytes | None:
    value = self._load().get(controller_id)
    return bytes.fromhex(value) if value else None

  def save(self, controller_id: str, ltpk: bytes) -> None:
    with self.lock:
      data = self._load()
      data[controller_id] = ltpk.hex()
      self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
      tmp = self.path.with_suffix(".tmp")
      fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
      with os.fdopen(fd, "w") as f:
        json.dump(data, f)
      os.replace(tmp, self.path)


class PairSetup:
  def __init__(self, identity, pairings: PairingStore):
    self.identity, self.pairings = identity, pairings
    self.srp: Srp6aServer | None = None
    self.key: bytes | None = None

  def handle(self, body: bytes) -> bytes:
    tlv = tlv_decode(body)
    state = tlv.get(T_STATE, b"\0")[0]
    try:
      if state == 1:
        self.srp = Srp6aServer()
        return tlv_encode([(T_STATE, b"\x02"), (T_PUBLIC_KEY, self.srp.public_key), (T_SALT, self.srp.salt)])
      if state == 3:
        result = self.srp.verify(tlv[T_PUBLIC_KEY], tlv[T_PROOF]) if self.srp else None
        if result is None:
          return error(4)
        self.key, proof = result
        return tlv_encode([(T_STATE, b"\x04"), (T_PROOF, proof)])
      if state == 5:
        return self._m6(tlv)
    except Exception:
      pass
    return error(state)

  def _m6(self, tlv: dict) -> bytes:
    enc_key = hkdf(self.key, b"Pair-Setup-Encrypt-Salt", b"Pair-Setup-Encrypt-Info")
    sub = tlv_decode(open_(enc_key, nonce_label(b"PS-Msg05"), tlv[T_ENCRYPTED]))
    controller_id, ltpk, signature = sub[T_IDENTIFIER], sub[T_PUBLIC_KEY], sub[T_SIGNATURE]
    sign_key = hkdf(self.key, b"Pair-Setup-Controller-Sign-Salt", b"Pair-Setup-Controller-Sign-Info")
    if not ed25519_verify(ltpk, sign_key + controller_id + ltpk, signature):
      return error(6)
    self.pairings.save(controller_id.decode(), ltpk)
    accessory_id = self.identity.pairing_id.encode()
    acc_key = hkdf(self.key, b"Pair-Setup-Accessory-Sign-Salt", b"Pair-Setup-Accessory-Sign-Info")
    acc_sig = self.identity.private.sign(acc_key + accessory_id + self.identity.public)
    sealed = seal(enc_key, nonce_label(b"PS-Msg06"),
                  tlv_encode([(T_IDENTIFIER, accessory_id), (T_PUBLIC_KEY, self.identity.public), (T_SIGNATURE, acc_sig)]))
    return tlv_encode([(T_STATE, b"\x06"), (T_ENCRYPTED, sealed)])


class PairVerify:
  def __init__(self, identity, pairings: PairingStore):
    self.identity, self.pairings = identity, pairings
    self.shared: bytes | None = None
    self.control_keys: tuple[bytes, bytes] | None = None
    self.controller_id: str | None = None

  def handle(self, body: bytes) -> bytes:
    tlv = tlv_decode(body)
    state = tlv.get(T_STATE, b"\0")[0]
    try:
      if state == 1:
        return self._m2(tlv)
      if state == 3:
        return self._m4(tlv)
    except Exception:
      pass
    return error(state)

  def _m2(self, tlv: dict) -> bytes:
    self.client_public = tlv[T_PUBLIC_KEY]
    private, self.own_public = x25519_pair()
    self.shared = private.exchange(x25519.X25519PublicKey.from_public_bytes(self.client_public))
    self.enc_key = hkdf(self.shared, b"Pair-Verify-Encrypt-Salt", b"Pair-Verify-Encrypt-Info")
    accessory_id = self.identity.pairing_id.encode()
    signature = self.identity.private.sign(self.own_public + accessory_id + self.client_public)
    sealed = seal(self.enc_key, nonce_label(b"PV-Msg02"), tlv_encode([(T_IDENTIFIER, accessory_id), (T_SIGNATURE, signature)]))
    return tlv_encode([(T_STATE, b"\x02"), (T_PUBLIC_KEY, self.own_public), (T_ENCRYPTED, sealed)])

  def _m4(self, tlv: dict) -> bytes:
    sub = tlv_decode(open_(self.enc_key, nonce_label(b"PV-Msg03"), tlv[T_ENCRYPTED]))
    controller_id, signature = sub[T_IDENTIFIER], sub[T_SIGNATURE]
    ltpk = self.pairings.get(controller_id.decode())
    if ltpk is None or not ed25519_verify(ltpk, self.client_public + controller_id + self.own_public, signature):
      return error(4)
    read_key = hkdf(self.shared, b"Control-Salt", b"Control-Write-Encryption-Key")
    write_key = hkdf(self.shared, b"Control-Salt", b"Control-Read-Encryption-Key")
    self.control_keys = (read_key, write_key)
    self.controller_id = controller_id.decode()
    return tlv_encode([(T_STATE, b"\x04")])


def auth_setup(body: bytes, mfi) -> bytes | None:
  """MFi auth-setup: X25519 exchange, sign SHA-256(ours||theirs), AES-128-CTR the signature."""
  if len(body) != 33 or body[0] != 1:
    return None
  peer = body[1:]
  private, public = x25519_pair()
  shared = private.exchange(x25519.X25519PublicKey.from_public_bytes(peer))
  if not any(shared):
    return None
  aes_key = hashlib.sha1(b"AES-KEY" + shared).digest()[:16]
  aes_iv = hashlib.sha1(b"AES-IV" + shared).digest()[:16]
  signature = mfi.sign(hashlib.sha256(public + peer).digest())
  encryptor = Cipher(algorithms.AES(aes_key), modes.CTR(aes_iv)).encryptor()
  encrypted = encryptor.update(signature) + encryptor.finalize()
  cert = mfi.certificate
  return public + len(cert).to_bytes(4, "big") + cert + len(encrypted).to_bytes(4, "big") + encrypted
