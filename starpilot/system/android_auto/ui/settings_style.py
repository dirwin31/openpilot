"""Responsive Galaxy surfaces for the car's settings.

Text is measured before controls are placed. Narrow rows stack values below
labels; scrolling, rather than smaller type, accommodates short displays.
"""
import pyray as rl

from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached

BG = rl.Color(12, 13, 20, 255)
SURFACE = rl.Color(21, 22, 32, 255)
BORDER = rl.Color(47, 48, 64, 255)
TEXT = rl.Color(241, 241, 248, 255)
MUTED = rl.Color(160, 164, 184, 255)
ACCENT = rl.Color(153, 119, 255, 255)
SELECTED = rl.Color(48, 37, 76, 255)


def text(rect, value, size=30, color=TEXT, bold=False, align='left'):
  """Fit a single line within its own reserved rectangle, with an ellipsis."""
  from openpilot.starpilot.system.android_auto.ui.settings_panels.starpilot.aethergrid import truncate_text_ellipsis
  font = gui_app.font(FontWeight.SEMI_BOLD if bold else FontWeight.NORMAL)
  value = truncate_text_ellipsis(font, str(value).replace('·', '-').replace('…', '...'), max(0, rect.width), size)
  x = rect.x
  if align != 'left':
    slack = max(0, rect.width - measure_text_cached(font, value, size).x)
    x += slack / 2 if align == 'center' else slack
  rl.draw_text_ex(font, value, rl.Vector2(round(x), round(rect.y + (rect.height - size) / 2)), size, 0, color)


def button(rect, label, selected=False, size=28, align='center', enabled=True):
  rl.draw_rectangle_rounded(rect, 0.22, 12, SELECTED if selected else SURFACE)
  rl.draw_rectangle_rounded_lines_ex(rect, 0.22, 12, 1, ACCENT if selected else BORDER)
  text(rl.Rectangle(rect.x + 16, rect.y, max(0, rect.width - 32), rect.height), label, size,
       TEXT if enabled else MUTED, bold=selected, align=align)


def switch(rect, value, enabled=True):
  color = ACCENT if value else BORDER
  if not enabled:
    color = rl.Color(color.r, color.g, color.b, 100)
  rl.draw_rectangle_rounded(rect, 1, 16, color)
  radius = rect.height / 2 - 5
  x = rect.x + (rect.width - rect.height / 2 if value else rect.height / 2)
  rl.draw_circle_v(rl.Vector2(x, rect.y + rect.height / 2), radius, TEXT if enabled else MUTED)


def _split_word(font, word, width, size):
  """Break a word wider than the line (a URL or path) so none of it is cut off."""
  parts, part = [], ''
  for char in word:
    if part and measure_text_cached(font, part + char, size).x > width:
      parts.append(part)
      part = char
    else:
      part += char
  return parts + [part]


def lines(value, width, size):
  """Wrap text without imposing a fixed row height or truncating descriptions."""
  font = gui_app.font(FontWeight.NORMAL)
  result = []
  for paragraph in str(value).split('\n'):
    line = ''
    for word in paragraph.split():
      if measure_text_cached(font, word, size).x > width:
        *full, word = _split_word(font, word, width, size)
        result.extend(([line] if line else []) + full)
        line = ''
      candidate = f'{line} {word}'.strip()
      if line and measure_text_cached(font, candidate, size).x > width:
        result.append(line)
        line = word
      else:
        line = candidate
    result.append(line)
  return result


VALUE_SIZE = 24


def message(rect, value, size=34, color=MUTED):
  """Draw a wrapped status message centered in its area, so it is never cut off."""
  wrapped = lines(value, max(1, rect.width - 40), size)
  y = rect.y + (rect.height - len(wrapped) * (size + 8)) / 2
  for line in wrapped:
    text(rl.Rectangle(rect.x + 20, y, max(0, rect.width - 40), size + 8), line, size, color, align='center')
    y += size + 8


def row_layout(width, title, subtitle, kind, value=''):
  """Return measured text and disjoint control geometry, in row-local units."""
  pad = 20
  size = 30 if width >= 600 else 26
  sub_size = 24 if width >= 600 else 22
  stacked = kind != 'toggle' and bool(value) and width < 850
  if kind == 'toggle':
    control_width = 88
  else:
    # Size the value to its text so short values sit at the right edge and long ones are not cut off.
    measured = measure_text_cached(gui_app.font(FontWeight.NORMAL), str(value), VALUE_SIZE).x + 8
    control_width = min(max(120, measured), width * .45)
  reserve = 0 if stacked or (kind != 'toggle' and not value) else control_width + 24
  available = max(40, width - pad * 2 - reserve)
  title_lines = lines(title, available, size) if title else []
  subtitle_lines = lines(subtitle, available, sub_size) if subtitle else []
  gap = 6 if title_lines and subtitle_lines else 0
  text_height = len(title_lines) * (size + 5) + len(subtitle_lines) * (sub_size + 5) + gap
  height = max(96, text_height + pad * 2 + (48 if stacked else 0))
  # Beside a control, the text block is centered on the same line as the control.
  text_top = pad if stacked else (height - text_height) / 2
  control = rl.Rectangle(pad if stacked else width - pad - control_width,
                         height - pad - 36 if stacked else (height - 44) / 2,
                         width - pad * 2 if stacked else control_width, 36 if stacked else 44)
  return height, available, size, sub_size, title_lines, subtitle_lines, control, text_top, stacked


