import pytest

from openpilot.starpilot.system.android_auto import car_ui


class FakeParams:
  def __init__(self, enabled):
    self.enabled = enabled
    self.reads = 0

  def get_bool(self, key):
    assert key == "NavigationUI"
    self.reads += 1
    return self.enabled


class FakeMap:
  def __init__(self):
    self.events = []

  def show_event(self):
    self.events.append("show")

  def hide_event(self):
    self.events.append("hide")


@pytest.fixture
def params(monkeypatch):
  from openpilot.selfdrive.ui.ui_state import ui_state
  fake = FakeParams(True)
  monkeypatch.setattr(ui_state, "params", fake)
  return fake


def test_split_puts_the_map_on_the_right_onroad(params):
  split = car_ui.NavSplit(1920, 1080)
  drive, nav = split.rects(started=True, now=0.0)
  assert drive.x == 0 and drive.width + nav.width == 1920
  assert nav.x == drive.width and 700 <= nav.width <= 1100
  assert split.rects(started=False, now=0.1) is None


def test_split_needs_a_wide_screen_and_the_toggle(params):
  assert car_ui.NavSplit(1600, 1080).rects(started=True, now=0.0) is None
  params.enabled = False
  assert car_ui.NavSplit(1920, 1080).rects(started=True, now=0.0) is None


def test_toggle_is_read_at_most_every_refresh_interval(params):
  split = car_ui.NavSplit(1920, 1080)
  for step in range(20):
    split.rects(started=True, now=step * 0.1)
  assert params.reads == 1
  split.rects(started=True, now=car_ui.NAV_PARAM_REFRESH + 0.01)
  assert params.reads == 2


def test_map_gets_show_and_hide_events_on_transitions(params):
  split = car_ui.NavSplit(1920, 1080)
  split._map = FakeMap()
  split.rects(started=True, now=0.0)
  split.rects(started=True, now=0.1)
  split.rects(started=False, now=0.2)
  assert split._map.events == ["show", "hide"]
