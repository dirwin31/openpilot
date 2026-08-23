"""Render and hand off the 2022+ Civic cluster lane scene. Adapted from MVL-Boston's lane_path.py"""

import math
from dataclasses import dataclass, replace

import numpy as np

from opendbc.can.parser import CANParser


NUM_INDICES = 10
OFFSETS_PER_INDEX = 4
NUM_PTS = NUM_INDICES * OFFSETS_PER_INDEX

# The camera sends ten points, repeated across four banks. LANE_PATH and
# HUD_OBJECTS have to stay on the same mux or the cluster can freeze.
MUX_CYCLE = tuple(index + bank * 16 for bank in range(4) for index in range(1, NUM_INDICES + 1))

OFFSET_UNAVAILABLE = 2047
OFFSET_VALID_MAX = 2046

SLEW_UPDATE_RATE_HZ = 50.0
SLEW_FULL_SCALE_SECONDS = 2.0
SLEW_MAX_STEP = OFFSET_VALID_MAX / (SLEW_FULL_SCALE_SECONDS * SLEW_UPDATE_RATE_HZ)

D_NEAR = 2.0
D_MAX = 100.0
LOOKAHEAD = np.linspace(D_NEAR, D_MAX, NUM_PTS)

# Raw offset units per metre of lateral, measured by comparing the factory
# camera's own LANE_PATH offsets against modelV2's lane centre on route
# 3792d010590cb83a|00000107 (187 paired sweeps, lane-line probability >= 0.4).
# That fit sets both the gain and the sign.

def _gain_at(distance):
  return 29.3 + 0.243 * distance - 0.00228 * distance ** 2


GAIN = _gain_at(LOOKAHEAD)

LANE_LINE_ON = 3
LANE_LENGTH_MAX_VALUE = 33  # Empirical full dash reach; the 6-bit DBC capacity is 63.
LANE_LENGTH_DBC_MAX = 63
LANE_WIDTH_DEFAULT = 3.2
LANE_WIDTH_MIN = 2.5
LANE_WIDTH_MAX = 5.0
LANE_WIDTH_DBC_MIN = 0.0
LANE_WIDTH_DBC_MAX = 6.3
LANE_WIDTH_SMOOTH_TAU = 0.5

DASH_PATH_PROB_ON = 0.25
DASH_PATH_PROB_OFF = 0.10
MODEL_DROPOUT_HOLD_NANOS = 500_000_000
STOCK_TIMEOUT_NANOS = 500_000_000

# The same speed-to-lookahead window lane_centering.py uses. Copied rather than
# imported so opendbc stays independent of the lateral stack; change both together.
CURVATURE_LOOKAHEAD_MIN = 8.0
CURVATURE_LOOKAHEAD_MAX = 35.0
CURVATURE_ERROR_MAX = 0.0012
CURVATURE_WARP_MAX = 0.30


def _is_available(offset: int | float) -> bool:
  return int(round(offset)) != OFFSET_UNAVAILABLE


def _encode(lateral):
  # modelV2 lateral is +right; the Honda path offset is +left.
  raw = np.clip(np.round(-GAIN * np.asarray(lateral, dtype=float)), -OFFSET_VALID_MAX, OFFSET_VALID_MAX)
  return [int(value) for value in raw]


def trajectory_from_model(model) -> tuple[np.ndarray | None, np.ndarray | None]:
  """Return the start of modelV2.position, stopping at the first bad point."""
  try:
    x_values = model.position.x
    y_values = model.position.y
  except (AttributeError, TypeError):
    return None, None

  count = min(len(x_values), len(y_values))
  x: list[float] = []
  y: list[float] = []
  previous_x = -math.inf
  for i in range(count):
    try:
      current_x = float(x_values[i])
      current_y = float(y_values[i])
    except (TypeError, ValueError):
      break
    if not math.isfinite(current_x) or not math.isfinite(current_y) or current_x <= previous_x:
      break
    x.append(current_x)
    y.append(current_y)
    previous_x = current_x

  if len(x) < 2 or x[-1] < D_NEAR:
    return None, None
  return np.asarray(x), np.asarray(y)


