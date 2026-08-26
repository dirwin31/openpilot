from types import SimpleNamespace

import pytest

from opendbc.can import CANPacker
from opendbc.car.honda import hud_objects
from opendbc.car.honda.tests import FakePacker


def radar_lead(status=True, d_rel=30.0, y_rel=0.0, v_rel=-2.0, v_lat=0.0,
               object_class="unknown", track_id=-1):
  return SimpleNamespace(status=status, dRel=d_rel, yRel=y_rel, vRel=v_rel, vLat=v_lat,
                         objectClass=object_class, radarTrackId=track_id)


def radar_state(lead_one=None, lead_two=None):
  radar_errors = SimpleNamespace(to_dict=dict)
  return SimpleNamespace(leadOne=lead_one or radar_lead(), leadTwo=lead_two or radar_lead(False),
                         radarErrors=radar_errors)


def plan(source):
  return SimpleNamespace(longitudinalPlanSource=source)


def starpilot_radar_state(left=None, right=None, stopped=None):
  return SimpleNamespace(
    leadLeft=left or radar_lead(False),
    leadRight=right or radar_lead(False),
    adjacentStopped=stopped or SimpleNamespace(status=False, dRel=0.0, yRel=0.0, radarTrackId=-1),
  )


def empty_tracks():
  return [hud_objects.HudObject(slot, 0, 0.0, 0.0, False, False)
          for slot in range(hud_objects.NUM_SLOTS)]


def test_radar_lead_converts_only_longitudinal_frame_and_preserves_metadata():
  valid, converted, _, _ = hud_objects.select_openpilot_leads(
    radar_state(radar_lead(d_rel=35.0, y_rel=1.5, v_rel=-4.0, v_lat=1.2,
                           object_class="truck", track_id=42)), plan("lead0"),
  )

  assert valid
  assert converted.status
  assert converted.dRel == pytest.approx(36.52)
  assert converted.yRel == 1.5
  assert converted.vRel == -4.0
  assert converted.vLat == 1.2
  assert converted.carType == hud_objects.CAR_TYPE_TRUCK
  assert converted.radarTrackId == 42


@pytest.mark.parametrize(("source", "controlling_distance", "secondary_distance"), [
  ("lead0", 21.52, 41.52),
  ("lead1", 41.52, 21.52),
])
def test_planner_selects_controlling_and_other_filtered_lead(source, controlling_distance, secondary_distance):
  state = radar_state(radar_lead(d_rel=20.0), radar_lead(d_rel=40.0))
  valid, controlling, secondary, _ = hud_objects.select_openpilot_leads(state, plan(source))

  assert valid
  assert controlling.dRel == pytest.approx(controlling_distance)
  assert secondary.dRel == pytest.approx(secondary_distance)


@pytest.mark.parametrize("source", ["cruise", "e2e"])
def test_cruise_and_e2e_have_no_highlighted_or_secondary_vehicle(source):
  valid, controlling, secondary, additional = hud_objects.select_openpilot_leads(
    radar_state(radar_lead(), radar_lead(d_rel=50.0)), plan(source),
  )

  assert valid
  assert not controlling.status
  assert not secondary.status
  assert not additional


def test_missing_or_unknown_inputs_request_complete_stock_passthrough():
  assert not hud_objects.select_openpilot_leads(None, plan("lead0"))[0]
  assert not hud_objects.select_openpilot_leads(radar_state(), None)[0]
  assert not hud_objects.select_openpilot_leads(radar_state(), plan("lead2"))[0]
  malformed = radar_state(radar_lead(d_rel=float("nan")), radar_lead(False))
  assert not hud_objects.select_openpilot_leads(malformed, plan("lead0"))[0]
  errors = SimpleNamespace(to_dict=lambda: {"canError": True})
  assert not hud_objects.select_openpilot_leads(
    SimpleNamespace(leadOne=radar_lead(), leadTwo=radar_lead(False), radarErrors=errors), plan("lead0"),
  )[0]


