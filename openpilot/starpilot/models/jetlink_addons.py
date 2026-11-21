"""Jetson add-ons over Jetlink's cable: V-ASM and sign vision ask the Jetson first, switched live by their settings.
Each is a jetlink Addon (docs/addons.md): the adapter's ADDONS puts it on the gadget and sends the Jetson its models."""
from __future__ import annotations

from pathlib import Path

from jetlink.addons.addon import Addon

_ROOT = Path(__file__).resolve().parents[3]
_SIGN_ASSETS = _ROOT / "openpilot/starpilot/speed_limits/vision/assets"

# interface class: vendor, then 'V' 'A' / 'S' 'L'; the models are the files and hashes the code that runs them pins
ADDONS = {
  "vasm": Addon("vasm", (0xFF, 0x56, 0x41), setting="JetlinkVasm", report_path=Path("/tmp/vasm_jetson_stats.json"),
                models=(("V-ASM", _ROOT / "starpilot/assets/vision_models/v_asm_model.onnx",
                         "5d20cdbb457ba18db51a537ee2e305bbe442264b1613956068d473e35d15900d"),)),
  "signs": Addon("slv", (0xFF, 0x53, 0x4C), setting="JetlinkSignVision", report_path=Path("/tmp/signs_jetson_stats.json"),
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
  return ADDONS[addon].wrap(net, sha256, outputs, params=params, scaled_u8=scaled_u8)
