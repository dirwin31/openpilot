"""Shared R4/R8 alert card and touch target for comma 3X and Android Auto."""
import pyray as rl

from openpilot.starpilot.ui.presentation import FontRole
from openpilot.starpilot.ui.onroad_customization import PALETTE, widget_palette
from openpilot.starpilot.uniden.feed import DetectorFeed
from openpilot.starpilot.uniden.protocol import alert_key

WIDGET = 'uniden_alerts'
SIZE = (460, 150)
MAX_SIZE = (620, 260)


def signal_color(strength, index):
  """Galaxy update progress colors: secondary teal to primary purple."""
  t = index / max(1, strength - 1)
  return rl.Color(round(77 + 80 * t), round(232 - 118 * t), round(232 + 23 * t), 255)


def card_size(alerts):
  return (620, 84 + 44 * min(len(alerts), 4)) if len(alerts) > 1 else SIZE


def direction_arrows(cx, cy, direction, accent, radius):
  # Side means both sides: this detector protocol cannot distinguish left/right.
  for name, dx, dy in (('front', 0, -1), ('rear', 0, 1), ('side', -1, 0), ('side', 1, 0)):
    if name != direction:
      continue
    # Center the active glyph in its row, rather than leaving it on a compass edge.
    center_x = cx + dx * radius if direction == 'side' else cx
    half_length = radius * (.65 if direction == 'side' else .85)
    tip = rl.Vector2(center_x + dx * half_length, cy + dy * half_length)
    left = rl.Vector2(center_x - dx * half_length - dy * radius, cy - dy * half_length + dx * radius)
    right = rl.Vector2(center_x - dx * half_length + dy * radius, cy - dy * half_length - dx * radius)
    rl.draw_triangle(tip, right, left, accent)


class UnidenWidget:
  def __init__(self, fonts, feed=None):
    self.fonts = fonts
    self.feed = feed or DetectorFeed()
    self.pressed = None
    self.runtime_active = False
    self.preview_data = None
    self.visible_until = 0.

  @staticmethod
  def placement(state):
    if state is None or state.alert.size != 'none':
      return None
    p = state.customization['layouts']['large'].get(WIDGET)
    return p if p and p['enabled'] else None

  def render(self, state):
    import time
    p = self.placement(state)
    self.visible_until = 0.
    self.feed.set_enabled(self.runtime_active and p is not None)
    data = self.preview_data or self.feed.snapshot()
    if not p or not data.get('config', {}).get('enabled'):
      return
    alerts = data.get('alerts', [])
    config = {**data.get('config', {}), **p}
    if not alerts and config.get('hide_when_no_alert', True):
      return
    self.visible_until = time.monotonic() + 1
    x, y = p['x'], p['y']
    width, height = card_size(alerts)
    palette = widget_palette({'palette': PALETTE, **state.customization}, 'large', WIDGET)
    def color(key):
      value = palette[key]
      return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5, 7))
    red, green, blue, _ = color('text')
    text = rl.Color(red, green, blue, 255)
    dim = rl.Color(round(red * .7), round(green * .7), round(blue * .7), 255)
    red, green, blue, alpha = color('cardFill')
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, width, height), .16, 12,
                              rl.Color(red, green, blue, round(alpha * (100 - config.get('transparency', 8)) / 100)))
    rl.draw_rectangle_rounded_lines_ex(rl.Rectangle(x, y, width, height), .16, 12, 2, rl.Color(*color('cardBorder')))
    def label(value, size, dx, dy, color=text):
      self.fonts.draw(value, FontRole.SEMI_BOLD, size, x + dx, y + dy, color)
    if not data.get('connected'):
      label('UNIDEN', 22, 24, 20, dim)
      label('Detector disconnected', 28, 24, 56)
      label('Waiting for fresh alerts', 20, 24, 105, dim)
      return
    if not alerts:
      label('UNIDEN', 22, 24, 20, dim)
      label('Detector ready', 28, 24, 56, rl.Color(100, 214, 151, 255))
      label('No active alerts', 20, 24, 105, dim)
      return
    muted = all(item['muted'] for item in alerts)
    hint = 'Muted' if muted else 'Mute sent • awaiting detector' if data.get('command') else 'Tap anywhere to mute' if data.get('can_mute') else 'Alert'

    def threat(item, row=None):
      tone = rl.Color(255, 91, 101, 255) if item['band'] in ('KA', 'LASER') else rl.Color(255, 186, 78, 255)
      frequency = f"{item['frequency']:.3f} GHz" if item['frequency'] is not None else 'Laser' if item['band'] == 'LASER' else 'Gatso'
      if row is None:
        label(item['band'], 42, 24, 16, tone)
        label(frequency, 25, 24, 66)
        bx, baseline, cx, cy, radius = 228, 93, 394, 68, 28
      else:
        top = 48 + row * 44
        label(item['band'], 24, 24, top, tone)
        label(frequency, 22, 124, top + 1)
        bx, baseline, cx, cy, radius = 304, top + 27, 466, top + 17, 14
        if item['muted']:
          label('Muted', 18, 520, top + 4, dim)
      for i in range(8):
        bar_height = 8 + i * (3 if row is None else 2)
        rl.draw_rectangle(int(x + bx + i * 12), int(y + baseline - bar_height), 8, bar_height,
                          signal_color(item['strength'], i) if i < item['strength'] else rl.Color(53, 67, 77, 255))
      if data.get('model') == 'R8':
        direction_arrows(x + cx, y + cy, item['direction'], tone, radius)

    if len(alerts) > 1:
      label(f"UNIDEN {data.get('model', '')}  •  {len(alerts)} THREATS", 20, 24, 14, dim)
      for row, item in enumerate(alerts[:4]):
        threat(item, row)
      label(hint, 19, 24, height - 29, dim)
      if len(alerts) > 4:
        label(f'+{len(alerts) - 4} more', 19, 510, height - 29, dim)
    else:
      threat(alerts[0])
      label(hint, 19, 24, 116, dim)

  def touch(self, kind, x, y, state):
    import time
    p, data = self.placement(state), self.feed.snapshot()
    valid = (p is not None and time.monotonic() < self.visible_until and data.get('connected') and
             data.get('can_mute') and bool(data.get('alerts')))
    width, height = card_size(data.get('alerts', []))
    inside = valid and p['x'] <= x <= p['x'] + width and p['y'] <= y <= p['y'] + height
    key = (data.get('address'), alert_key(data.get('alerts', [])))
    if kind == 'down':
      self.pressed = (key, x, y) if inside else None
    claimed = self.pressed is not None
    if kind in ('move', 'up') and self.pressed:
      old, px, py = self.pressed
      if not inside or old != key or (px - x) ** 2 + (py - y) ** 2 > 36 ** 2:
        self.pressed = None
      elif kind == 'up':
        self.feed.mute(data)
    if kind in ('up', 'cancel'):
      self.pressed = None
    return claimed

  def close(self):
    self.feed.close()
