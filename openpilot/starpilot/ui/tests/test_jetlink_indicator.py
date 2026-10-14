"""The home screen shows whether Jetlink's remote device is connected or driving."""
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from openpilot.starpilot.ui import home
from openpilot.starpilot.ui.jetlink_indicator import COLORS, JetlinkIndicator, read_state


def adapter(status, *, active=False, link=True):
  linked = NS(status=lambda: status) if link else None
  return NS(get_jetlink=lambda: linked, runtime_status=lambda: {"state": "running" if active else "joining", "active": active})


def status(**fields):
  return NS(**{"enabled": True, "present": False, "reason": None, **fields})


@pytest.mark.parametrize("mode,module,expected", [
  (0, None, "off"),
  (None, None, "off"),
  (2, adapter(status(enabled=False)), "off"),               # a Chestnut is fitted
  (2, adapter(status()), "waiting"),
  (2, adapter(status(present=True)), "connected"),
  (1, adapter(status(present=True), active=True), "active"),
  (2, adapter(status(reason="check cable")), "unavailable"),
  (2, adapter(status(), link=False), "unavailable"),
])
def test_state_follows_jetlink(mode, module, expected):
  params = NS(get=lambda key: mode)
  with patch.dict(sys.modules, {"openpilot.starpilot.models.jetlink_adapter": module}):
    assert read_state(params) == expected


def test_status_is_read_at_most_once_a_second():
  reads = []
  indicator = JetlinkIndicator(read=lambda params: reads.append(1) or "connected")
  assert indicator.state(None, 0) == "connected"
  assert indicator.state(None, 999_999_999) == "connected"
  assert len(reads) == 1
  indicator.state(None, 1_000_000_000)
  assert len(reads) == 2


def test_a_failed_read_shows_unavailable_without_raising():
  def broken(params):
    raise OSError("owner record unreadable")
  assert JetlinkIndicator(read=broken).state(None, 0) == "unavailable"


@pytest.mark.parametrize("state", ["connected", "active", "unavailable", "waiting"])
def test_icon_draws_in_its_state_color(monkeypatch, state):
  drawn = []
  monkeypatch.setattr(home.rl, "draw_rectangle_rounded_lines_ex", lambda rect, r, s, t, color: drawn.append(color))
  monkeypatch.setattr(home.rl, "draw_circle_v", lambda center, radius, color: drawn.append(color))
  monkeypatch.setattr(home.rl, "get_time", lambda: 0.0)
  home.draw_jetlink(home.rl.Rectangle(0, 0, 40, 42), state)
  assert len(drawn) == 2
  r, g, b, a = COLORS[state]
  assert (drawn[0].r, drawn[0].g, drawn[0].b) == (r, g, b)
  assert drawn[0].a == (int(a * 0.35) if state == "waiting" else a)


def test_off_draws_nothing(monkeypatch):
  monkeypatch.setattr(home.rl, "draw_rectangle_rounded_lines_ex", lambda *args: pytest.fail("drew an icon for off"))
  home.draw_jetlink(home.rl.Rectangle(0, 0, 40, 42), "off")
