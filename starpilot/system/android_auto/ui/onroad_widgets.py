"""Car versions of onroad widgets, used by CarOnroadView (onroad.py)."""

from __future__ import annotations

from collections.abc import Callable

import pyray as rl
from cereal import log

from openpilot.selfdrive.ui.lib.starpilot_status import ENGAGED_COLOR, EXPERIMENTAL_COLOR, TRAFFIC_COLOR
from openpilot.selfdrive.ui.onroad.exp_button import ExpButton
from openpilot.selfdrive.ui.onroad.alert_renderer import (
  ALERT_PADDING,
  MID_FONT_SIZE_1,
  MID_FONT_SIZE_2,
  SMALL_FONT_SIZE,
  Alert,
  AlertRenderer,
)
from openpilot.selfdrive.ui.onroad.hud_renderer import COLORS, CRUISE_DISABLED_CHAR, FONT_SIZES, HudRenderer
from openpilot.selfdrive.ui.onroad.starpilot.navigation_card import NavigationCardRenderer
from openpilot.selfdrive.ui.onroad.starpilot.pip_sidecam import PipSideCamera
from openpilot.selfdrive.ui.onroad.starpilot.slc_speed_limit import render_speed_limit_at
from openpilot.selfdrive.ui.onroad.starpilot.widget_style import draw_control_card
from openpilot.selfdrive.ui.onroad.starpilot.widgets import SetSpeedWidget, SpeedLimitWidget, StoppedTimerWidget
from openpilot.selfdrive.ui.ui_state import UIStatus, ui_state
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import draw_text_with_shadow, measure_text_cached

AlertSize = log.SelfdriveState.AlertSize

# The side-camera bubbles sit over the bottom alert band. They already show the
# lane change, so these banners are dropped when a bubble would cover their text.
BUBBLE_REDUNDANT_ALERTS = frozenset({
  "preLaneChangeLeft",
  "preLaneChangeRight",
  "laneChange",
  "laneChangeBlocked",
  "laneChangeBlockedLoud",
})

LATERAL_PAUSE_WIDTH = 220
LATERAL_PAUSE_HEIGHT = 96
TORQUE_BAR_MAX_RISE = 82  # max offset + thickness in torque_bar.py
TORQUE_BAR_GAP = 24

# The steering-wheel (experimental mode) button and the column under it (pedals)
# sit closer to the right edge than on the comma, leaving the stopped timer more room.
EXP_BUTTON_SIZE = 160
EXP_ICON_SIZE = 120
RIGHT_COLUMN_ANCHOR = 130  # column centre, from the camera pane's right edge (the comma uses 146)
SET_SPEED_LABEL_TOP = 8    # "MAX" sits at the card's top edge, like the speed-limit card's source label
SET_SPEED_VALUE_FONT = 100


def lateral_pause_rect(camera_rect: rl.Rectangle, display_width: float) -> rl.Rectangle:
  """Place the car's pause badge above the torque bar, centered in the camera pane."""
  torque_scale = camera_rect.height / 240.0 * (camera_rect.width / max(1.0, display_width))
  torque_bar_top = camera_rect.y + camera_rect.height - TORQUE_BAR_MAX_RISE * torque_scale
  return rl.Rectangle(
    camera_rect.x + (camera_rect.width - LATERAL_PAUSE_WIDTH) / 2,
    torque_bar_top - TORQUE_BAR_GAP - LATERAL_PAUSE_HEIGHT,
    LATERAL_PAUSE_WIDTH,
    LATERAL_PAUSE_HEIGHT,
  )


