"""Manager's opt-in Jetlink owner and nonblocking remote power-off handoff."""
from __future__ import annotations

import time
import logging

SHUTDOWN_TIMEOUT = 25.0
POWER_ACTIONS = ('DoUninstall', 'DoReboot', 'DoShutdown')


def selected(params) -> bool:
  raw = params.get('JetlinkMode')
  return type(raw) is int and raw in (1, 2, 3)


def enabled(params) -> bool:
  # Keep off/default startup independent of the transport and model runtime.
  if not selected(params):
    return False
  from openpilot.starpilot.models.jetlink_adapter import get_jetlink, supported_device
  if not supported_device():
    return False
  link = get_jetlink()
  return link is not None and link.enabled()


def power_action(params) -> str | None:
  return next((key for key in POWER_ACTIONS if params.get_bool(key)), None)


class ShutdownHandoff:
  """Request once, then let manager keep publishing and supervising until done.

  DoShutdown/DoReboot/DoUninstall stay set throughout the handoff; hardwared
  prevents another drive from starting while any of them is set.
  """
  def __init__(self):
    self.started_at = None
    self.link = None

  def ready(self, params, reason: str) -> bool:
    try:
      return self._ready(params, reason)
    except Exception:
      # Failure to contact an optional accelerator must not strand power-off.
      logging.getLogger(__name__).exception('Jetlink shutdown handoff failed')
      return True

  def _ready(self, params, reason: str) -> bool:
    if self.started_at is None:
      self.started_at = time.monotonic()
      if enabled(params):
        from openpilot.starpilot.models.jetlink_adapter import get_jetlink
        link = get_jetlink()
        if link is not None and link.request_shutdown(reason):
          self.link = link
          self.started_at = time.monotonic()
    # Poll at the deadline too: Jetlink withdraws an unanswered request there.
    return (self.link is None or not self.link.shutdown_pending() or
            time.monotonic() - self.started_at >= SHUTDOWN_TIMEOUT)


def unavailable_reason(params) -> str | None:
  if not selected(params):
    return None
  from openpilot.starpilot.models.jetlink_adapter import get_jetlink, supported_device
  if not supported_device():
    return 'Jetlink requires a comma 3X or comma 4.'
  link = get_jetlink()
  return link.reason() if link is not None else 'Jetlink could not be loaded. The local model remains available.'
