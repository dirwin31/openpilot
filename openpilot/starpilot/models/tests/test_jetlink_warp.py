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

  mapped = memoryview(bytearray(64))

  def as_memoryview(allow_zero_copy=False):   # this fork's tinygrad: no force_zero_copy
    raise AssertionError("the warp output must not be copied")
  output = NS(device='QCOM', as_memoryview=as_memoryview, _host_mv=lambda: mapped)
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
  assert model.output is mapped


def test_unrecognized_capture_does_not_bypass_fast_path_validation():
  assert warp._paired_capture(NS(_linear=NS(src=paired_steps())))
  assert not warp._paired_capture(NS(_linear=NS(src=paired_steps()[1:])))
  steps = list(paired_steps())
  steps[1] = NS(op=Ops.AFTER, src=(NS(op=Ops.CALL, src=(NS(op=Ops.COPY),)),))
  assert not warp._paired_capture(NS(_linear=NS(src=steps)))


def test_zero_copy_view_matches_this_tinygrad():
  """Against the real Buffer: 0.8.5's force_zero_copy failed every join on the comma."""
  from tinygrad.device import Buffer
  from tinygrad.dtype import dtypes
  buffer = Buffer('CPU', 8, dtypes.uint8, initial_value=bytes(range(8))).ensure_allocated()
  view = warp._zero_copy_view(buffer, sync=True)
  assert bytes(view) == bytes(range(8))
  view[:] = bytes(8)
  assert bytes(buffer.as_memoryview()) == bytes(8)   # the mapping itself, not a copy


def test_zero_copy_view_refuses_a_copy():
  unmapped = NS(device='QCOM', as_memoryview=lambda allow_zero_copy=False: memoryview(bytearray(4)), _host_mv=lambda: None)
  import pytest
  with pytest.raises(RuntimeError, match='no host mapping'):
    warp._zero_copy_view(unmapped, sync=False)
