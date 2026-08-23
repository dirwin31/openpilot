"""Track, merge, and render vehicles on the 2022+ Civic cluster."""

import math
from dataclasses import dataclass, replace

from opendbc.can.parser import CANParser


NUM_SLOTS = 10
TRACK_TIMEOUT_NANOS = 500_000_000
RADAR_TO_CAMERA = 1.52  # mirrors selfdrive/controls/radard.py; undoes the shift radard applies to dRel
MATCH_LONGITUDINAL_M = 5.0
MATCH_LATERAL_M = 1.5
LONG_DIST_CAP_M = 195.0
LONG_DIST_MAX_M = 194.0
LONG_DIST_STOCK_MAX_M = 196.9
LAT_DIST_MIN_M = -204.8
LAT_DIST_MAX_M = 204.7


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
    if not values["MUX"]:
      return
    last_nanos = int(cp_cam.ts_nanos["HUD_OBJECTS"]["MUX"])
    signals = ("MUX", "OBJECT_ID", "LONG_DIST", "LAT_DIST", "IS_LEAD_CAR", "CAR_TYPE", "ROTATION")
    rows = list(zip(*(values[signal] for signal in signals), strict=True))
    for mux, object_id, d_rel, y_rel, is_lead, car_type, rotation in rows:
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
  "LONG_DIST": LONG_DIST_STOCK_MAX_M,
  "LAT_DIST": LAT_DIST_MAX_M,
}

CAR_TYPE_CAR = 7
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
  """A lead from radard, shifted into the camera frame HUD_OBJECTS uses."""
  status: bool
  dRel: float
  yRel: float
  vRel: float


def no_lead() -> ModelLead:
  return ModelLead(False, 0.0, 0.0, 0.0)


def _lead_from_radar(lead) -> tuple[bool, ModelLead]:
  status = bool(lead.status)
  d_rel = float(lead.dRel) + RADAR_TO_CAMERA
  y_rel = float(lead.yRel)
  v_rel = float(lead.vRel)
  finite = all(math.isfinite(value) for value in (d_rel, y_rel, v_rel))
  if status and not finite:
    return False, no_lead()
  return True, ModelLead(status, d_rel if status else 0.0, y_rel if status else 0.0, v_rel if status else 0.0)


def select_openpilot_leads(radar_state, longitudinal_plan) -> tuple[bool, ModelLead, ModelLead]:
  """Return whether the inputs are usable, the lead we are following, and the other one."""
  unavailable = no_lead()
  if radar_state is None or longitudinal_plan is None:
    return False, unavailable, unavailable
  lead_one_valid, lead_one = _lead_from_radar(radar_state.leadOne)
  lead_two_valid, lead_two = _lead_from_radar(radar_state.leadTwo)
  source = str(longitudinal_plan.longitudinalPlanSource)
  if any(radar_state.radarErrors.to_dict().values()):
    return False, unavailable, unavailable
  if not lead_one_valid or not lead_two_valid:
    return False, unavailable, unavailable

  if source == "lead0":
    return True, lead_one, lead_two
  if source == "lead1":
    return True, lead_two, lead_one
  if source in ("cruise", "e2e"):
    return True, unavailable, unavailable
  return False, unavailable, unavailable


def create_hud_object(packer, bus, mux, track):
  values = {"MUX": mux}
  if track is None:
    values.update(INACTIVE)
  else:
    stock = track.get("stock", False)
    d_rel = track["d_rel"] if stock else min(max(track["d_rel"], 0.0), LONG_DIST_MAX_M)
    y_rel = track["y_rel"] if stock else min(max(track["y_rel"], LAT_DIST_MIN_M), LAT_DIST_MAX_M)
    values.update({
      "OBJECT_ID": int(track["object_id"]),
      "IS_LEAD_CAR": int(track["is_lead_car"]),
      "CAR_TYPE": int(track["car_type"]),
      "ROTATION": int(track["rotation"]),
      "LONG_DIST": d_rel,
      "LAT_DIST": y_rel,
    })
  return packer.make_can_msg("HUD_OBJECTS", bus, values)


def _stock_track(stock: HudObject | None, is_lead_car: bool | None = None):
  if stock is None or not stock.valid:
    return None
  return {
    "d_rel": stock.d_rel,
    "y_rel": stock.y_rel,
    "object_id": stock.object_id,
    "is_lead_car": stock.is_lead_car if is_lead_car is None else is_lead_car,
    "car_type": stock.car_type,
    "rotation": stock.rotation,
    "stock": True,
  }


def _slot_track(tracks, slot):
  return tracks[slot] if tracks and slot < len(tracks) else None


def forward_hud_object(packer, bus, mux, tracks):
  slot = (mux - 1) % 16
  return create_hud_object(packer, bus, mux, _stock_track(_slot_track(tracks, slot)))


