import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _load_pause_indicators(monkeypatch):
  draws = SimpleNamespace(backgrounds=[], outlines=[], rings=[], triangles=[], rectangles=[], text=[])
  rl = SimpleNamespace(
    Color=lambda r, g, b, a=255: SimpleNamespace(r=r, g=g, b=b, a=a),
    Rectangle=lambda x=0, y=0, width=0, height=0: SimpleNamespace(x=x, y=y, width=width, height=height),
    Vector2=lambda x, y: SimpleNamespace(x=x, y=y),
    WHITE=SimpleNamespace(r=255, g=255, b=255, a=255),
    draw_rectangle_rounded=lambda *args: draws.backgrounds.append(args),
    draw_rectangle_rounded_lines_ex=lambda *args: draws.outlines.append(args),
    draw_ring=lambda *args: draws.rings.append(args),
    draw_triangle=lambda *args: draws.triangles.append(args),
    draw_rectangle=lambda *args: draws.rectangles.append(args),
  )
  monkeypatch.setitem(sys.modules, "pyray", rl)

  status = ModuleType("openpilot.selfdrive.ui.lib.starpilot_status")
  status.TRAFFIC_COLOR = rl.Color(201, 34, 49)
  monkeypatch.setitem(sys.modules, status.__name__, status)

  text_measure = ModuleType("openpilot.system.ui.lib.text_measure")
  text_measure.measure_text_cached = lambda _font, text, size: SimpleNamespace(x=len(text) * size * 0.55, y=size * 0.8)
  text_measure.draw_text_with_shadow = lambda *args, **kwargs: draws.text.append((args, kwargs))
  monkeypatch.setitem(sys.modules, text_measure.__name__, text_measure)

  module_path = Path(__file__).parents[1] / "onroad/starpilot/pause_indicators.py"
  spec = importlib.util.spec_from_file_location("pause_indicators_under_test", module_path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module, draws


def test_android_auto_lateral_pause_is_centered_above_torque_bar(monkeypatch):
  indicators, _ = _load_pause_indicators(monkeypatch)
  display_width = 1920

  for camera in (indicators.rl.Rectangle(0, 0, 1920, 1080), indicators.rl.Rectangle(806, 0, 1114, 1080)):
    badge = indicators.android_auto_lateral_pause_rect(camera, display_width)
    scale = camera.height / 240.0 * (camera.width / display_width)
    torque_bar_top = camera.y + camera.height - indicators.ANDROID_AUTO_TORQUE_BAR_MAX_RISE * scale

    assert badge.width > 120 and badge.height > 72
    assert badge.x + badge.width / 2 == camera.x + camera.width / 2
    assert badge.y + badge.height == torque_bar_top - indicators.ANDROID_AUTO_TORQUE_BAR_GAP


def test_android_auto_lateral_pause_adds_label_and_larger_icon(monkeypatch):
  indicators, draws = _load_pause_indicators(monkeypatch)
  badge = indicators.rl.Rectangle(100, 200, indicators.ANDROID_AUTO_LATERAL_PAUSE_WIDTH,
                                  indicators.ANDROID_AUTO_LATERAL_PAUSE_HEIGHT)

  indicators.render_android_auto_lateral_paused(badge, object())

  assert len(draws.backgrounds) == 1
  assert len(draws.outlines) == 1
  assert draws.text[0][0][1] == "Lat"
  assert draws.text[0][0][2].x < draws.rings[0][0].x
  assert draws.rings[0][2] > 24  # larger than the original 3X icon
  assert len(draws.rectangles) == 2


def test_original_lateral_pause_graphic_keeps_its_dimensions(monkeypatch):
  indicators, draws = _load_pause_indicators(monkeypatch)

  indicators.render_lateral_paused(indicators.rl.Rectangle(0, 0, 120, 72))

  assert draws.rings[0][1:3] == (20, 24)
  assert draws.rectangles[0][2:4] == (5, 32)
  assert draws.rectangles[1][2:4] == (5, 32)
  assert draws.text == []
