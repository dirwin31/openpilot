import unittest

import numpy as np

from openpilot.tools.full_frame.smooth import acceleration_basis
from openpilot.tools.full_frame.trajectory import TIMES


class TestSmooth(unittest.TestCase):
  def test_constant_acceleration_integrates_to_half_at_squared(self):
    result = acceleration_basis() @ np.ones(6)
    np.testing.assert_allclose(result, .5*TIMES**2, atol=5e-6)

  def test_linear_acceleration_integrates_to_t_cubed_over_six(self):
    from openpilot.tools.full_frame.smooth import KNOTS
    result = acceleration_basis() @ KNOTS
    np.testing.assert_allclose(result, TIMES**3/6, atol=2e-5)

  def test_initial_position_and_velocity(self):
    result = acceleration_basis(np.array([0., 1e-5])) @ np.ones(6)
    self.assertEqual(result[0], 0)
    self.assertLess(abs(result[1]/1e-5), 1e-4)

  def test_basis_is_nonnegative(self):
    self.assertGreaterEqual(acceleration_basis().min(), -1e-6)

  def test_out_of_domain_rejected(self):
    with self.assertRaises(ValueError):
      acceleration_basis(np.array([11.]))


if __name__ == '__main__':
  unittest.main()
