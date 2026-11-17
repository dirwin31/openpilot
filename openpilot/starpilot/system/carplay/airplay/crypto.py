"""AirPlay pairing and channel crypto: TLV8, SRP-6a (3072-bit, SHA-512), HKDF, ChaCha20-Poly1305."""
from __future__ import annotations

import hashlib
import secrets
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import serialization

N = int(
  "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
  "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
  "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
  "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
  "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
  "15728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7"
  "ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864D87602733EC86A64521F2B18177B200C"
  "BBE117577A615D6C770988C0BAD946E208E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF", 16)
G = 5
N_BYTES = 384


def sha512(*parts: bytes) -> bytes:
  h = hashlib.sha512()
  for p in parts:
    h.update(p)
  return h.digest()


def to_bytes(value: int) -> bytes:
  return value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")


def pad(value: int) -> bytes:
  return value.to_bytes(N_BYTES, "big")


K = int.from_bytes(sha512(to_bytes(N), pad(G)), "big")


class Srp6aServer:
  def __init__(self, username: bytes = b"Pair-Setup", password: bytes = b"3939"):
    self.salt = secrets.token_bytes(16)
    self.username = username
    x = int.from_bytes(sha512(self.salt, sha512(username, b":", password)), "big")
    self.verifier = pow(G, x, N)
    self.b = int.from_bytes(secrets.token_bytes(32), "big")
    self.B = (K * self.verifier + pow(G, self.b, N)) % N
    self.public_key = pad(self.B)

  def verify(self, public_a: bytes, client_proof: bytes) -> tuple[bytes, bytes] | None:
    A = int.from_bytes(public_a, "big")
    if A % N == 0:
      return None
    u = int.from_bytes(sha512(pad(A), pad(self.B)), "big")
    S = pow(A * pow(self.verifier, u, N) % N, self.b, N)
    key = sha512(to_bytes(S))
    hn, hg = sha512(to_bytes(N)), sha512(to_bytes(G))
    expected = sha512(bytes(a ^ b for a, b in zip(hn, hg)), sha512(self.username), self.salt, pad(A), pad(self.B), key)
    if not secrets.compare_digest(expected, client_proof):
      return None
    return key, sha512(pad(A), client_proof, key)


def tlv_encode(items: list[tuple[int, bytes]]) -> bytes:
  out, previous = bytearray(), None
  for kind, value in items:
    if previous == kind:
      out += b"\xff\x00"
    offset = 0
    while True:
      chunk = value[offset:offset + 255]
      out += bytes([kind, len(chunk)]) + chunk
      offset += 255
      if offset >= len(value):
        break
    previous = kind
  return bytes(out)


def tlv_decode(data: bytes) -> dict[int, bytes]:
  out: dict[int, bytearray] = {}
  pos, last_type, last_len = 0, None, 0
  while pos + 2 <= len(data):
    kind, length = data[pos], data[pos + 1]
    value = data[pos + 2:pos + 2 + length]
    if len(value) != length:
      break
    pos += 2 + length
    if kind == last_type and last_len == 255:
      out[kind] += value
    else:
      out[kind] = bytearray(value)
    last_type, last_len = kind, length
  return {k: bytes(v) for k, v in out.items()}


def hkdf(key: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
  return HKDF(hashes.SHA512(), length, salt, info).derive(key)


def nonce_label(label: bytes) -> bytes:
  return b"\0" * 4 + label[:8].ljust(8, b"\0")


def nonce64(counter: int) -> bytes:
  return b"\0" * 4 + struct.pack("<Q", counter)


def seal(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes | None = None) -> bytes:
  return ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad)


def open_(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes | None = None) -> bytes:
  return ChaCha20Poly1305(key).decrypt(nonce, ciphertext, aad)


def ed25519_verify(public: bytes, data: bytes, signature: bytes) -> bool:
  try:
    ed25519.Ed25519PublicKey.from_public_bytes(public).verify(signature, data)
    return True
  except Exception:
    return False


def x25519_pair() -> tuple[x25519.X25519PrivateKey, bytes]:
  private = x25519.X25519PrivateKey.generate()
  return private, private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


class FrameCipher:
  """Length-prefixed ChaCha20-Poly1305 frames used by the control and event channels."""
  MAX_PAYLOAD = 0x4000

  def __init__(self, read_key: bytes, write_key: bytes):
    self.read = ChaCha20Poly1305(read_key)
    self.write = ChaCha20Poly1305(write_key)
    self.read_counter = 0
    self.write_counter = 0
    self.buffer = bytearray()

  def decrypt(self, data: bytes) -> bytes:
    self.buffer += data
    out = bytearray()
    while len(self.buffer) >= 2:
      length = self.buffer[0] | (self.buffer[1] << 8)
      end = 2 + length + 16
      if len(self.buffer) < end:
        break
      aad = bytes(self.buffer[:2])
      out += self.read.decrypt(nonce64(self.read_counter), bytes(self.buffer[2:end]), aad)
      self.read_counter += 1
      del self.buffer[:end]
    return bytes(out)

  def encrypt(self, plaintext: bytes) -> bytes:
    out, offset = bytearray(), 0
    while True:
      chunk = plaintext[offset:offset + self.MAX_PAYLOAD]
      header = struct.pack("<H", len(chunk))
      out += header + self.write.encrypt(nonce64(self.write_counter), chunk, header)
      self.write_counter += 1
      offset += self.MAX_PAYLOAD
      if offset >= len(plaintext):
        return bytes(out)
