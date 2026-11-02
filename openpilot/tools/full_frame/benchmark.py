#!/usr/bin/env python3
"""Benchmark the research trajectory network on one recorded full-view sample.

This is inference only. It does not connect to modeld, CAN, or vehicle controls.
Camera decode, transport, warp and recurrent production models are not measured.
"""
import argparse
import json
import os
from pathlib import Path
import time

import numpy as np


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('checkpoint', type=Path)
  parser.add_argument('sample', type=Path, help='One prepared numbered .npz example')
  parser.add_argument('--device', required=True, help='Explicit tinygrad backend, e.g. CPU, AMD or METAL')
  parser.add_argument('--iterations', type=int, default=100)
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  if args.iterations < 5 or args.output.exists():
    parser.error('Need at least five iterations and a new output file')
  os.environ['DEV'] = args.device
  from tinygrad import Tensor, TinyJit, Device
  from tinygrad.helpers import GlobalCounters
  from tinygrad.nn.state import safe_load, load_state_dict
  from openpilot.tools.full_frame.student import FullFrameDriver
  config = json.loads((args.checkpoint / 'config.json').read_text())
  model = FullFrameDriver(config['height'], config['width'], config['channels'], config.get('head', 'waypoints'))
  load_state_dict(model, safe_load(str(args.checkpoint / 'driver.safetensors')))
  with np.load(args.sample, allow_pickle=False) as sample:
    pixels = sample['full'].copy()
    # Match driving.py: speed is the last historical frame's scalar in m/s.
    speed = float(sample['speed'].item())
  image = Tensor(pixels, device=args.device).realize()
  speed_tensor = Tensor(np.array([[speed]], dtype=np.float32), device=args.device).realize()
  run = TinyJit(lambda x, v: model(x, v).realize())
  for _ in range(5):
    output = run(image, speed_tensor)
    Device[args.device].synchronize()
  timings = []
  for _ in range(args.iterations):
    start = time.perf_counter()
    output = run(image, speed_tensor)
    Device[args.device].synchronize()
    timings.append((time.perf_counter() - start)*1000)
  prediction = output.numpy()
  if prediction.shape != (1, 33, 2) or not np.isfinite(prediction).all():
    raise ValueError('Invalid trajectory output')
  result = {'scope': 'Research trajectory network compute only; not production modeld or Mountain Dew v3',
            'device': str(image.device), 'iterations': args.iterations,
            'input_shape': list(pixels.shape), 'output_shape': list(prediction.shape),
            'latency_ms': {key: float(value) for key, value in zip(('p50', 'p95', 'p99', 'max'),
                          (*np.percentile(timings, [50, 95, 99]), max(timings)))},
            'tinygrad_tracked_allocated_bytes': GlobalCounters.mem_used,
            'compute_only_over_50ms_fraction': float(np.mean(np.array(timings)>50)),
            'production_ready': False}
  args.output.write_text(json.dumps(result, indent=2)+'\n')
  print(json.dumps(result, indent=2))


if __name__ == '__main__':
  main()
