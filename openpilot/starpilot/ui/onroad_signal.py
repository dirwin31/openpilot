"""All connections: cellular and Wi-Fi strength plus Bluetooth status and device name."""
from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.network_signal import Signal, SignalFeed
from openpilot.starpilot.ui.onroad_customization import SIGNAL_GRIDS, SIGNAL_SCALE, SIGNAL_WIDGET, signal_scale
from openpilot.starpilot.ui.presentation import FontRole

ACCENT = rl.Color(199, 174, 247, 255)
BT_BLUE = rl.Color(110, 175, 255, 255)
ORANGE = rl.Color(255, 150, 60, 255)
GRAY = rl.Color(140, 136, 152, 255)
TEXT = rl.Color(245, 242, 252, 255)
MUTED = rl.Color(184, 178, 200, 255)
DIM = rl.Color(255, 255, 255, 43)
DIM_BACK = rl.Color(15, 13, 23, 130)
SHADOW = rl.Color(8, 6, 14, 175)


def lit(signal: Signal, radio: str) -> rl.Color:
  strength = getattr(signal, radio)
  if not strength:
    return GRAY
  if strength <= 1:
    return ORANGE
  return ACCENT if signal.active == radio else TEXT


def _bt_color(signal: Signal) -> rl.Color:
  if signal.bt == 'connected':
    return BT_BLUE
  return MUTED if signal.bt == 'on' else GRAY


def _arc(cx, cy, radius, width, start, end, color, s=1.0):
  dy = max(1.0, 1.5 * s)
  if color == DIM:
    rl.draw_ring(rl.Vector2(cx, cy), radius - width / 2, radius + width / 2, min(start, end), max(start, end), 24, DIM_BACK)
  else:
    rl.draw_ring(rl.Vector2(cx, cy + dy), radius - width / 2, radius + width / 2, min(start, end), max(start, end), 24, SHADOW)
  rl.draw_ring(rl.Vector2(cx, cy), radius - width / 2, radius + width / 2, min(start, end), max(start, end), 24, color)


def _pill(x, y, width, height, color, roundness=1.0, shadow=True, s=1.0):
  if shadow:
    if color == DIM:
      rl.draw_rectangle_rounded(rl.Rectangle(x, y, width, height), roundness, 12, DIM_BACK)
    else:
      rl.draw_rectangle_rounded(rl.Rectangle(x, y + max(1.0, 1.5 * s), width, height), roundness, 12, SHADOW)
  rl.draw_rectangle_rounded(rl.Rectangle(x, y, width, height), roundness, 12, color)


def _dot(cx, cy, radius, color, s=1.0):
  if color == DIM:
    rl.draw_circle_v(rl.Vector2(cx, cy), radius, DIM_BACK)
  else:
    rl.draw_circle_v(rl.Vector2(cx, cy + max(1.0, 1.5 * s)), radius, SHADOW)
  rl.draw_circle_v(rl.Vector2(cx, cy), radius, color)


def _cell_icon(x, y, s, color):
  dy = max(1.0, 1.5 * s)
  for c, off in ((SHADOW, dy), (color, 0.0)):
    for i in range(4):
      h = (4 + 4 * i) * s
      rl.draw_rectangle_rec(rl.Rectangle(x + i * 4.5 * s, y + off + 16 * s - h, 3 * s, h), c)


def _wifi_icon(x, y, s, color):
  dy = max(1.0, 1.5 * s)
  cx, cy = x + 9 * s, y + 14 * s
  rl.draw_circle_v(rl.Vector2(cx, cy + dy), 2 * s, SHADOW)
  for radius in (6.5, 11.5):
    rl.draw_ring(rl.Vector2(cx, cy + dy), radius * s - 1.2 * s, radius * s + 1.2 * s, 222, 318, 24, SHADOW)
  rl.draw_circle_v(rl.Vector2(cx, cy), 2 * s, color)
  for radius in (6.5, 11.5):
    rl.draw_ring(rl.Vector2(cx, cy), radius * s - 1.2 * s, radius * s + 1.2 * s, 222, 318, 24, color)


def _bt_icon(x, y, s, color):
  t = max(1.0, 1.8 * s)
  dy = max(1.0, 1.5 * s)
  segments = (
    ((6, 1), (6, 15)),
    ((6, 1), (10.5, 4.5)),
    ((10.5, 4.5), (1.5, 11)),
    ((1.5, 5), (10.5, 11.5)),
    ((10.5, 11.5), (6, 15)),
  )
  for c, off in ((SHADOW, dy), (color, 0.0)):
    for (x1, y1), (x2, y2) in segments:
      rl.draw_line_ex(rl.Vector2(x + x1 * s, y + off + y1 * s), rl.Vector2(x + x2 * s, y + off + y2 * s), t, c)


