"""The StarPort car screen split: the comma view gets one half of the car canvas, the phone the other."""
from __future__ import annotations

from dataclasses import dataclass

from openpilot.starpilot.system.phone_projection import settings as projection_settings
from openpilot.starpilot.system.phone_projection.layout import Rect, split


@dataclass(frozen=True)
class CarSplit:
  comma: Rect
  phone: Rect
  width: float
  height: float

  @property
  def viewport(self) -> tuple[int, int]:
    return int(self.comma.width), int(self.comma.height)

  def route(self, events, phone) -> list:
    """Touches normalized over the car canvas: the phone takes its own, the rest are renormalized for the comma view."""
    routed = []
    for event in events:
      x, y = event.x * self.width, event.y * self.height
      if phone.touch(event.kind, 0, x, y):
        continue
      if event.kind == "cancel":
        routed.append(event)
        continue
      if event.kind == "down" and not self.comma.contains(x, y):
        continue
      routed.append(type(event)(event.kind, min(max((x - self.comma.x) / self.comma.width, 0.0), 1.0),
                                min(max((y - self.comma.y) / self.comma.height, 0.0), 1.0)))
    return routed


def car_split(width: float, height: float, settings: projection_settings.Settings | None = None) -> CarSplit | None:
  """The split for a car canvas of this logical size, or None while phone projection is off."""
  settings = settings or projection_settings.load()
  if not settings.enabled:
    return None
  comma, phone = split(Rect(0, 0, width, height), settings.side)
  return CarSplit(comma, phone, width, height)
