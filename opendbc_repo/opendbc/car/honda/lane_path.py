"""Render openpilot's predicted ego-lane path on the 2022+ Civic cluster.

Adapted from mvl-boston/opendbc's sp-honda-dev-202608 branch. This StarPilot
port intentionally contains only the Bosch radarless protocol used by
``CAR.HONDA_CIVIC_2022``; the source branch's Honda CAN FD support is omitted.
"""

from dataclasses import dataclass

import numpy as np


NUM_INDICES = 10
OFFSETS_PER_INDEX = 4
NUM_PTS = NUM_INDICES * OFFSETS_PER_INDEX

# The dash consumes 40 offsets as ten logical indices repeated over four banks.
# Keep LANE_PATH and HUD_OBJECTS on the same mux or the rendering can freeze.
MUX_CYCLE = tuple(index + bank * 16 for bank in range(4) for index in range(1, NUM_INDICES + 1))

OFFSET_UNAVAILABLE = 2047
OFFSET_VALID_MAX = 2046

SLEW_UPDATE_RATE_HZ = 50.0
SLEW_FULL_SCALE_SECONDS = 2.0
SLEW_MAX_STEP = OFFSET_VALID_MAX / (SLEW_FULL_SCALE_SECONDS * SLEW_UPDATE_RATE_HZ)

D_NEAR = 2.0
D_MAX = 100.0
LOOKAHEAD = np.linspace(D_NEAR, D_MAX, NUM_PTS)

# Raw offset units per metre of lateral. Upstream fit this per look-ahead index by regressing
# the factory camera's own LANE_PATH offsets against modelV2's lane centre over route
# 3792d010590cb83a|00000107 (187 paired sweeps, lane-line prob >= 0.4, correlation 0.6 -> 0.96
# with distance). That regression is also what fixes the sign: it is measured against real stock
# frames, so "stock offset = -GAIN * modelV2 lateral" is empirical, not an assumption.


def _gain_at(distance):
  """Fitted gain law. Scalar or array; GAIN and curve_boost() must not drift apart."""
  return 29.3 + 0.243 * distance - 0.00228 * distance ** 2


def _gain_prev_at(distance):
  """The older, flatter law the HUD lead scaling was tuned against."""
  return 6.27 + 0.0106 * distance + 0.000354 * distance ** 2


GAIN = _gain_at(LOOKAHEAD)

LANE_LINE_ON = 3
LANE_LENGTH_MAX_VALUE = 33
LANE_WIDTH_DEFAULT = 32

DASH_PATH_PROB_ON = 0.25
DASH_PATH_PROB_OFF = 0.10
# Half a nominal 3.7 m lane, used only until both lines have been trusted once and the real
# half-width is learned. No camera-mounting correction: select_lane_render() works entirely in
# modelV2's frame, so an offset common to both lane lines cancels out of (left + right) / 2 and
# must cancel out of the single-line fallback too, or the two branches disagree. The user's
# CameraOffset setting needs no handling here either - modeld applies it as a shear on the model
# input transform (selfdrive/modeld/camera_offset.py), so modelV2 already carries it.
DASH_HALF_WIDTH_DEFAULT = 1.85
DASH_LANE_WIDTH_MIN = 2.5
DASH_LANE_WIDTH_MAX = 5.0
DASH_PATH_FULL_LEN_SPEED = 27.0
DASH_PATH_LEAD_FULL_DIST = 70.0
DASH_PATH_MIN_REACH = 0.15
MODEL_DROPOUT_HOLD_NANOS = 500_000_000


def curve_boost(distance: float) -> float:
  """Ratio of the fitted gain law to the previous one, so the HUD lead marker (tuned against the
  old, flatter lanes) still lands on the path now that the lanes carry full stock curvature."""
  distance = min(max(float(distance), D_NEAR), D_MAX)
  return _gain_at(distance) / _gain_prev_at(distance)


def _encode(lateral):
  # modelV2 lateral is +right; the Honda path offset is +left.
  raw = np.clip(np.round(-GAIN * np.asarray(lateral, dtype=float)), -OFFSET_VALID_MAX, OFFSET_VALID_MAX)
  return [int(value) for value in raw]


def encode_lane_path(x, y):
  """Convert an openpilot lane center into the cluster's 40 path offsets."""
  x = np.asarray(x, dtype=float)
  y = np.asarray(y, dtype=float)
  if x.size < 2 or y.size != x.size or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or x.max() < D_MAX:
    return [OFFSET_UNAVAILABLE] * NUM_PTS
  return _encode(np.interp(LOOKAHEAD, x, y))


def create_lane_path(packer, bus, offsets, mux):
  base = ((mux - 1) % 16) * OFFSETS_PER_INDEX
  values = {
    "MUX": mux,
    "PATH_OFFSET_1": offsets[base],
    "PATH_OFFSET_2": offsets[base + 1],
    "PATH_OFFSET_3": offsets[base + 2],
    "PATH_OFFSET_4": offsets[base + 3],
  }
  return packer.make_can_msg("LANE_PATH", bus, values)


