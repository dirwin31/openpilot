from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed, encode_dss_signature
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives.serialization import pkcs7
import datetime

from openpilot.starpilot.system.carplay.iap2.control import (Identification, WirelessEndpoint, identification_information,
                                                             rejected_messages, start_session, wifi_configuration)
from openpilot.starpilot.system.carplay.iap2.link import TUNNEL_CONFIG, LinkEngine, WIRELESS_CONFIG
from openpilot.starpilot.system.carplay.iap2.wire import Body, CsmFramer, Frame, parse_params, read_string
from openpilot.starpilot.system.carplay.mfi import MfiIdentity


def pump(a, b, now):
  for _ in range(50):
    moved = False
    for src, dst in ((a, b), (b, a)):
      data = src.take_output()
      if data:
        dst.feed(data, now)
        moved = True
    if not moved:
      return


def events(engine, kind):
  out = []
  while (e := engine.poll_event()) is not None:
    if e.kind == kind:
      out.append(e)
  return out


def test_link_negotiates_and_carries_control_both_ways():
  accessory, phone = LinkEngine(WIRELESS_CONFIG), LinkEngine(WIRELESS_CONFIG)
  accessory.start(False, 0)
  phone.start(True, 0)
  pump(accessory, phone, 0)
  assert accessory.state == phone.state == "normal"
  frame = Frame.build(0x1D00, Body().string(0, "hello"))
  phone.send(10, frame.encode(), 1)
  accessory.send(10, b"\x40\x40\x00\x06\xaa\x05", 1)
  pump(accessory, phone, 1)
  got = events(accessory, "control")
  assert [e.data for e in got] == [frame.encode()]
  assert [e.data for e in events(phone, "control")] == [b"\x40\x40\x00\x06\xaa\x05"]


def test_link_retransmits_lost_packet():
  a, b = LinkEngine(WIRELESS_CONFIG), LinkEngine(TUNNEL_CONFIG)
  a.start(True, 0)
  b.start(True, 0)
  pump(a, b, 0)
  b.send(10, b"x" * 10, 0)
  b.take_output()  # dropped on the air
  b.advance(5000)
  pump(a, b, 5000)
  assert [e.data for e in events(a, "control")] == [b"x" * 10]


def test_csm_framer_reassembles_split_frames():
  frames = [Frame.build(0x5702), Frame.build(0x4300, Body().u8(0, 1).string(1, "abc"))]
  data = b"".join(f.encode() for f in frames)
  framer = CsmFramer()
  out = []
  for i in range(0, len(data), 3):
    out += framer.offer(data[i:i + 3])
  assert [(f.message_id, f.payload) for f in out] == [(f.message_id, f.payload) for f in frames]


IDENT = Identification(name="StarPilot", model="comma", manufacturer="StarPilot", serial="SP-1",
                       bluetooth_mac="39:90:21:64:07:00", ssid="TheGalaxy-6b86")
ENDPOINT = WirelessEndpoint(ssid="TheGalaxy-6b86", passphrase="secretpass", channel=165, addresses=["172.31.254.1"],
                            port=7000, device_id="02:11:22:33:44:55", public_key_hex="ab" * 32)


def test_identification_has_wireless_components():
  params = dict(parse_params(identification_information(IDENT).payload))
  assert read_string(params[0]) == "StarPilot"
  assert params[8] == b"\x00"
  bt = dict(parse_params(params[17]))
  assert bt[3] == bytes.fromhex("399021640700")
  wifi = dict(parse_params(params[24]))
  assert read_string(wifi[1]) == "TheGalaxy-6b86"
  sent = params[6]
  assert b"\x57\x03" in [sent[i:i + 2] for i in range(0, len(sent), 2)]


def test_identification_retry_omits_rejected_messages():
  rejection = Frame.build(0x1D03, Body().u16_list(6, [0x5200]))
  omitted = rejected_messages(rejection)
  assert omitted == {0x5200}
  sent = dict(parse_params(identification_information(IDENT, omitted).payload))[6]
  assert b"\x52\x00" not in [sent[i:i + 2] for i in range(0, len(sent), 2)]
  assert rejected_messages(Frame.build(0x1D03, Body().u16_list(6, [0xAA01]))) is None
  assert rejected_messages(Frame.build(0x1D03, Body().void(17))) is None


def test_wifi_configuration_and_start_session():
  wifi = dict(parse_params(wifi_configuration(ENDPOINT).payload))
  assert read_string(wifi[1]) == "TheGalaxy-6b86" and wifi[3] == b"\x02" and wifi[4] == bytes([165])
  start = dict(parse_params(start_session(ENDPOINT).payload))
  assert start[2] == (7000).to_bytes(4, "big")
  wireless = parse_params(start[1])
  assert (3, b"172.31.254.1\0") in wireless


def test_mfi_identity_signs_raw_p256(tmp_path):
  key = ec.generate_private_key(ec.SECP256R1())
  name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
  now = datetime.datetime(2026, 1, 1)
  cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
          .sign(key, hashes.SHA256()))
  (tmp_path / "identity.pk8").write_bytes(key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                                            serialization.NoEncryption()))
  (tmp_path / "certificate.p7b").write_bytes(pkcs7.serialize_certificates([cert], serialization.Encoding.DER))
  identity = MfiIdentity.load(tmp_path)
  digest = bytes(range(32))
  raw = identity.sign(digest)
  assert len(raw) == 64
  der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
  key.public_key().verify(der, digest, ec.ECDSA(Prehashed(hashes.SHA256())))
