#!/usr/bin/env python3
"""Export the research driver to ONNX and check numerical parity with tinygrad.

This deliberately uses its own trajectory-only ABI. It cannot be installed as a
Dom driving artifact: perception heads and the controls contract are incomplete.
"""
import argparse
import json
from pathlib import Path

import numpy as np


def main():
  import onnx
  from onnx import TensorProto, helper, numpy_helper
  from tinygrad import Tensor
  from tinygrad.nn.onnx import OnnxRunner
  from tinygrad.nn.state import get_state_dict, safe_load, load_state_dict
  from openpilot.tools.full_frame.student import FullFrameDriver
  from openpilot.tools.full_frame.trajectory import TIMES
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('checkpoint', type=Path)
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  if args.output.exists():
    raise ValueError('Use a new export output path')
  config = json.loads((args.checkpoint / 'config.json').read_text())
  head = config.get('head', 'waypoints')
  model = FullFrameDriver(config['height'], config['width'], config['channels'], head)
  load_state_dict(model, safe_load(str(args.checkpoint / 'driver.safetensors')))
  initializers, nodes = [], []
  def constant(name, array):
    initializers.append(numpy_helper.from_array(np.asarray(array), name))
    return name
  for name, weight in get_state_dict(model).items():
    constant(name, weight.numpy())
  def node(op, inputs, output, **attrs):
    nodes.append(helper.make_node(op, inputs, [output], **attrs))
    return output
  divisor = constant('pixel_scale', np.float32(127.5))
  one = constant('one', np.float32(1))
  x = node('Cast', ['full'], 'pixels_float', to=TensorProto.FLOAT)
  x = node('Div', [x, divisor], 'scaled')
  x = node('Sub', [x, one], 'normalized')
  for i in range(4):
    x = node('Conv', [x, f'encoder.convs.{i}.weight', f'encoder.convs.{i}.bias'], f'conv{i}',
             strides=[2, 2], pads=[1, 1, 1, 1])
    x = node('Relu', [x], f'relu{i}')
  x = node('Flatten', [x], 'flat', axis=1)
  x = node('Gemm', [x, 'encoder.head.weight', 'encoder.head.bias'], 'feature_raw', transB=1)
  norm = node('ReduceL2', [x], 'feature_norm', axes=[1], keepdims=1)
  epsilon = constant('epsilon', np.float32(1e-6))
  norm = node('Max', [norm, epsilon], 'safe_norm')
  x = node('Div', [x, norm], 'features')
  speed_scale = constant('speed_scale', np.float32(30))
  speed = node('Div', ['speed', speed_scale], 'speed_scaled')
  x = node('Concat', [x, speed], 'context', axis=1)
  x = node('Gemm', [x, 'hidden.weight', 'hidden.bias'], 'policy_hidden', transB=1)
  x = node('Relu', [x], 'policy_relu')
  x = node('Gemm', [x, 'path.weight', 'path.bias'], 'residual_flat', transB=1)
  shape = constant('path_shape', np.array([1, 6 if head == 'smooth_accel' else 33, 2], dtype=np.int64))
  x = node('Reshape', [x, shape], 'residual')
  if head == 'smooth_accel':
    from openpilot.tools.full_frame.smooth import acceleration_basis
    x = node('Tanh', [x], 'bounded_acceleration')
    scale = constant('acceleration_scale', np.array([3., 2.], dtype=np.float32))
    x = node('Mul', [x, scale], 'acceleration')
    basis = constant('acceleration_integral', acceleration_basis()[None])
    x = node('MatMul', [basis, x], 'residual_metres')
  else:
    origin_index = constant('origin_index', np.array([0], dtype=np.int64))
    origin = node('Gather', [x, origin_index], 'residual_origin', axis=1)
    x = node('Sub', [x, origin], 'anchored_residual')
    scale = constant('path_scale', np.float32(10))
    x = node('Mul', [x, scale], 'residual_metres')
  times = constant('path_times', TIMES.astype(np.float32).reshape(1, 33))
  baseline = node('MatMul', ['speed', times], 'baseline_x_flat')
  axes = constant('expand_axis', np.array([2], dtype=np.int64))
  baseline = node('Unsqueeze', [baseline, axes], 'baseline_x')
  zero = constant('zero', np.float32(0))
  zeros = node('Mul', [baseline, zero], 'baseline_y')
  baseline = node('Concat', [baseline, zeros], 'baseline_xy', axis=2)
  node('Add', [x, baseline], 'trajectory')
  inputs = [helper.make_tensor_value_info('full', TensorProto.UINT8,
             [1, 24, config['height']//2, config['width']//2]), helper.make_tensor_value_info('speed', TensorProto.FLOAT, [1, 1])]
  outputs = [helper.make_tensor_value_info('trajectory', TensorProto.FLOAT, [1, 33, 2])]
  graph = helper.make_graph(nodes, 'full_frame_research_driver', inputs, outputs, initializers)
  exported = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 17)], producer_name='Dom full-frame offline research')
  helper.set_model_props(exported, {'scope': 'offline trajectory-only research', 'config': json.dumps(config)})
  onnx.checker.check_model(exported)
  onnx.save(exported, args.output)
  rng = np.random.default_rng(42)
  images = rng.integers(0, 256, (1, 24, config['height']//2, config['width']//2), dtype=np.uint8)
  speed_value = np.array([[15.]], dtype=np.float32)
  native = model(Tensor(images), Tensor(speed_value)).numpy()
  imported = OnnxRunner(args.output)({'full': Tensor(images), 'speed': Tensor(speed_value)})['trajectory'].numpy()
  np.testing.assert_allclose(imported, native, atol=1e-4, rtol=1e-5)
  result = {'onnx_checker': 'passed', 'tinygrad_roundtrip': 'passed',
            'maximum_absolute_error_m': float(np.max(np.abs(native-imported))), 'modeld_compatible': False}
  args.output.with_suffix('.verification.json').write_text(json.dumps(result, indent=2)+'\n')
  print(json.dumps(result, indent=2))


if __name__ == '__main__':
  main()