def render_lateral_paused(rect: rl.Rectangle, font: rl.Font):
  """Larger labeled lateral-pause badge."""
  rl.draw_rectangle_rounded(rect, 0.28, 12, rl.Color(0, 0, 0, 166))
  rl.draw_rectangle_rounded_lines_ex(rect, 0.28, 12, 5, TRAFFIC_COLOR)

  cy = rect.y + rect.height / 2.0
  label_center_x = rect.x + rect.width * 0.29
  icon_cx = rect.x + rect.width * 0.72
  font_size = max(1, int(rect.height * 0.44))
  label_size = measure_text_cached(font, "Lat", font_size)
  label_pos = rl.Vector2(label_center_x - label_size.x / 2, cy - label_size.y / 2)
  draw_text_with_shadow(font, "Lat", label_pos, font_size, rl.WHITE, shadow_alpha=190)

  # A larger version of the comma's curved-arrow pause symbol.
  rl.draw_ring(rl.Vector2(int(icon_cx), int(cy)), 26, 32, 45, 315, 0, rl.Color(255, 255, 255, 120))
  rl.draw_triangle(
    rl.Vector2(int(icon_cx + 16), int(cy - 27)),
    rl.Vector2(int(icon_cx + 34), int(cy - 16)),
    rl.Vector2(int(icon_cx + 27), int(cy - 34)),
    rl.Color(255, 255, 255, 120),
  )
  rl.draw_rectangle(int(icon_cx - 11), int(cy - 21), 7, 42, rl.WHITE)
  rl.draw_rectangle(int(icon_cx + 4), int(cy - 21), 7, 42, rl.WHITE)


class CarAlertRenderer(AlertRenderer):
  """Alerts that can be drawn after the side camera and dropped where it covers them."""

  def __init__(self):
    super().__init__()
    # Event names (the part of alertType before "/") dropped while their text would
    # land under something drawn on top, as reported by covers(text_rect).
    self.hidden_alert_names: frozenset[str] = frozenset()
    self.covers: Callable[[rl.Rectangle], bool] | None = None
    # Deferred: the road view's own pass skips the alert; the owner draws it later.
    self.deferred = False
    self._cover_rect = rl.Rectangle(0, 0, 0, 0)

  def render(self, rect: rl.Rectangle | None = None):
    if self.deferred:
      return None
    return super().render(rect)

  def _render(self, rect: rl.Rectangle):
    self._cover_rect = rect
    return super()._render(rect)

  def get_alert(self, sm) -> Alert | None:
    alert = super().get_alert(sm)
    if alert is not None and self._is_covered(alert, self._cover_rect):
      self._prev_alert = None  # vanish rather than slide out from under the cover
      return None
    return alert

  def _is_covered(self, alert: Alert, rect: rl.Rectangle) -> bool:
    if self.covers is None or (alert.alert_type or "").split("/", 1)[0] not in self.hidden_alert_names:
      return False
    return self.covers(self._text_bounds(alert, rect))

  def _text_bounds(self, alert: Alert, rect: rl.Rectangle) -> rl.Rectangle:
    """Where a settled alert's text lands: text1 centred in its band, text2 just below."""
    band = self._get_alert_rect(rect, alert.size)
    if alert.size == AlertSize.full:
      return band
    max_width = int(band.width - ALERT_PADDING * 2)
    lines = [(self._alert_text1_label, alert.text1, SMALL_FONT_SIZE if alert.size == AlertSize.small else MID_FONT_SIZE_1)]
    if alert.size == AlertSize.mid and alert.text2:
      lines.append((self._alert_text2_label, alert.text2, MID_FONT_SIZE_2))
    width = height = text1_height = 0.0
    for label, text, font_size in lines:
      label.set_text(text)
      label.set_font_size(font_size)
      line_height = label.get_content_height(max_width)
      text1_height = text1_height or line_height
      height += line_height
      width = max(width, label.text_width)
    return rl.Rectangle(band.x + (band.width - width) / 2, band.y + (band.height - text1_height) / 2, width, height)


