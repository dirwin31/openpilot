from types import SimpleNamespace

import pytest

from openpilot.selfdrive.ui import ui_state as ui_state_module


class FakeParams:
  def __init__(self, **values):
    self.values = values

  def get(self, key):
    return self.values.get(key)

  def get_bool(self, key, **_kwargs):
    return bool(self.values.get(key, False))

  def get_int(self, key, **_kwargs):
    return int(self.values.get(key, 0))


class PassthroughFilter:
  def update(self, value):
    return value


def make_device(monkeypatch, **overrides):
  values = {
    "ScreenManagement": True,
    "ScreenBrightness": 35,
    "ScreenBrightnessOnroad": 45,
    "ScreenTimeout": 30,
    "ScreenTimeoutOnroad": 10,
    "StandbyMode": False,
  }
  values.update(overrides)
  state = SimpleNamespace(
    ui_params=FakeParams(**values),
    params_memory=FakeParams(),
    status=ui_state_module.UIStatus.DISENGAGED,
    started=False,
    ignition=False,
    light_sensor=-1.0,
    sm={},
    android_auto_car_view=False,
  )
  monkeypatch.setattr(ui_state_module, "ui_state", state)
  monkeypatch.setattr(ui_state_module.gui_app, "big_ui", lambda: False)
  monkeypatch.setattr(ui_state_module.gui_app, "_mouse_events", [])
  device = ui_state_module.Device()
  device._brightness_filter = PassthroughFilter()
  return device, state


def test_manual_brightness_applies_to_current_device_state(monkeypatch):
  device, state = make_device(monkeypatch)

  assert device._calculate_brightness() == 35

  state.started = True
  assert device._calculate_brightness() == 45


def test_auto_brightness_preserves_existing_behavior(monkeypatch):
  device, state = make_device(monkeypatch, ScreenBrightness=101, ScreenBrightnessOnroad=101)

  assert device._calculate_brightness() == ui_state_module.BACKLIGHT_OFFROAD

  state.started = True
  state.light_sensor = -1.0
  assert device._calculate_brightness() == ui_state_module.BACKLIGHT_OFFROAD


def test_screen_management_off_ignores_custom_values(monkeypatch):
  device, state = make_device(monkeypatch, ScreenManagement=False, ScreenBrightness=10, ScreenBrightnessOnroad=20,
                              StandbyMode=True)

  assert device._calculate_brightness() == ui_state_module.BACKLIGHT_OFFROAD

  state.started = True
  device._interaction_time = 0
  assert device._calculate_brightness() == ui_state_module.BACKLIGHT_OFFROAD
  assert device.interactive_timeout == 30


def test_screen_settings_refresh_after_external_param_change(monkeypatch):
  now = 100.0
  monkeypatch.setattr(ui_state_module.time, "monotonic", lambda: now)
  device, state = make_device(monkeypatch)
  state.started = True
  state.ignition = True
  device._ignition = True
  device._interaction_time = now + 10

  state.ui_params.values["ScreenBrightnessOnroad"] = 72
  state.ui_params.values["ScreenTimeoutOnroad"] = 25
  now += device.SCREEN_SETTINGS_REFRESH_INTERVAL
  device._refresh_screen_settings()

  assert device._calculate_brightness() == 72
  assert device.interactive_timeout == 25
  assert device._interaction_time == now + 25


def test_standby_blanks_after_timeout_and_touch_wakes(monkeypatch):
  now = 100.0
  monkeypatch.setattr(ui_state_module.time, "monotonic", lambda: now)
  device, state = make_device(monkeypatch, StandbyMode=True)
  state.started = True
  state.ignition = True
  device._ignition = True
  device._interaction_time = now - 1

  assert device._calculate_brightness() == 0

  monkeypatch.setattr(ui_state_module.gui_app, "_mouse_events", [SimpleNamespace(left_down=True)])
  device._update_wakefulness()

  assert device._interaction_time == now + 10
  assert device._calculate_brightness() == 45


