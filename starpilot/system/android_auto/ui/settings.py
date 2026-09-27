"""Car settings: one sidebar, flat page selection, and a single Back path."""
from dataclasses import dataclass

import pyray as rl

from openpilot.selfdrive.ui.layouts.settings.types import PanelType
from openpilot.starpilot.system.android_auto.ui import settings_style as style
from openpilot.starpilot.system.android_auto.ui.car_display_settings import CarDisplaySettings
from openpilot.starpilot.system.android_auto.ui.settings_panels.bluetooth import BluetoothManagerUI
from openpilot.starpilot.system.android_auto.ui.settings_panels.developer import DeveloperLayout
from openpilot.starpilot.system.android_auto.ui.settings_panels.device import DeviceLayout
from openpilot.starpilot.system.android_auto.ui.settings_panels.network import NetworkUI
from openpilot.starpilot.system.android_auto.ui.settings_panels.software import SoftwareLayout
from openpilot.starpilot.system.android_auto.ui.settings_panels.toggles import TogglesLayout
from openpilot.starpilot.system.android_auto.ui.settings_panels.starpilot.aethergrid import AetherSettingsView, SettingRow, SettingSection
from openpilot.starpilot.system.android_auto.ui.settings_panels.starpilot.panel import StarPilotPanelType
from openpilot.starpilot.system.android_auto.ui.starpilot_settings import CarStarPilotLayout
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.bluetooth_manager import BluetoothManager
from openpilot.system.ui.lib.scroll_panel2 import GuiScrollPanel2
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.lib.wifi_manager import WifiManager
from openpilot.system.ui.widgets import Widget

HEADER_ROW = 60  # the Back bar's height plus the gap below it
SECTIONS = ('Car Display', 'Navigation', 'Driving', 'Sounds & Alerts', 'Connections', 'Vehicle', 'Device & System')


def sidebar_width(width):
  # On portrait/small screens the same labelled navigation becomes a drawer.
  return 0 if width < 760 else min(300, max(210, width * .18))


@dataclass
class SettingsPage:
  key: str
  title: str
  section: str
  widget: Widget
  subpanel: str = ''
  scope: str = ''