class HudObjectAuthor:
  """Blend openpilot's leads into the full list of cars Honda's camera reports."""

  def __init__(self):
    self._track_ids = [LeadObjectId(), LeadObjectId()]
    self._smoothers = [LeadSmoother(), LeadSmoother()]
    self._display_ids = [0, 0]
    self._previous_op_ids = [0, 0]

  @staticmethod
  def _match(lead: ModelLead, tracks, excluded: set[int]) -> HudObject | None:
    if not lead.status:
      return None
    candidates = [track for track in tracks or () if track.valid and track.slot not in excluded and
                  abs(track.d_rel - lead.dRel) <= MATCH_LONGITUDINAL_M and
                  abs(track.y_rel - lead.yRel) <= MATCH_LATERAL_M]
    if not candidates:
      return None
    return min(candidates, key=lambda track: abs(track.d_rel - lead.dRel) + abs(track.y_rel - lead.yRel))

  def _object_id(self, index: int, lead: ModelLead, matched: HudObject | None, now: float, in_use: set[int]) -> int:
    op_id = self._track_ids[index].update(lead.status, lead.dRel, lead.vRel, now)
    if not lead.status:
      self._display_ids[index] = 0
    elif matched is not None:
      self._display_ids[index] = matched.object_id
    elif (self._display_ids[index] == 0 or op_id != self._previous_op_ids[index] or
          self._display_ids[index] in in_use):
      candidate = self._display_ids[index] % MAX_OBJECT_ID + 1
      while candidate in in_use:
        candidate = candidate % MAX_OBJECT_ID + 1
      self._display_ids[index] = candidate
    self._previous_op_ids[index] = op_id
    return self._display_ids[index]

  def _lead_track(self, index: int, lead: ModelLead, matched: HudObject | None,
                  is_lead_car: bool, now: float, in_use: set[int]):
    if not lead.status:
      self._track_ids[index].update(False, 0.0, 0.0, now)
      return None
    object_id = self._object_id(index, lead, matched, now, in_use)
    d_rel, y_rel = self._smoothers[index].update(lead.dRel, lead.yRel, lead.vRel, object_id, now)
    in_use.add(object_id)
    return {
      "d_rel": d_rel,
      "y_rel": y_rel,
      "object_id": object_id,
      "is_lead_car": is_lead_car,
      "car_type": matched.car_type if matched is not None else CAR_TYPE_CAR,
      "rotation": matched.rotation if matched is not None else lead_rotation(y_rel),
    }

  @staticmethod
  def _first_free(slots) -> int | None:
    return next((slot for slot in range(1, NUM_SLOTS) if slots[slot] is None), None)

  def compose(self, controlling: ModelLead, secondary: ModelLead, tracks, now: float):
    valid_tracks = []
    seen_honda_ids: set[int] = set()
    for track in tracks or ():
      if track.valid and track.object_id not in seen_honda_ids:
        valid_tracks.append(track)
        seen_honda_ids.add(track.object_id)
    controlling_match = self._match(controlling, valid_tracks, set())
    excluded = {controlling_match.slot} if controlling_match is not None else set()
    secondary_match = self._match(secondary, valid_tracks, excluded)
    matched_slots = {track.slot for track in (controlling_match, secondary_match) if track is not None}

    slots = [None] * NUM_SLOTS
    in_use = {track.object_id for track in valid_tracks}

    slots[0] = self._lead_track(0, controlling, controlling_match, True, now, in_use)

    relocate: list[dict] = []
    for track in valid_tracks:
      if track.slot in matched_slots:
        continue
      # openpilot owns the highlighted car while it is controlling speed. Under
      # cruise and e2e, Honda's cars still show but nothing is highlighted.
      rendered = _stock_track(track, is_lead_car=False)
      # Each track owns its own slot, so the only one that can already be taken is
      # slot 0 - the lead we are following - and Honda's own lead then has to move.
      if slots[track.slot] is None:
        slots[track.slot] = rendered
      else:
        relocate.append(rendered)

    secondary_render = self._lead_track(1, secondary, secondary_match, False, now, in_use)
    if secondary_render is not None and secondary_match is not None:
      if slots[secondary_match.slot] is None:
        slots[secondary_match.slot] = secondary_render
      else:
        relocate.insert(0, secondary_render)

    # Honda's cars get first claim on the free slots; an unmatched second lead
    # only takes what is left over.
    for rendered in relocate:
      free = self._first_free(slots)
      if free is None:
        break
      slots[free] = rendered

    if secondary_render is not None and secondary_match is None:
      free = self._first_free(slots)
      if free is not None:
        slots[free] = secondary_render
    return slots

  def create(self, packer, bus, controlling, tracks, mux: int, now: float, secondary=None):
    slots = self.compose(controlling, secondary or no_lead(), tracks, now)
    return create_hud_object(packer, bus, mux, slots[(mux - 1) % 16])
