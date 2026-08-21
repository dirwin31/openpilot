import math
from types import SimpleNamespace

import numpy as np

from opendbc.can import CANPacker
from opendbc.car.honda import lane_path


def model_at(center_y, left_prob=1.0, right_prob=1.0, half_width=1.65):
  x = list(np.linspace(0.0, 110.0, 33))

  def line(y):
    return SimpleNamespace(x=x, y=[y] * len(x))

  return SimpleNamespace(
    laneLines=[line(center_y - 2 * half_width), line(center_y - half_width),
               line(center_y + half_width), line(center_y + 2 * half_width)],
    laneLineProbs=[0.0, left_prob, right_prob, 0.0],
  )


def test_lane_center_encoding_uses_honda_sign():
  rendered = lane_path.LanePathFitter().update(model_at(1.0), 30.0, 0.0)
  assert rendered.left_line and rendered.right_line
  assert all(offset < 0 for offset in rendered.offsets)


def test_single_lane_line_can_center_the_rendered_path():
  rendered = lane_path.LanePathFitter().update(model_at(0.0, left_prob=0.0), 15.0, 0.0)
  assert not rendered.left_line and rendered.right_line
  decoded = -np.asarray(rendered.offsets) / lane_path.GAIN
  assert np.max(np.abs(decoded)) < 0.3


def test_single_lane_fallbacks_match_both_lines():
  both = lane_path.LanePathFitter().update(model_at(0.6), 15.0, 0.0)
  right = lane_path.LanePathFitter().update(model_at(0.6, left_prob=0.0), 15.0, 0.0)
  left = lane_path.LanePathFitter().update(model_at(0.6, right_prob=0.0), 15.0, 0.0)

  both_y = -np.asarray(both.offsets) / lane_path.GAIN
  right_y = -np.asarray(right.offsets) / lane_path.GAIN
  left_y = -np.asarray(left.offsets) / lane_path.GAIN
  assert np.max(np.abs(right_y - both_y)) < 0.3
  assert np.max(np.abs(left_y - both_y)) < 0.3


def test_single_lane_fallback_uses_last_trusted_lane_width():
  fitter = lane_path.LanePathFitter()
  both = fitter.update(model_at(-0.4), 15.0, 0.0)
  right = fitter.update(model_at(-0.4, left_prob=0.0), 15.0, 0.0)

  assert right.offsets == both.offsets


def test_default_half_width_centers_a_nominal_lane_exactly():
  # Literal 3.70 m lane, deliberately NOT expressed via DASH_HALF_WIDTH_DEFAULT: the point is to
  # pin the constant to half a nominal lane. Fresh fitters have learned nothing, so the fallback
  # uses the default, and on this lane it must reproduce the both-lines answer exactly. A
  # camera-offset style fudge in the default shows up here as a constant bias.
  both = lane_path.LanePathFitter().update(model_at(0.0, half_width=1.85), 20.0, 0.0)
  right = lane_path.LanePathFitter().update(model_at(0.0, half_width=1.85, left_prob=0.0), 20.0, 0.0)
  left = lane_path.LanePathFitter().update(model_at(0.0, half_width=1.85, right_prob=0.0), 20.0, 0.0)

  assert right.offsets == both.offsets
  assert left.offsets == both.offsets


def test_lane_path_step_is_slew_limited():
  fitter = lane_path.LanePathFitter()
  previous = fitter.update(model_at(0.0), 30.0, 0.0).offsets
  current = fitter.update(model_at(-2.0), 30.0, 0.0).offsets
  assert all(abs(after - before) <= math.ceil(lane_path.SLEW_MAX_STEP)
             for before, after in zip(previous, current, strict=True))


def test_missing_model_blanks_and_resets_path():
  fitter = lane_path.LanePathFitter()
  fitter.update(model_at(0.0), 30.0, 0.0)
  blank = fitter.update(None, 30.0, 0.0)
  assert blank.offsets == [lane_path.OFFSET_UNAVAILABLE] * lane_path.NUM_PTS
  assert blank.reach == 0.0

  next_path = fitter.update(model_at(-2.0), 30.0, 0.0)
  expected = lane_path.encode_lane_path(model_at(-2.0).laneLines[1].x, [-2.0] * 33)
  assert next_path.offsets == expected


def test_short_model_dropout_holds_then_blanks_last_path():
  fitter = lane_path.LanePathFitter()
  rendered = fitter.update(model_at(0.4), 30.0, 0.0, now_nanos=1_000_000_000)
  # snapshot the values: the fitter hands back its own cached DashLane, so comparing
  # against the live object would pass no matter what the hold does
  expected_offsets = list(rendered.offsets)
  expected_reach = rendered.reach
  expected_lines = (rendered.left_line, rendered.right_line)
  assert expected_offsets != [lane_path.OFFSET_UNAVAILABLE] * lane_path.NUM_PTS

  held = fitter.update(None, 30.0, 0.0, now_nanos=1_400_000_000)
  expired = fitter.update(None, 30.0, 0.0, now_nanos=1_600_000_000)

  assert held.offsets == expected_offsets
  assert held.reach == expected_reach
  assert (held.left_line, held.right_line) == expected_lines
  assert expired.offsets == [lane_path.OFFSET_UNAVAILABLE] * lane_path.NUM_PTS
  assert expired.reach == 0.0


def test_lane_trust_hysteresis_resets_after_both_lines_drop():
  fitter = lane_path.LanePathFitter()
  assert fitter.update(model_at(0.0), 15.0, 0.0).left_line
  assert not fitter.update(model_at(0.0, left_prob=0.0, right_prob=0.0), 15.0, 0.0).left_line

  below_on_threshold = fitter.update(model_at(0.0, left_prob=0.15, right_prob=0.15), 15.0, 0.0)
  assert not below_on_threshold.left_line
  assert not below_on_threshold.right_line


def test_lane_departure_maps_to_cluster_crossed_side():
  assert lane_path.lane_cross_from_departures(True, False) == -1
  assert lane_path.lane_cross_from_departures(False, True) == 1
  assert lane_path.lane_cross_from_departures(False, False) == 0
  assert lane_path.lane_cross_from_departures(True, True) == 0


def test_civic_cluster_messages_pack_with_expected_extended_addresses():
  packer = CANPacker("honda_bosch_radarless_generated")
  offsets = [0] * lane_path.NUM_PTS

  lane_message = lane_path.create_lane_path(packer, 0, offsets, mux=1)
  hud_message = lane_path.create_lkas_hud_2(packer, 0, counter_2=0)

  assert lane_message[0] == 0x6CD5554
  assert len(lane_message[1]) == 8
  assert hud_message[0] == 0xF31AA54
  assert len(hud_message[1]) == 8