class CarPipSideCamera(PipSideCamera):
  """Remembers what the last frame drew, so the view can lay alerts out around it."""

  def __init__(self):
    super().__init__()
    self._drawn: list[tuple[str, rl.Rectangle]] = []  # ("bubble" | "curved", rect)

  def _render(self, content_rect: rl.Rectangle):
    self._drawn = []
    return super()._render(content_rect)

  def _blind_spot_monitors_visible(self) -> bool:
    return ui_state.android_auto_blind_spot_monitors_visible

  def _draw_bubble(self, bubble: rl.Rectangle, crop: rl.Rectangle):
    super()._draw_bubble(bubble, crop)
    self._drawn.append(("bubble", bubble))

  def _draw_curved(self, content_rect: rl.Rectangle, crop: rl.Rectangle):
    super()._draw_curved(content_rect, crop)
    self._drawn.append(("curved", content_rect))

  @property
  def showing(self) -> bool:
    return bool(self._drawn)

  def covers(self, area: rl.Rectangle) -> bool:
    """Whether the last frame's side camera overlaps area."""
    for shape, rect in self._drawn:
      if shape == "curved":
        if rect.x < area.x + area.width and area.x < rect.x + rect.width and \
           rect.y < area.y + area.height and area.y < rect.y + rect.height:
          return True
        continue
      radius = rect.width / 2
      cx, cy = rect.x + radius, rect.y + radius
      nearest_x = min(max(cx, area.x), area.x + area.width)
      nearest_y = min(max(cy, area.y), area.y + area.height)
      if (cx - nearest_x) ** 2 + (cy - nearest_y) ** 2 < radius ** 2:
        return True
    return False


class CarStoppedTimerWidget(StoppedTimerWidget):
  """A compact "Stopped" label and mm:ss timer in place of the speed readout.

  Centred in the space between the MAX / LIMIT column and the steering-wheel
  column, which is narrower on the car, rather than on the whole pane.
  """

  LEFT_CONTROLS_RESERVE = 290  # MAX / LIMIT column
  RIGHT_CONTROLS_RESERVE = RIGHT_COLUMN_ANCHOR + 90  # steering wheel and the 180-wide pedal icons below it
  LABEL_FONT = 104
  TIMER_FONT = 88
  HORIZONTAL_MARGIN = 38
  TEXT_TOP = 92
  LINE_GAP = 8

  def _render(self, rect: rl.Rectangle) -> None:
    duration = self._duration
    label_text, timer_text = "Stopped", f"{duration // 60:02d}:{duration % 60:02d}"
    full_width = measure_text_cached(self._font_bold, label_text, self.LABEL_FONT).x
    left = rect.x + self.LEFT_CONTROLS_RESERVE + self.HORIZONTAL_MARGIN
    right = rect.x + rect.width - self.RIGHT_CONTROLS_RESERVE - self.HORIZONTAL_MARGIN
    available = max(1.0, right - left)
    scale = min(1.0, available / full_width) if full_width > 0 else 1.0
    scale = max(0.6, scale)
    label_font = max(1, int(self.LABEL_FONT * scale))
    timer_font = max(1, int(self.TIMER_FONT * scale))
    label_size = measure_text_cached(self._font_bold, label_text, label_font)
    timer_size = measure_text_cached(self._font_normal, timer_text, timer_font)

    duration_color = self._duration_color()
    center_x = (left + right) / 2 if right > left else rect.x + rect.width / 2
    label_y = rect.y + self.TEXT_TOP
    timer_y = label_y + label_size.y + self.LINE_GAP
    self._draw_text(self._font_bold, label_text, rl.Vector2(center_x - label_size.x / 2, label_y), label_font, duration_color)
    self._draw_text(self._font_normal, timer_text, rl.Vector2(center_x - timer_size.x / 2, timer_y), timer_font, rl.WHITE)

  @staticmethod
  def _draw_text(font: rl.Font, text: str, pos: rl.Vector2, font_size: int, color: rl.Color) -> None:
    """Draw a soft dark drop shadow without an opaque panel behind the car HUD."""
    for offset_x, offset_y, alpha in ((5, 6, 70), (3, 4, 125), (1, 2, 190)):
      rl.draw_text_ex(font, text, rl.Vector2(pos.x + offset_x, pos.y + offset_y), font_size, 0, rl.Color(0, 0, 0, alpha))
    rl.draw_text_ex(font, text, pos, font_size, 0, color)

  def _duration_color(self) -> rl.Color:
    duration = self._duration
    if duration < 150:
      return self._blend_colors(ENGAGED_COLOR, EXPERIMENTAL_COLOR, (duration - 60) / 90.0)
    if duration < 300:
      return self._blend_colors(EXPERIMENTAL_COLOR, TRAFFIC_COLOR, (duration - 150) / 150.0)
    return TRAFFIC_COLOR


