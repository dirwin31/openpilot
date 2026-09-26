import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest


def _load_augmented_road_view(monkeypatch):
  def stub_module(name, **attributes):
    module = ModuleType(name)
    for key, value in attributes.items():
      setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)

  class CameraView:
    def _render(self, _rect):
      self.events.append("camera")

  class UIStatus:
    DISENGAGED = 0
    OVERRIDE = 1
    ENGAGED = 2

  stub_module(
    "openpilot.selfdrive.ui.ui_state",
    ui_state=SimpleNamespace(started=True, sm=SimpleNamespace()),
    UIStatus=UIStatus,
  )
  stub_module("openpilot.selfdrive.ui.lib.starpilot_visuals", get_border_width=lambda *_args: 0)
  stub_module("openpilot.selfdrive.ui.lib.starpilot_status", get_screen_edge_color=lambda *_args: None)
  stub_module("openpilot.selfdrive.ui.onroad.alert_renderer", AlertRenderer=object)
  stub_module("openpilot.selfdrive.ui.onroad.driver_state", DriverStateRenderer=object)
  stub_module("openpilot.selfdrive.ui.onroad.hud_renderer", HudRenderer=object)
  stub_module("openpilot.selfdrive.ui.onroad.model_renderer", ModelRenderer=object)
  stub_module("openpilot.selfdrive.ui.onroad.cameraview", CameraView=CameraView)
  stub_module("openpilot.system.ui.lib.application", gui_app=SimpleNamespace(target_fps=20))

  module_name = "openpilot.selfdrive.ui.onroad.augmented_road_view"
  monkeypatch.delitem(sys.modules, module_name, raising=False)
  return importlib.import_module(module_name)


def _load_starpilot_onroad_view(monkeypatch):
  def stub_module(name, **attributes):
    module = ModuleType(name)
    for key, value in attributes.items():
      setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)

  class AugmentedRoadView:
    pass

  dummy_widget = type("DummyWidget", (), {})
  color = SimpleNamespace(r=0, g=0, b=0, a=255)

  stub_module("openpilot.selfdrive.ui.onroad.augmented_road_view", AugmentedRoadView=AugmentedRoadView)
  stub_module(
    "openpilot.selfdrive.ui.onroad.starpilot.starpilot_border",
    render_behind=lambda *_args: None,
    render_overlay=lambda *_args: None,
    render_background_effects=lambda *_args: None,
  )
  stub_module(
    "openpilot.selfdrive.ui.onroad.starpilot.path",
    render_adjacent_lanes=lambda *_args: None,
    render_path_edges=lambda *_args: None,
  )
  stub_module("openpilot.selfdrive.ui.ui_state", ui_state=SimpleNamespace())
  stub_module("openpilot.selfdrive.ui.onroad.starpilot.torque_bar", TorqueBar=dummy_widget)
  stub_module("openpilot.selfdrive.ui.onroad.starpilot.widget_layout_manager", WidgetLayoutManager=dummy_widget)
  stub_module(
    "openpilot.selfdrive.ui.onroad.starpilot.widgets",
    SetSpeedWidget=dummy_widget,
    SpeedLimitWidget=dummy_widget,
    PedalIconsWidget=dummy_widget,
    AetherGaugeWidget=dummy_widget,
    PersonalityButtonWidget=dummy_widget,
    DriverMonitorWidget=dummy_widget,
    SteeringWheelWidget=dummy_widget,
    StoppedTimerWidget=dummy_widget,
    ModelSourceWidget=dummy_widget,
  )
  stub_module(
    "openpilot.selfdrive.ui.onroad.starpilot.stopping_point",
    render_stopping_point=lambda *_args: None,
  )
  stub_module(
    "openpilot.selfdrive.ui.onroad.starpilot.pause_indicators",
    render_lateral_paused=lambda *_args: None,
    render_longitudinal_paused=lambda *_args: None,
  )
  stub_module("openpilot.selfdrive.ui.onroad.starpilot.weather_icon", render_weather_icon=lambda *_args: None)
  stub_module(
    "openpilot.selfdrive.ui.lib.starpilot_status",
    get_screen_edge_color=lambda *_args: color,
    ENGAGED_COLOR=color,
    EXPERIMENTAL_COLOR=color,
    TRAFFIC_COLOR=color,
  )
  stub_module(
    "openpilot.system.ui.lib.application",
    MousePos=object,
    gui_app=SimpleNamespace(font=lambda *_args: None),
    FontWeight=SimpleNamespace(BOLD=0, MEDIUM=1),
  )
  stub_module(
    "openpilot.system.ui.lib.text_measure",
    draw_text_with_shadow=lambda *_args: None,
    measure_text_cached=lambda *_args: SimpleNamespace(x=0, y=0),
  )

  module_name = "openpilot.selfdrive.ui.onroad.starpilot.starpilot_onroad_view"
  monkeypatch.delitem(sys.modules, module_name, raising=False)
  return importlib.import_module(module_name)


