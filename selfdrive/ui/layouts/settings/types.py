"""Stable settings routes shared by the native and projected main layouts."""

from dataclasses import dataclass
from enum import IntEnum
import pyray as rl
from openpilot.system.ui.widgets import Widget


class PanelType(IntEnum):
  STARPILOT = 0
  DEVICE = 1
  NETWORK = 2
  BLUETOOTH = 3
  TOGGLES = 4
  SOFTWARE = 5
  DEVELOPER = 6


@dataclass
class PanelInfo:
  name: str
  instance: Widget
  button_rect: rl.Rectangle = rl.Rectangle(0, 0, 0, 0)