def render_settings(view, rect):
  """Draw SettingSection/SettingRow data using the Galaxy row language."""
  from openpilot.starpilot.system.android_auto.ui.settings_panels.starpilot.aethergrid import SettingRow, SettingSection
  view.set_rect(rect)
  view._interactive_rects.clear()
  rl.draw_rectangle_rec(rect, BG)
  pad = 16
  area = rl.Rectangle(rect.x + pad, rect.y, max(1, rect.width - pad * 2), max(1, rect.height))
  view._scroll_rect = area
  blocks = []
  y = 8
  if view._header_title and (not getattr(view, '_in_settings_shell', False) or getattr(view, '_choices', None)):
    for line in lines(tr(view._header_title), area.width - 24, 32):
      blocks.append(('heading', y, line, 42))
      y += 42
  if view._header_subtitle:
    for line in lines(tr(view._header_subtitle), area.width - 24, 23):
      blocks.append(('description', y, line, 30))
      y += 30
  y += 12
  if view._tab_defs:
    # Wrap tabs; none disappear off the right edge.
    tab_x = 0
    for tab in view._tab_defs:
      label = tr(tab.get('label', tab.get('title', tab['id'])))
      w = min(area.width, max(130, measure_text_cached(gui_app.font(FontWeight.NORMAL), label, 24).x + 32))
      if tab_x and tab_x + w > area.width:
        y += 62
        tab_x = 0
      blocks.append(('tab', y, (tab, label, tab_x, w), 54))
      tab_x += w + 8
    y += 70
  sections = list(view._active_sections())
  if view._parent_toggle:
    parent = view._parent_toggle
    sections.insert(0, SettingSection('', [SettingRow('__parent', 'toggle', parent.label, parent.subtitle,
                                                      get_state=parent.get_state, set_state=parent.set_state)]))
  for section in sections:
    rows = view._visible_rows(section)
    if not rows:
      continue
    if section.title:
      blocks.append(('section', y, tr(section.title), 46))
      y += 46
    for row in rows:
      enabled = row.enabled() if row.enabled else True
      subtitle = row.disabled_label if not enabled and row.disabled_label else row.subtitle
      value = str(row.get_value() if row.get_value else row.action_text or ('Open' if row.navigate_to or row.on_click else ''))
      layout = row_layout(area.width, tr(row.title), tr(subtitle), row.type, value)
      blocks.append(('row', y, (row, enabled, value, layout), layout[0]))
      y += layout[0] + 8
    y += 12
  view._content_height = y
  view._scroll_panel.set_enabled(view.is_visible)
  view._scroll_offset = view._scroll_panel.update(area, max(y, area.height))
  rl.begin_scissor_mode(int(area.x), int(area.y), int(area.width), int(area.height))
  for kind, top, data, height in blocks:
    bounds = rl.Rectangle(area.x, area.y + top + view._scroll_offset, area.width, height)
    if not rl.check_collision_recs(bounds, area):
      continue
    if kind in ('heading', 'description', 'section'):
      text(bounds, data, 32 if kind == 'heading' else 23, MUTED if kind == 'description' else TEXT, kind != 'description')
    elif kind == 'tab':
      tab, label, x, width = data
      bounds.x += x
      bounds.width = width
      button(bounds, label, view._active_tab_key == tab['id'], 24)
      view._interactive_rects[f"tab:{tab['id']}"] = bounds
    else:
      row, enabled, value, layout = data
      _, width, size, sub_size, title_lines, subtitle_lines, control, text_top, stacked = layout
      target = f'{row.type}:{row.id}' if row.id != '__parent' else f'parent_toggle:{view._parent_toggle.label}'
      view._interactive_rects[target] = bounds
      rl.draw_rectangle_rounded(bounds, .10, 10, SELECTED if view._pressed_target == target else SURFACE)
      control = rl.Rectangle(bounds.x + control.x, bounds.y + control.y, control.width, control.height)
      ty = bounds.y + text_top
      for line in title_lines:
        text(rl.Rectangle(bounds.x + 20, ty, width, size + 5), line, size, TEXT if enabled else MUTED, True)
        ty += size + 5
      ty += 6 if title_lines and subtitle_lines else 0
      for line in subtitle_lines:
        text(rl.Rectangle(bounds.x + 20, ty, width, sub_size + 5), line, sub_size, MUTED)
        ty += sub_size + 5
      if row.type == 'toggle':
        switch(control, row.get_state() if row.get_state else False, enabled)
      elif value:
        text(control, value, VALUE_SIZE, ACCENT if enabled else MUTED, align='left' if stacked else 'right')
  rl.end_scissor_mode()
  if y > area.height:
    # Draw the scrollbar in the right gutter, clear of the rows' edges and values.
    view._scrollbar.render(rl.Rectangle(area.x, area.y, area.width + pad - 2, area.height), y, view._scroll_offset)
