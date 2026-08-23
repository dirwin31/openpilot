import math
from types import SimpleNamespace

import numpy as np
import pytest

from opendbc.can import CANPacker, CANParser
from opendbc.car.honda import lane_path
from opendbc.car.honda.tests import FakePacker


def model_at(center=0.0, *, horizon=110.0, lane_width=3.7, left_prob=1.0, right_prob=1.0):
  x = np.linspace(0.0, horizon, 33)
  y = center(x) if callable(center) else np.full_like(x, center)

  def line(offset):
    return SimpleNamespace(x=x.tolist(), y=(y + offset).tolist())

  return SimpleNamespace(
    position=SimpleNamespace(x=x.tolist(), y=y.tolist()),
    laneLines=[line(-lane_width), line(-lane_width / 2.0), line(lane_width / 2.0), line(lane_width)],
    laneLineProbs=[0.0, left_prob, right_prob, 0.0],
  )


def stock_lane(offset=100, width=3.7, length=20, left=1, right=2, stock=True):
  return lane_path.DashLane([offset] * lane_path.NUM_PTS, length, left, right, lane_width=width,
                            left_lane_crossed=True, right_lane_crossed=False, stock=stock)


def test_model_position_is_rendered_without_lane_line_confidence():
  rendered = lane_path.LanePathFitter().update(
    model_at(1.0, left_prob=0.0, right_prob=0.0), 20.0, now_nanos=1_000_000_000,
  )

  assert rendered.left_line == lane_path.LANE_LINE_ON
  assert rendered.right_line == lane_path.LANE_LINE_ON
  assert all(offset < 0 for offset in rendered.offsets)
  assert rendered.lane_width == lane_path.LANE_WIDTH_DEFAULT


def test_curved_lane_change_and_laneless_trajectories_remain_visible():
  curved = lane_path.LanePathFitter().update(
    model_at(lambda x: 0.001 * x ** 2, left_prob=0.0, right_prob=0.0),
    20.0, now_nanos=1_000_000_000, curvature=0.002,
  )
  lane_change = lane_path.LanePathFitter().update(
    model_at(lambda x: np.clip(x / 50.0, 0.0, 1.0) * 3.5, left_prob=0.0, right_prob=0.0),
    20.0, now_nanos=1_000_000_000,
  )

  assert len(set(curved.offsets)) > 10
  assert lane_change.offsets[-1] < lane_change.offsets[0]
  assert lane_change.left_line and lane_change.right_line


def test_partial_horizon_marks_only_unavailable_suffix_and_sets_length():
  rendered = lane_path.LanePathFitter().update(model_at(0.2, horizon=51.0), 15.0, now_nanos=1_000_000_000)
  valid_count = sum(offset != lane_path.OFFSET_UNAVAILABLE for offset in rendered.offsets)

  assert 0 < valid_count < lane_path.NUM_PTS
  assert rendered.offsets[:valid_count] != [lane_path.OFFSET_UNAVAILABLE] * valid_count
  assert rendered.offsets[valid_count:] == [lane_path.OFFSET_UNAVAILABLE] * (lane_path.NUM_PTS - valid_count)
  assert rendered.lane_length == round(0.51 * lane_path.LANE_LENGTH_MAX_VALUE)


def test_nonmonotonic_model_is_truncated_at_first_bad_point():
  model = model_at(0.5)
  model.position.x[12] = model.position.x[11]
  rendered = lane_path.LanePathFitter().update(model, 15.0, now_nanos=1_000_000_000)
  horizon = model.position.x[11]

  for distance, offset in zip(lane_path.LOOKAHEAD, rendered.offsets, strict=True):
    assert (offset == lane_path.OFFSET_UNAVAILABLE) == (distance > horizon)


def test_malformed_value_preserves_the_valid_trajectory_prefix():
  model = model_at(0.5)
  model.position.y[12] = "bad"
  rendered = lane_path.LanePathFitter().update(model, 15.0, now_nanos=1_000_000_000)
  horizon = model.position.x[11]

  for distance, offset in zip(lane_path.LOOKAHEAD, rendered.offsets, strict=True):
    assert (offset == lane_path.OFFSET_UNAVAILABLE) == (distance > horizon)


