import hashlib
import plistlib
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519

from openpilot.starpilot.system.carplay.airplay.crypto import (G, K, N, FrameCipher, hkdf, nonce_label, open_, pad, seal,
                                                               sha512, tlv_decode, tlv_encode, to_bytes)
from openpilot.starpilot.system.carplay.airplay.info import ReceiverConfig, info_plist
from openpilot.starpilot.system.carplay.airplay.pairing import PairSetup, PairVerify, PairingStore


class Identity:
  def __init__(self):
    self.private = ed25519.Ed25519PrivateKey.generate()
    self.public = self.private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    self.pairing_id = "ACCESSORY-ID"


def raw(key):
  return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def test_pair_setup_then_verify_like_an_iphone(tmp_path):
  identity, store = Identity(), PairingStore(tmp_path / "pairings.json")
  setup = PairSetup(identity, store)
  m2 = tlv_decode(setup.handle(tlv_encode([(0, b"\x00"), (6, b"\x01")])))
  salt, B = m2[2], int.from_bytes(m2[3], "big")
  a = int.from_bytes(secrets.token_bytes(32), "big")
  A = pow(G, a, N)
  u = int.from_bytes(sha512(pad(A), pad(B)), "big")
  x = int.from_bytes(sha512(salt, sha512(b"Pair-Setup:3939")), "big")
  S = pow((B - K * pow(G, x, N)) % N, a + u * x, N)
  key = sha512(to_bytes(S))
  hx = bytes(p ^ q for p, q in zip(sha512(to_bytes(N)), sha512(to_bytes(G))))
  M1 = sha512(hx, sha512(b"Pair-Setup"), salt, pad(A), pad(B), key)
  m4 = tlv_decode(setup.handle(tlv_encode([(6, b"\x03"), (3, pad(A)), (4, M1)])))
  assert m4[6] == b"\x04" and m4[4] == sha512(pad(A), M1, key)

  controller = ed25519.Ed25519PrivateKey.generate()
  cid, ltpk = b"CONTROLLER", raw(controller)
  sign_key = hkdf(key, b"Pair-Setup-Controller-Sign-Salt", b"Pair-Setup-Controller-Sign-Info")
  sub = tlv_encode([(1, cid), (3, ltpk), (10, controller.sign(sign_key + cid + ltpk))])
  enc = hkdf(key, b"Pair-Setup-Encrypt-Salt", b"Pair-Setup-Encrypt-Info")
  m6 = tlv_decode(setup.handle(tlv_encode([(6, b"\x05"), (5, seal(enc, nonce_label(b"PS-Msg05"), sub))])))
  assert m6[6] == b"\x06"
  acc = tlv_decode(open_(enc, nonce_label(b"PS-Msg06"), m6[5]))
  assert acc[3] == identity.public

  verify = PairVerify(identity, store)
  eph = x25519.X25519PrivateKey.generate()
  m2 = tlv_decode(verify.handle(tlv_encode([(6, b"\x01"), (3, raw(eph))])))
  shared = eph.exchange(x25519.X25519PublicKey.from_public_bytes(m2[3]))
  venc = hkdf(shared, b"Pair-Verify-Encrypt-Salt", b"Pair-Verify-Encrypt-Info")
  sub = tlv_decode(open_(venc, nonce_label(b"PV-Msg02"), m2[5]))
  ed25519.Ed25519PublicKey.from_public_bytes(identity.public).verify(sub[10], m2[3] + b"ACCESSORY-ID" + raw(eph))
  proof = tlv_encode([(1, cid), (10, controller.sign(raw(eph) + cid + m2[3]))])
  m4 = tlv_decode(verify.handle(tlv_encode([(6, b"\x03"), (5, seal(venc, nonce_label(b"PV-Msg03"), proof))])))
  assert m4 == {6: b"\x04"}
  assert verify.shared == shared

  phone_write = hkdf(shared, b"Control-Salt", b"Control-Write-Encryption-Key")
  phone_read = hkdf(shared, b"Control-Salt", b"Control-Read-Encryption-Key")
  receiver = FrameCipher(*verify.control_keys)
  phone = FrameCipher(phone_read, phone_write)
  big = b"x" * 40000
  assert receiver.decrypt(phone.encrypt(big)) == big
  assert phone.decrypt(receiver.encrypt(b"RTSP/1.0 200 OK\r\n\r\n")) == b"RTSP/1.0 200 OK\r\n\r\n"


def test_info_plist_encodes_as_binary_plist():
  cfg = ReceiverConfig(name="StarPilot", device_id="02:00:00:00:00:01", bluetooth_mac="39:90:21:64:07:00", width=1280,
                       height=720)
  data = plistlib.dumps(info_plist(cfg), fmt=plistlib.FMT_BINARY)
  info = plistlib.loads(data)
  assert info["displays"][0]["widthPixels"] == 1280
  assert "audioFormats" not in info
  assert info["features"] & 0x10004540A00 == 0
