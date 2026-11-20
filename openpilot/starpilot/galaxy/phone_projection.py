"""Galaxy settings for the phone screen: CarPlay or Android Auto beside the comma's own view."""
from __future__ import annotations

from openpilot.starpilot.system.phone_projection import settings as projection_settings
from openpilot.starpilot.system.phone_projection.ipc import StatusReader

STATES = {"active", "connecting", "waiting"}
# Where each phone type is in its setup, so Galaxy can tick off the steps already done
RUNNER_STATES = {"starting", "unavailable", "waiting_hotspot", "waiting_phone", "waiting_projection", "connecting", "active",
                 "retrying"}


def _runner(value) -> dict:
  value = value if isinstance(value, dict) else {}
  state = value.get("state") if value.get("state") in RUNNER_STATES else ""
  return {"state": state, "message": str(value.get("message") or "")[:200] if state else ""}


def snapshot(reader: StatusReader | None = None, root=projection_settings.ROOT) -> dict:
  settings = projection_settings.load(root)
  status = (reader or StatusReader()).read() if settings.enabled else {}
  state = status.get("state") if status.get("state") in STATES else ("starting" if settings.enabled else "off")
  return {"enabled": settings.enabled, "side": settings.side, "audio": settings.audio,
          "sides": list(projection_settings.SIDES), "audioRoutes": list(projection_settings.AUDIO_ROUTES),
          "state": state, "kind": str(status.get("kind") or ""), "phone": str(status.get("phone") or "")[:64],
          "message": str(status.get("message") or "")[:200], "carplay": _runner(status.get("carplay")),
          "android": _runner(status.get("android"))}


def save(payload, root=projection_settings.ROOT) -> dict:
  if not isinstance(payload, dict):
    raise ValueError("Expected phone screen settings.")
  changes = {}
  if "enabled" in payload:
    if not isinstance(payload["enabled"], bool):
      raise ValueError("Phone screen on/off must be true or false.")
    changes["enabled"] = payload["enabled"]
  if "side" in payload:
    if payload["side"] not in projection_settings.SIDES:
      raise ValueError("Choose the left or right side.")
    changes["side"] = payload["side"]
  if "audio" in payload:
    if payload["audio"] not in projection_settings.AUDIO_ROUTES:
      raise ValueError("Choose where phone audio plays.")
    changes["audio"] = payload["audio"]
  if not changes:
    raise ValueError("Nothing to change.")
  projection_settings.update(root, **changes)
  return snapshot(root=root)
