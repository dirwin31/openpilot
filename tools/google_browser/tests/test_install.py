import hashlib
import io
import tarfile

import pytest

from tools.google_browser.install import install


def package(tmp_path, extra=None):
  archive = tmp_path / 'runtime.tar.gz'
  with tarfile.open(archive, 'w:gz') as bundle:
    files = {'etc/resolv.conf': b'', 'usr/local/bin/google-browser': b'#!/bin/sh\n'}
    elf = b'\x7fELF\x02' + bytes(13) + (183).to_bytes(2, 'little')
    files.update(dict.fromkeys(('usr/lib/chromium/chromium', 'usr/bin/bwrap', 'usr/bin/Xvfb'), elf))
    for name, data in files.items():
      entry = tarfile.TarInfo(name)
      entry.size = len(data)
      bundle.addfile(entry, io.BytesIO(data))
    if extra is not None:
      bundle.addfile(extra)
  return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def test_verified_atomic_install_and_no_overwrite(tmp_path):
  archive, checksum = package(tmp_path)
  destination = tmp_path / 'installed'
  install(archive, checksum, destination)
  assert (destination / 'sha256').read_text().strip() == checksum
  assert not list(tmp_path.glob('.google-browser-*'))
  with pytest.raises(ValueError, match='already exists'):
    install(archive, checksum, destination)


def test_checksum_failure_never_publishes(tmp_path):
  archive, _ = package(tmp_path)
  with pytest.raises(ValueError, match='checksum mismatch'):
    install(archive, '0' * 64, tmp_path / 'installed')
  assert not (tmp_path / 'installed').exists()


@pytest.mark.parametrize('symlink', [False, True])
def test_archive_escape_is_rejected_and_staging_removed(tmp_path, symlink):
  entry = tarfile.TarInfo('escape' if symlink else '../../escape')
  if symlink:
    entry.type = tarfile.SYMTYPE
    entry.linkname = '../../escape'
  archive, checksum = package(tmp_path, entry)
  with pytest.raises(tarfile.FilterError):
    install(archive, checksum, tmp_path / 'installed')
  assert not (tmp_path / 'installed').exists()
  assert not list(tmp_path.glob('.google-browser-*'))
