"""Track and render vehicle objects on the 2022+ Civic instrument cluster.

Adapted from mvl-boston/opendbc's sp-honda-dev-202608 branch and limited to
the Bosch radarless protocol used by ``CAR.HONDA_CIVIC_2022``.
"""

import math
from dataclasses import dataclass, replace

from opendbc.can.parser import CANParser
from opendbc.car.honda import lane_path


NUM_SLOTS = 10
LONG_DIST_CAP_M = 195.0
TRACK_TIMEOUT_NANOS = 500_000_000


@dataclass
class HudObject:
  slot: int
  object_id: int
  d_rel: float
  y_rel: float
  is_lead_car: bool
  valid: bool
  car_type: int = -1
  rotation: int = -128
  last_nanos: int = 0


class HudObjectTracker:
  """Persist the ten multiplexed objects reported by the stock camera."""

  def __init__(self):
    self._tracks = [
      HudObject(slot=slot, object_id=0, d_rel=0.0, y_rel=0.0, is_lead_car=False, valid=False)
      for slot in range(NUM_SLOTS)
    ]

  def update(self, cp_cam: CANParser) -> None:
    values = cp_cam.vl_all["HUD_OBJECTS"]
    last_nanos = int(cp_cam.ts_nanos["HUD_OBJECTS"]["MUX"])
    signals = ("MUX", "OBJECT_ID", "LONG_DIST", "LAT_DIST", "IS_LEAD_CAR", "CAR_TYPE", "ROTATION")
    for mux, object_id, d_rel, y_rel, is_lead, car_type, rotation in zip(
        *(values[signal] for signal in signals), strict=True):
      slot = (int(mux) - 1) % 16
      if 0 <= slot < NUM_SLOTS:
        valid = object_id != 0 and d_rel < LONG_DIST_CAP_M
        self._tracks[slot] = HudObject(slot, int(object_id), float(d_rel), float(y_rel), bool(is_lead), valid,
                                      int(car_type), int(rotation), last_nanos)

  def snapshot(self, now_nanos: int) -> list[HudObject]:
    tracks = []
    for track in self._tracks:
      age = now_nanos - track.last_nanos
      fresh = track.last_nanos > 0 and 0 <= age <= TRACK_TIMEOUT_NANOS
      tracks.append(replace(track, valid=track.valid and fresh))
    return tracks


INACTIVE = {
  "OBJECT_ID": 0,
  "IS_LEAD_CAR": 0,
  "CAR_TYPE": -1,
  "ROTATION": -128,
  "LONG_DIST": 196.9,
  "LAT_DIST": 204.7,
}

CAR_TYPE_CAR = 7
LONG_DIST_MAX_M = 194.0
LAT_DIST_LIM_M = 204.7
LAT_SCALE = 0.35
ROT_BAND_M = 1.5
ROT_MAX = 6

REID_GAP_M = 8.0
REID_TAU = 1.5
REID_REFRACTORY = 1.5
MAX_OBJECT_ID = 31

DREL_SMOOTH_TAU = 0.6
YREL_SMOOTH_TAU = 0.5
FF_VREL_MIN = 0.5
DREL_RESID_CLAMP = 1.5

LEAD_PROB_ON = 0.5
LEAD_PROB_OFF = 0.35
LEAD_HOLD_S = 0.6


class LeadObjectId:
  def __init__(self):
    self.object_id = 0
    self._on = False
    self._prediction = 0.0
    self._previous_time = 0.0
    self._reid_time = -1e9

  def update(self, status: bool, d_rel: float, v_rel: float, now: float) -> int:
    if not status:
      self._on = False
      return 0

    new_lead = not self._on
    if self._on:
      dt = max(now - self._previous_time, 1e-3)
      self._prediction += v_rel * dt
      self._prediction += min(dt / REID_TAU, 1.0) * (d_rel - self._prediction)
      if abs(d_rel - self._prediction) > REID_GAP_M and now - self._reid_time > REID_REFRACTORY:
        new_lead = True
    self._previous_time = now

    if new_lead:
      self.object_id = self.object_id % MAX_OBJECT_ID + 1
      self._reid_time = now
      self._prediction = d_rel
    self._on = True
    return self.object_id