class CarSetSpeedWidget(SetSpeedWidget):
  """MAX laid out like the speed-limit card below it: the label tight to the top edge,
  the value large in the space left under it."""

  def _render(self, rect: rl.Rectangle) -> None:
    draw_control_card(rect)
    hud = self.hud_renderer
    max_color, value_color = COLORS.GREY, COLORS.DARK_GREY
    if hud.is_cruise_set:
      value_color = COLORS.WHITE
      if ui_state.status == UIStatus.ENGAGED:
        max_color = COLORS.ENGAGED
      elif ui_state.status in (UIStatus.DISENGAGED, UIStatus.OVERRIDE):
        max_color = COLORS.DISENGAGED

    label = tr("MAX")
    label_size = measure_text_cached(self._font_semi_bold, label, FONT_SIZES.max_speed)
    label_y = rect.y + SET_SPEED_LABEL_TOP
    rl.draw_text_ex(self._font_semi_bold, label, rl.Vector2(rect.x + (rect.width - label_size.x) / 2, label_y),
                    FONT_SIZES.max_speed, 0, max_color)

    value = CRUISE_DISABLED_CHAR if not hud.is_cruise_set else str(round(hud.set_speed))
    font_size = SET_SPEED_VALUE_FONT
    value_size = measure_text_cached(self._font_bold, value, font_size)
    if value_size.x > rect.width - 16:  # three digits in km/h
      font_size = max(1, int(font_size * (rect.width - 16) / value_size.x))
      value_size = measure_text_cached(self._font_bold, value, font_size)
    top = label_y + label_size.y
    value_y = top + (rect.y + rect.height - top - value_size.y) / 2
    rl.draw_text_ex(self._font_bold, value, rl.Vector2(rect.x + (rect.width - value_size.x) / 2, value_y),
                    font_size, 0, value_color)


class CarSpeedLimitWidget(SpeedLimitWidget):
  def _render(self, rect: rl.Rectangle) -> None:
    if self._slc_state is None:
      return
    # Beside the map the per-source list is noise; the sign alone is enough.
    expanded = ui_state.ui_params.get_bool("SpeedLimitSources") and not ui_state.nav_map_beside_road
    self._sign_rect = render_speed_limit_at(self._slc_state, rect, expanded)


class CarNavigationCardRenderer(NavigationCardRenderer):
  def render(self, rect: rl.Rectangle | None = None):
    if ui_state.nav_map_beside_road:  # the map beside the road shows the same turn, larger
      return None
    return super().render(rect)


class CarHudRenderer(HudRenderer):
  def __init__(self):
    super().__init__()
    self._exp_button = ExpButton(EXP_BUTTON_SIZE, EXP_ICON_SIZE)

  def _create_navigation_card(self):
    return CarNavigationCardRenderer()


class NoFavoriteMenu:
  """The car routes onroad touches to its own quick menu, never the radial menu."""

  def process_mouse_events(self, events, rect) -> bool:
    return False

  def blocks_pointer(self, mouse_pos) -> bool:
    return False

  def collapse(self) -> None:
    pass

  def render(self, rect) -> None:
    pass

  def render_corner_hint(self, rect) -> None:
    pass
