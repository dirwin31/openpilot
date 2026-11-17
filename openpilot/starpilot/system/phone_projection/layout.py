"""Split-screen geometry shared by the 3X screen, the StarPort car screen and the daemon."""
from __future__ import annotations

from dataclasses import dataclass

# The picture each phone is asked for: half of a 3X screen, or about half of a 16:9 car screen through StarPort.
PANEL_SIZES = {"tici": (1080, 1080), "tizi": (1080, 1080), "mici": (960, 1080)}
DEFAULT_PANEL = (1080, 1080)
# Android Auto renders one of its fixed resolutions; the margin centres a panel-shaped picture inside it.
ANDROID_RESOLUTION = (1920, 1080)
ANDROID_RESOLUTION_INDEX = 3
# CarPlay draws the same layout with more detail when asked for more pixels at the same physical size.
CARPLAY_SCALE = 1.5


@dataclass(frozen=True)
class Rect:
  x: float
  y: float
  width: float
  height: float

  def contains(self, x: float, y: float) -> bool:
    return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


def panel_size(device_type: str) -> tuple[int, int]:
  return PANEL_SIZES.get(device_type, DEFAULT_PANEL)


def carplay_size(panel: tuple[int, int]) -> tuple[int, int]:
  return tuple(round(side * CARPLAY_SCALE / 2) * 2 for side in panel)


def split(rect: Rect, side: str) -> tuple[Rect, Rect]:
  """(comma half, phone half) of rect; the phone takes the chosen side."""
  half = round(rect.width / 2)
  left = Rect(rect.x, rect.y, half, rect.height)
  right = Rect(rect.x + half, rect.y, rect.width - half, rect.height)
  return (left, right) if side == "right" else (right, left)


def fit(width: float, height: float, rect: Rect) -> Rect:
  """The largest width:height picture centred in rect."""
  if width <= 0 or height <= 0 or rect.width <= 0 or rect.height <= 0:
    return rect
  scale = min(rect.width / width, rect.height / height)
  w, h = width * scale, height * scale
  return Rect(rect.x + (rect.width - w) / 2, rect.y + (rect.height - h) / 2, w, h)


def android_margins(panel: tuple[int, int]) -> tuple[int, int]:
  """Margins that leave a panel-shaped picture in the middle of the Android Auto frame."""
  width, height = ANDROID_RESOLUTION
  aspect = panel[0] / panel[1]
  if width / height > aspect:
    content = min(width, int(round(height * aspect)) // 2 * 2)
    return width - content, 0
  content = min(height, int(round(width / aspect)) // 2 * 2)
  return 0, height - content


def android_crop(panel: tuple[int, int]) -> tuple[int, int, int, int]:
  """The part of the Android Auto frame the phone draws into: (x, y, width, height)."""
  margin_w, margin_h = android_margins(panel)
  width, height = ANDROID_RESOLUTION
  return margin_w // 2, margin_h // 2, width - margin_w, height - margin_h
