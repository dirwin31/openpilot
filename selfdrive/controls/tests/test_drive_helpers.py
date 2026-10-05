import pytest

from openpilot.selfdrive.controls.lib.drive_helpers import ModelCurvatureRamp, get_kona_non_scc_lateral_active, get_lateral_active


def test_get_lateral_active_requires_enabled_without_aol():
  assert not get_lateral_active(False, True, False, False, False, False, False, True)


def test_get_lateral_active_allows_aol_while_disabled():
  assert get_lateral_active(False, False, True, False, False, False, False, True)


def test_get_lateral_active_does_not_retry_after_a_latched_temporary_fault():
  assert not get_lateral_active(False, False, True, False, False, False, False, True, True)
  assert get_lateral_active(False, False, True, False, False, False, False, True, False)


def test_kona_non_scc_aol_waits_for_driver_steering_to_release():
  assert not get_kona_non_scc_lateral_active(
    False, False, True, False, False, False, False, True, True, False,
  )
  assert get_kona_non_scc_lateral_active(
    False, False, True, False, False, False, False, True, False, False,
  )
  assert get_kona_non_scc_lateral_active(
    False, False, True, False, False, False, False, True, True, True,
  )


def test_kona_non_scc_aol_gate_does_not_change_fault_or_normal_lateral_gates():
  assert not get_kona_non_scc_lateral_active(
    False, False, True, True, False, False, False, True, False, False,
  )
  assert get_kona_non_scc_lateral_active(
    True, True, False, False, False, False, False, True, True, False,
  )


def test_kona_non_scc_recovers_after_temporary_fault_clears():
  assert not get_kona_non_scc_lateral_active(
    False, False, True, True, False, False, False, True, False, True,
  )
  assert not get_kona_non_scc_lateral_active(
    False, False, True, False, False, False, False, True, True, False,
  )
  assert get_kona_non_scc_lateral_active(
    False, False, True, False, False, False, False, True, False, False,
  )


def test_get_lateral_active_honors_manual_pause_while_cruise_is_engaged():
  assert not get_lateral_active(True, True, False, False, False, False, False, False)


def test_model_curvature_ramp_spreads_each_model_step_across_the_frame():
  ramp = ModelCurvatureRamp()
  ramp.reset(0.0)
  values = [ramp.update(0.01, model_updated=(i == 0)) for i in range(5)]
  assert values == pytest.approx([0.002, 0.004, 0.006, 0.008, 0.01])
  assert ramp.update(0.01, model_updated=False) == pytest.approx(0.01)


def test_model_curvature_ramp_starts_new_step_from_current_held_value():
  ramp = ModelCurvatureRamp()
  ramp.reset(0.0)
  ramp.update(0.01, model_updated=True)
  ramp.update(0.01, model_updated=False)          # held at 0.004
  assert ramp.update(0.0, model_updated=True) == pytest.approx(0.004 * 0.8)
