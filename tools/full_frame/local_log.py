"""Read local logs without importing the remote-route or native IPC stack."""
import bz2
from pathlib import Path


def read_events(path):
  from cereal import log
  path = Path(path)
  data = path.read_bytes()
  if path.suffix == '.zst':
    import zstandard
    with zstandard.ZstdDecompressor().stream_reader(data) as stream:
      data = stream.read()
  elif path.suffix == '.bz2':
    data = bz2.decompress(data)
  yield from log.Event.read_multiple_bytes(data)