@pytest.mark.parametrize(("v_lat", "v_rel", "v_ego", "expected"), [
  (0.0, 0.0, 25.0, 0),
  (2.0, 0.0, 20.0, -1),
  (-2.0, 0.0, 20.0, 1),
  (20.0, -30.0, 20.0, -6),
])
def test_lead_rotation_uses_velocity_heading(v_lat, v_rel, v_ego, expected):
  assert hud_objects.lead_rotation_from_velocity(v_lat, v_rel, v_ego) == expected


def test_selects_and_deduplicates_starpilot_adjacent_leads():
  main = radar_lead(d_rel=20.0, track_id=11)
  duplicate_left = radar_lead(d_rel=20.0, y_rel=3.0, track_id=11)
  right = radar_lead(d_rel=35.0, y_rel=-3.2, object_class="motorcycle", track_id=12)
  stopped = SimpleNamespace(status=True, dRel=50.0, yRel=3.4, radarTrackId=13, objectClass="truck")

  valid, controlling, secondary, adjacent = hud_objects.select_openpilot_leads(
    radar_state(main), plan("lead0"), starpilot_radar_state(duplicate_left, right, stopped), v_ego=15.0,
  )

  assert valid and controlling.status and not secondary.status
  assert len(adjacent) == 3
  assert not adjacent[0].status
  assert adjacent[1].status and adjacent[1].carType == hud_objects.CAR_TYPE_MOTORCYCLE
  assert adjacent[2].status and adjacent[2].vRel == -15.0
  assert adjacent[2].carType == hud_objects.CAR_TYPE_TRUCK


def test_camera_tracks_expire_after_half_second():
  tracker = hud_objects.HudObjectTracker()
  values = {
    "MUX": [2], "OBJECT_ID": [12], "LONG_DIST": [18.0], "LAT_DIST": [3.2],
    "IS_LEAD_CAR": [0], "CAR_TYPE": [7], "ROTATION": [-1],
  }
  cp_cam = SimpleNamespace(
    vl_all={"HUD_OBJECTS": values},
    ts_nanos={"HUD_OBJECTS": {"MUX": 1_000_000_000}},
  )
  tracker.update(cp_cam)

  assert tracker.snapshot(1_500_000_000)[1].valid
  assert not tracker.snapshot(1_500_000_001)[1].valid


def test_camera_parking_sentinel_with_nonzero_id_is_not_a_vehicle():
  tracker = hud_objects.HudObjectTracker()
  values = {
    "MUX": [1], "OBJECT_ID": [12], "LONG_DIST": [196.0], "LAT_DIST": [0.0],
    "IS_LEAD_CAR": [0], "CAR_TYPE": [-1], "ROTATION": [-128],
  }
  tracker.update(SimpleNamespace(
    vl_all={"HUD_OBJECTS": values}, ts_nanos={"HUD_OBJECTS": {"MUX": 1_000_000_000}},
  ))
  assert not tracker.snapshot(1_100_000_000)[0].valid


def test_controlling_lead_uses_slot_zero_and_matched_honda_identity():
  tracks = empty_tracks()
  tracks[4] = hud_objects.HudObject(4, 9, 38.0, 0.4, True, True, car_type=-7, rotation=2)
  controlling = hud_objects.ModelLead(True, 42.0, 0.5, -1.0)
  slots = hud_objects.HudObjectAuthor().compose(
    controlling, hud_objects.no_lead(), tracks, now=10.0,
  )

  assert slots[0]["object_id"] == 9
  assert slots[0]["is_lead_car"]
  assert slots[0]["car_type"] == -7
  assert slots[0]["rotation"] == 2
  assert slots[4] is None


