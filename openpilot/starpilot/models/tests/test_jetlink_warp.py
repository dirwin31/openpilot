"""The shipped paired warp remains runnable with Jetlink's graph optimization."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest
from tinygrad.uop.ops import Ops
from jetlink.openpilot import warp


@pytest.mark.parametrize('layout', ['catalog', 'native', 'adapted_catalog', 'adapted_native', 'empty'])
def test_fallback_reset_clears_history_without_replacing_buffers(layout):
  from tinygrad import Tensor

  queue = Tensor([7., 8.], device='CPU').realize()
  host = np.ones(12, dtype=np.uint8)
  desire = np.ones(8, dtype=np.float32)
  keeper = NS(input_queues={} if layout == 'empty' else {'history': queue})
  if layout.endswith('native'):
    keeper.state_pairs = {'history': 'next_history'}
    keeper.packed_input = host
  else:
    keeper.numpy_inputs = {'host': host}
  model = NS(prev_desire=desire, adapter=keeper) if layout.startswith('adapted') else keeper
  model.prev_desire = desire
  reset = warp.prepare_reset(model)
  buffer = queue.uop.base.buffer
  for _ in range(2):
    queue.assign(9).realize()
    host[:] = 5
    desire[:] = 3
    reset()
    assert queue.uop.base.buffer is buffer
    if layout != 'empty':
      np.testing.assert_array_equal(queue.numpy(), [0., 0.])
    assert not host.any() and not desire.any()


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


def paired_warp(fast_writes):
  """A Warp over a paired capture whose full replay writes 0x5a into the output,
  and a _PairedReplay stand-in that writes `fast_writes`."""
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
  output = NS(device='QCOM', as_memoryview=lambda allow_zero_copy=False: None, _host_mv=lambda: mapped)
  capture = Mock(ret=NS(uop=NS(base=NS(buffer=output))), _linear=NS(src=paired_steps()))
  capture.side_effect = lambda inputs, var_vals: mapped.__setitem__(slice(None), bytes([0x5a]) * 64)
  fast = Mock(side_effect=lambda inputs, var_vals: mapped.__setitem__(slice(None), bytes([fast_writes]) * 64))
  log = Mock()
  with patch('tinygrad.tensor.Tensor', Tensor), patch('tinygrad.device.Device', {'QCOM': NS(synchronize=Mock())}), \
       patch.object(warp, '_make_coherent'), patch.object(warp, '_PairedReplay', return_value=fast), \
       patch.object(warp, '_graph_alone', side_effect=warp.UnsupportedWarpCapture('interleaved capture')):
    model = warp.Warp(Mock(captured=capture), 64, log)
  return model, capture, fast, log


def test_paired_capture_replays_directly_when_it_warps_identically():
  model, capture, fast, log = paired_warp(0x5a)
  assert model._replay is fast
  assert fast.call_count == 3 and capture.call_count == 3   # the self-check, frame for frame
  log.warning.assert_called_once_with('jetlink: warp replays its paired capture directly')


def test_paired_capture_keeps_full_replay_when_the_direct_one_differs():
  model, capture, fast, log = paired_warp(0x00)
  assert model._replay is capture
  assert 'warped a frame differently' in str(log.warning.call_args)


def test_paired_replay_copies_before_its_kernels(monkeypatch):
  import tinygrad.engine.realize as realize
  order = []
  def buffer(name, host_value):
    storage = NS(host=bytearray(4))
    buf = NS(device='QCOM' if name.startswith('dest') else 'NPY', allocator=NS(dev=NS(synchronize=lambda: order.append(f'sync {name}'))),
             get_storage=lambda: storage, ensure_allocated=lambda: buf, host=storage.host)
    buf.host[:] = host_value
    return buf
  pairs = [(buffer('dest_tfm', b'\0' * 4), buffer('tfm', b'tfm!')), (buffer('dest_big', b'\0' * 4), buffer('big', b'big!'))]
  steps = paired_steps()
  monkeypatch.setattr(realize, 'resolve_params', lambda step, inputs: step)
  copies = iter(pairs)
  monkeypatch.setattr(realize, 'unwrap_multi', lambda step, params: [(next(copies), {})])
  monkeypatch.setattr(realize, 'link_linear', lambda linear, allow_cache: ('kernels', linear.src))
  monkeypatch.setattr(realize, 'run_linear', lambda linear, var_vals, input_uops, jit: order.append(('run', linear, jit)))
  captured = NS(_linear=NS(src=steps, replace=lambda src: NS(src=src)))
  replay = warp._PairedReplay(captured, ('inputs',))
  replay(('frame inputs',), {})
  assert [bytes(d.host) for d, _ in pairs] == [b'tfm!', b'big!']
  assert order[-1] == ('run', ('kernels', steps[1::2]), True)
  assert all(isinstance(o, str) for o in order[:-1])   # every copy, then the kernels