def test_standby_powers_down_onroad_and_touch_wakes(monkeypatch):
  now = 100.0
  monkeypatch.setattr(ui_state_module.time, "monotonic", lambda: now)
  monkeypatch.setattr(ui_state_module, "PC", False)
  display_power = []
  monkeypatch.setattr(ui_state_module.HARDWARE, "set_display_power", display_power.append)
  device, state = make_device(monkeypatch, StandbyMode=True)
  state.started = True
  state.ignition = True
  device._ignition = True
  device._interaction_time = now - 1

  device._update_wakefulness()

  assert display_power == [False]
  assert not device.awake
  assert device._calculate_brightness() == 0

  monkeypatch.setattr(ui_state_module.gui_app, "_mouse_events", [SimpleNamespace(left_down=True)])
  device._update_wakefulness()

  assert display_power == [False, True]
  assert device.awake
  assert device._calculate_brightness() == 45


def test_hide_ui_blanks_after_timeout_and_touch_wakes(monkeypatch):
  now = 100.0
  monkeypatch.setattr(ui_state_module.time, "monotonic", lambda: now)
  device, state = make_device(monkeypatch, ScreenBrightnessOnroad=0)
  state.started = True
  state.ignition = True
  device._ignition = True
  device._interaction_time = now - 1

  assert device._calculate_brightness() == 0

  monkeypatch.setattr(ui_state_module.gui_app, "_mouse_events", [SimpleNamespace(left_down=True)])
  device._update_wakefulness()

  assert device._interaction_time == now + 10
  assert device._calculate_brightness() == 5


def test_standby_wakes_for_visible_alert(monkeypatch):
  now = 100.0
  monkeypatch.setattr(ui_state_module.time, "monotonic", lambda: now)
  device, state = make_device(monkeypatch, StandbyMode=True)
  state.started = True
  state.ignition = True
  device._ignition = True
  device._interaction_time = now - 1
  device._active_standby_alerts = lambda: {"StandbyWakeInfoAlert"}

  device._update_wakefulness()

  assert device._interaction_time == now + 10
  assert device._calculate_brightness() == 45


@pytest.fixture
def aa_sleep_device(monkeypatch):
  clock, streaming, setting = [100.0], [True], [True]
  power, rendering = [], []
  monkeypatch.setattr(ui_state_module.time, "monotonic", lambda: clock[0])
  monkeypatch.setattr(ui_state_module, "PC", False)
  monkeypatch.setattr(ui_state_module.HARDWARE, "get_device_type", lambda: "mici")
  monkeypatch.setattr(ui_state_module.HARDWARE, "set_display_power", power.append)
  monkeypatch.setattr(ui_state_module.gui_app, "set_should_render", rendering.append)
  monkeypatch.setattr(ui_state_module.gui_app, "ui_stream_wants_frames", lambda: False)
  monkeypatch.setattr(ui_state_module.gui_app, "_aa_enabled", True)
  device, state = make_device(monkeypatch)
  state.started = state.ignition = device._ignition = device._screen_off_started = True
  device._aa_car_frames = SimpleNamespace(recently_sent=lambda: streaming[0])
  device._aa_screen_settings = SimpleNamespace(poll=lambda: {"sleep_device_screen": setting[0]})
  device._update_wakefulness()
  assert device.awake and device._interaction_time == 110.0
  clock[0] = 111.0
  return SimpleNamespace(device=device, state=state, clock=clock, streaming=streaming, setting=setting, power=power, rendering=rendering)