def test_extra_road_overlays_render_between_model_and_hud_and_alerts_last(monkeypatch):
  augmented_road_view = _load_augmented_road_view(monkeypatch)
  events = []

  class LayeredRoadView(augmented_road_view.AugmentedRoadView):
    def _render_extra_road_overlays(self, _rect):
      events.append("road_overlays")

  class Renderer:
    def __init__(self, name):
      self.name = name

    def render(self, _rect):
      events.append(self.name)

  view = object.__new__(LayeredRoadView)
  view.events = events
  view.stream_type = augmented_road_view.ROAD_CAM
  view._camera_view = lambda: augmented_road_view.CAMERA_VIEW_STANDARD
  view._switch_stream_if_needed = lambda *_args: None
  view._is_in_reverse = lambda: False
  view._update_calibration = lambda: None
  view._get_border_width = lambda: 0
  view._draw_border = lambda _rect: events.append("border")
  view.model_renderer = Renderer("model")
  view._hud_renderer = Renderer("hud")
  view.driver_state_renderer = Renderer("driver_state")
  view.alert_renderer = Renderer("alert")
  view._draw_driver_state = True
  view._draw_alerts = True
  view._pm = SimpleNamespace(send=lambda *_args: events.append("publish"))

  monkeypatch.setattr(augmented_road_view.rl, "begin_scissor_mode", lambda *_args: events.append("scissor_begin"))
  monkeypatch.setattr(augmented_road_view.rl, "end_scissor_mode", lambda: events.append("scissor_end"))
  monkeypatch.setattr(
    augmented_road_view.messaging,
    "new_message",
    lambda *_args: SimpleNamespace(uiDebug=SimpleNamespace(drawTimeMillis=0.0)),
  )

  view._render(augmented_road_view.rl.Rectangle(0, 0, 100, 50))

  assert events == [
    "scissor_begin",
    "camera",
    "model",
    "road_overlays",
    "hud",
    "driver_state",
    "alert",
    "scissor_end",
    "border",
    "publish",
  ]


def test_android_auto_side_camera_can_hide_lane_change_banners_and_lifts_other_alerts(monkeypatch):
  starpilot_onroad_view = _load_starpilot_onroad_view(monkeypatch)
  view = object.__new__(starpilot_onroad_view.StarPilotOnroadView)
  view.alert_renderer = SimpleNamespace(hidden_alert_names=frozenset(), covers=None)
  view._pip_sidecam = SimpleNamespace(showing=True, covers=lambda _area: True)
  ui_state = starpilot_onroad_view.ui_state

  ui_state.android_auto_car_view = True
  assert view._layout_alerts_around_side_camera()
  assert not view._draw_alerts
  assert {"preLaneChangeLeft", "preLaneChangeRight", "laneChange", "laneChangeBlocked",
          "laneChangeBlockedLoud"} == view.alert_renderer.hidden_alert_names
  assert view.alert_renderer.covers is view._pip_sidecam.covers

  view._pip_sidecam.showing = False
  assert not view._layout_alerts_around_side_camera()
  assert view._draw_alerts
  assert view.alert_renderer.hidden_alert_names == frozenset()
  assert view.alert_renderer.covers is None

  # The comma's own screen keeps its alerts as they are, bubbles or not.
  ui_state.android_auto_car_view = False
  view._pip_sidecam.showing = True
  assert not view._layout_alerts_around_side_camera()
  assert view._draw_alerts
  assert view.alert_renderer.hidden_alert_names == frozenset()
  assert view.alert_renderer.covers is None


def test_full_alert_detection_uses_the_alert_size(monkeypatch):
  starpilot_onroad_view = _load_starpilot_onroad_view(monkeypatch)
  view = object.__new__(starpilot_onroad_view.StarPilotOnroadView)

  view.alert_renderer = SimpleNamespace(
    will_render=lambda: (SimpleNamespace(size=starpilot_onroad_view.AlertSize.full), False),
  )
  assert view._full_alert_showing()

  view.alert_renderer = SimpleNamespace(
    will_render=lambda: (SimpleNamespace(size=starpilot_onroad_view.AlertSize.mid), False),
  )
  assert not view._full_alert_showing()

  view.alert_renderer = SimpleNamespace(will_render=lambda: (None, True))
  assert not view._full_alert_showing()


