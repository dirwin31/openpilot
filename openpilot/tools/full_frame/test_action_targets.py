import unittest

import numpy as np

from openpilot.tools.full_frame.action_targets import calibrated_motion,action_proxy,aligned_future
from openpilot.tools.full_frame.trajectory import rotation


class TestActionTargets(unittest.TestCase):
  def test_nonzero_camera_mount_rotation_recovers_calibrated_motion(self):
    rpy = [.1,-.2,.3]
    matrix = rotation(rpy)[0]
    expected = ([15,0,0],[0,0,-.1],[-2,0,0])
    actual = calibrated_motion(*(matrix@np.array(v) for v in expected),rpy)
    for value,reference in zip(actual,expected):
      np.testing.assert_allclose(value,reference,atol=1e-12)

  def test_action_encoding_uses_current_speed_even_when_future_speed_changes(self):
    target,mask = action_proxy(20.,[10.,0,0],[0,0,.1],-2.)
    self.assertAlmostEqual(target[0,0]/20**2,.1/10)
    self.assertEqual(target[0,1],-2.)
    self.assertTrue(mask.all())

  def test_stationary_lateral_target_is_masked_without_losing_longitudinal_stop(self):
    target,mask = action_proxy(0.,[0,0,0],[0,0,.01],0.)
    np.testing.assert_array_equal(mask,[[False,True]])
    np.testing.assert_array_equal(target,[[0,0]])

  def test_delayed_targets_do_not_cross_engagement_changes(self):
    times = np.arange(10)*.05
    values = np.stack((times,2*times),axis=1)
    valid = np.ones(10,bool)
    np.testing.assert_allclose(aligned_future(times,values,valid,valid,.05,.125),[.175,.35])
    eligible = valid.copy();eligible[2]=False
    self.assertIsNone(aligned_future(times,values,valid,eligible,.05,.125))

  def test_missing_future_or_pose_gap_is_rejected(self):
    times = np.array([0.,.05,.1,.4,.45])
    values = times[:,None]
    valid = np.ones(5,bool)
    self.assertIsNone(aligned_future(times,values,valid,valid,.05,.3))
    self.assertIsNone(aligned_future(times,values,valid,valid,.4,.3))
