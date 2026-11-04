"""Shared R4/R8 alert card and touch target for comma 3X and Android Auto."""
from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.presentation import FontRole
from openpilot.starpilot.ui.onroad_customization import PALETTE, widget_palette
from openpilot.starpilot.uniden.feed import DetectorFeed
from openpilot.starpilot.uniden.protocol import alert_key

WIDGET = 'uniden_alerts'
SIZE = (460, 150)
MAX_SIZE = (620, 260)
MAX_ROWS = 4
ROW, PAD, RADIUS, BORDER = 60, 8, 14, 2
SCALE_RANGE = (50, 150)       # percent
MEMORY_MUTES = ('memory', 'auto_memory')


def shown_alerts(alerts, config):
  """Alerts the driver asked to see: Quiet Ride and mute-memory alerts can be left off the card."""
  hidden = (('quiet_ride',) if config.get('hide_quiet_ride') else ()) + (MEMORY_MUTES if config.get('hide_mute_memory') else ())
  return [item for item in alerts if item.get('mute_mode', '') not in hidden]


def card_scale(config):
  value = config.get('scale', 100)
  return min(SCALE_RANGE[1], max(SCALE_RANGE[0], value)) / 100 if type(value) is int else 1.


def signal_color(strength, index):
  """Galaxy update progress colors: secondary teal to primary purple."""
  t = index / max(1, strength - 1)
  return rl.Color(round(77 + 80 * t), round(232 - 118 * t), round(232 + 23 * t), 255)


def card_size(alerts):
  """One row per alert, the band, frequency, signal and arrow side by side."""
  return (SIZE[0], PAD * 2 + ROW * min(len(alerts), MAX_ROWS)) if alerts else SIZE


