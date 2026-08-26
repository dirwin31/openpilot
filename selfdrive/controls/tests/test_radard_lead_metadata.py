from types import SimpleNamespace

from openpilot.selfdrive.controls.radard import KalmanParams, Track, _model_lead_v_lat


def test_model_lead_lateral_velocity_uses_predicted_heading():
  lead = SimpleNamespace(t=[0.0, 0.5], y=[1.0, 2.0])

  assert _model_lead_v_lat(lead) == -2.0


def test_radar_track_propagates_lateral_velocity_and_object_class():
  track = Track(42, 20.0, KalmanParams(0.05))
  track.update(30.0, 3.0, -1.0, 20.0, True, yv_rel=1.25, object_class="motorcycle")

  lead = track.get_RadarState()
  assert lead["vLat"] == 1.25
  assert lead["objectClass"] == "motorcycle"
