"""The car view's Navigate screen: the one place to pick a destination and start a route.

It opens from the quick menu or the home screen's Navigate button and fills the car
screen: search, favorites and recent destinations on the left, the route preview on
the right. Starting a route (or the Back button) returns to where the driver was.
Onroad it can only be opened below NAV_UNLOCK_MPH, measured by the car's own wheel
speed, and it closes by itself if the car goes faster.
"""

from __future__ import annotations

from collections.abc import Callable

import pyray as rl

from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import Widget

NAV_UNLOCK_MPH = 10.0
MPH_TO_MS = 0.44704
HEADER_HEIGHT = 120.0
BACK_WIDTH = 330.0
BACK_HEIGHT = 84.0
SIDE_MARGIN = 28.0

HEADER_BG = rl.Color(12, 15, 24, 255)
DIVIDER = rl.Color(255, 255, 255, 22)
BACK_BG = rl.Color(30, 40, 60, 255)
BACK_BG_PRESSED = rl.Color(44, 58, 86, 255)
TEXT = rl.Color(240, 244, 250, 255)
SUBTEXT = rl.Color(160, 170, 186, 255)


def speed_allows_navigation(started: bool, speed_ms: float | None) -> bool:
  """Destinations can be set offroad, or onroad below NAV_UNLOCK_MPH of wheel speed.
  An unknown speed onroad (no recent carState) counts as moving."""
  if not started:
    return True
  return speed_ms is not None and abs(speed_ms) < NAV_UNLOCK_MPH * MPH_TO_MS


def locked_text() -> str:
  return tr("Slow below {} mph to set a destination").format(int(NAV_UNLOCK_MPH))


class CarNavigateScreen(Widget):
  def __init__(self, on_started: Callable[[], None], on_close: Callable[[], None]):
    super().__init__()
    from openpilot.selfdrive.ui.layouts.settings.starpilot.navigation import StarPilotNavigationLayout
    self._on_close = on_close
    self.page = StarPilotNavigationLayout(include_offline=False, on_started=on_started)
    self.back_label = tr("Back to driving")
    self._back_pressed = False
    self._font_bold = self._font_medium = None  # loaded on first draw

  def show_event(self):
    super().show_event()
    self.page.show_event()

  def hide_event(self):
    super().hide_event()
    self.page.hide_event()

  def back_rect(self) -> rl.Rectangle:
    return rl.Rectangle(self._rect.x + SIDE_MARGIN, self._rect.y + (HEADER_HEIGHT - BACK_HEIGHT) / 2, BACK_WIDTH, BACK_HEIGHT)

  def _handle_mouse_press(self, mouse_pos) -> None:
    self._back_pressed = rl.check_collision_point_rec(rl.Vector2(mouse_pos.x, mouse_pos.y), self.back_rect())

  def _handle_mouse_release(self, mouse_pos) -> None:
    pressed, self._back_pressed = self._back_pressed, False
    if pressed and rl.check_collision_point_rec(rl.Vector2(mouse_pos.x, mouse_pos.y), self.back_rect()):
      self._on_close()

  def _render(self, rect: rl.Rectangle) -> None:
    if self._font_bold is None:
      self._font_bold = gui_app.font(FontWeight.BOLD)
      self._font_medium = gui_app.font(FontWeight.MEDIUM)
    rl.draw_rectangle_rec(rect, rl.Color(6, 6, 15, 255))
    header = rl.Rectangle(rect.x, rect.y, rect.width, HEADER_HEIGHT)
    rl.draw_rectangle_rec(header, HEADER_BG)
    rl.draw_line_ex(rl.Vector2(rect.x, rect.y + HEADER_HEIGHT), rl.Vector2(rect.x + rect.width, rect.y + HEADER_HEIGHT), 2, DIVIDER)

    back = self.back_rect()
    rl.draw_rectangle_rounded(back, 0.5, 10, BACK_BG_PRESSED if self._back_pressed else BACK_BG)
    chevron_x, chevron_y = back.x + 40, back.y + back.height / 2
    rl.draw_line_ex(rl.Vector2(chevron_x + 12, chevron_y - 16), rl.Vector2(chevron_x, chevron_y), 5, TEXT)
    rl.draw_line_ex(rl.Vector2(chevron_x, chevron_y), rl.Vector2(chevron_x + 12, chevron_y + 16), 5, TEXT)
    rl.draw_text_ex(self._font_bold, self.back_label, rl.Vector2(back.x + 72, back.y + (back.height - 34) / 2), 34, 0, TEXT)

    title_x = back.x + back.width + 40
    rl.draw_text_ex(self._font_bold, tr("Navigate"), rl.Vector2(title_x, header.y + 18), 50, 0, TEXT)
    rl.draw_text_ex(self._font_medium, tr("Search or pick a favorite, check the route, then Start"),
                    rl.Vector2(title_x, header.y + 74), 28, 0, SUBTEXT)

    self.page.render(rl.Rectangle(rect.x, rect.y + HEADER_HEIGHT, rect.width, rect.height - HEADER_HEIGHT))