def outline(x, y, width, height, color, gap=None):
  """Rounded card edge; gap=(left, right) leaves the top edge open there for an inline label."""
  r, t = RADIUS, BORDER
  top = [(x + r, x + width - r)] if gap is None else [(x + r, gap[0]), (gap[1], x + width - r)]
  for left, right in top:
    if right > left:
      rl.draw_rectangle_rec(rl.Rectangle(left, y, right - left, t), color)
  rl.draw_rectangle_rec(rl.Rectangle(x + r, y + height - t, width - 2 * r, t), color)
  rl.draw_rectangle_rec(rl.Rectangle(x, y + r, t, height - 2 * r), color)
  rl.draw_rectangle_rec(rl.Rectangle(x + width - t, y + r, t, height - 2 * r), color)
  for cx, cy, start in ((x + r, y + r, 180), (x + width - r, y + r, 270), (x + width - r, y + height - r, 0), (x + r, y + height - r, 90)):
    rl.draw_ring(rl.Vector2(cx, cy), r - t, r, start, start + 90, 8, color)


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
    config = {**data.get('config', {}), **p}
    alerts = shown_alerts(data.get('alerts', []), config)
    if not alerts and config.get('hide_when_no_alert', True):
      return
    self.visible_until = time.monotonic() + 1
    scale = card_scale(config)
    if scale == 1:
      self._card(state, data, alerts, config, p['x'], p['y'])
      return
    rl.rl_push_matrix()
    try:
      rl.rl_translatef(p['x'], p['y'], 0)
      rl.rl_scalef(scale, scale, 1)
      self._card(state, data, alerts, config, 0, 0)
    finally:
      rl.rl_pop_matrix()

  def _card(self, state, data, alerts, config, x, y):
    width, height = card_size(alerts)
    palette = widget_palette({'palette': PALETTE, **state.customization}, 'large', WIDGET)
    def color(key):
      value = palette[key]
      return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5, 7))
    red, green, blue, _ = color('text')
    text = rl.Color(red, green, blue, 255)
    dim = rl.Color(round(red * .7), round(green * .7), round(blue * .7), 255)
    red, green, blue, alpha = color('cardFill')
    border = rl.Color(*color('cardBorder'))
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, width, height), RADIUS * 2 / min(width, height), 12,
                              rl.Color(red, green, blue, round(alpha * (100 - config.get('transparency', 8)) / 100)))
    def label(value, size, dx, dy, color=text):
      self.fonts.draw(value, FontRole.SEMI_BOLD, size, x + dx, y + dy, color)
    if not data.get('connected') or not alerts:
      outline(x, y, width, height, border)
      label('UNIDEN', 22, 24, 20, dim)
      if not data.get('connected'):
        label('Detector disconnected', 28, 24, 56)
        label('Waiting for fresh alerts', 20, 24, 105, dim)
      else:
        label('Detector ready', 28, 24, 56, rl.Color(100, 214, 151, 255))
        label('No active alerts', 20, 24, 105, dim)
      return

    # The mute state rides in the top edge of the card rather than taking a row of its own.
    muted = sum(bool(item['muted']) for item in alerts)
    notes = ['Mute sent' if data.get('command') and muted < len(alerts) else
             'Muted' if muted == len(alerts) else f'{muted} muted' if muted else '']
    if len(alerts) > MAX_ROWS:
      notes.append(f'+{len(alerts) - MAX_ROWS} more')
    note = '  •  '.join(n for n in notes if n)
    gap = None
    if note:
      size = 18
      measured = self.fonts.measure(note, FontRole.SEMI_BOLD, size)
      gap = (x + 20, x + 20 + measured.width + 16)
      self.fonts.draw(note, FontRole.SEMI_BOLD, size, gap[0] + 8, y + BORDER / 2 - measured.height / 2, border)
    outline(x, y, width, height, border, gap)

    shown = alerts[:MAX_ROWS]
    band_size, frequency_size = 36, 28
    band_width = max(self.fonts.measure(item['band'], FontRole.SEMI_BOLD, band_size).width for item in shown)
    frequency_x = 24 + max(64, band_width + 18)
    arrows = data.get('model') == 'R8'
    arrow_x = width - 24 - 25                       # room for the two side arrows, which sit either side of this
    bars_x = (arrow_x - 25 - 18 if arrows else width - 24) - 8 * 11 + 3
    for row, item in enumerate(shown):
      cy = PAD + ROW * row + ROW / 2
      faded = item['muted'] and muted < len(alerts)
      tone = rl.Color(255, 91, 101, 255) if item['band'] in ('KA', 'LASER') else rl.Color(255, 186, 78, 255)
      if faded:
        tone = rl.Color(round(tone.r * .55), round(tone.g * .55), round(tone.b * .55), 255)
      band = self.fonts.measure(item['band'], FontRole.SEMI_BOLD, band_size)
      label(item['band'], band_size, 24, cy - band.height / 2, tone)
      if item['frequency'] is not None or item['band'] not in ('LASER',):
        frequency = f"{item['frequency']:.3f} GHz" if item['frequency'] is not None else 'Gatso'
        measured = self.fonts.measure(frequency, FontRole.SEMI_BOLD, frequency_size)
        label(frequency, frequency_size, frequency_x, cy - measured.height / 2, dim if faded else text)
      for i in range(8):
        bar_height = 8 + i * 3
        lit = i < item['strength']
        fill = signal_color(item['strength'], i) if lit else rl.Color(53, 67, 77, 255)
        if lit and faded:
          fill = rl.Color(round(fill.r * .55), round(fill.g * .55), round(fill.b * .55), 255)
        rl.draw_rectangle(int(x + bars_x + i * 11), int(y + cy + 15 - bar_height), 8, bar_height, fill)
      if arrows:
        direction_arrows(x + arrow_x, y + cy, item['direction'], tone, 15)

  def touch(self, kind, x, y, state):
    import time
    p, data = self.placement(state), self.feed.snapshot()
    config = {**data.get('config', {}), **(p or {})}
    shown = shown_alerts(data.get('alerts', []), config)
    valid = (p is not None and time.monotonic() < self.visible_until and data.get('connected') and
             data.get('can_mute') and bool(shown))
    scale = card_scale(config)
    width, height = (side * scale for side in card_size(shown))
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
