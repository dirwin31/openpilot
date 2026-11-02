#!/usr/bin/env python3
"""Train/evaluate full-frame trajectory prediction from recorded future motion."""
import argparse
import json
from pathlib import Path
import time

import numpy as np

from openpilot.tools.full_frame.lab import write_json
from openpilot.tools.full_frame.trajectory import TIMES


def dataset(path):
  manifest = json.loads((path / 'manifest.json').read_text())
  samples = sorted(path.glob('[0-9]*.npz'))
  if manifest.get('sources'):
    if samples:
      raise ValueError('An assembled dataset must not mix local samples with source segments')
    samples = []
    for source in manifest['sources']:
      child, files = dataset(Path(source))
      if child.get('sources'):
        raise ValueError('Nested assembled datasets are not supported')
      if not set(child.get('route_ids', [child['route_id']])) <= set(manifest['route_ids']):
        raise ValueError('Source route is missing from the assembled route IDs')
      for key in ('canvas_size', 'frame_skip', 'camera_size', 'motion_labels'):
        if child[key] != manifest[key]:
          raise ValueError(f'Source {key} differs from assembled manifest')
      if child.get('frame_period', .05) != manifest.get('frame_period', .05):
        raise ValueError('Source frame period differs from assembled manifest')
      samples.extend(files)
  if not manifest.get('motion_labels') or not samples or len(samples) != manifest['samples']:
    raise ValueError('Prepare a capture containing valid livePose future-motion labels first')
  return manifest, samples


def read(path):
  with np.load(path, allow_pickle=False) as sample:
    image, speed, target = sample['full'], sample['speed'], sample['target_path']
    masks = sample['legacy_masks']
  if (target.shape != (1, 33, 2) or speed.shape != (1, 1) or masks.shape != (4, *image.shape[-2:]) or
      not np.isfinite(target).all() or not np.isfinite(speed).all()):
    raise ValueError(f'Invalid driving example: {path}')
  return image, speed, target, masks


def training_distribution(samples, mode):
  """Build weights from training motion only; never inspect evaluation targets."""
  if mode == 'uniform':
    return None, {'mode': mode, 'samples': len(samples)}
  targets, speeds = [], []
  for path in samples:
    with np.load(path, allow_pickle=False) as sample:
      targets.append(sample['target_path'][0]); speeds.append(sample['speed'].item())
  groups = motion_groups(np.asarray(targets), np.asarray(speeds))
  category = np.full(len(samples), 3)
  category[groups['bend_over_15deg']] = 2
  category[groups['stop_within_10s']] = 1
  category[groups['slow_or_stopped_at_anchor']] = 0
  counts = np.bincount(category, minlength=4)
  fractions = np.array([.1, .25, .4, .25])
  weights = fractions[category]/counts[category]
  weights /= weights.sum()
  return weights, {'mode': mode, 'category_order': ['slow_or_stopped', 'stop_approach', 'bend_over_15deg', 'other_moving'],
                   'category_counts': counts.tolist(), 'sampling_mass': [float(weights[category==k].sum()) for k in range(4)]}


def old_view_only(image, masks):
  result = image.copy()
  for frame in range(4):
    for channel in range(6):
      fill = 16 if channel < 4 else 128
      result[0, 6*frame+channel] = np.where(masks[frame], result[0, 6*frame+channel], fill)
  return result


def motion_groups(paths, speeds):
  """Motion-based evaluation slices; these do not label signs or parked cars."""
  final_velocity = (paths[:, -1]-paths[:, -2])/(TIMES[-1]-TIMES[-2])
  final_speed = np.linalg.norm(final_velocity, axis=1)
  heading = np.arctan2(final_velocity[:, 1], final_velocity[:, 0])
  return {'moving_at_anchor': speeds > 2., 'slow_or_stopped_at_anchor': speeds <= 2.,
          'stop_within_10s': (speeds > 2.) & (final_speed < .5),
          'bend_over_15deg': (speeds > 2.) & (final_speed > 2.) & (np.abs(heading) > np.deg2rad(15))}


