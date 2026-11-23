"""Jetson add-ons over Jetlink's cable: V-ASM and sign vision ask the Jetson first, switched live by their settings.
Each is a jetlink Addon (docs/addons.md): the adapter's ADDONS puts it on the gadget and sends the Jetson its models."""
from __future__ import annotations

import time
from pathlib import Path

from jetlink.addons.addon import Addon

_ROOT = Path(__file__).resolve().parents[3]
_SIGN_ASSETS = _ROOT / "openpilot/starpilot/speed_limits/vision/assets"

# a link's state is logged when it changes, and its counts this often while it does not
SUMMARY_S = 300.0
_logged: dict[str, tuple] = {}
_summary_at: dict[str, float] = {}


def log_report(name: str, report: dict, now: float | None = None) -> bool:
  """Log a link's report (jetlink.addons.link, every 30 s while it is used) to swaglog as jetlink.addon:
  at once when it connects, drops, starts or stops yielding, or a model is missing on the Jetson, and
  otherwise every SUMMARY_S with what the Jetson answered. Whether it logged."""
  now = time.monotonic() if now is None else now
  state = (bool(report.get("connected")), bool(report.get("yielding")), tuple(report.get("missing") or ()))
  changed = _logged.get(name) != state
  if not changed and now < _summary_at.get(name, 0.0):
    return False
  _logged[name], _summary_at[name] = state, now + SUMMARY_S
  link = report.get("link") if isinstance(report.get("link"), dict) else {}
  trips = report.get("round_trips") if isinstance(report.get("round_trips"), dict) else {}
  try:
    # lazily: the owner reads ADDONS off the adapter with the standard library only
    from openpilot.common.swaglog import cloudlog
    cloudlog.event("jetlink.addon", addon=name, changed=changed, connected=state[0], yielding=state[1], missing=list(state[2]),
                   answered=int(link.get("answered", 0) or 0), timeouts=int(link.get("timeouts", 0) or 0),
                   round_trips=int(trips.get("n", 0) or 0), round_trip_ms=trips.get("total_ms"), jetson_ms=trips.get("jetson_ms"))
  except Exception:
    return False
  return True


class LoggedAddon(Addon):
  """A jetlink Addon whose link reports also go to swaglog (log_report)."""

  def write_report(self, report: dict) -> None:
    log_report(self.name, report)
    super().write_report(report)


# interface class: vendor, then 'V' 'A' / 'S' 'L'; the models are the files and hashes the code that runs them pins
ADDONS = {
  "vasm": LoggedAddon("vasm", (0xFF, 0x56, 0x41), setting="JetlinkVasm",
                models=(("V-ASM", _ROOT / "starpilot/assets/vision_models/v_asm_model.onnx",
                         "5d20cdbb457ba18db51a537ee2e305bbe442264b1613956068d473e35d15900d"),)),
  "signs": LoggedAddon("slv", (0xFF, 0x53, 0x4C), setting="JetlinkSignVision",
                 models=(("Sign detector", _SIGN_ASSETS / "traffic_signs_us_detector.onnx",
                          "dd9398ca0df68abdbc1120d41859d17d634a21a625b1191d10b5e4ddf88de7c8"),
                         ("Stop sign detector", _SIGN_ASSETS / "traffic_signs_us_stop_detector.onnx",
                          "a744df1b585bfd80a2a983229509f52bf2f09714d9df011109ad1b8b875aaecc"))),
}


def with_jetson(net, addon: str, sha256: str, outputs, *, scaled_u8: bool = False, params=None):
  """The net, asking the Jetson first while the add-on's setting is on."""
  if params is None:
    from openpilot.common.params import Params
    params = Params()
  wrapped = ADDONS[addon].wrap(net, sha256, outputs, params=params, scaled_u8=scaled_u8)
  try:
    from openpilot.common.swaglog import cloudlog
    cloudlog.info(f"jetlink: {addon} {sha256[:16]} asks the Jetson first while {ADDONS[addon].setting} is on"
                  if wrapped is not net else f"jetlink: {addon} {sha256[:16]} runs on the comma only")
  except Exception:
    pass
  return wrapped