def test_starpilot_road_overlays_use_the_parent_scissor(monkeypatch):
  starpilot_onroad_view = _load_starpilot_onroad_view(monkeypatch)
  events = []
  view = object.__new__(starpilot_onroad_view.StarPilotOnroadView)
  view.model_renderer = SimpleNamespace(
    _path=SimpleNamespace(projected_points=SimpleNamespace(size=1)),
    _track_edge_vertices=SimpleNamespace(size=4),
  )
  view._font_bold = object()
  view._get_border_width = lambda: 0

  monkeypatch.setattr(starpilot_onroad_view, "render_path_edges", lambda *_args: events.append("path_edges"))
  monkeypatch.setattr(starpilot_onroad_view, "render_adjacent_lanes", lambda *_args: events.append("adjacent_lanes"))
  monkeypatch.setattr(starpilot_onroad_view, "render_stopping_point", lambda *_args: events.append("stopping_point"))

  def fail_scissor(*_args):
    raise AssertionError("road overlay changed the parent scissor")

  monkeypatch.setattr(starpilot_onroad_view.rl, "begin_scissor_mode", fail_scissor)
  monkeypatch.setattr(starpilot_onroad_view.rl, "end_scissor_mode", fail_scissor)

  view._render_extra_road_overlays(object())

  assert events == ["path_edges", "adjacent_lanes", "stopping_point"]


@pytest.mark.parametrize("android_auto", [False, True], ids=["device", "android_auto"])
def test_radial_favorites_lifecycle_is_bypassed_only_for_android_auto(monkeypatch, mocker, android_auto):
  Mock = mocker.Mock
  module = _load_starpilot_onroad_view(monkeypatch)
  rect = module.rl.Rectangle(0, 0, 1000, 600)
  state = module.ui_state
  state.android_auto_car_view = android_auto
  state.started = True
  state.ui_params, state.params_memory, state.sm = object(), object(), object()
  module.gui_app.mouse_events = [object()]
  original_events = list(module.gui_app.mouse_events)

  def base_init(view, *_args):
    view._content_rect = rect
    view._hud_renderer = SimpleNamespace(_exp_button=object())
    view.driver_state_renderer = SimpleNamespace(is_rhd=False)
    view.alert_renderer = Mock()
    view._draw_hud_controls = True

  monkeypatch.setattr(module.AugmentedRoadView, "__init__", base_init)
  monkeypatch.setattr(module.AugmentedRoadView, "_child", lambda self, widget: widget, raising=False)
  monkeypatch.setattr(module.AugmentedRoadView, "is_in_reverse", lambda self: False, raising=False)
  monkeypatch.setattr(module.AugmentedRoadView, "_render",
                      lambda self, area: self._draw_border(area) if state.started else None, raising=False)
  parent_click = Mock()
  monkeypatch.setattr(module.AugmentedRoadView, "_handle_mouse_press", parent_click, raising=False)
  monkeypatch.setattr(module.PedalIconsWidget, "__init__", lambda self, *_args: None)
  monkeypatch.setattr(module, "PipSideCamera", Mock())
  monkeypatch.setattr(module, "WidgetLayoutManager", lambda *_args: Mock(zones={}))
  menu = Mock()
  menu.process_mouse_events.return_value = False
  menu.blocks_pointer.return_value = True
  factory = Mock(return_value=menu)
  monkeypatch.setattr(module, "FavoriteRadialMenu", factory)
  monkeypatch.setattr(module, "get_pulse_glide_border_color", lambda *_args: module.rl.BLACK)
  for name in ("begin_scissor_mode", "end_scissor_mode", "draw_rectangle_lines_ex", "draw_rectangle_rounded_lines_ex"):
    monkeypatch.setattr(module.rl, name, lambda *_args: None)

  view = module.StarPilotOnroadView()
  view._stopped_timer_widget.replaces_current_speed = False
  view._get_border_width = lambda: 0
  view._layout_alerts_around_side_camera = lambda: False
  view._full_alert_showing = lambda: False
  for name in ("_render_slc", "_render_overlays", "_render_road_name"):
    monkeypatch.setattr(view, name, lambda: None)

  view._render(rect)
  view._handle_mouse_press(object())
  view._draw_hud_controls = False
  view._render(rect)
  state.started = False
  view._render(rect)
  assert module.gui_app.mouse_events == original_events
  if android_auto:
    factory.assert_not_called()
    assert view._favorite_radial_menu is None
    parent_click.assert_called_once()
  else:
    factory.assert_called_once()
    assert menu.process_mouse_events.call_count == 2
    menu.render_corner_hint.assert_called_once_with(rect)
    menu.render.assert_called_once_with(rect)
    assert menu.collapse.call_count == 2
    parent_click.assert_not_called()