@pytest.mark.parametrize("position", [
  SimpleNamespace(x=[0.0], y=[0.0]),
  SimpleNamespace(x=[0.0, 10.0], y=[0.0, math.nan]),
  SimpleNamespace(x=[0.0, 10.0], y=[0.0]),
])
def test_malformed_model_blanks_without_stock(position):
  model = SimpleNamespace(position=position, laneLines=[], laneLineProbs=[])
  rendered = lane_path.LanePathFitter().update(model, 15.0, now_nanos=1_000_000_000)

  assert rendered == lane_path.blank_lane()


def test_curvature_warp_sign_continuity_and_command_alignment():
  x = np.linspace(0.0, 50.0, 501)
  y = np.zeros_like(x)
  warped = lane_path.warp_trajectory(x, y, 0.001, 20.0)
  # The model path is straight, so the whole command counts as error. The warp
  # levels off at the correction for a 20 m (= v_ego) lookahead.
  endpoint = 0.5 * 0.001 * 20.0 ** 2

  assert warped[0] == pytest.approx(0.0)
  assert (warped[1] - warped[0]) / (x[1] - x[0]) == pytest.approx(0.0, abs=2e-4)
  assert warped[np.searchsorted(x, 20.0)] == pytest.approx(endpoint)
  assert np.allclose(warped[x >= 20.0], endpoint)

  negative = lane_path.warp_trajectory(x, y, -0.001, 20.0)
  assert np.allclose(negative[x >= 20.0], -endpoint)


def test_curvature_and_lateral_correction_caps_are_independent():
  x = np.linspace(0.0, 110.0, 111)
  y = np.zeros_like(x)

  # At 35 m the uncapped correction is 0.735 m, so the lateral cap binds first.
  assert lane_path.warp_trajectory(x, y, 1.0, 35.0)[-1] == pytest.approx(lane_path.CURVATURE_WARP_MAX)
  # At 8 m it is only 0.038 m, leaving the curvature cap as the sole limit.
  assert lane_path.warp_trajectory(x, y, 1.0, 8.0)[-1] == \
    pytest.approx(0.5 * lane_path.CURVATURE_ERROR_MAX * 8.0 ** 2)


def test_short_horizon_is_not_warped_without_curvature_lookahead():
  x = np.linspace(0.0, 10.0, 11)
  y = 0.01 * x

  assert np.array_equal(lane_path.warp_trajectory(x, y, 0.01, 20.0), y)


def test_curvature_estimate_recovers_a_quadratic_path():
  x = np.linspace(0.0, 100.0, 101)
  curvature = 0.0008
  y = 0.5 * curvature * x ** 2

  expected = curvature / ((1.0 + (curvature * 20.0) ** 2) ** 1.5)
  assert lane_path.estimate_curvature(x, y, 20.0) == pytest.approx(expected, rel=1e-4)


def test_lane_width_default_acceptance_smoothing_and_retention():
  fitter = lane_path.LanePathFitter()
  default = fitter.update(model_at(0.0, lane_width=4.0, left_prob=0.0, right_prob=0.0),
                          15.0, now_nanos=1_000_000_000)
  first = fitter.update(model_at(0.0, lane_width=3.6), 15.0, now_nanos=1_020_000_000)
  smoothed = fitter.update(model_at(0.0, lane_width=4.6), 15.0, now_nanos=1_520_000_000)
  one_line = fitter.update(model_at(0.0, lane_width=2.8, left_prob=0.0), 15.0, now_nanos=2_020_000_000)
  rejected = fitter.update(model_at(0.0, lane_width=5.5), 15.0, now_nanos=2_520_000_000)
  rejected_low = fitter.update(model_at(0.0, lane_width=2.4), 15.0, now_nanos=3_020_000_000)

  assert default.lane_width == lane_path.LANE_WIDTH_DEFAULT
  assert first.lane_width == pytest.approx(3.6)
  assert smoothed.lane_width == pytest.approx(3.6 + (1.0 - math.exp(-1.0)) * 1.0)
  assert one_line.lane_width == smoothed.lane_width
  assert rejected.lane_width == smoothed.lane_width
  assert rejected_low.lane_width == smoothed.lane_width


