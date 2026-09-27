"""The car screen's status sidebar.

The comma's developer sidebar with car additions: the car's status-slot
setting chooses the metrics (and shows the sidebar) instead of the Developer
Sidebar toggles, device metrics 18-22 (CPU, GPU, TEMP, MEMORY, STORAGE) exist
for those slots, and a slot can show the StarPilot logo (23) or stay blank (-1),
keeping its place in the column.
"""

import pyray as rl

from openpilot.selfdrive.ui.onroad.starpilot.developer_sidebar import METRIC_HEIGHT, METRIC_MARGIN, METRIC_WIDTH, DeveloperSidebar
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app

LOGO_METRIC = 23
BLANK_METRIC = -1
LOGO_HEIGHT = METRIC_HEIGHT - 16
LOGO_SIZE = (round(LOGO_HEIGHT * 750 / 770), LOGO_HEIGHT)  # StarPilotLogo.png is 750x770


class _ShowSidebarParams:
  """Reads through to Params, except the car's slots always show the sidebar."""

  def __init__(self, params, owner: "CarDeveloperSidebar"):
    self._params = params
    self._owner = owner

  def get_bool(self, key, *args, **kwargs):
    if key == "DeveloperSidebar" and self._owner.metric_override is not None:
      return True
    return self._params.get_bool(key, *args, **kwargs)

  def __getattr__(self, name):
    return getattr(self._params, name)


class CarDeveloperSidebar(DeveloperSidebar):
  def __init__(self):
    super().__init__()
    self.metric_override: list[int] | None = None
    self._params = _ShowSidebarParams(self._params, self)
    self._slot_ids: list[int] = []  # every car slot in order, blanks included
    self._logo = None

  def _refresh_cache(self):
    super()._refresh_cache()
    if self.metric_override is not None:
      # -1 marks an empty slot: it is neither a toggle fallback (0) nor a metric.
      self._cached_metrics = [metric if metric > 0 else -1 for metric in self.metric_override]

  def update(self):
    super().update()
    if not self._visible:
      return
    self._slot_ids = list(self._cached_metrics) if self.metric_override is not None else []
    sm = ui_state.sm
    device_state = sm["deviceState"] if sm.valid.get("deviceState", False) else None
    cpu_list = list(device_state.cpuUsagePercent) if device_state else []
    cpu_pct = int(sum(cpu_list) / len(cpu_list)) if cpu_list else 0
    gpu_pct = int(device_state.gpuUsagePercent) if device_state else -1
    self._metrics.update({
      18: ("CPU", f"{cpu_pct}%"),
      19: ("GPU", f"{gpu_pct}%" if gpu_pct >= 0 else "N/A"),
      20: ("TEMP", f"{int(device_state.maxTempC) if device_state else 0}°C"),
      21: ("MEMORY", f"{int(device_state.memoryUsagePercent) if device_state else 0}%"),
      22: ("STORAGE", f"{int(device_state.freeSpacePercent) if device_state else 0}% FREE"),
    })

  def render(self, sidebar_rect: rl.Rectangle):
    slots = self._slot_ids
    if not self._visible or self.metric_override is None or not slots or all(slot <= 0 for slot in slots):
      super().render(sidebar_rect)
      return
    rl.draw_rectangle_rec(sidebar_rect, rl.BLACK)
    count = len(slots)
    spacing = max(1, (int(sidebar_rect.height) - count * METRIC_HEIGHT) // (count + 1))
    y = sidebar_rect.y + spacing
    for metric_id in slots:
      if metric_id == LOGO_METRIC:
        self._draw_logo(sidebar_rect, y)
      elif metric_id in self._metrics:
        label_first, label_second = self._metrics[metric_id]
        self._draw_metric(sidebar_rect, label_first, label_second, self._metric_colors.get(metric_id, self._metric_color), y)
      y += METRIC_HEIGHT + spacing

  def _draw_logo(self, sidebar_rect: rl.Rectangle, y: float) -> None:
    if self._logo is None:
      self._logo = gui_app.texture("images/StarPilotLogo.png", *LOGO_SIZE)
    card_x = sidebar_rect.x + sidebar_rect.width - METRIC_MARGIN - METRIC_WIDTH
    x = card_x + (METRIC_WIDTH - self._logo.width) / 2
    rl.draw_texture_v(self._logo, rl.Vector2(round(x), round(y + (METRIC_HEIGHT - self._logo.height) / 2)), rl.WHITE)
