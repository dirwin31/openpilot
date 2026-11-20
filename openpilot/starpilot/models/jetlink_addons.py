"""Jetson add-ons over Jetlink's cable: V-ASM and sign vision run on the Jetson when it answers.

Each add-on is an extra interface on Jetlink's USB gadget (jetlink.comma.functions), declared by the adapter's
gadget_functions while its setting is on. The owner reads them once at start, so a change takes effect after a
reboot. The comma side asks the Jetson first and falls back to its own CPU model on any doubt (jetlink.addons).

The top level imports the standard library only: the gadget owner (jetlinkd) reads this through the adapter.
Ported from Peter Nguyen's Jetson work (trung791997/openpilot); see CREDITS.md.
"""
from __future__ import annotations

import json
import os
import time

# name, interface class (vendor, 'V' 'A' / 'S' 'L'), setting
VASM = ("vasm", (0xFF, 0x56, 0x41), "JetlinkVasm")
SIGNS = ("slv", (0xFF, 0x53, 0x4C), "JetlinkSignVision")
ADDONS = {"vasm": VASM, "signs": SIGNS}
STATS = {"vasm": "/tmp/vasm_jetson_stats.json", "signs": "/tmp/signs_jetson_stats.json"}
STATS_FRESH_S = 150.0


def gadget_function(addon: str):
  from jetlink.comma.functions import GadgetFunction
  name, iface_class, _ = ADDONS[addon]
  return GadgetFunction(name, iface_class)


def gadget_functions(params) -> tuple:
  """The interfaces the adapter declares: one per add-on whose setting is on. params reads the store as files."""
  return tuple(gadget_function(addon) for addon, (_, _, key) in ADDONS.items() if params.get_bool(key))


def present(addon: str) -> bool:
  """The gadget carries this add-on's interface now: it was on when the owner last built it."""
  try:
    from jetlink.comma.functions import present as function_present
    return function_present(gadget_function(addon))
  except ImportError:
    return False


def with_jetson(net, addon: str, sha256: str, outputs, *, scaled_u8: bool = False, params=None):
  """The net, asking the Jetson first while the add-on's setting is on; the plain net otherwise or on failure."""
  try:
    if params is None:
      from openpilot.common.params import Params
      params = Params()
    if not params.get_bool(ADDONS[addon][2]):
      return net
    from jetlink.addons.link import with_host
    return with_host(net, gadget_function(addon), sha256, outputs, scaled_u8=scaled_u8,
                     report=lambda report: write_stats(addon, report))
  except Exception:
    return net


def write_stats(addon: str, report: dict) -> None:
  path = STATS[addon]
  try:
    with open(path + ".tmp", "w") as f:
      json.dump(report, f)
    os.replace(path + ".tmp", path)
  except OSError:
    pass


def read_stats(addon: str, now: float | None = None) -> dict | None:
  """The add-on's last report, while fresh."""
  path = STATS[addon]
  try:
    if (now if now is not None else time.time()) - os.path.getmtime(path) > STATS_FRESH_S:  # noqa: TID251 (file times are wall clock)
      return None
    with open(path) as f:
      report = json.load(f)
  except (OSError, ValueError):
    return None
  return report if isinstance(report, dict) else None