def test_authored_lane_width_clamps_and_dbc_packs_metres_to_raw_tenths():
  _, _, low = lane_path.create_lkas_hud_2(FakePacker(), 0, 0, stock_lane(width=2.0, stock=False))
  _, _, high = lane_path.create_lkas_hud_2(FakePacker(), 0, 0, stock_lane(width=7.0, stock=False))
  _, _, stock = lane_path.create_lkas_hud_2(FakePacker(), 0, 0, stock_lane(width=2.0))
  assert low["LANE_WIDTH"] == lane_path.LANE_WIDTH_MIN
  assert high["LANE_WIDTH"] == lane_path.LANE_WIDTH_MAX
  assert stock["LANE_WIDTH"] == 2.0

  packer = CANPacker("honda_bosch_radarless_generated")
  parser = CANParser("honda_bosch_radarless_generated", [("LKAS_HUD_2", 0)], 0)
  message = lane_path.create_lkas_hud_2(packer, 0, 0, stock_lane(width=3.7, stock=False))
  parser.update([1_000_000_000, [message]])

  assert parser.vl["LKAS_HUD_2"]["LANE_WIDTH"] == pytest.approx(3.7)
  assert (message[1][1] >> 2) == 37


def test_stock_tracker_reconstructs_complete_scene_and_expires():
  tracker = lane_path.StockLaneTracker()
  offsets = list(range(lane_path.NUM_PTS))
  values = {
    "MUX": list(range(1, 11)),
    "PATH_OFFSET_1": offsets[0::4],
    "PATH_OFFSET_2": offsets[1::4],
    "PATH_OFFSET_3": offsets[2::4],
    "PATH_OFFSET_4": offsets[3::4],
  }
  hud = {
    "LANE_WIDTH": [3.9], "LANE_LENGTH": [41], "LEFT_LANE": [1], "RIGHT_LANE": [2],
    "LEFT_LANE_CROSSED": [1], "RIGHT_LANE_CROSSED": [0],
  }
  cp_cam = SimpleNamespace(
    vl_all={"LANE_PATH": values, "LKAS_HUD_2": hud},
    ts_nanos={"LANE_PATH": {"MUX": 1_000_000_000}, "LKAS_HUD_2": {"LANE_WIDTH": 1_000_000_000}},
  )
  tracker.update(cp_cam)

  snapshot = tracker.snapshot(1_500_000_000)
  assert snapshot is not None
  assert snapshot.offsets == offsets
  assert snapshot.lane_width == 3.9
  assert snapshot.lane_length == 41
  assert (snapshot.left_line, snapshot.right_line) == (1, 2)
  assert snapshot.left_lane_crossed and not snapshot.right_lane_crossed
  assert tracker.snapshot(1_500_000_001) is None


def test_stock_tracker_requires_a_complete_path_snapshot():
  tracker = lane_path.StockLaneTracker()
  values = {"MUX": [1], "PATH_OFFSET_1": [1], "PATH_OFFSET_2": [2], "PATH_OFFSET_3": [3], "PATH_OFFSET_4": [4]}
  hud = {"LANE_WIDTH": [3.7], "LANE_LENGTH": [20], "LEFT_LANE": [3], "RIGHT_LANE": [3],
         "LEFT_LANE_CROSSED": [0], "RIGHT_LANE_CROSSED": [0]}
  tracker.update(SimpleNamespace(
    vl_all={"LANE_PATH": values, "LKAS_HUD_2": hud},
    ts_nanos={"LANE_PATH": {"MUX": 1_000_000_000}, "LKAS_HUD_2": {"LANE_WIDTH": 1_000_000_000}},
  ))

  assert tracker.snapshot(1_100_000_000) is None


def test_startup_without_stock_snapshot_shows_valid_openpilot_scene_immediately():
  fitter = lane_path.LanePathFitter()
  assert fitter.update(None, 20.0, now_nanos=1_000_000_000, lat_active=False) == lane_path.blank_lane()

  rendered = fitter.update(model_at(0.3), 20.0, now_nanos=1_020_000_000, lat_active=True)
  assert rendered.left_line == lane_path.LANE_LINE_ON
  assert rendered.right_line == lane_path.LANE_LINE_ON
  assert rendered.lane_length == lane_path.LANE_LENGTH_MAX_VALUE
  assert rendered.offsets[0] != lane_path.OFFSET_UNAVAILABLE


