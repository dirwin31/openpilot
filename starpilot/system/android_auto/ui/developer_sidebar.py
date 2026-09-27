"""The car screen's status sidebar.

The comma's developer sidebar with two car additions: the car's status-slot
setting chooses the metrics (and shows the sidebar) instead of the Developer
Sidebar toggles, and device metrics 18-22 (CPU, GPU, TEMP, MEMORY, STORAGE)
exist for those slots.
"""

from openpilot.selfdrive.ui.onroad.starpilot.developer_sidebar import DeveloperSidebar
from openpilot.selfdrive.ui.ui_state import ui_state


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

  def _refresh_cache(self):
    super()._refresh_cache()
    if self.metric_override is not None:
      # -1 marks an empty slot: it is neither a toggle fallback (0) nor a metric.
      self._cached_metrics = [metric if metric > 0 else -1 for metric in self.metric_override]

  def update(self):
    super().update()
    if not self._visible:
      return
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