def _icon(radio, x, y, s, color):
  (_cell_icon if radio == 'cell' else _wifi_icon)(x, y, s, color)


class SignalWidget:
  def __init__(self, fonts, feed=None):
    self.fonts = fonts
    self.feed = feed or SignalFeed()
    self.preview: Signal | None = None
    self.s = SIGNAL_SCALE  # px per grid unit: the 88 px grid scale times the placement's size percentage

  def _text(self, text, x, cy, size, color, align='center'):
    """Draws text with its ink centered on cy; x is the centre, or the left edge with align='left'."""
    size *= self.s
    top, bottom = self.fonts.vertical_ink(text, FontRole.SEMI_BOLD, size)
    if align == 'center':
      x -= self.fonts.measure(text, FontRole.SEMI_BOLD, size).width / 2
    y = cy - (top + bottom) / 2
    self.fonts.draw(text, FontRole.SEMI_BOLD, size, x, y + max(1.0, 1.5 * self.s), SHADOW)
    self.fonts.draw(text, FontRole.SEMI_BOLD, size, x, y, color)

  def _fit(self, text, size, max_width):
    limit = max_width * self.s
    px_size = size * self.s
    if self.fonts.measure(text, FontRole.SEMI_BOLD, px_size).width <= limit:
      return text
    trimmed = text
    while len(trimmed) > 1 and self.fonts.measure(f"{trimmed}…", FontRole.SEMI_BOLD, px_size).width > limit:
      trimmed = trimmed[:-1].rstrip()
    return f"{trimmed}…" if trimmed else "…"

  def render(self, state):
    placed = state.customization['layouts']['large'].get(SIGNAL_WIDGET)
    if not placed or not placed['enabled'] or state.alert.size != 'none':
      return
    signal = self.preview or self.feed.snapshot()
    style = placed.get('size', 'ring')
    style = style if style in SIGNAL_GRIDS else 'ring'
    self.s = S = SIGNAL_SCALE * signal_scale(placed)
    background = rl.Color(15, 13, 23, round(235 * placed.get('opacity', 85) / 100))
    radios = [radio for radio in ('cell', 'wifi') if getattr(signal, radio)]
    x, y = placed['x'], placed['y']
    show_bt = placed.get('bluetooth', True)
    if style == 'ring':
      self._ring(signal, radios, x, y, background, show_bt)
      return
    width, height = SIGNAL_GRIDS[style]
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, width * S, height * S), 28 / 56 if style == 'horizontal' else 24 / 56, 24, background)
    (self._lines if style == 'horizontal' else self._columns)(signal, radios, x, y, show_bt)

  def _label(self, signal, radio):
    if radio == 'cell':
      return signal.tech if signal.cell else 'Cell'
    return 'Wi-Fi'

  def _bt_device_label(self, signal, size, max_width):
    if signal.bt == 'connected':
      if signal.bt_devices:
        joined = ', '.join(signal.bt_devices)
        if self.fonts.measure(joined, FontRole.SEMI_BOLD, size * self.s).width <= max_width * self.s:
          return joined
        if len(signal.bt_devices) > 1:
          suffix = f" +{len(signal.bt_devices) - 1}"
          suffix_w = self.fonts.measure(suffix, FontRole.SEMI_BOLD, size * self.s).width / self.s
          if max_width > suffix_w + 12:
            return f"{self._fit(signal.bt_devices[0], size, max_width - suffix_w)}{suffix}"
        return self._fit(signal.bt_devices[0], size, max_width)
      return 'Connected'
    return 'Bluetooth'

  def _draw_bt_footer(self, signal, cx, cy, max_width):
    S = self.s
    bt_color = _bt_color(signal)
    if signal.bt == 'connected':
      bt_text = self._bt_device_label(signal, 13, max_width)
    else:
      bt_text = 'BT On' if signal.bt == 'on' else 'BT Off'
    text_w = self.fonts.measure(bt_text, FontRole.SEMI_BOLD, 13 * S).width
    start_x = cx - (17 * S + text_w) / 2
    _bt_icon(start_x, cy - 8 * S, 1.0 * S, bt_color)
    self._text(bt_text, start_x + 17 * S, cy, 13, TEXT if signal.bt == 'connected' else bt_color, 'left')

  def _ring(self, signal, radios, x, y, background, show_bt=True):
    S = self.s
    cx, cy = x + 56 * S, y + 56 * S
    _pill(x, y, 112 * S, 128 * S, background, shadow=False)
    radius, width = 45 * S, 7 * S
    for i in range(4):
      c_start = 150 + i * 29
      _arc(cx, cy, radius, width, c_start, c_start + 24, lit(signal, 'cell') if signal.cell and i < signal.cell else DIM, S)
      w_start = 30 - i * 29
      _arc(cx, cy, radius, width, w_start - 24, w_start, lit(signal, 'wifi') if signal.wifi and i < signal.wifi else DIM, S)

    cell_color = lit(signal, 'cell') if signal.active == 'cell' else (MUTED if signal.cell else GRAY)
    wifi_color = lit(signal, 'wifi') if signal.active == 'wifi' else (MUTED if signal.wifi else GRAY)
    icon_y = y + (36 if show_bt else 44) * S
    _cell_icon(x + 34 * S, icon_y, 1.0 * S, cell_color)
    _wifi_icon(x + 60 * S, icon_y, 1.0 * S, wifi_color)

    if not show_bt:
      primary = signal.active if signal.active in ('cell', 'wifi') else (radios[0] if radios else None)
      self._text(self._label(signal, primary) if primary else 'No link', cx, y + 80 * S, 14, TEXT if primary else GRAY)
      return

    bt_color = _bt_color(signal)
    _bt_icon(x + 50 * S, y + 63 * S, 1.0 * S, bt_color)
    if signal.bt == 'connected':
      devices = signal.bt_devices or ('Connected',)
      self._text(self._fit(devices[0], 12, 86), cx, y + 89 * S, 12, TEXT)
      if len(devices) > 1:
        second = devices[1] if len(devices) == 2 else f"{devices[1]} +{len(devices) - 2}"
        self._text(self._fit(second, 11, 78), cx, y + 105 * S, 11, MUTED)
    else:
      self._text('BT On' if signal.bt == 'on' else 'BT Off', cx, y + 91 * S, 12, bt_color)

  def _lines(self, signal, radios, x, y, show_bt=True):
    S = self.s

    def row(radio, cy):
      strength = getattr(signal, radio) or 0
      active = signal.active == radio
      color = lit(signal, radio)
      icon_color = color if active else (MUTED if strength else GRAY)
      text_color = TEXT if active else (MUTED if strength else GRAY)
      _icon(radio, x + 24 * S, y + (cy - 9) * S, 1.0 * S, icon_color)
      self._text(self._label(signal, radio), x + 50 * S, y + cy * S, 20, text_color, 'left')
      for i in range(4):
        _pill(x + (122 + i * 32) * S, y + (cy - 4) * S, 27 * S, 8 * S, color if i < strength else DIM, s=S)

    row('cell', 26 if show_bt else 37)
    row('wifi', 56 if show_bt else 75)
    if not show_bt:
      return

    bt_color = _bt_color(signal)
    _bt_icon(x + 27 * S, y + 78 * S, 1.0 * S, bt_color)
    name_color = TEXT if signal.bt == 'connected' else (MUTED if signal.bt == 'on' else GRAY)
    self._text(self._bt_device_label(signal, 18, 128), x + 50 * S, y + 86 * S, 18, name_color, 'left')
    status = 'Connected' if signal.bt == 'connected' else ('On' if signal.bt == 'on' else 'Off')
    status_w = self.fonts.measure(status, FontRole.SEMI_BOLD, 16 * S).width
    self._text(status, x + 245 * S - status_w, y + 86 * S, 16, bt_color, 'left')
    _dot(x + 245 * S - status_w - 10 * S, y + 86 * S, 4 * S, bt_color, S)

  def _columns(self, signal, radios, x, y, show_bt=True):
    S = self.s
    top_off = 0 if show_bt else 14

    def column(radio, cx):
      strength = getattr(signal, radio) or 0
      on = strength > 0
      active = on and signal.active == radio
      color = lit(signal, radio)
      for i in range(4):
        _dot(x + cx * S, y + (24 + top_off + (3 - i) * 22) * S, 8 * S, color if i < strength else DIM, S)
      icon_w = 16.5 if radio == 'cell' else 18
      _icon(radio, x + (cx - icon_w / 2) * S, y + (110 + top_off) * S, 1.0 * S, color if active else (MUTED if on else GRAY))
      self._text(self._label(signal, radio), x + cx * S, y + (142 + top_off) * S, 13, TEXT if active else (MUTED if on else GRAY))

    column('cell', 34)
    column('wifi', 78)
    if not show_bt:
      return
    rl.draw_line_ex(rl.Vector2(x + 14 * S, y + 156 * S), rl.Vector2(x + 98 * S, y + 156 * S), max(1.0, 1.5 * S), DIM)
    self._draw_bt_footer(signal, x + 56 * S, y + 176 * S, 70)
