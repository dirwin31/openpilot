import unittest

import numpy as np

from openpilot.tools.full_frame.geometry import legacy_visibility
from openpilot.tools.full_frame.driving import old_view_only
from openpilot.tools.full_frame.trajectory import motion_target, rotation, TIMES


class TestTrajectory(unittest.TestCase):
  def setUp(self):
    self.times = np.arange(241)*.05
    self.orientations = np.zeros((241, 3))
    self.velocities = np.tile([10., 0, 0], (241, 1))
    self.valid = np.ones(241, dtype=bool)

  def target(self, anchor=1, calibration=(0., 0., 0.)):
    return motion_target(self.times, self.orientations, self.velocities, self.valid, anchor, calibration)

  def test_straight_motion_metres_and_origin(self):
    target, speed = self.target()
    np.testing.assert_allclose(target[:, 0], 10*TIMES, atol=1e-5)
    np.testing.assert_allclose(target[:, 1], 0, atol=1e-5)
    np.testing.assert_allclose(speed, [[10.]])
    np.testing.assert_array_equal(target[0], [0, 0])

  def test_world_yaw_does_not_rotate_local_straight_path(self):
    self.orientations[:, 2] = np.pi/2
    target, _ = self.target()
    np.testing.assert_allclose(target[:, 0], 10*TIMES, atol=1e-5)
    np.testing.assert_allclose(target[:, 1], 0, atol=1e-5)

  def test_turn_has_positive_right_displacement(self):
    self.orientations[:, 2] = self.times*.1
    target, _ = self.target()
    self.assertGreater(target[-1, 1], 40)
    self.assertLess(target[-1, 0], 90)

  def test_calibration_transform(self):
    target, _ = self.target(calibration=(0, 0, np.pi/2))
    np.testing.assert_allclose(target[:, 0], 0, atol=1e-5)
    np.testing.assert_allclose(target[:, 1], -10*TIMES, atol=1e-5)

  def test_invalid_future_and_short_horizon_rejected(self):
    self.assertIsNone(self.target(anchor=4))
    self.valid[40] = False
    self.assertIsNone(self.target())

  def test_speed_input_uses_past_only(self):
    self.velocities[21:] = [20, 0, 0]
    _, speed = self.target(anchor=1.01)
    self.assertEqual(speed.item(), 10)

  def test_rotation_is_orthonormal(self):
    r = rotation([.1, .2, .3])[0]
    np.testing.assert_allclose(r.T@r, np.eye(3), atol=1e-12)

  def test_ablation_preserves_old_region(self):
    masks = np.zeros((4, 2, 2), dtype=bool)
    masks[:, 0, 0] = True
    image = np.full((1, 24, 2, 2), 77, dtype=np.uint8)
    masked = old_view_only(image, masks)
    np.testing.assert_array_equal(masked[:, :, 0, 0], image[:, :, 0, 0])
    self.assertEqual(masked[0, 0, 1, 1], 16)
    self.assertEqual(masked[0, 4, 1, 1], 128)

  def test_legacy_visibility_projects_crop(self):
    # A 1024x512 source fit at half size; the old 512x256 view sees its top-left quarter.
    mask = legacy_visibility(np.eye(3), np.diag([.5, .5, 1]), 512, 256)
    self.assertTrue(mask[0, 0])
    self.assertFalse(mask[-1, -1])
    self.assertLess(mask.mean(), .26)
    self.assertGreater(mask.mean(), .24)


if __name__ == '__main__':
  unittest.main()
