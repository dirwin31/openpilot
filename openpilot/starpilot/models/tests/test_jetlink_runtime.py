"""Scheduling and control authority remain local across the Jetlink upgrade."""
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jetlink.openpilot.model_state import JetlinkModelState, PROVING_FRAMES, Trips
from openpilot.starpilot.models.jetlink_runtime import configure_gpu, in_control


@pytest.mark.parametrize('device', ['tizi', 'mici', 'tici', 'pc'])
@pytest.mark.parametrize('mode', [None, 0, 1, 2, True, b'2', 3])
def test_gpu_priority_only_changes_for_supported_enabled_jetlink(monkeypatch, device, mode):
  monkeypatch.setenv('QCOM_PRIORITY', '12')
  params = SimpleNamespace(get=lambda key: mode)
  enabled = device in ('tizi', 'mici') and type(mode) is int and mode in (1, 2)
  assert configure_gpu(params, device_type=device) is enabled
  import os
  assert os.environ['QCOM_PRIORITY'] == ('11' if enabled else '12')


@pytest.mark.parametrize('guard', ['chestnut', 'recovery'])
def test_gpu_priority_preserved_for_chestnut_and_recovery(monkeypatch, guard):
  monkeypatch.setenv('QCOM_PRIORITY', '12')
  assert not configure_gpu(SimpleNamespace(get=lambda key: 2), device_type='mici', **{guard: True})
  import os
  assert os.environ['QCOM_PRIORITY'] == '12'


class Controls:
  def __init__(self):
    self.fresh = True
    self.messages = {'selfdriveState': SimpleNamespace(enabled=False, active=False),
                     'carControl': SimpleNamespace(latActive=False, longActive=False)}

  def all_alive(self, services):
    assert services == ['selfdriveState', 'carControl']
    return self.fresh

  def all_valid(self, services):
    assert services == ['selfdriveState', 'carControl']
    return True

  def __getitem__(self, key):
    return self.messages[key]


def test_swap_is_blocked_for_any_control_axis_and_unknown_engagement():
  controls = Controls()
  assert not in_control(controls)
  for service, field in (('selfdriveState', 'enabled'), ('selfdriveState', 'active'),
                         ('carControl', 'latActive'), ('carControl', 'longActive')):
    setattr(controls[service], field, True)
    assert in_control(controls)
    setattr(controls[service], field, False)
  controls.fresh = False
  assert in_control(controls)


def modeld_controls(*, valid=True):
  # modeld's own SubMaster, read once a frame at 20 Hz while the controls publish at 100 Hz
  import openpilot.cereal.messaging as messaging
  sm = messaging.SubMaster(['carState', 'selfdriveState', 'carControl'])
  for frame in range(200):
    msgs = []
    for service in ('carState', 'selfdriveState', 'carControl'):
      msg = messaging.new_message(service)
      msg.valid = valid
      msgs.append(msg.as_reader())
    sm.update_msgs(frame * 0.05, msgs)
  return sm


def test_disengaged_controls_read_at_modelds_rate_allow_the_swap():
  assert not in_control(modeld_controls())


def test_invalid_or_silent_controls_block_the_swap():
  assert in_control(modeld_controls(valid=False))
  sm = modeld_controls()
  sm.update_msgs(10.0 + 1.0, [])
  assert in_control(sm)


def model_holds():
  model = JetlinkModelState.__new__(JetlinkModelState)
  model._frame_id = PROVING_FRAMES + 1
  model._holds = deque()
  model._holds_in_a_row = 0
  model.trips = Trips()
  return model


def test_intermittent_holds_allow_40_but_sustained_lag_hands_back():
  model = model_holds()
  with patch('jetlink.openpilot.model_state.time.monotonic', return_value=100.):
    for _ in range(40):
      assert model._note_hold(True) is None
      assert model._note_hold(False) is None
    assert model._note_hold(True) == 'held 41 frames in 10 s'
  model = model_holds()
  for _ in range(4):
    assert model._note_hold(True) is None
  assert model._note_hold(True) == 'held 5 frames in a row'


def test_holds_expire_and_post_join_proving_remains_strict():
  model = model_holds()
  with patch('jetlink.openpilot.model_state.time.monotonic', return_value=100.):
    for _ in range(40):
      model._note_hold(True)
      model._note_hold(False)
  with patch('jetlink.openpilot.model_state.time.monotonic', return_value=111.):
    assert model._note_hold(True) is None
    assert len(model._holds) == 1
  model._frame_id = 4
  assert model._note_hold(True) == 'held frame 4 of the first 20'


@pytest.mark.parametrize('mode', [0, 1, 2])
def test_jetlink_cable_does_not_select_amd_inference(mode):
  from openpilot.starpilot.models.jetlink_runtime import chestnut_available
  params = SimpleNamespace(get=lambda key: mode)
  assert chestnut_available(params, present=False, cable=lambda: True) is (mode == 0)
  assert chestnut_available(params, present=True, cable=lambda: pytest.fail('cable probed beside a chestnut'))
  assert not chestnut_available(params, present=False, cable=lambda: False)