def test_matched_secondary_reuses_honda_slot_and_is_not_duplicated():
  tracks = empty_tracks()
  tracks[6] = hud_objects.HudObject(6, 12, 33.0, -0.2, False, True, car_type=6, rotation=-1)
  secondary = hud_objects.ModelLead(True, 35.0, -0.1, 0.0)
  slots = hud_objects.HudObjectAuthor().compose(
    hud_objects.no_lead(), secondary, tracks, now=10.0,
  )

  assert slots[6]["object_id"] == 12
  assert not slots[6]["is_lead_car"]
  assert slots[6]["car_type"] == 6
  assert sum(slot is not None and slot["object_id"] == 12 for slot in slots) == 1


def test_unmatched_secondary_uses_first_free_nonlead_slot():
  tracks = empty_tracks()
  tracks[1] = hud_objects.HudObject(1, 12, 18.0, 3.2, False, True, car_type=7, rotation=-1)
  secondary = hud_objects.ModelLead(True, 50.0, -1.0, 0.0)
  slots = hud_objects.HudObjectAuthor().compose(
    hud_objects.ModelLead(True, 25.0, 0.0, -1.0), secondary, tracks, now=10.0,
  )

  assert slots[0]["is_lead_car"]
  assert slots[1]["object_id"] == 12
  assert slots[2] is not None and not slots[2]["is_lead_car"]


def test_unmatched_adjacent_leads_fill_remaining_slots_with_distinct_icons_and_heading():
  additional = [
    hud_objects.ModelLead(True, 30.0, 3.1, 0.0, 0.0, hud_objects.CAR_TYPE_MOTORCYCLE, 20),
    hud_objects.ModelLead(True, 45.0, -3.2, 0.0, 2.0, hud_objects.CAR_TYPE_TRUCK, 21),
    hud_objects.ModelLead(True, 60.0, 3.3, -15.0, 0.0, hud_objects.CAR_TYPE_UNKNOWN, 22),
  ]
  slots = hud_objects.HudObjectAuthor().compose(
    hud_objects.no_lead(), hud_objects.no_lead(), empty_tracks(), now=10.0,
    additional=additional, v_ego=20.0,
  )

  assert slots[0] is None
  rendered = [slot for slot in slots if slot is not None]
  assert len(rendered) == 3
  assert [slot["car_type"] for slot in rendered] == [6, -7, 0]
  assert [slot["rotation"] for slot in rendered] == [0, -1, 0]


def test_unmatched_honda_lead_is_retained_as_normal_vehicle():
  tracks = empty_tracks()
  tracks[0] = hud_objects.HudObject(0, 9, 80.0, 0.0, True, True, car_type=-7, rotation=2)
  slots = hud_objects.HudObjectAuthor().compose(
    hud_objects.ModelLead(True, 25.0, 0.0, -1.0),
    hud_objects.no_lead(), tracks, now=10.0,
  )

  assert slots[0]["is_lead_car"]
  assert slots[1]["object_id"] == 9
  assert not slots[1]["is_lead_car"]


def test_honda_objects_take_priority_over_secondary_when_slots_are_full():
  tracks = [hud_objects.HudObject(slot, slot + 1, 50.0 + slot * 10.0, 3.0, slot == 0, True)
            for slot in range(hud_objects.NUM_SLOTS)]
  slots = hud_objects.HudObjectAuthor().compose(
    hud_objects.ModelLead(True, 20.0, 0.0, -1.0),
    hud_objects.ModelLead(True, 180.0, -3.0, 0.0), tracks, now=10.0,
  )

  assert slots[0]["is_lead_car"]
  assert all(slot is not None for slot in slots)
  assert not any(slot["d_rel"] == pytest.approx(180.0) for slot in slots[1:])
  assert {slot["object_id"] for slot in slots[1:]} == set(range(2, 11))


def test_unmatched_openpilot_ids_do_not_collide_with_honda_ids_or_each_other():
  tracks = empty_tracks()
  tracks[5] = hud_objects.HudObject(5, 1, 100.0, 5.0, False, True)
  slots = hud_objects.HudObjectAuthor().compose(
    hud_objects.ModelLead(True, 20.0, 0.0, 0.0),
    hud_objects.ModelLead(True, 40.0, 0.0, 0.0), tracks, now=10.0,
  )
  ids = [slot["object_id"] for slot in slots if slot is not None]

  assert len(ids) == len(set(ids))
  assert 1 in ids


