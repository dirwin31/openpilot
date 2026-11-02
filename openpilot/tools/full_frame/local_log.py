"""Read local logs without importing the remote-route or native IPC stack."""
import bz2
from pathlib import Path


def log_schema(schema_root=None):
  if schema_root is None:
    from openpilot.cereal import log
    return log
  import capnp
  schema_root = Path(schema_root).resolve()
  # Load historical schemas explicitly: current Dom renamed wire-compatible
  # events and changed their fields. Never reinterpret old poses as new ones.
  return capnp.load(str(schema_root / 'log.capnp'), imports=[str(schema_root)])


def read_events(path, schema_root=None):
  log = log_schema(schema_root)
  path = Path(path)
  data = path.read_bytes()
  if path.suffix == '.zst':
    import zstandard
    with zstandard.ZstdDecompressor().stream_reader(data) as stream:
      data = stream.read()
  elif path.suffix == '.bz2':
    data = bz2.decompress(data)
  yield from log.Event.read_multiple_bytes(data)
