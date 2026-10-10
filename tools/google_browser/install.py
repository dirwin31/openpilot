#!/usr/bin/env python3
"""Install a checksum-verified browser bundle on AGNOS.

Offline; refuses to overwrite an existing runtime directory.
"""
import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import tarfile
import tempfile


def install(archive: Path, expected: str, destination: Path, *, check=lambda: None):
  if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
    raise ValueError('Supply the SHA-256 recorded by the trusted build')
  with archive.open('rb') as source:
    digest = hashlib.sha256()
    while block := source.read(1024 * 1024):
      check()
      digest.update(block)
    if digest.hexdigest() != expected:
      raise ValueError('Browser package checksum mismatch')
    if destination.exists():
      raise ValueError('Browser runtime already exists; stage upgrades with Galaxy stopped')
    source.seek(0)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.google-browser-', dir=destination.parent))
    try:
      root = staging / 'root'
      root.mkdir()
      total = 0

      def safe_member(member, path):
        nonlocal total
        check()
        total += member.size
        if total > 2 * 1024**3:
          raise ValueError('Browser package exceeds installed size limit')
        # Debian absolute symlinks refer to paths inside the packaged root.
        if member.issym() and member.linkname.startswith('/'):
          member = member.replace(linkname=os.path.relpath(member.linkname.lstrip('/'), str(PurePosixPath(member.name).parent)))
        return tarfile.data_filter(member, path)

      with tarfile.open(fileobj=source, mode='r:gz') as bundle:
        bundle.extractall(root, filter=safe_member)
      for directory in ('proc', 'dev', 'tmp', 'session'):
        (root / directory).mkdir(exist_ok=True)
      (root / 'etc/resolv.conf').touch()
      for relative in ('usr/lib/chromium/chromium', 'usr/bin/bwrap', 'usr/bin/Xvfb'):
        binary = root / relative
        with binary.open('rb') as executable:
          header = executable.read(20)
        if header[:5] != b'\x7fELF\x02' or int.from_bytes(header[18:20], 'little') != 183:
          raise ValueError('Browser package is not Linux ARM64')
      (staging / 'sha256').write_text(expected + '\n')
      check()
      staging.rename(destination)
    finally:
      if staging.exists():
        shutil.rmtree(staging)


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('archive', type=Path)
  parser.add_argument('--sha256', required=True)
  # Galaxy installs the pinned package itself; this is for manual testing only.
  parser.add_argument('--destination', type=Path, required=True,
                      help='new runtime directory, e.g. /data/starpilot/google-browser/manual')
  args = parser.parse_args()
  if platform.system() != 'Linux' or platform.machine() != 'aarch64':
    parser.error('Install this package on an ARM64 Linux device')
  install(args.archive, args.sha256, args.destination)