def test_unmatched_controlling_id_does_not_collide_with_matched_secondary_id():
  tracks = empty_tracks()
  tracks[3] = hud_objects.HudObject(3, 1, 60.0, 0.0, False, True)
  slots = hud_objects.HudObjectAuthor().compose(
    hud_objects.ModelLead(True, 20.0, 0.0, 0.0),
    hud_objects.ModelLead(True, 60.5, 0.0, 0.0), tracks, now=10.0,
  )
  ids = [slot["object_id"] for slot in slots if slot is not None]

  assert len(ids) == len(set(ids))
  assert slots[3]["object_id"] == 1


def test_no_controlling_lead_preserves_all_ten_honda_objects_and_slot_zero():
  tracks = [hud_objects.HudObject(slot, slot + 1, 20.0 + slot * 10.0, 0.0, slot == 0, True)
            for slot in range(hud_objects.NUM_SLOTS)]
  unavailable = hud_objects.no_lead()
  slots = hud_objects.HudObjectAuthor().compose(unavailable, unavailable, tracks, now=10.0)

  assert all(slot is not None for slot in slots)
  assert [slot["object_id"] for slot in slots] == list(range(1, 11))
  assert not any(slot["is_lead_car"] for slot in slots)


def test_secondary_matched_to_slot_zero_stays_in_slot_zero_without_a_controlling_lead():
  tracks = empty_tracks()
  tracks[0] = hud_objects.HudObject(0, 9, 40.0, 0.2, True, True, car_type=6, rotation=-1)
  slots = hud_objects.HudObjectAuthor().compose(
    hud_objects.no_lead(),
    hud_objects.ModelLead(True, 41.0, 0.1, 0.0), tracks, now=10.0,
  )

  assert slots[0]["object_id"] == 9
  assert not slots[0]["is_lead_car"]
  assert slots[0]["car_type"] == 6
  assert all(slot is None for slot in slots[1:])


def test_duplicate_honda_ids_are_rendered_only_once():
  tracks = empty_tracks()
  tracks[2] = hud_objects.HudObject(2, 14, 25.0, 1.0, False, True)
  tracks[7] = hud_objects.HudObject(7, 14, 26.0, 1.1, False, True)
  slots = hud_objects.HudObjectAuthor().compose(
    hud_objects.no_lead(),
    hud_objects.no_lead(), tracks, now=10.0,
  )

  assert sum(slot is not None and slot["object_id"] == 14 for slot in slots) == 1
  assert slots[2]["object_id"] == 14


def test_inactive_forwarding_preserves_complete_honda_slot():
  tracks = empty_tracks()
  tracks[3] = hud_objects.HudObject(3, 17, 22.0, -2.0, True, True, car_type=-7, rotation=4)
  _, _, values = hud_objects.forward_hud_object(FakePacker(), 0, mux=4, tracks=tracks)

  assert values == {
    "MUX": 4, "OBJECT_ID": 17, "IS_LEAD_CAR": 1, "CAR_TYPE": -7,
    "ROTATION": 4, "LONG_DIST": 22.0, "LAT_DIST": -2.0,
  }


def test_inactive_forwarding_does_not_apply_openpilot_distance_clamps():
  tracks = empty_tracks()
  tracks[1] = hud_objects.HudObject(1, 17, 194.8, -204.8, False, True)
  _, _, values = hud_objects.forward_hud_object(FakePacker(), 0, mux=2, tracks=tracks)

  assert values["LONG_DIST"] == 194.8
  assert values["LAT_DIST"] == -204.8


def test_hud_object_packs_with_expected_extended_address():
  packer = CANPacker("honda_bosch_radarless_generated")
  message = hud_objects.create_hud_object(packer, 0, mux=1, track=None)

  assert message[0] == 0x6CD5557
  assert len(message[1]) == 8
