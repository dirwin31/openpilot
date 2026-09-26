from __future__ import annotations

import pyray as rl
from openpilot.selfdrive.ui.lib.starpilot_status import TRAFFIC_COLOR
from openpilot.system.ui.lib.text_measure import draw_text_with_shadow, measure_text_cached


ANDROID_AUTO_LATERAL_PAUSE_WIDTH = 220
ANDROID_AUTO_LATERAL_PAUSE_HEIGHT = 96
ANDROID_AUTO_TORQUE_BAR_MAX_RISE = 82  # max offset + thickness in torque_bar.py
ANDROID_AUTO_TORQUE_BAR_GAP = 24


def draw_pause_symbol(cx: float, cy: float):
  # Draw two vertical bars || in the center
  rl.draw_rectangle(int(cx - 8), int(cy - 16), 5, 32, rl.WHITE)
  rl.draw_rectangle(int(cx + 3), int(cy - 16), 5, 32, rl.WHITE)


def render_lateral_paused(rect: rl.Rectangle):
  # Draw background & red border
  rl.draw_rectangle_rounded(rect, 0.3, 10, rl.Color(0, 0, 0, 166))
  rl.draw_rectangle_rounded_lines_ex(rect, 0.3, 10, 4, TRAFFIC_COLOR)

  cx = rect.x + rect.width / 2.0
  cy = rect.y + rect.height / 2.0

  # Draw turn/curved arrow icon (translucent)
  rl.draw_ring(rl.Vector2(int(cx), int(cy)), 20, 24, 45, 315, 0, rl.Color(255, 255, 255, 100))
  # Arrowhead
  rl.draw_triangle(
    rl.Vector2(int(cx + 12), int(cy - 20)),
    rl.Vector2(int(cx + 25), int(cy - 12)),
    rl.Vector2(int(cx + 20), int(cy - 25)),
    rl.Color(255, 255, 255, 100)
  )

  # Draw pause overlay
  draw_pause_symbol(cx, cy)


def android_auto_lateral_pause_rect(camera_rect: rl.Rectangle, display_width: float) -> rl.Rectangle:
  """Place the car-only pause badge above the torque bar, centered in the camera pane."""
  torque_scale = camera_rect.height / 240.0 * (camera_rect.width / max(1.0, display_width))
  torque_bar_top = camera_rect.y + camera_rect.height - ANDROID_AUTO_TORQUE_BAR_MAX_RISE * torque_scale
  return rl.Rectangle(
    camera_rect.x + (camera_rect.width - ANDROID_AUTO_LATERAL_PAUSE_WIDTH) / 2,
    torque_bar_top - ANDROID_AUTO_TORQUE_BAR_GAP - ANDROID_AUTO_LATERAL_PAUSE_HEIGHT,
    ANDROID_AUTO_LATERAL_PAUSE_WIDTH,
    ANDROID_AUTO_LATERAL_PAUSE_HEIGHT,
  )


def render_android_auto_lateral_paused(rect: rl.Rectangle, font: rl.Font):
  """Larger labeled lateral-pause badge used only by the Android Auto car UI."""
  rl.draw_rectangle_rounded(rect, 0.28, 12, rl.Color(0, 0, 0, 166))
  rl.draw_rectangle_rounded_lines_ex(rect, 0.28, 12, 5, TRAFFIC_COLOR)

  cy = rect.y + rect.height / 2.0
  label_center_x = rect.x + rect.width * 0.29
  icon_cx = rect.x + rect.width * 0.72
  font_size = max(1, int(rect.height * 0.44))
  label_size = measure_text_cached(font, "Lat", font_size)
  label_pos = rl.Vector2(label_center_x - label_size.x / 2, cy - label_size.y / 2)
  draw_text_with_shadow(font, "Lat", label_pos, font_size, rl.WHITE, shadow_alpha=190)

  # A larger car-screen version of the existing curved-arrow pause symbol.
  rl.draw_ring(rl.Vector2(int(icon_cx), int(cy)), 26, 32, 45, 315, 0, rl.Color(255, 255, 255, 120))
  rl.draw_triangle(
    rl.Vector2(int(icon_cx + 16), int(cy - 27)),
    rl.Vector2(int(icon_cx + 34), int(cy - 16)),
    rl.Vector2(int(icon_cx + 27), int(cy - 34)),
    rl.Color(255, 255, 255, 120),
  )
  rl.draw_rectangle(int(icon_cx - 11), int(cy - 21), 7, 42, rl.WHITE)
  rl.draw_rectangle(int(icon_cx + 4), int(cy - 21), 7, 42, rl.WHITE)


def render_longitudinal_paused(rect: rl.Rectangle):
  # Draw background & red border
  rl.draw_rectangle_rounded(rect, 0.3, 10, rl.Color(0, 0, 0, 166))
  rl.draw_rectangle_rounded_lines_ex(rect, 0.3, 10, 4, TRAFFIC_COLOR)

  cx = rect.x + rect.width / 2.0
  cy = rect.y + rect.height / 2.0

  # Draw speedometer arc (translucent)
  rl.draw_ring(rl.Vector2(int(cx), int(cy + 8)), 20, 24, -45, 225, 0, rl.Color(255, 255, 255, 100))
  # Needle
  rl.draw_line_ex(
    rl.Vector2(int(cx), int(cy + 8)),
    rl.Vector2(int(cx + 14), int(cy - 6)),
    3,
    rl.Color(255, 255, 255, 100)
  )

  # Draw pause overlay
  draw_pause_symbol(cx, cy)
