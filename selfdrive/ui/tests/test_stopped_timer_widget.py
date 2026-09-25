import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _load_stopped_timer(monkeypatch):
  rl = SimpleNamespace(
    Color=lambda r, g, b, a=255: SimpleNamespace(r=r, g=g, b=b, a=a),
    Rectangle=lambda x=0, y=0, width=0, height=0: SimpleNamespace(x=x, y=y, width=width, height=height),
    Vector2=lambda x, y: SimpleNamespace(x=x, y=y),
    WHITE=SimpleNamespace(r=255, g=255, b=255, a=255),
    draw_text_ex=lambda *_args: None,
  )
  monkeypatch.setitem(sys.modules, "pyray", rl)

  def module(name, **attributes):
    result = ModuleType(name)
    for key, value in attributes.items():
      setattr(result, key, value)
    monkeypatch.setitem(sys.modules, name, result)

  class Widget:
    def __init__(self):
      pass

    def set_enabled(self, _enabled):
      pass

  module("openpilot.system.ui.widgets", Widget=Widget)
  module(
    "openpilot.system.ui.lib.application",
    FontWeight=SimpleNamespace(BOLD=1, NORMAL=2),
    gui_app=SimpleNamespace(font=lambda *_args: None),
  )
  module(
    "openpilot.system.ui.lib.text_measure",
    measure_text_cached=lambda *_args: SimpleNamespace(x=100, y=20),
  )
  module(
    "openpilot.selfdrive.ui.lib.starpilot_status",
    ENGAGED_COLOR=rl.Color(22, 127, 64),
    EXPERIMENTAL_COLOR=rl.Color(218, 111, 37),
    TRAFFIC_COLOR=rl.Color(201, 34, 49),
  )
  module("openpilot.selfdrive.ui.ui_state", ui_state=SimpleNamespace())

  module_path = Path(__file__).parents[1] / "onroad/starpilot/widgets/stopped_timer.py"
  spec = importlib.util.spec_from_file_location("stopped_timer_under_test", module_path)
  stopped_timer = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(stopped_timer)
  return stopped_timer


def test_stopped_timer_visibility_waits_for_onroad_grace_period(monkeypatch):
  stopped_timer = _load_stopped_timer(monkeypatch)
  params = SimpleNamespace(get_bool=lambda key: key in {"QOLVisuals", "StoppedTimer"})
  car_state = SimpleNamespace(standstill=True)

  class SubMaster:
    valid = {"carState": True}
    recv_frame = {"carState": 1}

    def __getitem__(self, _key):
      return car_state

  ui_state = SimpleNamespace(
    started=True,
    started_frame=1,
    started_time=100.0,
    ui_params=params,
    sm=SubMaster(),
  )
  monkeypatch.setattr(stopped_timer, "ui_state", ui_state)

  now = iter((100.0, 159.9, 160.0))
  monkeypatch.setattr(stopped_timer.time, "monotonic", lambda: next(now))

  widget = stopped_timer.StoppedTimerWidget()

  assert not widget.is_visible
  assert not widget.is_visible
  assert widget.is_visible


def test_stopped_timer_uses_text_contract(monkeypatch):
  stopped_timer = _load_stopped_timer(monkeypatch)

  assert stopped_timer.StoppedTimerWidget._format_duration_text(61) == ("1 minute", "1 second")
  assert stopped_timer.StoppedTimerWidget._format_duration_text(121) == ("2 minutes", "1 second")


def test_stopped_timer_draws_positions_and_opaque_seconds(monkeypatch):
  stopped_timer = _load_stopped_timer(monkeypatch)
  widget = stopped_timer.StoppedTimerWidget()
  widget._duration = 61
  draws = []
  monkeypatch.setattr(stopped_timer.rl, "draw_text_ex", lambda *args: draws.append(args))

  widget._render(stopped_timer.rl.Rectangle(0, 0, 2160, 1080))

  assert draws[0][2].x == 1030
  assert draws[0][2].y == 190
  assert draws[0][3] == 176
  assert draws[1][2].x == 1030
  assert draws[1][2].y == 270
  assert draws[1][3] == 66
  assert draws[1][5].a == 255


def _render_sizes(monkeypatch, stopped_timer, width, beside_map, duration=61):
  monkeypatch.setattr(stopped_timer, "ui_state", SimpleNamespace(nav_map_beside_road=beside_map))
  # Width grows with the font size, like real text.
  monkeypatch.setattr(stopped_timer, "measure_text_cached",
                      lambda _font, text, size: SimpleNamespace(x=len(text) * size * 0.55, y=size * 0.8))
  widget = stopped_timer.StoppedTimerWidget()
  widget._duration = duration
  draws = []
  monkeypatch.setattr(stopped_timer.rl, "draw_text_ex", lambda *args: draws.append(args))
  widget._render(stopped_timer.rl.Rectangle(0, 0, width, 1080))
  return draws


def test_stopped_timer_halves_beside_the_car_map(monkeypatch):
  stopped_timer = _load_stopped_timer(monkeypatch)
  full = _render_sizes(monkeypatch, stopped_timer, 2160, beside_map=False)
  beside = _render_sizes(monkeypatch, stopped_timer, 2160, beside_map=True)
  assert (full[0][3], full[1][3]) == (176, 66)
  assert (beside[0][3], beside[1][3]) == (88, 33)
  # Smaller text stays centred where the current speed is drawn (y = 180).
  assert abs(beside[0][2].y + 88 * 0.8 / 2 - 180) < 1


def test_stopped_timer_fits_a_narrow_driving_pane(monkeypatch):
  stopped_timer = _load_stopped_timer(monkeypatch)
  draws = _render_sizes(monkeypatch, stopped_timer, 700, beside_map=True, duration=12 * 60 + 5)
  reserve = stopped_timer.StoppedTimerWidget.LEFT_CONTROLS_RESERVE
  minute_width = len("12 minutes") * draws[0][3] * 0.55
  assert minute_width <= 0.8 * (700 - reserve) + 1
  assert draws[0][2].x >= reserve, "clear of the MAX / LIMIT column"
  assert draws[0][2].x + minute_width <= 700
