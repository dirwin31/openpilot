import pyray as rl
from openpilot.common.time_helpers import system_time_valid
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.widgets.pairing_dialog import PairingDialog
from openpilot.system.ui.lib.application import gui_app, FontWeight, FONT_SCALE
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.wrap_text import wrap_text
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.confirm_dialog import alert_dialog
from openpilot.system.ui.widgets.button import Button, ButtonStyle


LOGO_WIDTH = 750
LOGO_HEIGHT = 770


class StarPilotLogoWidget(Widget):
  def __init__(self):
    super().__init__()
    self._logo_texture = gui_app.texture("images/StarPilotLogo.png", LOGO_WIDTH, LOGO_HEIGHT)

  def _render(self, rect: rl.Rectangle):
    scale = min(1.0, rect.width / self._logo_texture.width, rect.height / self._logo_texture.height)
    width = self._logo_texture.width * scale
    height = self._logo_texture.height * scale
    x = rect.x + (rect.width - width) / 2
    y = rect.y + (rect.height - height) / 2
    rl.draw_texture_ex(self._logo_texture, rl.Vector2(x, y), 0.0, scale, rl.WHITE)


class SetupWidget(Widget):
  def __init__(self):
    super().__init__()
    self._pairing_dialog: PairingDialog | None = None
    self._pair_device_btn = Button(lambda: tr("Pair device"), self._show_pairing, button_style=ButtonStyle.PRIMARY)
    self._logo_widget = StarPilotLogoWidget()

  def _render(self, rect: rl.Rectangle):
    if not ui_state.prime_state.is_paired():
      self._render_registration(rect)
    else:
      self._render_logo(rect)

  def _render_registration(self, rect: rl.Rectangle):
    """Render registration prompt with Galaxy card plate and accent styling."""

    # Galaxy card plate with refined border
    card_rect = rl.Rectangle(rect.x, rect.y, rect.width, rect.height)
    rl.draw_rectangle_rounded(card_rect, 0.04, 16, rl.Color(18, 18, 36, 255))
    rl.draw_rectangle_rounded_lines_ex(card_rect, 0.04, 16, 1.5, rl.Color(35, 35, 68, 255))

    # Cosmic purple top accent bar
    accent_rect = rl.Rectangle(rect.x + 24, rect.y + 12, rect.width - 48, 5)
    rl.draw_rectangle_rounded(accent_rect, 1.0, 8, rl.Color(139, 108, 197, 255))

    padding_x = max(28, min(64, int(rect.width * 0.08)))
    padding_y = max(24, min(48, int(rect.height * 0.06)))
    x = rect.x + padding_x
    y = rect.y + padding_y
    w = rect.width - padding_x * 2

    # Title with adaptive sizing
    title_size = max(48, min(72, int(rect.height * 0.11)))
    font = gui_app.font(FontWeight.BOLD)
    rl.draw_text_ex(font, tr("Finish Setup"), rl.Vector2(x, y), title_size, 0, rl.Color(250, 248, 255, 255))
    y += title_size + max(16, int(rect.height * 0.03))

    # Description with adaptive sizing
    desc = tr("Pair your device with comma connect (connect.comma.ai) and claim your comma prime offer.")
    desc_size = max(34, min(46, int(rect.height * 0.065)))
    light_font = gui_app.font(FontWeight.NORMAL)
    wrapped = wrap_text(light_font, desc, desc_size, int(w))
    for line in wrapped:
      rl.draw_text_ex(light_font, line, rl.Vector2(x, y), desc_size, 0, rl.Color(160, 160, 190, 255))
      y += int(desc_size * FONT_SCALE)

    remaining_h = rect.y + rect.height - y - padding_y
    btn_h = max(90, min(160, int(remaining_h - 16)))
    btn_y = rect.y + rect.height - padding_y - btn_h
    button_rect = rl.Rectangle(x, btn_y, w, btn_h)
    self._pair_device_btn.render(button_rect)

  def _render_logo(self, rect: rl.Rectangle):
    self._logo_widget.render(rect)

  def _show_pairing(self):
    if not system_time_valid():
      dlg = alert_dialog(tr("Please connect to Wi-Fi to complete initial pairing"))
      gui_app.push_widget(dlg)
      return

    if not self._pairing_dialog:
      self._pairing_dialog = PairingDialog()
    gui_app.push_widget(self._pairing_dialog)

  def __del__(self):
    if self._pairing_dialog:
      del self._pairing_dialog
