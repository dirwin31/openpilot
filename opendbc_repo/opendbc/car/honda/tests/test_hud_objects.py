from types import SimpleNamespace

import pytest

from opendbc.can import CANPacker
from opendbc.car.honda import hud_objects


class FakePacker:
  @staticmethod
  def make_can_msg(name, bus, values):
    return name, bus, values


def test_model_lead_converts_lateral_sign_and_relative_speed():
  lead = SimpleNamespace(prob=0.9, x=[35.0], y=[1.5], v=[20.0])
  model = SimpleNamespace(leadsV3=[lead], velocity=SimpleNamespace(x=[24.0]))

  converted = hud_objects.lead_from_model(model, v_ego=25.0)

  assert converted.status
  # camera-frame distance, matching the camera tracks forwarded into the other slots
  assert converted.dRel == 35.0
  assert converted.yRel == -1.5
  assert converted.vRel == -4.0


def test_model_lead_falls_back_to_car_speed_without_model_velocity():
  lead = SimpleNamespace(prob=0.9, x=[20.0], y=[0.0], v=[12.0])
  converted = hud_objects.lead_from_model(SimpleNamespace(leadsV3=[lead]), v_ego=15.0)

  assert converted.vRel == -3.0


def test_camera_tracks_expire_after_half_second():
  tracker = hud_objects.HudObjectTracker()
  values = {
    "MUX": [2],
    "OBJECT_ID": [12],
    "LONG_DIST": [18.0],
    "LAT_DIST": [3.2],
    "IS_LEAD_CAR": [0],
    "CAR_TYPE": [7],
    "ROTATION": [-1],
  }
  cp_cam = SimpleNamespace(
    vl_all={"HUD_OBJECTS": values},
    ts_nanos={"HUD_OBJECTS": {"MUX": 1_000_000_000}},
  )
  tracker.update(cp_cam)

  assert tracker.snapshot(1_500_000_000)[1].valid
  assert not tracker.snapshot(1_500_000_001)[1].valid


def test_openpilot_lead_replaces_stock_lead_and_reuses_identity():
  tracks = [
    hud_objects.HudObject(0, 9, 30.0, 0.1, True, True, car_type=-7, rotation=2),
    *[hud_objects.HudObject(slot, 0, 0.0, 0.0, False, False) for slot in range(1, hud_objects.NUM_SLOTS)],
  ]
  lead = hud_objects.ModelLead(True, 42.0, 0.5, -1.0, prob=0.9)

  _, bus, values = hud_objects.HudObjectAuthor().create(FakePacker(), 0, lead, tracks, mux=1, now=10.0)

  assert bus == 0
  assert values["OBJECT_ID"] == 9
  assert values["IS_LEAD_CAR"] == 1
  assert values["CAR_TYPE"] == -7
  assert values["LONG_DIST"] == pytest.approx(42.0, abs=0.11)


def test_adjacent_camera_vehicle_is_forwarded():
  tracks = [hud_objects.HudObject(slot, 0, 0.0, 0.0, False, False) for slot in range(hud_objects.NUM_SLOTS)]
  tracks[1] = hud_objects.HudObject(1, 12, 18.0, 3.2, False, True, car_type=7, rotation=-1)
  no_lead = hud_objects.ModelLead(False, 0.0, 0.0, 0.0)

  _, _, values = hud_objects.HudObjectAuthor().create(FakePacker(), 0, no_lead, tracks, mux=2, now=10.0)

  assert values["OBJECT_ID"] == 12
  assert values["IS_LEAD_CAR"] == 0
  assert values["LONG_DIST"] == 18.0
  assert values["LAT_DIST"] == 3.2


def test_stock_lead_is_not_rendered_when_openpilot_has_none():
  tracks = [
    hud_objects.HudObject(0, 9, 30.0, 0.1, True, True),
    *[hud_objects.HudObject(slot, 0, 0.0, 0.0, False, False) for slot in range(1, hud_objects.NUM_SLOTS)],
  ]

  _, _, values = hud_objects.HudObjectAuthor().create(
    FakePacker(), 0, hud_objects.ModelLead(False, 0.0, 0.0, 0.0), tracks, mux=1, now=10.0,
  )

  assert values["OBJECT_ID"] == 0
  assert values["IS_LEAD_CAR"] == 0


def test_hud_object_packs_with_expected_extended_address():
  packer = CANPacker("honda_bosch_radarless_generated")
  message = hud_objects.create_hud_object(packer, 0, mux=1, track=None)

  assert message[0] == 0x6CD5557
  assert len(message[1]) == 8