def test_engagement_and_fallback_slew_then_resume_exact_honda_scene():
  fitter = lane_path.LanePathFitter()
  stock = stock_lane(offset=100, width=4.0, length=18, left=1, right=2)
  inactive = fitter.update(None, 20.0, now_nanos=1_000_000_000, lat_active=False, stock_lane=stock)
  assert inactive == stock

  active = fitter.update(model_at(1.0, lane_width=3.6), 20.0, now_nanos=1_020_000_000,
                         lat_active=True, stock_lane=stock)
  assert all(abs(after - before) <= math.ceil(lane_path.SLEW_MAX_STEP)
             for before, after in zip(stock.offsets, active.offsets, strict=True))
  assert (active.left_line, active.right_line) == (1, 2)

  held = fitter.update(None, 20.0, now_nanos=1_400_000_000, lat_active=True, stock_lane=stock)
  assert held == active
  fallback = fitter.update(None, 20.0, now_nanos=1_600_000_000, lat_active=True, stock_lane=stock)
  assert fallback.offsets != active.offsets

  for i in range(120):
    fallback = fitter.update(None, 20.0, now_nanos=1_620_000_000 + i * 20_000_000,
                             lat_active=False, stock_lane=stock)
  assert fallback == stock

  moving_stock = stock_lane(offset=-250, width=3.8, length=22, left=2, right=1)
  assert fitter.update(None, 20.0, now_nanos=4_100_000_000,
                       lat_active=False, stock_lane=moving_stock) == moving_stock


def test_disengagement_starts_stock_handoff_without_model_dropout_hold():
  fitter = lane_path.LanePathFitter()
  model = model_at(0.0)
  stock = stock_lane(offset=300)
  active = fitter.update(model, 20.0, now_nanos=1_000_000_000, lat_active=True, stock_lane=stock)

  disengaged = fitter.update(model, 20.0, now_nanos=1_020_000_000, lat_active=False, stock_lane=stock)

  assert disengaged.offsets != active.offsets
  assert all(0 < after - before <= math.ceil(lane_path.SLEW_MAX_STEP)
             for before, after in zip(active.offsets, disengaged.offsets, strict=True))


def test_transition_smooths_numeric_metadata_and_switches_discrete_state_at_midpoint():
  fitter = lane_path.LanePathFitter()
  stock = stock_lane(offset=500, width=3.9, length=30, left=1, right=2)
  fitter.update(None, 20.0, now_nanos=1_000_000_000, lat_active=False, stock_lane=stock)
  model = model_at(0.0, lane_width=3.5)

  for i in range(12):
    rendered = fitter.update(model, 20.0, now_nanos=1_020_000_000 + i * 20_000_000,
                             lat_active=True, stock_lane=stock)
  assert (rendered.left_line, rendered.right_line) == (1, 2)
  assert 3.5 < rendered.lane_width < 3.9
  assert 30 < rendered.lane_length < lane_path.LANE_LENGTH_MAX_VALUE

  rendered = fitter.update(model, 20.0, now_nanos=1_260_000_000, lat_active=True, stock_lane=stock)
  assert (rendered.left_line, rendered.right_line) == (lane_path.LANE_LINE_ON, lane_path.LANE_LINE_ON)


def test_missing_both_sources_hides_scene_after_dropout_hold():
  fitter = lane_path.LanePathFitter()
  fitter.update(model_at(0.2), 20.0, now_nanos=1_000_000_000)
  held = fitter.update(None, 20.0, now_nanos=1_500_000_000)
  blank = fitter.update(None, 20.0, now_nanos=1_500_000_001)

  assert held.offsets != [lane_path.OFFSET_UNAVAILABLE] * lane_path.NUM_PTS
  assert blank == lane_path.blank_lane(blank.lane_width)


def test_lane_departures_pack_independently():
  lane = lane_path.DashLane([0] * lane_path.NUM_PTS, lane_path.LANE_LENGTH_MAX_VALUE,
                            lane_path.LANE_LINE_ON, lane_path.LANE_LINE_ON,
                            left_lane_crossed=True, right_lane_crossed=True)
  _, _, values = lane_path.create_lkas_hud_2(FakePacker(), 0, 0, lane)
  assert values["LEFT_LANE_CROSSED"] == 1
  assert values["RIGHT_LANE_CROSSED"] == 1


def test_civic_cluster_messages_pack_with_expected_extended_addresses():
  packer = CANPacker("honda_bosch_radarless_generated")
  lane_message = lane_path.create_lane_path(packer, 0, [0] * lane_path.NUM_PTS, mux=1)
  hud_message = lane_path.create_lkas_hud_2(packer, 0, 0, stock_lane(offset=0, stock=False))

  assert lane_message[0] == 0x6CD5554
  assert len(lane_message[1]) == 8
  assert hud_message[0] == 0xF31AA54
  assert len(hud_message[1]) == 8
