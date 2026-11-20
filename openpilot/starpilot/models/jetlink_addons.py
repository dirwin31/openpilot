"""Jetson add-ons over Jetlink's cable: V-ASM and sign vision ask the Jetson first, switched live by their settings."""
from __future__ import annotations

import json
import os
import time
from typing import NamedTuple


class Addon(NamedTuple):
  name: str
  iface_class: tuple[int, int, int]
  setting: str


# interface class: vendor, then 'V' 'A' / 'S' 'L'
ADDONS = {"vasm": Addon("vasm", (0xFF, 0x56, 0x41), "JetlinkVasm"),
          "signs": Addon("slv", (0xFF, 0x53, 0x4C), "JetlinkSignVision")}
STATS = {"vasm": "/tmp/vasm_jetson_stats.json", "signs": "/tmp/signs_jetson_stats.json"}
STATS_FRESH_S = 75.0
SETTING_REFRESH_S = 2.0


def gadget_function(addon: str):
  from jetlink.comma.functions import GadgetFunction
  return GadgetFunction(ADDONS[addon].name, ADDONS[addon].iface_class)


def gadget_functions() -> tuple:
  """Every add-on's interface, whatever its setting: one nothing opens costs nothing."""
  return tuple(gadget_function(addon) for addon in ADDONS)


def live_setting(key: str, params):
  """The setting as a cheap callable, read from the store at most every SETTING_REFRESH_S."""
  read_at: float | None = None
  value = False

  def on() -> bool:
    nonlocal read_at, value
    now = time.monotonic()
    if read_at is None or now - read_at >= SETTING_REFRESH_S:
      read_at, value = now, bool(params.get_bool(key))
    return value
  return on


def present(addon: str) -> bool:
  from jetlink.comma.functions import present as function_present
  return function_present(gadget_function(addon))


def with_jetson(net, addon: str, sha256: str, outputs, *, scaled_u8: bool = False, params=None):
  """The net, asking the Jetson first while the add-on's setting is on."""
  from jetlink.addons.link import with_host
  if params is None:
    from openpilot.common.params import Params
    params = Params()
  return with_host(net, gadget_function(addon), sha256, outputs, scaled_u8=scaled_u8,
                   report=lambda report: write_stats(addon, report), enabled=live_setting(ADDONS[addon].setting, params))


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