def measure(model, samples, output):
  from tinygrad import Tensor
  predictions, targets, masked_predictions, baselines, latency, speeds = [], [], [], [], [], []
  for path in samples:
    image, speed, target, masks = read(path)
    start = time.monotonic()
    predictions.append(model(Tensor(image), Tensor(speed)).numpy()[0])
    latency.append(1000*(time.monotonic()-start))
    masked_predictions.append(model(Tensor(old_view_only(image, masks)), Tensor(speed)).numpy()[0])
    targets.append(target[0])
    speeds.append(speed.item())
    baselines.append(np.stack((speed.item()*TIMES, np.zeros(33)), axis=1))
  pred, truth, masked, baseline = map(np.asarray, (predictions, targets, masked_predictions, baselines))
  def metrics(value, expected=truth):
    distance = np.linalg.norm(value-expected, axis=-1)
    return {'average_displacement_m': float(distance[:, 1:].mean()), 'final_displacement_m': float(distance[:, -1].mean()),
      'average_displacement_0_to_3s_m': float(distance[:, (TIMES>0)&(TIMES<=3)].mean()),
      'average_displacement_3_to_5s_m': float(distance[:, (TIMES>3)&(TIMES<=5)].mean()),
      'average_displacement_5_to_10s_m': float(distance[:, TIMES>5].mean()),
      'lateral_rmse_m': float(np.sqrt(np.mean((value[:, 1:, 1]-expected[:, 1:, 1])**2))),
      'longitudinal_rmse_m': float(np.sqrt(np.mean((value[:, 1:, 0]-expected[:, 1:, 0])**2)))}
  groups = motion_groups(truth, np.asarray(speeds))
  from openpilot.tools.full_frame.scenarios import scenario_metrics
  result = {'samples': len(samples), 'full_view': metrics(pred), 'legacy_regions_only': metrics(masked),
    'constant_velocity': metrics(baseline),
    'scenario_proxies': {'full_view': scenario_metrics(pred, truth, speeds),
                         'legacy_regions_only': scenario_metrics(masked, truth, speeds),
                         'constant_velocity': scenario_metrics(baseline, truth, speeds)},
    'new_context_ablation_prediction_change_m': float(np.linalg.norm(pred-masked, axis=-1)[:, 1:].mean()),
    'inference_including_first_compile_ms_p50': float(np.percentile(latency, 50)),
    'inference_including_first_compile_ms_p95': float(np.percentile(latency, 95)),
    'motion_slices': {name: {'samples': int(selection.sum()),
      **({'full_view': metrics(pred[selection], truth[selection]),
          'legacy_regions_only': metrics(masked[selection], truth[selection]),
          'constant_velocity': metrics(baseline[selection], truth[selection])} if selection.any() else {})}
      for name, selection in groups.items()},
    'unlabeled_scenarios': ['corner boundary clearance', 'parked-car clearance', 'stop-sign detection and stop-line compliance'],
    'interpretation': 'Full-view error must beat baselines on held-out drives. Ablation sensitivity alone is insufficient; masking is out of distribution.',
    'scope': 'Open-loop recorded-motion prediction, no closed-loop evaluation or modeld deployment.'}
  write_json(output, result)
  np.savez_compressed(Path(output).with_suffix('.npz'), predictions=pred, target_paths=truth, legacy_region_predictions=masked,
                      constant_velocity=baseline, path_times=TIMES, anchor_speeds=np.asarray(speeds),
                      **{f'group_{name}': selection for name, selection in groups.items()})
  print(json.dumps(result, indent=2))


