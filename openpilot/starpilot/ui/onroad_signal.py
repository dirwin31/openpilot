"""Signal ring: cellular climbs the left half, Wi-Fi the right; a single radio closes the ring."""
from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.network_signal import Signal, SignalFeed
from openpilot.starpilot.ui.onroad_customization import SIGNAL_SIZE, SIGNAL_WIDGET
from openpilot.starpilot.ui.presentation import FontRole

ACCENT = rl.Color(199, 174, 247, 255)
ORANGE = rl.Color(255, 150, 60, 255)
GRAY = rl.Color(140, 136, 152, 255)
TEXT = rl.Color(245, 242, 252, 255)
MUTED = rl.Color(184, 178, 200, 255)
DIM = rl.Color(255, 255, 255, 43)
S = SIGNAL_SIZE[0] / 88  # drawn on an 88 px grid


def lit(signal: Signal, radio: str) -> rl.Color:
  if getattr(signal, radio) <= 1:
    return ORANGE
  return ACCENT if signal.active == radio else TEXT


def _arc(cx, cy, radius, width, start, end, color):
  rl.draw_ring(rl.Vector2(cx, cy), radius - width / 2, radius + width / 2, min(start, end), max(start, end), 24, color)


def _cell_icon(x, y, s, color):
  for i in range(4):
    h = (4 + 4 * i) * s
    rl.draw_rectangle_rec(rl.Rectangle(x + i * 4.5 * s, y + 16 * s - h, 3 * s, h), color)


def _wifi_icon(x, y, s, color):
  cx, cy = x + 9 * s, y + 14 * s
  rl.draw_circle_v(rl.Vector2(cx, cy), 2 * s, color)
  for radius in (6.5, 11.5):
    _arc(cx, cy, radius * s, 2.4 * s, 222, 318, color)


class SignalWidget:
  def __init__(self, fonts, feed=None):
    self.fonts = fonts
    self.feed = feed or SignalFeed()
    self.preview: Signal | None = None

  def _label(self, text, cx, y, color):
    size = 14 * S
    width = self.fonts.measure(text, FontRole.SEMI_BOLD, size).width
    self.fonts.draw(text, FontRole.SEMI_BOLD, size, cx - width / 2, y, color)

  def render(self, state):
    placed = state.customization['layouts']['large'].get(SIGNAL_WIDGET)
    if not placed or not placed['enabled'] or state.alert.size != 'none':
      return
    signal = self.preview or self.feed.snapshot()
    x, y = placed['x'], placed['y']
    cx, cy = x + 44 * S, y + 44 * S
    rl.draw_circle_v(rl.Vector2(cx, cy), 44 * S, rl.Color(15, 13, 23, round(235 * placed.get('opacity', 85) / 100)))
    radios = [radio for radio in ('cell', 'wifi') if getattr(signal, radio)]
    radius, width = 34 * S, 7 * S
    if len(radios) == 2:
      for i in range(4):
        start = 98 + i * 42.25
        _arc(cx, cy, radius, width, start, start + 37.25, lit(signal, 'cell') if i < signal.cell else DIM)
        start = 82 - i * 42.25
        _arc(cx, cy, radius, width, start - 37.25, start, lit(signal, 'wifi') if i < signal.wifi else DIM)
      _cell_icon(x + 24 * S, y + 22 * S, S, lit(signal, 'cell') if signal.active == 'cell' else MUTED)
      _wifi_icon(x + 46 * S, y + 22 * S, S, lit(signal, 'wifi') if signal.active == 'wifi' else MUTED)
      self._label(signal.tech if signal.active == 'cell' else 'Wi-Fi', cx, y + 47 * S, TEXT)
      return
    radio = radios[0] if radios else None
    for i in range(4):
      start = 94 + i * 90
      _arc(cx, cy, radius, width, start, start + 82, lit(signal, radio) if radio and i < getattr(signal, radio) else DIM)
    if radio == 'cell':
      _cell_icon(x + 33 * S, y + 19 * S, 1.3 * S, lit(signal, radio))
      self._label(signal.tech, cx, y + 48 * S, TEXT)
    elif radio == 'wifi':
      _wifi_icon(x + 32 * S, y + 19 * S, 1.3 * S, lit(signal, radio))
      self._label('Wi-Fi', cx, y + 48 * S, TEXT)
    else:
      _cell_icon(x + 24 * S, y + 22 * S, S, GRAY)
      _wifi_icon(x + 46 * S, y + 22 * S, S, GRAY)
      self._label('No link', cx, y + 47 * S, GRAY)
