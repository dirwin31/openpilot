import time

import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.starpilot.navigation.destination_store import parse_destination_json
from openpilot.system.ui.lib.application import FONT_SCALE, FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

BUTTON_BG = rl.Color(22, 34, 58, 255)
BUTTON_BG_PRESSED = rl.Color(30, 46, 78, 255)
BUTTON_BORDER = rl.Color(64, 150, 255, 90)
ACCENT = rl.Color(64, 150, 255, 255)
TEXT = rl.Color(236, 242, 250, 255)
SUBTEXT = rl.Color(170, 182, 200, 255)


class NavigateButton(Widget):
  """Home-screen shortcut to the Navigation panel, naming the active destination."""

  def __init__(self):
    super().__init__()
    self.params = ui_state.ui_params
    self._subtitle = ""
    self._refreshed_at = 0.0
    self.locked_text = ""  # when set, the button is dimmed and says why it can't be used

  def show_event(self):
    super().show_event()
    self._refresh()

  def _refresh(self):
    self._refreshed_at = time.monotonic()
    destination = parse_destination_json(self.params.get("NavDestination"))
    if destination is not None:
      self._subtitle = tr("To {}").format(destination.get("place_name") or destination.get("name") or tr("destination"))
    else:
      self._subtitle = tr("Search an address and see the route")

  def _update_state(self):
    if time.monotonic() - self._refreshed_at >= 1.0:
      self._refresh()

  def _fit(self, font, text: str, size: int, width: float) -> str:
    if measure_text_cached(font, text, size).x <= width:
      return text
    while text and measure_text_cached(font, text + "...", size).x > width:
      text = text[:-1]
    return text.rstrip() + "..."

  def _render(self, rect):
    locked = bool(self.locked_text)
    rl.draw_rectangle_rounded(rect, 0.19, 10, BUTTON_BG_PRESSED if self.is_pressed and not locked else BUTTON_BG)
    rl.draw_rectangle_rounded_lines_ex(rect, 0.19, 10, 2, BUTTON_BORDER)

    # A simple route glyph: two stops joined by a line.
    icon_x, icon_mid = rect.x + 58, rect.y + rect.height / 2
    rl.draw_line_ex(rl.Vector2(icon_x, icon_mid - 26), rl.Vector2(icon_x, icon_mid + 26), 6, ACCENT)
    rl.draw_circle_v(rl.Vector2(icon_x, icon_mid - 28), 11, TEXT)
    rl.draw_circle_v(rl.Vector2(icon_x, icon_mid + 28), 11, ACCENT)

    bold, medium = gui_app.font(FontWeight.BOLD), gui_app.font(FontWeight.MEDIUM)
    text_x = rect.x + 108
    width = rect.x + rect.width - text_x - 70
    title_size, subtitle_size = 44, 30
    top = rect.y + (rect.height - (title_size + subtitle_size + 8) * FONT_SCALE) / 2
    rl.draw_text_ex(bold, tr("Navigate"), rl.Vector2(int(text_x), int(top)), title_size, 0, SUBTEXT if locked else TEXT)
    rl.draw_text_ex(medium, self._fit(medium, self.locked_text or self._subtitle, subtitle_size, width),
                    rl.Vector2(int(text_x), int(top + (title_size + 8) * FONT_SCALE)), subtitle_size, 0, SUBTEXT)

    chevron_x, chevron_y = rect.x + rect.width - 44, rect.y + rect.height / 2
    rl.draw_line_ex(rl.Vector2(chevron_x - 12, chevron_y - 16), rl.Vector2(chevron_x, chevron_y), 5, SUBTEXT)
    rl.draw_line_ex(rl.Vector2(chevron_x, chevron_y), rl.Vector2(chevron_x - 12, chevron_y + 16), 5, SUBTEXT)
