"""Jet Link's Android Auto card; both live rendering and previews use this drawing."""
import pyray as rl

from openpilot.starpilot.system.android_auto.projection_jetlink import JetlinkStatus
from openpilot.starpilot.system.android_auto.projection_layout import JETLINK_SIZE, JETLINK_WIDGET
from openpilot.starpilot.ui.presentation import FontRole

LABELS = {'active': 'Link up', 'connected': 'USB connected', 'ready': 'Ready to switch',
          'connecting': 'Connecting', 'disconnected': 'Disconnected', 'unavailable': 'Unavailable'}


class JetlinkStatusWidget:
  def __init__(self, fonts):
    self.fonts = fonts
    self.status = JetlinkStatus()

  def render(self, state):
    placed = state.customization['layouts']['large'].get(JETLINK_WIDGET)
    status = self.status
    if not placed or not placed['enabled'] or status.state == 'off' or state.alert.size != 'none':
      return
    rect = rl.Rectangle(placed['x'], placed['y'], *JETLINK_SIZE)
    color = rl.Color(70, 200, 110, 255) if status.state == 'active' else (
      rl.Color(255, 150, 60, 255) if status.state == 'unavailable' else rl.Color(199, 174, 247, 255))
    rl.draw_rectangle_rounded(rect, .2, 8, rl.Color(15, 13, 23, 245))
    rl.draw_rectangle_rounded_lines_ex(rect, .2, 8, 3, color)
    title = f'Jet Link: {status.device}' if status.device else 'Jet Link'
    latency = f'{status.latency_ms:.1f} ms' if status.latency_ms is not None and status.state == 'active' else '--'
    rows = ((title, FontRole.SEMI_BOLD, 27, 14),
            (LABELS.get(status.state, 'Unavailable'), FontRole.SEMI_BOLD, 32, 56),
            (f'App latency: {latency}', FontRole.NORMAL, 25, 108))
    for text, role, size, y in rows:
      measured = self.fonts.measure(text, role, size)
      if measured.width > rect.width - 32:
        size *= (rect.width - 32) / measured.width
      self.fonts.draw(text, role, size, rect.x + 16, rect.y + y, color)
