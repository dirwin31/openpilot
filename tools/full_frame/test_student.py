"""Run with a working tinygrad backend, e.g. DEV=CPU."""
import unittest

import numpy as np
from tinygrad import Tensor
from tinygrad.helpers import Context
from tinygrad.nn.state import get_parameters

from openpilot.tools.full_frame.student import FullFrameDriver


class TestStudent(unittest.TestCase):
  def test_smooth_trajectory_gradient_and_origin(self):
    model = FullFrameDriver(32, 64, 4, 'smooth_accel')
    image = Tensor(np.random.default_rng(3).uniform(16, 235, (1, 24, 16, 32)).astype(np.float32))
    prediction = model(image, Tensor([[10.]]))
    np.testing.assert_array_equal(prediction.numpy()[:, 0], [[0, 0]])
    gradient = prediction.square().mean().gradient(get_parameters(model.encoder)[0])[0].numpy()
    self.assertTrue(np.isfinite(gradient).all())
    self.assertGreater(np.abs(gradient).sum(), 0)

  def test_trajectory_gradient_reaches_new_camera_regions_and_encoder(self):
    Tensor.manual_seed(42)
    model = FullFrameDriver(32, 64, 4)
    image = Tensor(np.random.default_rng(2).uniform(16, 235, (1, 24, 16, 32)).astype(np.float32))
    with Context(TRAINING=1):
      prediction = model(image, Tensor([[10.]]))
      self.assertEqual(prediction.shape, (1, 33, 2))
      np.testing.assert_array_equal(prediction.numpy()[:, 0], [[0, 0]])
      loss = prediction.square().mean()
      input_grad, encoder_grad = loss.gradient(image, get_parameters(model.encoder)[0])
      input_grad, encoder_grad = input_grad.numpy(), encoder_grad.numpy()
    outer = np.ones((16, 32), dtype=bool)
    outer[4:12, 8:24] = False
    self.assertTrue(np.isfinite(input_grad).all())
    self.assertGreater(np.count_nonzero(input_grad[0, :, outer]), 0)
    self.assertTrue(np.isfinite(encoder_grad).all())
    self.assertGreater(np.abs(encoder_grad).sum(), 0)


if __name__ == '__main__':
  unittest.main()
