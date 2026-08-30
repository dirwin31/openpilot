import time
from types import SimpleNamespace

import pytest

from test_dashboard_stats import FakeParams, MODULE_DIR, _install_server_import_stubs


def _load_server_module():
  import importlib.util
  import sys

  _install_server_import_stubs()
  spec = importlib.util.spec_from_file_location("override_plots_server", MODULE_DIR / "the_galaxy.py")
  module = importlib.util.module_from_spec(spec)
  sys.modules["override_plots_server"] = module
  spec.loader.exec_module(module)
  return module


the_galaxy = _load_server_module()


def _controls_state(curvature=0.0):
  return SimpleNamespace(curvature=curvature)


def _model_v2(desired_curvature=0.0):
  return SimpleNamespace(action=SimpleNamespace(desiredCurvature=desired_curvature))


def _car_state(steering_pressed=False, steering_torque=0.0):
  return SimpleNamespace(steeringPressed=steering_pressed, steeringTorque=steering_torque)


def test_lateral_command_converts_curvature_to_lateral_accel():
  # At 20 m/s a 0.01 rad/m curvature is 0.01 * 400 = 4.0 m/s^2 of lateral accel.
  result = the_galaxy._extract_lateral_command_values(
    _controls_state(curvature=0.008),
    _model_v2(desired_curvature=0.01),
    _car_state(steering_pressed=True, steering_torque=1.5),
    20.0,
  )

  assert result["modelCurvature"] == 0.01
  assert result["modelLateralAccel"] == 4.0
  assert result["driverLateralAccel"] == 3.2
  assert result["steeringPressed"] is True
  assert result["steeringTorque"] == 1.5
  assert result["speed"] == 20.0


def test_lateral_command_model_intent_survives_missing_action():
  # Missing/garbage fields must not raise; they fall back to zero.
  result = the_galaxy._extract_lateral_command_values(
    _controls_state(curvature=0.005),
    SimpleNamespace(),   # no .action
    SimpleNamespace(),   # no steering fields
    10.0,
  )

  assert result["modelCurvature"] == 0.0
  assert result["modelLateralAccel"] == 0.0
  assert result["driverLateralAccel"] == 0.5
  assert result["steeringPressed"] is False


def test_finalize_override_episode_averages_and_signs():
  # Two samples: model asks ~2.0, driver does ~2.6 → driver steers tighter (positive gap).
  episode = {
    "startTime": 100.0,
    "endTime": 101.4,
    "sampleCount": 2,
    "modelSum": 4.0,
    "driverSum": 5.2,
    "speedSum": 40.0,
    "modelCurvatureSum": 0.02,   # positive curvature → RIGHT turn in this fork
    "modelPeak": 2.2,
    "driverPeak": 2.8,
    "dirGapSum": 1.2,            # +0.6 tighter per sample
    "gapPeak": 0.7,
  }

  record = the_galaxy._finalize_override_episode(episode)

  assert record["durationS"] == 1.4
  assert record["avgSpeed"] == 20.0
  assert record["direction"] == "right"
  assert record["modelLatAccelMean"] == 2.0
  assert record["driverLatAccelMean"] == 2.6
  assert record["gapMean"] == 0.6   # positive = tighter
  assert record["gapPeak"] == 0.7
  assert record["sampleCount"] == 2


def test_directional_gap_reads_tighter_in_both_turn_directions():
  # Right turn (positive accel in this fork): driver does more → tighter → positive.
  assert the_galaxy._directional_gap(1.0, 1.4) == pytest.approx(0.4)
  # Left turn (negative accel): driver does more (more negative) → still tighter → positive.
  assert the_galaxy._directional_gap(-1.0, -1.5) == pytest.approx(0.5)
  # Left turn, driver eases off (less negative) → wider → negative.
  assert the_galaxy._directional_gap(-1.0, -0.6) == pytest.approx(-0.4)


