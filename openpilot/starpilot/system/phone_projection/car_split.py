"""The StarPort car screen split: the comma view gets one half of the car canvas, the phone the other."""
from __future__ import annotations

import math
from dataclasses import dataclass

from openpilot.starpilot.system.phone_projection import settings as projection_settings
from openpilot.starpilot.system.phone_projection.layout import Rect


@dataclass(frozen=True)
class CarSplit:
  comma: Rect   # logical canvas units, the comma view's viewport
  phone: Rect   # output pixels in the visible-size target, on even pixels of the car frame
  width: float  # logical canvas
  height: float
  scale: float = 1.0
  left: float = 0.0
  top: float = 0.0

  @property
  def viewport(self) -> tuple[int, int]:
    return int(round(self.comma.width)), int(round(self.comma.height))

  @property
  def target(self) -> tuple[int, int]:
    return int(self.phone.width), int(self.phone.height)

  def route(self, events, phone) -> list:
    """Touches normalized over the car canvas: the phone takes its own, the rest are renormalized for the comma view."""
    routed = []
    for event in events:
      x, y = event.x * self.width, event.y * self.height
      if phone.touch(event.kind, 0, self.left + x * self.scale, self.top + y * self.scale):
        continue
      if event.kind == "cancel":
        routed.append(event)
        continue
      if event.kind == "down" and not self.comma.contains(x, y):
        continue
      routed.append(type(event)(event.kind, min(max((x - self.comma.x) / self.comma.width, 0.0), 1.0),
                                min(max((y - self.comma.y) / self.comma.height, 0.0), 1.0)))
    return routed


def car_layout(geometry, side: str, origin: tuple[int, int] = (0, 0)) -> CarSplit:
  """Phone half in exact output pixels (even in the car frame, same size for either side); the comma gets the rest."""
  scale = geometry.scale
  width, height = geometry.logical_width * scale, geometry.logical_height * scale
  left, top = (geometry.width - width) / 2, (geometry.height - height) / 2
  ox, oy = origin

  def up(v: float, o: int) -> int:
    return math.ceil((v + o) / 2 - 1e-6) * 2 - o

  def down(v: float, o: int) -> int:
    return math.floor((v + o) / 2 + 1e-6) * 2 - o

  y0, y1 = up(top, oy), down(top + height, oy)
  phone_w = int(width / 2) // 2 * 2
  if side == "left":
    x0 = up(left, ox)
    comma_x = (x0 + phone_w - left) / scale
    comma = Rect(comma_x, 0, geometry.logical_width - comma_x, geometry.logical_height)
  else:
    x0 = down(left + width, ox) - phone_w
    comma = Rect(0, 0, (x0 - left) / scale, geometry.logical_height)
  return CarSplit(comma, Rect(x0, y0, phone_w, y1 - y0), geometry.logical_width, geometry.logical_height, scale, left, top)


def layout_viewport(geometry) -> tuple[int, int]:
  """The saved split layout's canvas: the same for either side and any frame origin."""
  return car_layout(geometry, 'right').viewport


def car_split(geometry, origin: tuple[int, int] = (0, 0), settings: projection_settings.Settings | None = None) -> CarSplit | None:
  """The split for this car geometry, or None while phone projection is off."""
  settings = settings or projection_settings.load()
  if not settings.enabled:
    return None
  return car_layout(geometry, settings.side, origin)