def create_lkas_hud_2(packer, bus, counter_2, reach=1.0, lane_cross=0, left_line=True, right_line=True):
  lane_length = max(0, min(LANE_LENGTH_MAX_VALUE, round(reach * LANE_LENGTH_MAX_VALUE)))
  shown = lane_length > 0
  values = {
    "COUNTER_2": counter_2,
    "SET_ME_X01": 1,
    "LANE_WIDTH": LANE_WIDTH_DEFAULT,
    "LEFT_LANE": LANE_LINE_ON if shown and left_line else 0,
    "RIGHT_LANE": LANE_LINE_ON if shown and right_line else 0,
    "LEFT_LANE_CROSSED": int(shown and lane_cross < 0),
    "RIGHT_LANE_CROSSED": int(shown and lane_cross > 0),
    "LANE_LENGTH": lane_length,
  }
  return packer.make_can_msg("LKAS_HUD_2", bus, values)


def _line_trusted(probability, was_on):
  return probability >= (DASH_PATH_PROB_OFF if was_on else DASH_PATH_PROB_ON)


def lane_cross_from_departures(left_departure, right_departure):
  if bool(left_departure) == bool(right_departure):
    return 0
  return -1 if left_departure else 1


def select_lane_render(model, prev_left, prev_right, half_width=DASH_HALF_WIDTH_DEFAULT):
  """Return the ego-lane center and the two line confidence states."""
  lane_lines, probabilities = model.laneLines, model.laneLineProbs
  if len(lane_lines) < 3 or len(probabilities) < 3 or len(lane_lines[1].x) == 0:
    return None, None, False, False

  left = _line_trusted(probabilities[1], prev_left)
  right = _line_trusted(probabilities[2], prev_right)
  x = np.asarray(lane_lines[1].x)
  left_y = np.asarray(lane_lines[1].y)
  right_y = np.asarray(lane_lines[2].y)
  if len(left_y) != len(x) or len(right_y) != len(x):
    return None, None, False, False

  if left and right:
    y = (left_y + right_y) / 2.0
  elif right:
    y = right_y - half_width
  elif left:
    y = left_y + half_width
  else:
    return None, None, False, False
  return x, y, left, right


@dataclass
class DashLane:
  offsets: list[int]
  reach: float
  left_line: bool
  right_line: bool


class LanePathFitter:
  def __init__(self):
    self._left_on = False
    self._right_on = False
    self._half_width = DASH_HALF_WIDTH_DEFAULT
    self._displayed = None
    self._last_model_nanos = 0
    self._last_lane = DashLane([OFFSET_UNAVAILABLE] * NUM_PTS, 0.0, False, False)

  def _slew(self, offsets):
    if offsets[0] == OFFSET_UNAVAILABLE:
      self._displayed = None
      return offsets

    target = np.asarray(offsets, dtype=float)
    if self._displayed is None:
      self._displayed = target
    else:
      delta = np.clip(target - self._displayed, -SLEW_MAX_STEP, SLEW_MAX_STEP)
      self._displayed = self._displayed + delta
    return [int(value) for value in np.round(self._displayed)]

  def update(self, model, v_ego, lead_distance, now_nanos=None) -> DashLane:
    blank = DashLane([OFFSET_UNAVAILABLE] * NUM_PTS, 0.0, False, False)

    if model is None and now_nanos is not None and self._last_model_nanos > 0:
      model_age = now_nanos - self._last_model_nanos
      if 0 <= model_age <= MODEL_DROPOUT_HOLD_NANOS:
        return self._last_lane

    x = y = None
    left_on = right_on = False
    if model is not None:
      x, y, left_on, right_on = select_lane_render(model, self._left_on, self._right_on, self._half_width)
    self._left_on, self._right_on = left_on, right_on
    if x is None:
      self._displayed = None
      self._last_lane = blank
      return blank

    if left_on and right_on:
      lane_widths = np.asarray(model.laneLines[2].y, dtype=float) - np.asarray(model.laneLines[1].y, dtype=float)
      finite_widths = lane_widths[np.isfinite(lane_widths)]
      if finite_widths.size:
        lane_width = float(np.median(finite_widths))
        if DASH_LANE_WIDTH_MIN <= lane_width <= DASH_LANE_WIDTH_MAX:
          self._half_width = lane_width / 2.0

    reach = float(np.clip(max(v_ego / DASH_PATH_FULL_LEN_SPEED,
                              lead_distance / DASH_PATH_LEAD_FULL_DIST,
                              DASH_PATH_MIN_REACH), 0.0, 1.0))
    self._last_lane = DashLane(self._slew(encode_lane_path(x, y)), reach, left_on, right_on)
    if now_nanos is not None:
      self._last_model_nanos = now_nanos
    return self._last_lane
