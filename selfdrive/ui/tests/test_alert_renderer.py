from cereal import log

import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.onroad.alert_renderer import ALERT_HEIGHTS, AlertRenderer, AlertSize
from openpilot.selfdrive.ui.ui_state import ui_state

SCREEN = rl.Rectangle(0, 0, 1920, 1080)
CHAR_WIDTH = 20  # the fake labels measure every character this wide


class FakeSM(dict):
  def __init__(self, alert_type: str, text1: str = "Changing Lanes"):
    super().__init__(selfdriveState=log.SelfdriveState.new_message(
      alertSize="small", alertText1=text1, alertStatus="normal", alertType=alert_type,
    ))
    self.updated = {"selfdriveState": True}
    self.recv_frame = {"selfdriveState": 10}


class FakeLabel:
  def __init__(self):
    self.text = ""
    self.text_width = 0.0

  def set_text(self, text):
    self.text = text

  def set_font_size(self, _size):
    pass

  def get_content_height(self, _max_width):
    self.text_width = len(self.text) * CHAR_WIDTH
    return 60.0


def _renderer(monkeypatch, covers=None):
  monkeypatch.setattr(ui_state, "starpilot_toggles", {})
  monkeypatch.setattr(ui_state, "started_frame", 0)
  renderer = AlertRenderer.__new__(AlertRenderer)
  renderer._prev_alert = None
  renderer._current_alert = None
  renderer._alpha_filter = FirstOrderFilter(0, 0.05, 0.05)
  renderer._alert_y_filter = FirstOrderFilter(0, 0.05, 0.05)
  renderer._draw_background = lambda _alert: None
  renderer._draw_text = lambda _alert: None
  renderer._alert_text1_label = FakeLabel()
  renderer._alert_text2_label = FakeLabel()
  renderer.hidden_alert_names = frozenset({"laneChange"})
  renderer.covers = covers
  return renderer


def test_text_bounds_are_the_centred_text_not_the_whole_band(monkeypatch):
  renderer = _renderer(monkeypatch)
  alert = renderer.get_alert(FakeSM("laneChange/warning", text1="x" * 10))

  bounds = renderer._text_bounds(alert, SCREEN)

  assert (bounds.x, bounds.width) == (860, 200)
  band_top = SCREEN.height - ALERT_HEIGHTS[AlertSize.small]
  assert bounds.y == band_top + (ALERT_HEIGHTS[AlertSize.small] - 60) / 2
  assert bounds.height == 60


def test_hidden_banner_drops_only_when_its_text_is_covered(monkeypatch):
  covered = []
  renderer = _renderer(monkeypatch, covers=lambda area: covered.append(area) or area.width > 400)
  monkeypatch.setattr(ui_state, "sm", FakeSM("laneChange/warning", text1="x" * 10))
  renderer._render(SCREEN)
  assert renderer._current_alert is not None  # 200px of text fits between the bubbles

  monkeypatch.setattr(ui_state, "sm", FakeSM("laneChange/warning", text1="x" * 40))
  renderer._render(SCREEN)
  assert renderer._current_alert is None and renderer._prev_alert is None


def test_only_named_alerts_can_be_hidden(monkeypatch):
  renderer = _renderer(monkeypatch, covers=lambda _area: True)
  monkeypatch.setattr(ui_state, "sm", FakeSM("steerSaturated/warning"))
  assert not renderer._is_covered(renderer.get_alert(ui_state.sm), SCREEN)

  renderer.covers = None
  monkeypatch.setattr(ui_state, "sm", FakeSM("laneChange/warning"))
  assert not renderer._is_covered(renderer.get_alert(ui_state.sm), SCREEN)
