"""Car-owned dialogs with a scrollable body and a fixed, non-overlapping footer."""
import re
from html import unescape

import pyray as rl

from openpilot.starpilot.system.android_auto.ui import settings_style as style
from openpilot.starpilot.system.android_auto.ui.settings_panels.starpilot.aethergrid import AetherSettingsView, SettingRow, SettingSection
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget, DialogResult


class SettingsDialog(Widget):
  def __init__(self, title, confirm_text, cancel_text, callback):
    super().__init__()
    self.title = title
    self._confirm_text = confirm_text
    self._cancel_text = cancel_text
    self._callback = callback
    self._targets = {}
    self._pressed = None
    self._view = self._child(AetherSettingsView(self, []))

  def _set_result(self, result):
    gui_app.pop_widget()
    if self._callback:
      self._callback(result)

  def _handle_mouse_press(self, pos):
    self._pressed = next((key for key, rect in self._targets.items() if rl.check_collision_point_rec(pos, rect)), None)

  def _handle_mouse_cancel(self):
    self._pressed = None

  def _handle_mouse_release(self, pos):
    key = self._pressed
    self._pressed = None
    if key in self._targets and rl.check_collision_point_rec(pos, self._targets[key]):
      self._set_result(DialogResult.CONFIRM if key == 'confirm' else DialogResult.CANCEL)

  def _render(self, rect):
    rl.draw_rectangle_rec(rect, rl.Color(0, 0, 0, 180))
    width, height = min(1100, rect.width - 24), rect.height - 24
    box = rl.Rectangle(rect.x + (rect.width - width) / 2, rect.y + 12, width, height)
    rl.draw_rectangle_rounded(box, .04, 16, style.BG)
    style.text(rl.Rectangle(box.x + 24, box.y + 12, box.width - 48, 52), self.title, 30, bold=True)
    body = rl.Rectangle(box.x + 8, box.y + 76, box.width - 16, max(1, box.height - 160))
    self._view.set_parent_rect(body)
    self._view.render(body)
    gap = 12
    button_width = (box.width - 48 - gap) / 2 if self._cancel_text else box.width - 48
    self._targets.clear()
    y = box.y + box.height - 72
    if self._cancel_text:
      cancel = rl.Rectangle(box.x + 24, y, button_width, 56)
      style.button(cancel, self._cancel_text)
      self._targets['cancel'] = cancel
    confirm = rl.Rectangle(box.x + box.width - 24 - button_width, y, button_width, 56)
    style.button(confirm, self._confirm_text, self._can_confirm(), enabled=self._can_confirm())
    if self._can_confirm():
      self._targets['confirm'] = confirm

  def _can_confirm(self):
    return True


class MultiOptionDialog(SettingsDialog):
  def __init__(self, title, options, current='', option_font_weight=None, callback=None):
    super().__init__(title, 'Select', 'Cancel', callback)
    self.options = options
    self.current = current
    self.selection = current
    self._view._sections = [SettingSection('', [SettingRow(str(i), 'value', label,
      get_value=lambda label=label: 'Selected' if self.selection == label else '',
      on_click=lambda label=label: self._on_option_clicked(label)) for i, label in enumerate(options)])]

  def _on_option_clicked(self, option):
    self.selection = option

  def _can_confirm(self):
    return bool(self.selection)


class ConfirmDialog(SettingsDialog):
  def __init__(self, text, confirm_text, cancel_text=None, rich=False, callback=None):
    super().__init__('Confirm' if cancel_text != '' else 'Information', confirm_text, 'Cancel' if cancel_text is None else cancel_text, callback)
    self.set_text(text)

  def set_text(self, text):
    description = unescape(re.sub('<[^>]+>', ' ', str(text)))
    self._view._sections = [SettingSection('', [SettingRow('message', 'value', '', description)])]


def alert_dialog(message, button_text=None):
  return ConfirmDialog(message, button_text or 'OK', cancel_text='')
