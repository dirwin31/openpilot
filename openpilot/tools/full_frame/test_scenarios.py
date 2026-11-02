import unittest

import numpy as np

from openpilot.tools.full_frame.scenarios import inward_deviation, scenario_metrics
from openpilot.tools.full_frame.trajectory import TIMES


class TestScenarios(unittest.TestCase):
  def test_inside_shortcut_is_detected(self):
    angle = np.linspace(0, np.pi/2, 33)
    reference = np.stack((10*np.sin(angle), 10*(1-np.cos(angle))), axis=1)
    chord = np.stack((np.linspace(0, 10, 33), np.linspace(0, 10, 33)), axis=1)
    self.assertGreater(inward_deviation(chord, reference), 2.8)
    self.assertLess(inward_deviation(chord, reference), 3.1)

  def test_left_and_right_turns_are_symmetric(self):
    angle = np.linspace(0, np.pi/2, 33)
    r = np.stack((10*np.sin(angle), 10*(1-np.cos(angle))), axis=1)
    p = np.stack((np.linspace(0, 10, 33), np.linspace(0, 10, 33)), axis=1)
    self.assertAlmostEqual(inward_deviation(p, r), inward_deviation(p*[1, -1], r*[1, -1]))

  def test_straight_path_is_not_called_a_corner(self):
    path = np.stack((TIMES*10, np.zeros(33)), axis=1)
    self.assertIsNone(inward_deviation(path, path))

  def test_perfect_recorded_stop_does_not_imply_stop_sign_detection(self):
    path = np.stack((10*np.minimum(TIMES, 3), np.zeros(33)), axis=1)[None]
    result = scenario_metrics(path, path, [10.])
    self.assertEqual(result['recorded_stop_approaches'], 1)
    self.assertEqual(result['mean_predicted_speed_at_recorded_stop_mps'], 0)
    self.assertTrue(result['stop_sign_detection'].startswith('not evaluated'))


if __name__ == '__main__':
  unittest.main()
