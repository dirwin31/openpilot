import hashlib
import io
import json
import stat
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

from openpilot.starpilot.galaxy.carplay_setup import CarPlaySetup
from openpilot.starpilot.system.carplay import key_import as importer, mfi


@pytest.fixture
def identity():
  key = ec.generate_private_key(ec.SECP256R1())
  name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic test accessory')])
  now = datetime.now(UTC)
  cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(1).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
          .sign(key, hashes.SHA256()))
  return {importer.KEY_NAME: key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()),
          importer.CERT_NAME: pkcs7.serialize_certificates([cert], serialization.Encoding.DER)}


def apk(tmp_path, identity, *, code=True):
  path = tmp_path / 'input.apk'
  with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
    if code:
      archive.writestr('classes.dex', b'dex\n035\0')
      archive.writestr('AndroidManifest.xml', b'test manifest')
    for name, data in identity.items():
      archive.writestr('assets/offline-mfi/' + name, data)
  return path


def test_extract_and_install(tmp_path, identity):
  path = apk(tmp_path, identity)
  stages = []
  files, metadata = importer.extract_key(path, progress=stages.append)
  assert files == identity
  assert metadata['apk_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
  assert stages == ['reading', 'extracting', 'verifying']
  target = tmp_path / 'private'
  importer.install_key(files, metadata, target)
  assert importer.key_status(target)['installed']
  assert len(mfi.MfiIdentity.load(target).sign(bytes(32))) == 64
  assert stat.S_IMODE(target.stat().st_mode) == 0o700
  assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in target.iterdir())
  assert 'PRIVATE' not in json.dumps(importer.key_status(target))


@pytest.mark.parametrize('failure,code', [('missing', 'UNSUPPORTED_VERSION'), ('oversize', 'INVALID'),
                                         ('mismatch', 'INVALID'), ('garbage', 'INVALID'), ('no_code', 'NO_CODE')])
def test_rejected_package(tmp_path, identity, failure, code):
  if failure == 'missing':
    identity.pop(importer.KEY_NAME)
  elif failure == 'oversize':
    identity[importer.KEY_NAME] = b'x' * (mfi.MAX_FILE_BYTES + 1)
  elif failure == 'mismatch':
    identity[importer.KEY_NAME] = ec.generate_private_key(ec.SECP256R1()).private_bytes(
      serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
  elif failure == 'garbage':
    identity[importer.CERT_NAME] = b'not a certificate'
  with pytest.raises(importer.KeyImportError) as caught:
    importer.extract_key(apk(tmp_path, identity, code=failure != 'no_code'))
  assert caught.value.code == code


def test_duplicate_asset(tmp_path, identity):
  path = apk(tmp_path, identity)
  with zipfile.ZipFile(path, 'a') as archive, pytest.warns(UserWarning):
    archive.writestr('assets/offline-mfi/' + importer.KEY_NAME, identity[importer.KEY_NAME])
  with pytest.raises(importer.KeyImportError, match='Duplicate'):
    importer.extract_key(path)


def test_invalid_replacement_preserves_existing(tmp_path, identity):
  target = tmp_path / 'private'
  importer.install_key(identity, {}, target)
  with pytest.raises(importer.KeyImportError):
    importer.install_key({**identity, importer.KEY_NAME: b'bad'}, {}, target)
  assert (target / importer.KEY_NAME).read_bytes() == identity[importer.KEY_NAME]


def test_upload_background_save_and_cleanup(tmp_path, identity):
  data = apk(tmp_path, identity).read_bytes()
  job = importer.ImportJob(work_dir=tmp_path / 'upload', key_dir=tmp_path / 'private')
  setup = CarPlaySetup(session_valid=lambda session: session == ('valid',), import_job=job,
                       key_status=lambda: importer.key_status(job.key_dir))
  setup.upload(('valid',), io.BytesIO(data), len(data))
  job.thread.join(timeout=5)
  status = setup.status(('valid',))
  assert status['extractorAvailable']
  assert status['import']['state'] == 'done'
  assert status['key']['installed']
  assert not list(job.work_dir.iterdir())


def test_cancel_preserves_key_and_removes_upload(tmp_path, identity):
  target = tmp_path / 'private'
  importer.install_key(identity, {}, target)
  path = apk(tmp_path, identity)
  job = importer.ImportJob(key_dir=target)
  job.start(path=path, enabled=lambda: False)
  job.thread.join(timeout=5)
  assert job.status()['code'] == 'CANCELLED'
  assert not path.exists()
  assert importer.key_status(target)['installed']


def test_install_failure_rolls_back(tmp_path, identity, monkeypatch):
  target = tmp_path / 'private'
  importer.install_key(identity, {}, target)
  replace = importer.os.replace

  def fail_staging(source, destination):
    if source.name.startswith('.carplay-'):
      raise OSError('simulated installation failure')
    replace(source, destination)

  monkeypatch.setattr(importer.os, 'replace', fail_staging)
  with pytest.raises(OSError):
    importer.install_key(identity, {}, target)
  assert importer.key_status(target)['installed']
