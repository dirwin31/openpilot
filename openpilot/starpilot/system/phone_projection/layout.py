"""Split-screen geometry shared by the 3X screen, the StarPort car screen and the daemon."""
from __future__ import annotations

from dataclasses import dataclass

# The phone half before a car screen has said what it shows: half of a 3X screen, or half of a 1080p car screen.
PANEL_SIZES = {"tici": (1080, 1080), "tizi": (1080, 1080), "mici": (960, 1080)}
DEFAULT_PANEL = (1080, 1080)
# Narrower than this, CarPlay is asked for exactly twice the panel (verify the real minimum on an iPhone).
CARPLAY_MIN_WIDTH = 800
# CarPlay sizes its UI from the physical size; this keeps it the same size on screen for any panel.
CARPLAY_MM_PER_PIXEL = 200 / 960
# Android Auto renders one of its fixed resolutions; margins leave exactly the panel in the middle.
ANDROID_RESOLUTIONS = {1: (800, 480), 2: (1280, 720), 3: (1920, 1080), 6: (720, 1280), 7: (1080, 1920)}


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


def carplay_scale(panel: tuple[int, int]) -> int:
  return 1 if panel[0] >= CARPLAY_MIN_WIDTH else 2


def carplay_size(panel: tuple[int, int]) -> tuple[int, int]:
  """Exactly 1x or 2x the panel, so the picture is never scaled by a fraction."""
  k = carplay_scale(panel)
  return panel[0] * k, panel[1] * k


def carplay_width_mm(panel: tuple[int, int]) -> int:
  return max(1, round(panel[0] * CARPLAY_MM_PER_PIXEL))


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


def snap(rect: Rect) -> Rect:
  """rect on even output pixels, so the picture and its half-resolution colour land on whole pixels."""
  x0, y0 = _even(rect.x), _even(rect.y)
  return Rect(x0, y0, _even(rect.x + rect.width) - x0, _even(rect.y + rect.height) - y0)


def _even(value: float) -> int:
  return int(round(value / 2)) * 2


def android_mode(panel: tuple[int, int]) -> tuple[int, tuple[int, int]]:
  """(resolution index, size): the smallest Android Auto resolution that holds the panel."""
  fits = [(w * h, index, (w, h)) for index, (w, h) in ANDROID_RESOLUTIONS.items() if w >= panel[0] and h >= panel[1]]
  if not fits:
    return 3, ANDROID_RESOLUTIONS[3]
  _, index, size = min(fits)
  return index, size


def android_margins(panel: tuple[int, int]) -> tuple[int, int]:
  """Margins that leave exactly the panel (or the largest panel-shaped picture) in the middle of the frame."""
  _, (width, height) = android_mode(panel)
  if panel[0] <= width and panel[1] <= height:
    return width - panel[0], height - panel[1]
  aspect = panel[0] / panel[1]
  if width / height > aspect:
    content = min(width, int(round(height * aspect)) // 2 * 2)
    return width - content, 0
  content = min(height, int(round(width / aspect)) // 2 * 2)
  return 0, height - content


def android_crop(panel: tuple[int, int]) -> tuple[int, int, int, int]:
  """The part of the Android Auto frame the phone draws into: (x, y, width, height)."""
  margin_w, margin_h = android_margins(panel)
  _, (width, height) = android_mode(panel)
  return margin_w // 2, margin_h // 2, width - margin_w, height - margin_h
