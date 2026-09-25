from types import SimpleNamespace

import pytest

from openpilot.selfdrive.ui.onroad.starpilot import developer_sidebar as module


class FakeParams:
  def get_int(self, key, **kwargs):
    return {"DeveloperSidebarMetric1": 5, "DeveloperSidebarMetric2": 6, "DeveloperSidebarMetric3": 7}.get(key, 0)

  def get_bool(self, key, default=False, **kwargs):
    return key == "DeveloperSidebar" or default

  def get_float(self, key, **kwargs):
    return 0.0

  def get(self, key, **kwargs):
    return None


class FakeSM(dict):
  frame = 100

  def __init__(self, device_state):
    super().__init__(deviceState=device_state)
    self.valid = {"deviceState": True}


@pytest.fixture
def sidebar(monkeypatch):
  monkeypatch.setattr(module.gui_app, "font", lambda *args, **kwargs: None)
  monkeypatch.setattr(module.ui_state, "ui_params", FakeParams(), raising=False)
  device_state = SimpleNamespace(cpuUsagePercent=[20, 40], gpuUsagePercent=63, maxTempC=71.4)
  monkeypatch.setattr(module.ui_state, "sm", FakeSM(device_state), raising=False)
  monkeypatch.setattr(module.ui_state, "started_frame", 0, raising=False)
  monkeypatch.setattr(module.ui_state, "starpilot_toggles", {}, raising=False)
  return module.DeveloperSidebar()


def test_device_screen_keeps_the_auto_tune_values(sidebar):
  sidebar.update()
  assert [sidebar._metrics[i][0] for i in (5, 6, 7)] == ["LAT ACCEL", "STEER RATIO", "STEER STIFF"]


def test_car_screen_shows_cpu_gpu_and_temp_in_their_place(sidebar):
  sidebar.device_load_in_place_of_tuning = True
  sidebar.update()
  assert sidebar._active_ids == [5, 6, 7]
  assert [sidebar._metrics[i] for i in (5, 6, 7)] == [("CPU", "30%"), ("GPU", "63%"), ("TEMP", "71°C")]
  assert not any(i in sidebar._metric_colors for i in (5, 6, 7)), "device load isn't colored like an auto-tune value"
