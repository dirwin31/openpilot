"""Signal strength: a split ring, or a named track per radio laid out across or in columns."""
from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.network_signal import Signal, SignalFeed
from openpilot.starpilot.ui.onroad_customization import SIGNAL_GRIDS, SIGNAL_SCALE, SIGNAL_WIDGET
from openpilot.starpilot.ui.presentation import FontRole

ACCENT = rl.Color(199, 174, 247, 255)
ORANGE = rl.Color(255, 150, 60, 255)
GRAY = rl.Color(140, 136, 152, 255)
TEXT = rl.Color(245, 242, 252, 255)
MUTED = rl.Color(184, 178, 200, 255)
DIM = rl.Color(255, 255, 255, 43)
S = SIGNAL_SCALE


def lit(signal: Signal, radio: str) -> rl.Color:
  if getattr(signal, radio) <= 1:
    return ORANGE
  return ACCENT if signal.active == radio else TEXT


def _arc(cx, cy, radius, width, start, end, color):
  rl.draw_ring(rl.Vector2(cx, cy), radius - width / 2, radius + width / 2, min(start, end), max(start, end), 24, color)


def _pill(x, y, width, height, color, roundness=1.0):
  rl.draw_rectangle_rounded(rl.Rectangle(x, y, width, height), roundness, 12, color)


def _cell_icon(x, y, s, color):
  for i in range(4):
    h = (4 + 4 * i) * s
    rl.draw_rectangle_rec(rl.Rectangle(x + i * 4.5 * s, y + 16 * s - h, 3 * s, h), color)


def _wifi_icon(x, y, s, color):
  cx, cy = x + 9 * s, y + 14 * s
  rl.draw_circle_v(rl.Vector2(cx, cy), 2 * s, color)
  for radius in (6.5, 11.5):
    _arc(cx, cy, radius * s, 2.4 * s, 222, 318, color)


def _icon(radio, x, y, s, color):
  (_cell_icon if radio == 'cell' else _wifi_icon)(x, y, s, color)


class SignalWidget:
  def __init__(self, fonts, feed=None):
    self.fonts = fonts
    self.feed = feed or SignalFeed()
    self.preview: Signal | None = None

  def _text(self, text, x, cy, size, color, align='center'):
    """Draws text with its ink centered on cy; x is the centre, or the left edge with align='left'."""
    size *= S
    top, bottom = self.fonts.vertical_ink(text, FontRole.SEMI_BOLD, size)
    if align == 'center':
      x -= self.fonts.measure(text, FontRole.SEMI_BOLD, size).width / 2
    self.fonts.draw(text, FontRole.SEMI_BOLD, size, x, cy - (top + bottom) / 2, color)

  def render(self, state):
    placed = state.customization['layouts']['large'].get(SIGNAL_WIDGET)
    if not placed or not placed['enabled'] or state.alert.size != 'none':
      return
    signal = self.preview or self.feed.snapshot()
    style = placed.get('size', 'ring')
    style = style if style in SIGNAL_GRIDS else 'ring'
    background = rl.Color(15, 13, 23, round(235 * placed.get('opacity', 85) / 100))
    radios = [radio for radio in ('cell', 'wifi') if getattr(signal, radio)]
    x, y = placed['x'], placed['y']
    if style == 'ring':
      self._ring(signal, radios, x, y, background)
      return
    width, height = SIGNAL_GRIDS[style]
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, width * S, height * S), 1.0 if style == 'horizontal' else 24 / 48, 24, background)
    (self._lines if style == 'horizontal' else self._columns)(signal, radios, x, y)

  def _label(self, signal, radio):
    return signal.tech if radio == 'cell' else 'Wi-Fi'

  def _ring(self, signal, radios, x, y, background):
    cx, cy = x + 44 * S, y + 44 * S
    rl.draw_circle_v(rl.Vector2(cx, cy), 44 * S, background)
    radius, width = 34 * S, 7 * S
    if len(radios) == 2:
      for i in range(4):
        start = 98 + i * 42.25
        _arc(cx, cy, radius, width, start, start + 37.25, lit(signal, 'cell') if i < signal.cell else DIM)
        start = 82 - i * 42.25
        _arc(cx, cy, radius, width, start - 37.25, start, lit(signal, 'wifi') if i < signal.wifi else DIM)
      _cell_icon(x + 24 * S, y + 22 * S, S, lit(signal, 'cell') if signal.active == 'cell' else MUTED)
      _wifi_icon(x + 46 * S, y + 22 * S, S, lit(signal, 'wifi') if signal.active == 'wifi' else MUTED)
      self._text(self._label(signal, 'cell' if signal.active == 'cell' else 'wifi'), cx, y + 57 * S, 14, TEXT)
      return
    radio = radios[0] if radios else None
    for i in range(4):
      start = 94 + i * 90
      _arc(cx, cy, radius, width, start, start + 82, lit(signal, radio) if radio and i < getattr(signal, radio) else DIM)
    if radio:
      _icon(radio, x + (33 if radio == 'cell' else 32) * S, y + 19 * S, 1.3 * S, lit(signal, radio))
      self._text(self._label(signal, radio), cx, y + 59 * S, 14, TEXT)
    else:
      _cell_icon(x + 24 * S, y + 22 * S, S, GRAY)
      _wifi_icon(x + 46 * S, y + 22 * S, S, GRAY)
      self._text('No link', cx, y + 57 * S, 13, GRAY)

  def _lines(self, signal, radios, x, y):
    def row(radio, cy, big):
      active = signal.active == radio
      color = lit(signal, radio)
      icon_s, track_h = (1.2, 10) if big else (1.0, 8)
      _icon(radio, x + 24 * S, y + (cy - (11 if big else 9)) * S, icon_s * S, color if active else MUTED)
      self._text(self._label(signal, radio), x + (54 if big else 50) * S, y + cy * S, 22 if big else 20, TEXT if active else MUTED, 'left')
      for i in range(4):
        _pill(x + (122 + i * 32) * S, y + (cy - track_h / 2) * S, 27 * S, track_h * S, color if i < getattr(signal, radio) else DIM)

    if len(radios) == 2:
      row('cell', 28, False)
      row('wifi', 57, False)
    elif radios:
      row(radios[0], 42, True)
    else:
      rl.draw_circle_v(rl.Vector2(x + 42 * S, y + 42 * S), 9 * S, GRAY)
      self._text('No connection', x + 66 * S, y + 42 * S, 22, GRAY, 'left')

  def _columns(self, signal, radios, x, y):
    def column(radio, cx, segment, big, on=True):
      active = on and signal.active == radio
      color = lit(signal, radio) if on else GRAY
      for i in range(4):
        filled = on and i < getattr(signal, radio)
        _pill(x + (cx - segment / 2) * S, y + (16 + (3 - i) * 22) * S, segment * S, 16 * S, color if filled else DIM, 10 / 16)
      icon_s = 1.2 if big else 1.0
      icon_w = (16.5 if radio == 'cell' else 18) * icon_s
      _icon(radio, x + (cx - icon_w / 2) * S, y + 110 * S, icon_s * S, color if active else (MUTED if on else GRAY))
      self._text(self._label(signal, radio), x + cx * S, y + 142 * S, 16 if big else 13, TEXT if active else (MUTED if on else GRAY))

    if len(radios) == 1:
      column(radios[0], 48, 32, True)
    else:
      column('cell', 28, 22, False, bool(radios))
      column('wifi', 68, 22, False, bool(radios))
