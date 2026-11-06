"""Big model card shared by the native and Android Auto screens: which model drives, and the external GPU's state."""
from typing import NamedTuple

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.models.jetlink_status import JetlinkStatus
from openpilot.starpilot.ui.onroad_customization import JETLINK_SIZE, JETLINK_WIDGET
from openpilot.starpilot.ui.presentation import FontRole

GREEN = rl.Color(70, 200, 110, 255)
ORANGE = rl.Color(255, 150, 60, 255)
ACCENT = rl.Color(199, 174, 247, 255)
GRAY = rl.Color(140, 136, 152, 255)
TEXT = rl.Color(245, 242, 252, 255)
MUTED = rl.Color(184, 178, 200, 255)
BIG, SMALL = 'Big model', 'Small model'  # the model driving now
# 20 Hz frames: a host this close to 50 ms is about to fall behind
SLOW_MS = 40.
JETLINK_DETAIL = {'ready': ('Jetlink ready', ACCENT), 'connecting': ('Jetlink connecting', ACCENT),
                  'connected': ('Jetlink connected', ACCENT), 'disconnected': ('Jetlink offline', GRAY),
                  'unavailable': ('Jetlink error', ORANGE)}
CHESTNUT_DETAIL = {'loading': ('Chestnut loading', ACCENT), 'ready': ('Chestnut ready', ACCENT),
                   'uncompiled': ('Chestnut not built', ACCENT)}


class Card(NamedTuple):
  title: str
  detail: str
  dot: rl.Color
  latency_ms: float | None = None


def big_model_card(jetlink: JetlinkStatus, gpu: str | None = None, chestnut: bool = False) -> Card:
  """`gpu` is UIState.chestnut_state's value (Jetlink's or the Chestnut's, by modelV2.big), None where
  nobody tracks it; `chestnut` that one is fitted."""
  if jetlink.state != 'off':
    if jetlink.state == 'active':
      return Card(BIG, 'Jetlink', GREEN, jetlink.latency_ms)
    if jetlink.offer:
      return Card(SMALL, 'Re-engage to switch', ORANGE if gpu == 'failed' else ACCENT)
    if gpu == 'failed' and jetlink.state not in ('disconnected', 'unavailable'):
      return Card(SMALL, 'Fell back', ORANGE)
    if jetlink.state == 'connected' and jetlink.building:
      return Card(SMALL, 'Jetlink building', ACCENT)
    return Card(SMALL, *JETLINK_DETAIL.get(jetlink.state, JETLINK_DETAIL['unavailable']))
  if not chestnut:
    return Card(SMALL, 'Disconnected', GRAY)
  if gpu == 'active':
    return Card(BIG, 'Chestnut', GREEN)
  if gpu == 'failed':
    return Card(SMALL, 'Fell back', ORANGE)
  return Card(SMALL, *CHESTNUT_DETAIL.get(gpu, ('Chestnut', ACCENT)))


def gpu_inputs(ui) -> tuple[str | None, bool]:
  """(UIState.chestnut_state's value, a Chestnut is fitted) for big_model_card. Never raises."""
  try:
    return ui.chestnut_state.value, bool(ui.sm["deviceState"].chestnutPresent)
  except Exception:
    return None, False


class JetlinkStatusWidget:
  def __init__(self, fonts):
    self.fonts = fonts
    self.status = JetlinkStatus()
    self.gpu: str | None = None
    self.chestnut = False

  def _fit(self, text, role, size, width):
    measured = self.fonts.measure(text, role, size)
    return size * width / measured.width if measured.width > width else size

  def render(self, state):
    placed = state.customization['layouts']['large'].get(JETLINK_WIDGET)
    if not placed or not placed['enabled'] or state.alert.size != 'none':
      return
    card = big_model_card(self.status, self.gpu, self.chestnut)
    rect = rl.Rectangle(placed['x'], placed['y'], *JETLINK_SIZE)
    h = rect.height
    rl.draw_rectangle_rounded(rect, 1.0, 16, rl.Color(15, 13, 23, round(235 * placed.get('opacity', 85) / 100)))
    rl.draw_circle_v(rl.Vector2(rect.x + h / 2, rect.y + h / 2), 9, card.dot)
    right = rect.x + rect.width - 30
    if card.latency_ms is not None:
      value, unit = f'{card.latency_ms:.1f}', 'ms'
      unit_w = self.fonts.measure(unit, FontRole.NORMAL, 22).width
      value_w = self.fonts.measure(value, FontRole.SEMI_BOLD, 34).width
      self.fonts.draw(unit, FontRole.NORMAL, 22, right - unit_w, rect.y + h / 2 - 6, MUTED)
      right -= unit_w + 8
      self.fonts.draw(value, FontRole.SEMI_BOLD, 34, right - value_w, rect.y + h / 2 - 18,
                      ORANGE if card.latency_ms >= SLOW_MS else TEXT)
      right -= value_w + 16
    left = rect.x + h - 6
    width = right - left
    self.fonts.draw(card.title, FontRole.SEMI_BOLD, self._fit(card.title, FontRole.SEMI_BOLD, 28, width), left, rect.y + 12, TEXT)
    self.fonts.draw(card.detail, FontRole.NORMAL, self._fit(card.detail, FontRole.NORMAL, 22, width), left, rect.y + 46, MUTED)