class CarSettingsLayout(Widget):
  def __init__(self):
    super().__init__()
    self._close_callback = None
    self._active = False
    self._drawer = False
    self._page_list = False
    self._targets = {}
    self._pressed = None
    self._nav_scroll = GuiScrollPanel2(horizontal=False)
    self._tabs_scroll = GuiScrollPanel2(horizontal=True, handle_out_of_bounds=False)
    self._section = SECTIONS[0]
    self._page = 'display'
    self._history = []
    self._pages = {}
    self._remembered = {}
    self._hub = CarStarPilotLayout()
    self._build_pages()
    self._picker = AetherSettingsView(self, [])

  def _add(self, key, title, section, widget, subpanel='', scope=''):
    self._pages[key] = SettingsPage(key, title, section, widget, subpanel, scope)
    if hasattr(widget, 'set_navigate_callback'):
      widget.set_navigate_callback(self._navigate)
      widget.set_back_callback(self._back)

  def _build_pages(self):
    panels = self._hub._panels
    def panel(kind):
      return panels[kind].instance
    def add_subpages(prefix, section, owner, names, scope='Shared with comma display'):
      for subpanel, title in names:
        self._add(f'{prefix}:{subpanel}', title, section, owner, subpanel, scope)

    self._add('display', 'Layout', 'Car Display', CarDisplaySettings())
    self._add('widgets', 'Status Widgets', 'Car Display', CarDisplaySettings(metrics=True))
    appearance = panel(StarPilotPanelType.VISUALS)
    add_subpages('appearance', 'Car Display', appearance, [
      ('hud', 'Driving Widgets'), ('model', 'Road & Path'), ('declutter', 'Visibility'), ('dev', 'Advanced Metrics'),
      ('dev_sidebar', 'Comma Status Widgets'),
    ])
    self._add('navigation', 'Routes', 'Navigation', panel(StarPilotPanelType.NAVIGATION))
    self._add('maps', 'Offline Maps', 'Navigation', panel(StarPilotPanelType.MAPS))
    add_subpages('appearance', 'Navigation', appearance, [('nav', 'Map Labels')])
    self._add('toggles', 'General', 'Driving', TogglesLayout(), scope='Driving behavior · Applies to the vehicle')
    longitudinal = panel(StarPilotPanelType.LONGITUDINAL)
    add_subpages('longitudinal', 'Driving', longitudinal, [
      ('tune', 'Gas / Brake'), ('personality', 'Personalities'), ('slc', 'Speed Limits'),
      ('csc', 'Curve Speed'), ('ce', 'Drive Modes'), ('vision_speed_limits', 'Vision Speed Limits'),
      ('daily', 'Weather & Comfort'), ('advanced', 'Actuators'),
    ], 'Driving behavior · Applies to the vehicle')
    add_subpages('lateral', 'Driving', panel(StarPilotPanelType.LATERAL), [
      ('behavior', 'Steering'), ('lane_changes', 'Lane Changes'), ('advanced', 'Steering Tuning'),
    ], 'Driving behavior · Applies to the vehicle')
    self._add('model', 'Driving Model', 'Driving', panel(StarPilotPanelType.DRIVING_MODEL))
    self._add('sounds', 'Sounds & Alerts', 'Sounds & Alerts', panel(StarPilotPanelType.SOUNDS))
    wifi = WifiManager()
    wifi.set_active(False)
    bluetooth = BluetoothManager()
    bluetooth.set_active(False)
    self._add('wifi', 'Wi-Fi', 'Connections', NetworkUI(wifi))
    self._add('bluetooth', 'Bluetooth', 'Connections', BluetoothManagerUI(bluetooth))
    from openpilot.starpilot.system.android_auto.ui.connection_settings import AndroidAutoSettings
    self._add('android_auto', 'Android Auto', 'Connections', AndroidAutoSettings(lambda: self.open_page('bluetooth')))
    self._add('vehicle', 'Vehicle Settings', 'Vehicle', panel(StarPilotPanelType.VEHICLE))
    self._add('device', 'Device', 'Device & System', DeviceLayout(), scope='Comma device')
    self._add('system', 'Preferences', 'Device & System', panel(StarPilotPanelType.SYSTEM), scope='Comma device')
    add_subpages('appearance', 'Device & System', appearance, [('system', 'Camera & Startup')])
    self._add('software', 'Software', 'Device & System', SoftwareLayout())
    self._add('developer', 'Developer', 'Device & System', DeveloperLayout())

  @property
  def _current(self):
    return self._pages[self._page]

  def set_callbacks(self, on_close):
    self._close_callback = on_close

  def refresh_developer_visibility(self):
    pass

  def get_panel_depth(self):
    return len(self._history) + int(self._page_list)

  def set_current_panel(self, panel_type):
    pages = {PanelType.DEVICE: 'device', PanelType.NETWORK: 'wifi', PanelType.BLUETOOTH: 'bluetooth',
             PanelType.TOGGLES: 'toggles', PanelType.SOFTWARE: 'software', PanelType.DEVELOPER: 'developer'}
    # Opening Settings again retains the last page; deep links remain explicit.
    if panel_type in pages:
      self.open_page(pages[panel_type])

  def open_panel(self, key):
    page = {'MAPS': 'maps', 'OFFLINE_MAPS': 'maps', 'NAVIGATION': 'navigation', 'SOUNDS': 'sounds',
            'SYSTEM': 'system', 'DRIVING_MODEL': 'model', 'LONGITUDINAL': 'longitudinal:tune',
            'LATERAL': 'lateral:behavior', 'VISUALS': 'display', 'VEHICLE': 'vehicle'}.get(key)
    if page:
      self.open_page(page)
      if key in ('MAPS', 'OFFLINE_MAPS'):
        self._current.widget.open_segment(1 if key == 'MAPS' else 0)

  def open_page(self, key):
    if self._active:
      (self._picker if self._page_list else self._current.widget).hide_event()
    self._page_list = False
    self._drawer = False
    self._page = key
    self._section = self._current.section
    self._remembered[self._section] = key
    self._history = []
    if hasattr(self._current.widget, 'set_current_sub_panel'):
      self._current.widget.set_current_sub_panel(self._current.subpanel)
    if hasattr(self._current.widget, 'back'):
      self._current.widget.back()
    self._tabs_scroll.set_offset(0)
    self._reveal_tab = True
    if self._active:
      self._current.widget.show_event()

  def _navigate(self, subpanel):
    owner = self._current.widget
    previous = self._history[-1] if self._history else self._current.subpanel
    # Old controllers assign the destination before notifying the shell.
    owner.set_current_sub_panel(previous)
    owner.hide_event()
    if subpanel:
      self._history.append(subpanel)
    else:
      self._history.clear()
    owner.set_current_sub_panel(subpanel or self._current.subpanel)
    owner.show_event()

  def _back(self):
    if self._drawer:
      self._drawer = False
    elif self._page_list:
      self._picker.hide_event()
      self._page_list = False
      self._current.widget.show_event()
    elif hasattr(self._current.widget, 'back') and self._current.widget.back():
      pass
    elif hasattr(getattr(self._current.widget, '_scroller', None), 'back') and self._current.widget._scroller.back():
      pass
    elif self._history:
      self._current.widget.hide_event()
      self._history.pop()
      self._current.widget.set_current_sub_panel(self._history[-1] if self._history else self._current.subpanel)
      self._current.widget.show_event()
    elif self._close_callback:
      self._close_callback()

  def _show_pages(self):
    if self._page_list:
      self._back()
      return
    self._current.widget.hide_event()
    self._picker._header_title = self._section
    self._picker._header_subtitle = 'Choose a page'
    self._picker._sections = [SettingSection('', [
      SettingRow(page.key, 'value', page.title, page.scope, on_click=lambda key=page.key: self.open_page(key))
      for page in self._pages.values() if page.section == self._section
    ])]
    self._picker._scroll_panel.set_offset(0)
    self._picker.show_event()
    self._page_list = True

  def _target(self, point):
    return next((key for key, rect in self._targets.items() if rl.check_collision_point_rec(point, rect)), None)

  def _handle_mouse_press(self, pos):
    self._pressed = self._target(pos)

  def _handle_mouse_cancel(self):
    self._pressed = None

  def _handle_mouse_release(self, pos):
    target = self._target(pos)
    valid = self._nav_scroll.is_touch_valid() and self._tabs_scroll.is_touch_valid()
    if target and target == self._pressed and valid:
      if target == 'back':
        self._back()
      elif target == 'sections':
        self._drawer = not self._drawer
      elif target == 'pages':
        self._show_pages()
      elif target.startswith('section:'):
        section = target[8:]
        key = self._remembered.get(section) or next(key for key, page in self._pages.items() if page.section == section)
        self.open_page(key)
      elif target.startswith('page:'):
        self.open_page(target[5:])
      elif target in ('previous', 'next'):
        self._step_tabs(-1 if target == 'previous' else 1)
    self._pressed = None

  def _first_tab(self, limit, right, width):
    """The first tab edge from which everything up to `right` is visible, no later than tab `limit`."""
    return next((i for i, start in enumerate(self._tab_starts[:limit + 1]) if right - start <= width), limit)

  def _step_tabs(self, step):
    starts = self._tab_starts
    left = -self._tabs_scroll.get_offset()
    current = max(i for i, start in enumerate(starts) if start <= left + 1)
    if step < 0:
      index = current - 1 if starts[current] >= left - 1 else current
    else:
      last = self._first_tab(len(starts) - 1, starts[-1] + self._tab_widths[-1], self._tab_view_width)
      index = min(current + 1, last)
    self._tabs_scroll.set_offset(-starts[max(0, index)])

  def _button(self, key, rect, label, selected=False, clip=None, size=26, align='center'):
    from openpilot.starpilot.system.android_auto.ui.settings_panels.starpilot.aethergrid import draw_chevron_icon
    if key in ('previous', 'next'):
      draw_chevron_icon(rl.Rectangle(rect.x + 10, rect.y + 17, 20, 20), style.MUTED, direction='left' if key == 'previous' else 'right')
    elif key == 'back':
      # A full-width bar: the chevron and label sit at its left edge, like a list row.
      style.button(rect, '', self._pressed == 'back', size)
      draw_chevron_icon(rl.Rectangle(rect.x + 18, rect.y + (rect.height - 20) / 2, 20, 20), style.TEXT, direction='left')
      style.text(rl.Rectangle(rect.x + 48, rect.y, max(0, rect.width - 64), rect.height), label, size)
    else:
      style.button(rect, label, selected, size, align)
    bounds = rl.get_collision_rec(rect, clip) if clip else rect
    if bounds.width > 0 and bounds.height > 0:
      self._targets[key] = bounds

  def _draw_navigation(self, rect):
    rl.draw_rectangle_rec(rect, style.BG)
    style.text(rl.Rectangle(rect.x + 18, rect.y + 16, rect.width - 36, 44), 'Settings', 30, bold=True)
    area = rl.Rectangle(rect.x + 12, rect.y + 76, rect.width - 24, max(1, rect.height - 88))
    row_height = min(70, max(48, (area.height - 8 * (len(SECTIONS) - 1)) / len(SECTIONS)))
    offset = self._nav_scroll.update(area, len(SECTIONS) * row_height + (len(SECTIONS) - 1) * 8)
    rl.begin_scissor_mode(int(area.x), int(area.y), int(area.width), int(area.height))
    for index, section in enumerate(SECTIONS):
      item = rl.Rectangle(area.x, area.y + index * (row_height + 8) + offset, area.width, row_height)
      label = {'Sounds & Alerts': 'Sounds', 'Device & System': 'System'}.get(section, section)
      self._button(f'section:{section}', item, label, section == self._section, area, size=23 if rect.width < 250 else 26, align='left')
    rl.end_scissor_mode()

  def _render(self, rect):
    self._targets.clear()
    rl.draw_rectangle_rec(rect, style.BG)
    width = sidebar_width(rect.width)
    if width:
      self._draw_navigation(rl.Rectangle(rect.x, rect.y, width, rect.height))
    gap = 16 if width else 8
    content = rl.Rectangle(rect.x + width + gap, rect.y + 12, max(1, rect.width - width - gap * 2), rect.height - 24)
    # Row 1 is a full-width Back bar; row 2 holds the section title and All Pages.
    self._button('back', rl.Rectangle(content.x, content.y, content.width, 52), 'Back', size=24)
    row = content.y + HEADER_ROW
    sections_w = 0 if width else 148
    if not width:
      self._button('sections', rl.Rectangle(content.x, row, 140, 52), 'Sections', size=24)
    title_x = content.x + sections_w + 16
    title_w = max(0, content.x + content.width - title_x - 152)
    style.text(rl.Rectangle(title_x, row, title_w, 52), self._section, 28, bold=True)
    self._button('pages', rl.Rectangle(content.x + content.width - 144, row, 144, 52), 'All Pages', self._page_list, size=24)
    pages = [page for page in self._pages.values() if page.section == self._section]
    widths = [max(130, measure_text_cached(gui_app.font(FontWeight.NORMAL), page.title, 25).x + 40) for page in pages]
    starts = [sum(widths[:i]) + 8 * i for i in range(len(pages))]
    total = sum(widths) + 8 * max(0, len(pages) - 1)
    # Tabs that fit line up with the rows below; arrows take the edges only when they overflow.
    overflow = total > content.width - 32
    tabs_y = content.y + HEADER_ROW + 64
    tabs = rl.Rectangle(content.x + 48, tabs_y, max(1, content.width - 96), 54) if overflow else \
      rl.Rectangle(content.x + 16, tabs_y, max(1, content.width - 32), 54)
    self._tab_starts, self._tab_widths, self._tab_view_width = starts, widths, tabs.width
    # Scroll only to tab edges, so no tab is left half-drawn at the left.
    extent = starts[self._first_tab(len(pages) - 1, total, tabs.width)] + tabs.width if overflow else total
    if getattr(self, '_reveal_tab', False) or getattr(self, '_last_tab_width', None) != tabs.width:
      self._last_tab_width = tabs.width
      index = next(i for i, page in enumerate(pages) if page.key == self._page)
      self._tabs_scroll.set_offset(-starts[self._first_tab(index, starts[index] + widths[index], tabs.width)])
      self._reveal_tab = False
    offset = self._tabs_scroll.update(tabs, extent)
    rl.begin_scissor_mode(int(tabs.x), int(tabs.y), int(tabs.width), int(tabs.height))
    for page, x, w in zip(pages, starts, widths, strict=True):
      self._button(f'page:{page.key}', rl.Rectangle(tabs.x + offset + x, tabs.y, w, tabs.height), page.title, page.key == self._page, tabs, 25)
    rl.end_scissor_mode()
    if overflow:
      self._button('previous', rl.Rectangle(content.x, tabs.y, 40, 54), '‹', size=28)
      self._button('next', rl.Rectangle(content.x + content.width - 40, tabs.y, 40, 54), '›', size=28)
    scope_h = 32 if self._current.scope and not self._page_list else 0
    if scope_h:
      style.text(rl.Rectangle(content.x + 16, tabs_y + 62, content.width - 32, scope_h), self._current.scope, 22, style.MUTED)
    top = HEADER_ROW + 130 + scope_h
    body = rl.Rectangle(content.x, content.y + top, content.width, max(1, content.height - top))
    if self._drawer and not width:
      self._targets = {key: value for key, value in self._targets.items() if key in ('back', 'sections')}
      self._draw_navigation(body)
    else:
      widget = self._picker if self._page_list else self._current.widget
      widget.set_parent_rect(body)
      view = getattr(widget, '_sub_panels', {}).get(getattr(widget, '_current_sub_panel', ''), getattr(widget, '_manager_view', widget))
      if view is not None:
        view._in_settings_shell = True
      rl.begin_scissor_mode(int(body.x), int(body.y), int(body.width), int(body.height))
      widget.render(body)
      rl.end_scissor_mode()

  def show_event(self):
    super().show_event()
    self._active = True
    (self._picker if self._page_list else self._current.widget).show_event()

  def hide_event(self):
    (self._picker if self._page_list else self._current.widget).hide_event()
    self._active = False
    self._pressed = None
    super().hide_event()