def encode_lane_path(x, y):
  """Sample the model path from 2 to 100 m, keeping however far it really reaches."""
  x = np.asarray(x, dtype=float)
  y = np.asarray(y, dtype=float)
  if (x.size < 2 or y.size != x.size or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or
      not np.all(np.diff(x) > 0.0) or x[-1] < D_NEAR):
    return [OFFSET_UNAVAILABLE] * NUM_PTS

  lateral = np.interp(LOOKAHEAD, x, y)
  offsets = _encode(lateral)
  for i, distance in enumerate(LOOKAHEAD):
    if distance > x[-1]:
      offsets[i] = OFFSET_UNAVAILABLE
  return offsets


def estimate_curvature(x, y, distance: float) -> float:
  """Estimate how sharply the path curves near the given distance."""
  x = np.asarray(x, dtype=float)
  y = np.asarray(y, dtype=float)
  if x.size < 3 or y.size != x.size:
    return math.nan

  distance = float(np.clip(distance, x[0], x[-1]))
  nearest = np.argsort(np.abs(x - distance))[:min(7, x.size)]
  fit_x = x[nearest] - distance
  fit_y = y[nearest]
  if np.ptp(fit_x) <= 1e-6:
    return math.nan
  try:
    quadratic, slope, _ = np.polyfit(fit_x, fit_y, 2)
  except (ValueError, np.linalg.LinAlgError):
    return math.nan
  return float((2.0 * quadratic) / ((1.0 + slope ** 2) ** 1.5))


def warp_trajectory(x, y, commanded_curvature: float, v_ego: float) -> np.ndarray:
  """Bend the near path toward the steering command openpilot actually sent.

  The correction starts at zero with zero slope, eases in, and levels off at the
  lookahead distance. Curvature and sideways shift are capped separately.
  """
  x = np.asarray(x, dtype=float)
  y = np.asarray(y, dtype=float)
  lookahead = float(np.clip(v_ego, CURVATURE_LOOKAHEAD_MIN, CURVATURE_LOOKAHEAD_MAX))
  if x.size < 3 or x[-1] < lookahead:
    return y.copy()
  model_curvature = estimate_curvature(x, y, lookahead)
  try:
    commanded_curvature = float(commanded_curvature)
  except (TypeError, ValueError):
    return y.copy()
  if not math.isfinite(model_curvature) or not math.isfinite(commanded_curvature):
    return y.copy()

  curvature_error = float(np.clip(commanded_curvature - model_curvature,
                                  -CURVATURE_ERROR_MAX, CURVATURE_ERROR_MAX))
  endpoint = float(np.clip(0.5 * curvature_error * lookahead ** 2,
                           -CURVATURE_WARP_MAX, CURVATURE_WARP_MAX))
  unit_distance = np.clip(x / lookahead, 0.0, 1.0)
  cubic = unit_distance ** 2 * (3.0 - 2.0 * unit_distance)
  return y + endpoint * cubic


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


def create_lkas_hud_2(packer, bus, counter_2, lane: "DashLane"):
  lane_length = max(0, min(LANE_LENGTH_DBC_MAX, int(lane.lane_length)))
  shown = lane_length > 0
  width_min, width_max = ((LANE_WIDTH_DBC_MIN, LANE_WIDTH_DBC_MAX) if lane.stock else
                          (LANE_WIDTH_MIN, LANE_WIDTH_MAX))
  values = {
    "COUNTER_2": counter_2,
    "SET_ME_X01": 1,
    "LANE_WIDTH": float(np.clip(lane.lane_width, width_min, width_max)),
    "LEFT_LANE": int(lane.left_line) if shown else 0,
    "RIGHT_LANE": int(lane.right_line) if shown else 0,
    "LEFT_LANE_CROSSED": int(shown and lane.left_lane_crossed),
    "RIGHT_LANE_CROSSED": int(shown and lane.right_lane_crossed),
    "LANE_LENGTH": lane_length,
  }
  return packer.make_can_msg("LKAS_HUD_2", bus, values)