def test_finalize_override_episode_left_turn_reports_tighter_as_positive():
  # Left turn (negative curvature/accel) where the driver steers tighter: gapMean must be positive.
  episode = {
    "startTime": 0.0, "endTime": 0.5, "sampleCount": 1,
    "modelSum": -1.0, "driverSum": -1.5, "speedSum": 15.0,
    "modelCurvatureSum": -0.01, "modelPeak": -1.0, "driverPeak": -1.5,
    "dirGapSum": 0.5, "gapPeak": 0.5,
  }

  record = the_galaxy._finalize_override_episode(episode)

  assert record["direction"] == "left"   # negative curvature → left in this fork
  assert record["gapMean"] == 0.5         # positive = tighter, regardless of turn direction


def test_commit_override_episode_appends_valid_and_skips_short():
  # This is the path used when the drive ends mid-override (offroad finalize) and on release.
  with the_galaxy._override_log_lock:
    the_galaxy._override_log["episodes"] = []

  short = {
    "startTime": 0.0, "endTime": 0.05, "sampleCount": 3,   # below _OVERRIDE_LOG_MIN_DURATION_S
    "modelSum": 3.0, "driverSum": 3.9, "speedSum": 60.0,
    "modelCurvatureSum": 0.03, "modelPeak": 1.0, "driverPeak": 1.3,
    "dirGapSum": 0.9, "gapPeak": 0.4,
  }
  the_galaxy._commit_override_episode(short)
  with the_galaxy._override_log_lock:
    assert len(the_galaxy._override_log["episodes"]) == 0   # momentary blip dropped

  valid = dict(short, endTime=1.0)
  the_galaxy._commit_override_episode(valid)
  with the_galaxy._override_log_lock:
    episodes = the_galaxy._override_log["episodes"]
    assert len(episodes) == 1
    assert episodes[0]["direction"] == "right"   # positive curvature
    assert episodes[0]["gapMean"] == 0.3         # dirGapSum 0.9 / 3 samples


def _make_client(monkeypatch):
  assert the_galaxy._import_galaxy_web_symbols()
  monkeypatch.setattr(the_galaxy, "params", FakeParams({"IsOnroad": False}))
  app = the_galaxy.Flask(
    f"override_plots_{time.monotonic_ns()}",
    template_folder=str(MODULE_DIR / "templates"),
    static_folder=str(MODULE_DIR / "assets"),
  )
  the_galaxy.setup(app)
  return app.test_client()


def test_live_endpoint_exposes_model_vs_driver_fields(monkeypatch):
  # Seed the shared state so we don't depend on the background worker (no live cereal in tests).
  with the_galaxy._plots_lock:
    the_galaxy._plots_state.update({
      "timestamp": time.time(),
      "modelLateralAccel": 1.25,
      "driverLateralAccel": 1.60,
      "steeringPressed": True,
      "steeringTorque": 0.8,
    })

  client = _make_client(monkeypatch)
  response = client.get("/api/plots/overrides")  # also starts the recorder without erroring
  assert response.status_code == 200

  response = client.get("/api/plots/live")
  assert response.status_code == 200
  payload = response.get_json()
  for key in ("modelLateralAccel", "driverLateralAccel", "steeringPressed", "steeringTorque", "latModelSource"):
    assert key in payload
  assert payload["modelLateralAccel"] == 1.25
  assert payload["driverLateralAccel"] == 1.60
  assert payload["steeringPressed"] is True


def test_overrides_endpoint_returns_episode_list(monkeypatch):
  with the_galaxy._override_log_lock:
    the_galaxy._override_log["driveId"] = 7
    the_galaxy._override_log["episodes"] = [
      the_galaxy._finalize_override_episode({
        "startTime": 5.0, "endTime": 6.0, "sampleCount": 1,
        "modelSum": 1.0, "driverSum": 1.4, "speedSum": 18.0,
        "modelCurvatureSum": 0.01, "modelPeak": 1.0, "driverPeak": 1.4,
        "dirGapSum": 0.4, "gapPeak": 0.4,
      }),
    ]

  client = _make_client(monkeypatch)
  response = client.get("/api/plots/overrides")
  assert response.status_code == 200
  payload = response.get_json()

  assert payload["driveId"] == 7
  assert payload["isOnroad"] is False
  assert len(payload["episodes"]) == 1
  assert payload["episodes"][0]["direction"] == "right"   # positive curvature → right
  assert payload["episodes"][0]["gapMean"] == 0.4
