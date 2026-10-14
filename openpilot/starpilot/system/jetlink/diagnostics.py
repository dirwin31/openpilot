"""Bounded Jetlink processing history, written only by the runtime worker."""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time


class RuntimeDiagnostics:
  def __init__(self, path=Path('/data/log/jetlink-runtime.jsonl')):
    self.path = path
    self.handler = None
    self.previous = None
    self.last_ns = 0

  def record(self, record, android_auto_enabled):
    # State changes are immediate; steady operation gets one sample per 10 s.
    identity = (record['state'], record['handovers'], android_auto_enabled)
    now = record['mono_ns']
    if identity == self.previous and now - self.last_ns < 10_000_000_000:
      return
    self.previous, self.last_ns = identity, now
    try:
      if self.handler is None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handler = RotatingFileHandler(self.path, maxBytes=2 << 20, backupCount=2)
        self.handler.setFormatter(logging.Formatter('%(message)s'))
      output = record['remote_output_mono_ns']
      data = {**record, 'event': 'jetlinkRuntime', 'wall_time': time.time(),
              'android_auto_enabled': android_auto_enabled,
              'remote_output_age_ms': (now - output) / 1e6 if output else None}
      self.handler.emit(logging.LogRecord('jetlink.runtime', logging.INFO, __file__, 0,
                                         json.dumps(data), (), None))
    except Exception:
      # Diagnostics must never stop runtime publication or model processing.
      logging.getLogger(__name__).exception('Jetlink diagnostics unavailable')

  def close(self):
    if self.handler is not None:
      self.handler.close()