def test_aa_screen_sleeps_after_timeout_and_tap_wakes(aa_sleep_device, monkeypatch):
  s = aa_sleep_device
  s.device._update_wakefulness()
  assert not s.device.awake and s.power == [False] and s.rendering == [False]
  assert s.device._calculate_brightness() == 0
  monkeypatch.setattr(ui_state_module.gui_app, "_mouse_events", [SimpleNamespace(left_down=True)])
  s.device._update_wakefulness()
  assert s.device.awake and s.power == [False, True] and s.rendering == [False, True]
  assert s.device._interaction_time == 121.0
  monkeypatch.setattr(ui_state_module.gui_app, "_mouse_events", [])
  s.clock[0] = 122.0
  s.device._update_wakefulness()
  assert not s.device.awake


@pytest.mark.parametrize("reason", ["stale_or_unfocused", "toggle_off", "aa_disabled", "offroad", "car_renderer", "not_c4"])
def test_aa_sleep_fails_awake(aa_sleep_device, monkeypatch, reason):
  s = aa_sleep_device
  s.device._update_wakefulness()
  assert not s.device.awake
  if reason == "stale_or_unfocused":
    s.streaming[0] = False
  elif reason == "toggle_off":
    s.setting[0] = False
  elif reason == "aa_disabled":
    monkeypatch.setattr(ui_state_module.gui_app, "_aa_enabled", False)
  elif reason == "offroad":
    s.state.started = s.state.ignition = False
  elif reason == "car_renderer":
    s.state.android_auto_car_view = True
  elif reason == "not_c4":
    s.device._aa_car_frames = None
  s.device._update_wakefulness()
  assert s.device.awake and s.device._render_awake
  assert s.power == [False, True] and s.rendering == [False, True]


def test_critical_alert_keeps_aa_sleeping_screen_awake(aa_sleep_device):
  s = aa_sleep_device
  s.device._update_wakefulness()
  s.device._active_standby_alerts = lambda: {"StandbyWakeCriticalAlert"}
  for now in (112.0, 125.0, 140.0):
    s.clock[0] = now
    s.device._update_wakefulness()
    assert s.device.awake and s.device._interaction_time == now + 10
  s.device._active_standby_alerts = lambda: set()
  s.clock[0] = 151.0
  s.device._update_wakefulness()
  assert not s.device.awake


def test_native_live_ui_keeps_rendering_with_aa_screen_asleep(aa_sleep_device, monkeypatch):
  s = aa_sleep_device
  monkeypatch.setattr(ui_state_module.gui_app, "ui_stream_wants_frames", lambda: True)
  s.device._update_wakefulness()
  assert not s.device.awake and s.device._render_awake
  assert s.power == [False] and s.rendering == []


def test_sleeping_render_loop_skips_gpu_work_but_keeps_yielding(monkeypatch):
  from openpilot.system.ui.lib import application
  app = application.gui_app
  progress = []
  monkeypatch.setattr(application, "PC", False)
  monkeypatch.setattr(application.rl, "window_should_close", lambda: False)
  monkeypatch.setattr(application.rl, "begin_drawing", lambda: pytest.fail("sleeping UI drew a frame"))
  monkeypatch.setattr(application.rl, "begin_texture_mode", lambda *args: pytest.fail("sleeping UI rendered a texture"))
  monkeypatch.setattr(application.time, "sleep", lambda _: None)
  monkeypatch.setattr(app, "_should_render", False)
  monkeypatch.setattr(app, "_profile_render_frames", 0)
  monkeypatch.setattr(app, "_window_close_requested", False)
  monkeypatch.setattr(app, "_ui_stream_pending", False)
  monkeypatch.setattr(app, "_ui_stream", None)
  monkeypatch.setattr(app, "_apply_render_mode", lambda: None)
  monkeypatch.setattr(app, "_mark_progress", progress.append)
  monkeypatch.setattr(app, "_service_ui_stream", lambda capture: progress.append(("stream", capture)))
  loop = app.render()
  try:
    assert [next(loop) for _ in range(60)] == [False] * 60
    assert progress.count("gui_app.skip_render") == 60
    assert progress.count(("stream", False)) == 60
  finally:
    loop.close()