def _line_trusted(probability, was_on):
  try:
    probability = float(probability)
  except (TypeError, ValueError):
    return False
  return math.isfinite(probability) and probability >= (DASH_PATH_PROB_OFF if was_on else DASH_PATH_PROB_ON)


@dataclass
class DashLane:
  offsets: list[int]
  lane_length: int
  left_line: int
  right_line: int
  lane_width: float = LANE_WIDTH_DEFAULT
  left_lane_crossed: bool = False
  right_lane_crossed: bool = False
  stock: bool = False


def blank_lane(lane_width=LANE_WIDTH_DEFAULT) -> DashLane:
  return DashLane([OFFSET_UNAVAILABLE] * NUM_PTS, 0, 0, 0, lane_width=lane_width)


class StockLaneTracker:
  """Rebuild Honda's own lane scene from the camera messages, when they arrive."""

  def __init__(self):
    self._offsets = [OFFSET_UNAVAILABLE] * NUM_PTS
    self._index_nanos = [0] * NUM_INDICES
    self._hud_nanos = 0
    self._lane_width = LANE_WIDTH_DEFAULT
    self._lane_length = 0
    self._left_line = 0
    self._right_line = 0
    self._left_crossed = False
    self._right_crossed = False

  def update(self, cp_cam: CANParser) -> None:
    values = cp_cam.vl_all["LANE_PATH"]
    if values["MUX"]:
      signals = ("MUX", "PATH_OFFSET_1", "PATH_OFFSET_2", "PATH_OFFSET_3", "PATH_OFFSET_4")
      last_nanos = int(cp_cam.ts_nanos["LANE_PATH"]["MUX"])
      rows = list(zip(*(values[signal] for signal in signals), strict=True))
      for mux, *offsets in rows:
        index = (int(mux) - 1) % 16
        if 0 <= index < NUM_INDICES:
          base = index * OFFSETS_PER_INDEX
          self._offsets[base:base + OFFSETS_PER_INDEX] = [int(value) for value in offsets]
          self._index_nanos[index] = last_nanos

    hud_values = cp_cam.vl_all["LKAS_HUD_2"]
    if hud_values["LANE_WIDTH"]:
      i = -1
      self._lane_width = float(hud_values["LANE_WIDTH"][i])
      self._lane_length = int(hud_values["LANE_LENGTH"][i])
      self._left_line = int(hud_values["LEFT_LANE"][i])
      self._right_line = int(hud_values["RIGHT_LANE"][i])
      self._left_crossed = bool(hud_values["LEFT_LANE_CROSSED"][i])
      self._right_crossed = bool(hud_values["RIGHT_LANE_CROSSED"][i])
      self._hud_nanos = int(cp_cam.ts_nanos["LKAS_HUD_2"]["LANE_WIDTH"])

  def snapshot(self, now_nanos: int) -> DashLane | None:
    path_fresh = all(timestamp > 0 and 0 <= now_nanos - timestamp <= STOCK_TIMEOUT_NANOS
                     for timestamp in self._index_nanos)
    hud_fresh = self._hud_nanos > 0 and 0 <= now_nanos - self._hud_nanos <= STOCK_TIMEOUT_NANOS
    if not path_fresh or not hud_fresh:
      return None
    return DashLane(list(self._offsets), self._lane_length, self._left_line, self._right_line,
                    lane_width=self._lane_width,
                    left_lane_crossed=self._left_crossed, right_lane_crossed=self._right_crossed, stock=True)


