"""Twice-integrated linear acceleration basis with zero initial residual velocity."""
import numpy as np

from openpilot.tools.full_frame.trajectory import TIMES

KNOTS = np.array([0., 1., 2., 4., 7., 10.])


def acceleration_basis(times=TIMES, knots=KNOTS):
  times, knots = np.asarray(times, dtype=np.float64), np.asarray(knots, dtype=np.float64)
  if times.ndim != 1 or knots.ndim != 1 or len(knots) < 2 or knots[0] != 0 or np.any(np.diff(knots) <= 0):
    raise ValueError('Need increasing knots starting at zero and a 1D time grid')
  if np.any(times < 0) or np.any(times > knots[-1]):
    raise ValueError('Time outside acceleration basis domain')
  result = np.zeros((len(times), len(knots)))
  for j, (left, right) in enumerate(zip(knots[:-1], knots[1:])):
    end = np.clip(times, left, right)
    # Integral of (t-s)*(a+b*s) ds over the interval covered at each t.
    integral_constant = times*(end-left) - (end**2-left**2)/2
    integral_linear = times*(end**2-left**2)/2 - (end**3-left**3)/3
    result[:, j] += (right*integral_constant-integral_linear)/(right-left)
    result[:, j+1] += (integral_linear-left*integral_constant)/(right-left)
  return result.astype(np.float32)