def train(args):
  from tinygrad import Tensor
  from tinygrad.helpers import Context
  from tinygrad.nn.optim import Adam
  from tinygrad.nn.state import get_parameters, get_state_dict, safe_save, safe_load, load_state_dict
  from openpilot.tools.full_frame.student import FullFrameDriver
  tm, train_files = dataset(args.train)
  vm, validation_files = dataset(args.validation)
  train_routes = set(tm.get('route_ids', [tm['route_id']]))
  validation_routes = set(vm.get('route_ids', [vm['route_id']]))
  if train_routes & validation_routes:
    raise ValueError('Use different whole drives for training and validation')
  if any(tm[k] != vm[k] for k in ('canvas_size', 'frame_skip', 'camera_size', 'motion_labels')):
    raise ValueError('Train/validation camera geometry, history, or label definition differs')
  if tm.get('frame_period', .05) != vm.get('frame_period', .05):
    raise ValueError('Train/validation image timing differs')
  if args.steps < 1 or args.channels < 1:
    raise ValueError('Steps and channels must be positive')
  args.output.mkdir(parents=True, exist_ok=False)
  Tensor.manual_seed(args.seed)
  width, height = tm['canvas_size']
  model = FullFrameDriver(height, width, args.channels, args.head)
  if args.encoder:
    config = json.loads((args.encoder / 'config.json').read_text())
    if [config['width'], config['height'], config['channels']] != [width, height, args.channels]:
      raise ValueError('Warm-up encoder dimensions differ')
    load_state_dict(model.encoder, safe_load(str(args.encoder / 'student.safetensors')))
  optimizer = Adam(get_parameters(model), lr=args.lr)
  rng, losses = np.random.default_rng(args.seed), []
  probabilities, sampling = training_distribution(train_files, args.sampling)
  for step in range(args.steps):
    index = int(rng.integers(len(train_files))) if probabilities is None else int(rng.choice(len(train_files), p=probabilities))
    image, speed, target, masks = read(train_files[index])
    if args.view == 'legacy':
      image = old_view_only(image, masks)
    with Context(TRAINING=1):
      optimizer.zero_grad()
      prediction = model(Tensor(image), Tensor(speed))
      error = ((prediction-Tensor(target)) / Tensor([10., 2.])).abs()[:, 1:]
      quadratic = error.minimum(1.)
      loss = (.5*quadratic.square()+error-quadratic).mean()
      loss.backward()
      optimizer.step()
      value = float(loss.item())
    if not np.isfinite(value):
      raise ValueError('Non-finite trajectory loss')
    losses.append(value)
    if step == 0 or (step+1) % 10 == 0:
      print(json.dumps({'step': step+1, 'trajectory_loss': value}), flush=True)
  safe_save(get_state_dict(model), str(args.output / 'driver.safetensors'))
  config = {'width': width, 'height': height, 'channels': args.channels, 'frame_skip': tm['frame_skip'],
    'camera_size': tm['camera_size'], 'train_route': tm['route_id'], 'validation_route': vm['route_id'],
    'train_route_ids': sorted(train_routes), 'validation_route_ids': sorted(validation_routes),
    'training_view': args.view,
    'head': args.head, 'frame_period': tm.get('frame_period', .05),
    'sampling': sampling,
    'label_definition': tm['motion_labels'], 'steps': args.steps, 'seed': args.seed, 'lr': args.lr,
    'loss': 'Huber on XY with scales 10m forward/2m lateral; t=0 excluded', 'training_loss': losses,
    'input': 'full 4-frame packed YUV camera history + current speed', 'output': '33 future XY positions in metres',
    'deployment': 'offline research, requires policy/ABI integration and closed-loop validation'}
  write_json(args.output / 'config.json', config)
  measure(model, validation_files, args.output / 'validation.json')


def evaluate(args):
  from tinygrad.nn.state import safe_load, load_state_dict
  from openpilot.tools.full_frame.student import FullFrameDriver
  config = json.loads((args.checkpoint / 'config.json').read_text())
  manifest, files = dataset(args.dataset)
  if set(manifest.get('route_ids', [manifest['route_id']])) & set(config.get('train_route_ids', [config['train_route']])):
    raise ValueError('Use a held-out drive')
  if (manifest['canvas_size'] != [config['width'], config['height']] or manifest['frame_skip'] != config['frame_skip'] or
      manifest['camera_size'] != config['camera_size'] or manifest['motion_labels'] != config['label_definition']):
    raise ValueError('Evaluation geometry/history/labels differ from training')
  if manifest.get('frame_period', .05) != config.get('frame_period', .05):
    raise ValueError('Evaluation image timing differs from training')
  model = FullFrameDriver(config['height'], config['width'], config['channels'], config.get('head', 'waypoints'))
  load_state_dict(model, safe_load(str(args.checkpoint / 'driver.safetensors')))
  measure(model, files, args.output)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest='command', required=True)
  p = commands.add_parser('train')
  p.add_argument('--train', type=Path, required=True)
  p.add_argument('--validation', type=Path, required=True)
  p.add_argument('--output', type=Path, required=True)
  p.add_argument('--encoder', type=Path, help='Optional feature-distillation warm-up checkpoint')
  p.add_argument('--steps', type=int, default=1000)
  p.add_argument('--channels', type=int, default=16)
  p.add_argument('--lr', type=float, default=.0001)
  p.add_argument('--seed', type=int, default=42)
  p.add_argument('--view', choices=('full', 'legacy'), default='full', help='Matched old-visible-region training baseline')
  p.add_argument('--head', choices=('smooth_accel', 'waypoints'), default='smooth_accel')
  p.add_argument('--sampling', choices=('uniform', 'balanced_motion'), default='uniform')
  p.set_defaults(func=train)
  p = commands.add_parser('evaluate')
  p.add_argument('dataset', type=Path)
  p.add_argument('--checkpoint', type=Path, required=True)
  p.add_argument('--output', type=Path, required=True)
  p.set_defaults(func=evaluate)
  args = parser.parse_args()
  args.func(args)


if __name__ == '__main__':
  main()
