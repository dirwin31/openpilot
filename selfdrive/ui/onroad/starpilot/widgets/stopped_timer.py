import time
from collections.abc import Callable

import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.lib.starpilot_status import (
  ENGAGED_COLOR, EXPERIMENTAL_COLOR, TRAFFIC_COLOR,
)
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget


class StoppedTimerWidget(Widget):
  SHOW_AFTER_SECONDS = 60
  MINUTE_FONT = 176
  SECOND_FONT = 66
  BESIDE_MAP_SCALE = 0.5   # the car view's driving pane is narrower with the map beside it
  MAX_WIDTH_FRACTION = 0.8
  LEFT_CONTROLS_RESERVE = 290  # MAX / LIMIT column; the narrow car-view pane has no room to centre over it
  CAR_LABEL_FONT = 88
  CAR_TIMER_FONT = 72
  CAR_CARD_PADDING_X = 38
  CAR_CARD_PADDING_Y = 28
  CAR_CARD_TOP = 70
  CAR_LINE_GAP = 8

  def __init__(self, in_reverse: Callable[[], bool] | None = None):
    super().__init__()
    self.set_enabled(False)
    self._in_reverse = in_reverse or (lambda: False)
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_normal = gui_app.font(FontWeight.NORMAL)
    self._standstill_started_at: float | None = None
    self._started_frame = -1
    self._duration = 0

  @property
  def is_visible(self) -> bool:
    return self._update_timer() > 0

  @property
  def replaces_current_speed(self) -> bool:
    return self.is_visible

  def _update_timer(self) -> int:
    started_frame = getattr(ui_state, "started_frame", 0)
    if started_frame != self._started_frame:
      self._started_frame = started_frame
      self._reset_timer()

    params = ui_state.ui_params
    if (not ui_state.started or
        not params.get_bool("QOLVisuals") or
        not params.get_bool("StoppedTimer") or
        not ui_state.sm.valid.get("carState", False)):
      self._reset_timer()
      return 0

    if self._in_reverse():
      self._reset_timer()
      return 0

    try:
      if ui_state.sm.recv_frame["carState"] < started_frame:
        self._reset_timer()
        return 0
    except (AttributeError, KeyError, TypeError):
      pass

    if not getattr(ui_state.sm["carState"], "standstill", False):
      self._reset_timer()
      return 0

    now = time.monotonic()
    if self._standstill_started_at is None:
      self._standstill_started_at = now

    if now - getattr(ui_state, "started_time", 0.0) < self.SHOW_AFTER_SECONDS:
      self._duration = 0
      return 0

    self._duration = max(0, int(now - self._standstill_started_at))
    return self._duration

  def _reset_timer(self) -> None:
    self._standstill_started_at = None
    self._duration = 0

  @staticmethod
  def _format_duration_text(duration: int) -> tuple[str, str]:
    minutes = duration // 60
    seconds = duration % 60
    return (
      f"{minutes} minute{'s' if minutes != 1 else ''}",
      f"{seconds} second{'s' if seconds != 1 else ''}",
    )

  @staticmethod
  def _format_car_duration(duration: int) -> tuple[str, str]:
    return "Stopped", f"{duration // 60:02d}:{duration % 60:02d}"

  def _scale(self, minute_text: str, width: float) -> float:
    scale = self.BESIDE_MAP_SCALE if getattr(ui_state, "nav_map_beside_road", False) else 1.0
    full_width = measure_text_cached(self._font_bold, minute_text, self.MINUTE_FONT).x
    if full_width > 0:
      scale = min(scale, self.MAX_WIDTH_FRACTION * width / full_width)
    return max(0.3, scale)

  def _render(self, rect: rl.Rectangle) -> None:
    duration = self._duration

    if getattr(ui_state, "android_auto_car_view", False):
      self._render_car_timer(rect, *self._format_car_duration(duration))
      return

    minute_text, second_text = self._format_duration_text(duration)
    left = rect.x
    width = rect.width
    if getattr(ui_state, "nav_map_beside_road", False):
      left += self.LEFT_CONTROLS_RESERVE
      width = max(1.0, width - self.LEFT_CONTROLS_RESERVE)
    scale = self._scale(minute_text, width)
    minute_font = int(self.MINUTE_FONT * scale)  # round down so a fitted size never overflows
    second_font = int(self.SECOND_FONT * scale)
    minute_size = measure_text_cached(self._font_bold, minute_text, minute_font)
    second_size = measure_text_cached(self._font_normal, second_text, second_font)
    # Full size keeps the original layout; smaller sizes stay centred where the speed would be.
    minute_bottom = rect.y + (210 if scale >= 1.0 else 180 + minute_size.y / 2)
    second_bottom = minute_bottom + 80 * scale

    duration_color = self._duration_color()

    center_x = left + width / 2
    rl.draw_text_ex(
      self._font_bold,
      minute_text,
      rl.Vector2(center_x - minute_size.x / 2, minute_bottom - minute_size.y),
      minute_font,
      0,
      duration_color,
    )
    rl.draw_text_ex(
      self._font_normal,
      second_text,
      rl.Vector2(center_x - second_size.x / 2, second_bottom - second_size.y),
      second_font,
      0,
      rl.Color(255, 255, 255, 255),
    )

  def _render_car_timer(self, rect: rl.Rectangle, label_text: str, timer_text: str) -> None:
    """Replace the Android Auto speed readout with a compact stopped timer."""
    full_width = measure_text_cached(self._font_bold, label_text, self.CAR_LABEL_FONT).x
    available = max(1.0, rect.width - 2 * (self.LEFT_CONTROLS_RESERVE + self.CAR_CARD_PADDING_X))
    scale = min(1.0, available / full_width) if full_width > 0 else 1.0
    scale = max(0.6, scale)
    label_font = max(1, int(self.CAR_LABEL_FONT * scale))
    timer_font = max(1, int(self.CAR_TIMER_FONT * scale))
    label_size = measure_text_cached(self._font_bold, label_text, label_font)
    timer_size = measure_text_cached(self._font_normal, timer_text, timer_font)

    card_width = min(rect.width - 32, max(label_size.x, timer_size.x) + 2 * self.CAR_CARD_PADDING_X)
    card_height = label_size.y + timer_size.y + self.CAR_LINE_GAP + 2 * self.CAR_CARD_PADDING_Y
    card = rl.Rectangle(
      rect.x + (rect.width - card_width) / 2,
      rect.y + self.CAR_CARD_TOP,
      card_width,
      card_height,
    )

    duration_color = self._duration_color()
    shadow = rl.Rectangle(card.x + 8, card.y + 10, card.width, card.height)
    rl.draw_rectangle_rounded(shadow, 0.18, 12, rl.Color(0, 0, 0, 145))
    rl.draw_rectangle_rounded(card, 0.18, 12, rl.Color(8, 11, 18, 238))
    rl.draw_rectangle_rounded_lines_ex(card, 0.18, 12, 3, duration_color)

    center_x = card.x + card.width / 2
    label_y = card.y + self.CAR_CARD_PADDING_Y
    timer_y = label_y + label_size.y + self.CAR_LINE_GAP
    rl.draw_text_ex(self._font_bold, label_text,
                    rl.Vector2(center_x - label_size.x / 2, label_y), label_font, 0, duration_color)
    rl.draw_text_ex(self._font_normal, timer_text,
                    rl.Vector2(center_x - timer_size.x / 2, timer_y), timer_font, 0, rl.WHITE)

  def _duration_color(self) -> rl.Color:
    duration = self._duration
    if duration < 150:
      return self._blend_colors(ENGAGED_COLOR, EXPERIMENTAL_COLOR, (duration - 60) / 90.0)
    if duration < 300:
      return self._blend_colors(EXPERIMENTAL_COLOR, TRAFFIC_COLOR, (duration - 150) / 150.0)
    return TRAFFIC_COLOR

  @staticmethod
  def _blend_colors(start: rl.Color, end: rl.Color, transition: float) -> rl.Color:
    transition = min(max(transition, 0.0), 1.0)
    return rl.Color(
      int(start.r + transition * (end.r - start.r)),
      int(start.g + transition * (end.g - start.g)),
      int(start.b + transition * (end.b - start.b)),
      255,
    )