class LeadSmoother:
  def __init__(self):
    self._object_id = 0
    self._d_rel = 0.0
    self._y_rel = 0.0
    self._time = 0.0

  def update(self, d_rel: float, y_rel: float, v_rel: float, object_id: int, now: float) -> tuple[float, float]:
    if object_id != self._object_id:
      self._object_id, self._d_rel, self._y_rel, self._time = object_id, d_rel, y_rel, now
      return d_rel, y_rel

    dt = max(now - self._time, 1e-3)
    self._time = now
    if abs(v_rel) >= FF_VREL_MIN:
      self._d_rel += v_rel * dt
    residual = min(max(d_rel - self._d_rel, -DREL_RESID_CLAMP), DREL_RESID_CLAMP)
    self._d_rel += (1.0 - math.exp(-dt / DREL_SMOOTH_TAU)) * residual
    self._y_rel += (1.0 - math.exp(-dt / YREL_SMOOTH_TAU)) * (y_rel - self._y_rel)
    return self._d_rel, self._y_rel


def lead_rotation(lateral_left_m: float) -> int:
  magnitude = min(round(abs(lateral_left_m) / ROT_BAND_M), ROT_MAX)
  return -magnitude if lateral_left_m > 0 else magnitude


@dataclass
class ModelLead:
  status: bool
  dRel: float
  yRel: float
  vRel: float
  prob: float = 0.0


def lead_from_model(model, v_ego):
  if model is None or len(model.leadsV3) == 0 or len(model.leadsV3[0].x) == 0:
    return ModelLead(False, 0.0, 0.0, 0.0)

  lead = model.leadsV3[0]
  model_v_ego = float(v_ego)
  try:
    candidate_v_ego = float(model.velocity.x[0])
    if math.isfinite(candidate_v_ego):
      model_v_ego = candidate_v_ego
  except (AttributeError, IndexError, TypeError, ValueError):
    pass

  # modelV2 lateral is +right; Honda's HUD_OBJECTS lateral is +left.
  #
  # dRel is modelV2's camera-frame distance, deliberately NOT converted to openpilot's
  # radar-frame dRel (radard.py subtracts RADAR_TO_CAMERA). HUD_OBJECTS is a camera-authored
  # message and slots 1-9 forward the camera's own camera-frame distances unchanged, so the
  # lead must stay in the same frame or it renders 1.5 m nearer than an equidistant camera
  # track. LAT_SCALE and curve_boost() upstream were also tuned against this unshifted dRel.
  return ModelLead(bool(lead.prob >= LEAD_PROB_ON), float(lead.x[0]), -float(lead.y[0]),
                   float(lead.v[0]) - model_v_ego, float(lead.prob))


def create_hud_object(packer, bus, mux, track):
  values = {"MUX": mux}
  if track is None:
    values.update(INACTIVE)
  else:
    values.update({
      "OBJECT_ID": int(track["object_id"]),
      "IS_LEAD_CAR": int(track["is_lead_car"]),
      "CAR_TYPE": int(track["car_type"]),
      "ROTATION": int(track["rotation"]),
      "LONG_DIST": min(max(track["d_rel"], 0.0), LONG_DIST_MAX_M),
      "LAT_DIST": min(max(track["y_rel"], -LAT_DIST_LIM_M), LAT_DIST_LIM_M),
    })
  return packer.make_can_msg("HUD_OBJECTS", bus, values)