class LanePathFitter:
  """Draw openpilot's path and fade smoothly to and from Honda's own scene."""

  def __init__(self):
    self._left_on = False
    self._right_on = False
    self._lane_width = LANE_WIDTH_DEFAULT
    self._lane_width_initialized = False
    self._last_width_nanos = 0
    self._displayed: DashLane | None = None
    self._source = "blank"
    self._transition_start: DashLane | None = None
    self._transition_progress = 1.0
    self._transition_step = 1.0
    self._last_model_nanos = 0
    self._last_op_lane: DashLane | None = None

  def _update_lane_width(self, model, now_nanos: int | None) -> float:
    try:
      lane_lines = model.laneLines
      probabilities = model.laneLineProbs
      left_on = len(lane_lines) > 2 and len(probabilities) > 2 and _line_trusted(probabilities[1], self._left_on)
      right_on = len(lane_lines) > 2 and len(probabilities) > 2 and _line_trusted(probabilities[2], self._right_on)
    except (AttributeError, TypeError):
      left_on = right_on = False
      lane_lines = ()
    self._left_on, self._right_on = left_on, right_on

    measurement = None
    if left_on and right_on:
      try:
        left_y = np.asarray(lane_lines[1].y, dtype=float)
        right_y = np.asarray(lane_lines[2].y, dtype=float)
        if left_y.size and left_y.size == right_y.size:
          widths = np.abs(right_y - left_y)
          widths = widths[np.isfinite(widths)]
          if widths.size:
            candidate = float(np.median(widths))
            if LANE_WIDTH_MIN <= candidate <= LANE_WIDTH_MAX:
              measurement = candidate
      except (AttributeError, TypeError, ValueError):
        pass

    if measurement is not None:
      if not self._lane_width_initialized:
        self._lane_width = measurement
        self._lane_width_initialized = True
      else:
        dt = 1.0 / SLEW_UPDATE_RATE_HZ
        if now_nanos is not None and self._last_width_nanos > 0:
          dt = max(0.0, (now_nanos - self._last_width_nanos) * 1e-9)
        alpha = 1.0 - math.exp(-dt / LANE_WIDTH_SMOOTH_TAU)
        self._lane_width += alpha * (measurement - self._lane_width)
      if now_nanos is not None:
        self._last_width_nanos = now_nanos
    return float(np.clip(self._lane_width, LANE_WIDTH_MIN, LANE_WIDTH_MAX))

  def _openpilot_lane(self, model, v_ego, curvature, now_nanos, left_departure, right_departure):
    x, y = trajectory_from_model(model)
    if x is None:
      return None
    width = self._update_lane_width(model, now_nanos)
    offsets = encode_lane_path(x, warp_trajectory(x, y, curvature, v_ego))
    valid_count = sum(_is_available(offset) for offset in offsets)
    if valid_count == 0:
      return None
    # Length follows how far the model actually reaches, not how fast we are
    # going, so a full 100 m path still draws in full at low speed.
    lane_length = round(float(np.clip(x[-1] / D_MAX, 0.0, 1.0)) * LANE_LENGTH_MAX_VALUE)
    # The rails show where openpilot is steering, lane changes included. Lane
    # confidence only decides whether the measured width is trusted.
    return DashLane(offsets, lane_length, LANE_LINE_ON, LANE_LINE_ON, lane_width=width,
                    left_lane_crossed=bool(left_departure), right_lane_crossed=bool(right_departure))

  @staticmethod
  def _copy(lane: DashLane) -> DashLane:
    return replace(lane, offsets=list(lane.offsets))

  def _begin_transition(self, source: str, target: DashLane) -> None:
    if self._displayed is None:
      self._displayed = self._copy(target)
      self._source = source
      self._transition_progress = 1.0
      return

    self._source = source
    self._transition_start = self._copy(self._displayed)
    distances = [abs(after - before) for before, after in zip(self._displayed.offsets, target.offsets, strict=True)
                 if _is_available(before) and _is_available(after)]
    distances.extend([
      abs(self._displayed.lane_width - target.lane_width) / (LANE_WIDTH_MAX - LANE_WIDTH_MIN) * OFFSET_VALID_MAX,
      abs(int(self._displayed.lane_length) - int(target.lane_length)) / LANE_LENGTH_MAX_VALUE * OFFSET_VALID_MAX,
    ])
    steps = max(1, math.ceil(max(distances, default=0.0) / SLEW_MAX_STEP))
    self._transition_progress = 0.0
    self._transition_step = 1.0 / steps

  def _slew_to(self, target: DashLane) -> DashLane:
    if self._displayed is None:
      self._displayed = self._copy(target)
      return self._copy(self._displayed)
    if self._source == "stock" and self._transition_start is None:
      self._displayed = self._copy(target)
      return self._copy(target)

    offsets = []
    for current, desired in zip(self._displayed.offsets, target.offsets, strict=True):
      if not _is_available(current) or not _is_available(desired):
        offsets.append(int(desired))
      else:
        offsets.append(int(round(current + float(np.clip(desired - current, -SLEW_MAX_STEP, SLEW_MAX_STEP)))))

    if self._transition_progress < 1.0 and self._transition_start is not None:
      self._transition_progress = min(1.0, self._transition_progress + self._transition_step)
      progress = self._transition_progress
      width = self._transition_start.lane_width + progress * (target.lane_width - self._transition_start.lane_width)
      length = round(int(self._transition_start.lane_length) + progress *
                     (int(target.lane_length) - int(self._transition_start.lane_length)))
      discrete = target if progress >= 0.5 else self._transition_start
    else:
      width = target.lane_width
      length = int(target.lane_length)
      discrete = target

    complete = offsets == target.offsets and self._transition_progress >= 1.0
    if complete:
      self._transition_start = None
      result = self._copy(target)
    else:
      result = DashLane(offsets, length, discrete.left_line, discrete.right_line,
                        lane_width=width,
                        left_lane_crossed=discrete.left_lane_crossed,
                        right_lane_crossed=discrete.right_lane_crossed, stock=discrete.stock)
    self._displayed = self._copy(result)
    return result

  def update(self, model, v_ego, now_nanos=None, *, curvature=0.0, lat_active=True,
             stock_lane: DashLane | None = None, left_departure=False, right_departure=False) -> DashLane:
    if now_nanos is None:
      now_nanos = 0

    op_lane = None
    if lat_active and model is not None:
      op_lane = self._openpilot_lane(model, v_ego, curvature, now_nanos or None, left_departure, right_departure)
    if op_lane is not None:
      self._last_op_lane = self._copy(op_lane)
      self._last_model_nanos = now_nanos
      desired_source = "openpilot"
      target = op_lane
    else:
      age = now_nanos - self._last_model_nanos
      holding = (lat_active and self._source == "openpilot" and self._last_op_lane is not None and self._last_model_nanos > 0 and
                 0 <= age <= MODEL_DROPOUT_HOLD_NANOS)
      if holding:
        return self._copy(self._displayed if self._displayed is not None else self._last_op_lane)
      desired_source = "stock" if stock_lane is not None else "blank"
      target = stock_lane if stock_lane is not None else blank_lane(self._lane_width)

    if desired_source == "blank":
      self._source = "blank"
      self._transition_start = None
      self._transition_progress = 1.0
      self._displayed = self._copy(target)
      return self._copy(target)

    if desired_source != self._source:
      # Start the handoff from the camera's newest complete scene, even if
      # something older was on screen while that snapshot was still filling in.
      if desired_source == "openpilot" and stock_lane is not None:
        self._displayed = self._copy(stock_lane)
      elif desired_source == "openpilot" and self._source == "blank":
        self._displayed = None
      self._begin_transition(desired_source, target)
    return self._slew_to(target)
