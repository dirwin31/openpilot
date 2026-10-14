"""The shipped paired warp remains runnable with Jetlink's graph optimization."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
from tinygrad.uop.ops import Ops
from jetlink.openpilot import warp


def paired_steps():
  copy = NS(op=Ops.CALL, src=(NS(op=Ops.COPY),))
  kernel = NS(op=Ops.AFTER, src=(NS(op=Ops.CALL, src=(NS(op=Ops.PROGRAM),)),))
  return (copy, kernel, copy, kernel)


def test_valid_paired_capture_keeps_full_replay_and_transform_updates():
  class Tensor:
    def __init__(self, array, **kwargs):
      self.array = array
      self.uop = NS(base=self)

    def realize(self):
      return self

    @staticmethod
    def from_blob(address, shape, **kwargs):
      return Tensor(np.zeros(shape, dtype=np.uint8))

  output = NS(device='QCOM', as_memoryview=lambda **kwargs: memoryview(bytearray(64)))
  capture = Mock(ret=NS(uop=NS(base=NS(buffer=output))), _linear=NS(src=paired_steps()))
  jit = Mock(captured=capture)
  log = Mock()
  with patch('tinygrad.tensor.Tensor', Tensor), patch('tinygrad.device.Device', {'QCOM': NS(synchronize=Mock())}), \
       patch.object(warp, '_make_coherent'), \
       patch.object(warp, '_graph_alone', side_effect=warp.UnsupportedWarpCapture('interleaved capture')):
    model = warp.Warp(jit, 64, log)
    assert model._replay is capture
    model.start(100, 200, np.eye(3) * 2, np.eye(3) * 3)
  capture.assert_called_once()
  inputs = capture.call_args.args[0]
  np.testing.assert_array_equal(inputs[3].array, np.eye(3) * 2)
  np.testing.assert_array_equal(inputs[1].array, np.eye(3) * 3)
  log.warning.assert_called_once()


def test_unrecognized_capture_does_not_bypass_fast_path_validation():
  assert warp._paired_capture(NS(_linear=NS(src=paired_steps())))
  assert not warp._paired_capture(NS(_linear=NS(src=paired_steps()[1:])))
  steps = list(paired_steps())
  steps[1] = NS(op=Ops.AFTER, src=(NS(op=Ops.CALL, src=(NS(op=Ops.COPY),)),))
  assert not warp._paired_capture(NS(_linear=NS(src=steps)))