def _stock_track(stock: 'HudObject | None', is_lead_car: bool | None = None):
  """HudObject -> the dict create_hud_object() packs, or None for an inactive slot.
  is_lead_car overrides the camera's flag (the author owns slot 0's lead marker)."""
  if stock is None or not stock.valid:
    return None
  return {
    "d_rel": stock.d_rel,
    "y_rel": stock.y_rel,
    "object_id": stock.object_id,
    "is_lead_car": stock.is_lead_car if is_lead_car is None else is_lead_car,
    "car_type": stock.car_type,
    "rotation": stock.rotation,
  }


def _slot_track(tracks, slot):
  return tracks[slot] if tracks and slot < len(tracks) else None


def forward_hud_object(packer, bus, mux, tracks):
  slot = (mux - 1) % 16
  return create_hud_object(packer, bus, mux, _stock_track(_slot_track(tracks, slot)))


class HudObjectAuthor:
  """Replace the camera lead with openpilot's lead and retain adjacent cars."""

  def __init__(self):
    self._track_id = LeadObjectId()
    self._smoother = LeadSmoother()
    self._lead_id = 0
    self._previous_op_id = 0
    self._lead_on = False
    self._lead_hold: ModelLead | None = None
    self._lead_seen_time = -1e9

  def _gate_lead(self, lead: ModelLead, now: float) -> ModelLead:
    threshold = LEAD_PROB_OFF if self._lead_on else LEAD_PROB_ON
    if lead.prob >= threshold:
      self._lead_on = True
      self._lead_hold = lead
      self._lead_seen_time = now
      return lead if lead.status else ModelLead(True, lead.dRel, lead.yRel, lead.vRel, lead.prob)
    if self._lead_on and self._lead_hold is not None and now - self._lead_seen_time < LEAD_HOLD_S:
      held = self._lead_hold
      return ModelLead(True, held.dRel + held.vRel * (now - self._lead_seen_time), held.yRel, held.vRel, held.prob)
    self._lead_on = False
    self._lead_hold = None
    return ModelLead(False, 0.0, 0.0, 0.0)

  def _lead_object_id(self, status, op_id, stock_lead_id, in_use):
    if not status:
      self._lead_id = 0
    elif stock_lead_id is not None:
      self._lead_id = stock_lead_id
    elif self._lead_id == 0 or op_id != self._previous_op_id or self._lead_id in in_use:
      next_id = self._lead_id % MAX_OBJECT_ID + 1
      while next_id in in_use:
        next_id = next_id % MAX_OBJECT_ID + 1
      self._lead_id = next_id
    self._previous_op_id = op_id
    return self._lead_id

  def create(self, packer, bus, lead, tracks, mux: int, now: float):
    lead = self._gate_lead(lead, now)
    op_id = self._track_id.update(lead.status, lead.dRel, lead.vRel, now)

    stock_lead = None
    in_use = set()
    for track in tracks or ():
      if not track.valid:
        continue
      if track.is_lead_car:
        stock_lead = track
      elif track.slot != 0:
        in_use.add(track.object_id)

    stock_lead_id = stock_lead.object_id if stock_lead is not None else None
    lead_id = self._lead_object_id(lead.status, op_id, stock_lead_id, in_use)
    lateral_scale = LAT_SCALE * lane_path.curve_boost(lead.dRel)
    d_rel, y_rel = self._smoother.update(lead.dRel, lateral_scale * lead.yRel, lead.vRel, lead_id, now)

    slot = (mux - 1) % 16
    if slot == 0 and lead.status:
      track = {
        "d_rel": d_rel,
        "y_rel": y_rel,
        "object_id": lead_id,
        "is_lead_car": 1,
        "car_type": stock_lead.car_type if stock_lead is not None else CAR_TYPE_CAR,
        "rotation": stock_lead.rotation if stock_lead is not None else lead_rotation(y_rel / lateral_scale),
      }
    else:
      stock = _slot_track(tracks, slot)
      # the camera's own lead is dropped from its slot; slot 0 carries openpilot's instead
      track = _stock_track(stock, is_lead_car=0) if stock is not None and not stock.is_lead_car else None
    return create_hud_object(packer, bus, mux, track)
