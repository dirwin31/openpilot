#!/usr/bin/env python3
"""Offline preparation, teacher labeling, student training, and holdout evaluation."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from openpilot.tools.full_frame.geometry import full_view, legacy_visibility, teacher_view, temporal_pairs, unpack_nv12

ROOT = Path(__file__).resolve().parents[2]
DOM_SHA = '934dadbcdbaaaac78dd6badd09a1ebadd298bae8'


def digest(path):
  return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
  Path(path).write_text(json.dumps(value, indent=2) + '\n')


def audit(args):
  from tinygrad.nn.onnx import OnnxPBParser
  from openpilot.selfdrive.modeld.get_model_metadata import make_metadata_dict
  result = {'dom_reference': DOM_SHA, 'models': {}}
  for path in (args.vision, args.policy):
    graph = OnnxPBParser(path).parse()['graph']
    metadata = make_metadata_dict(path)
    result['models'][str(path)] = {
      'sha256': digest(path), 'resolved_path': str(path.resolve()), 'checkpoint': metadata['model_checkpoint'],
      'inputs': {x['name']: {'shape': x['parsed_type'].shape, 'dtype': str(x['parsed_type'].dtype)} for x in graph['input']},
      'outputs': metadata['output_shapes'],
      'slices': {k: [v.start, v.stop, v.step] for k, v in metadata['output_slices'].items()},
      'weight_elements': sum(np.prod(x['parsed_tensor'].shape).item() for x in graph['initializer']),
      'ops': dict(Counter(x['op_type'] for x in graph['node'])),
    }
  result['camera_payload_MB_per_second_at_20Hz'] = {f'{w}x{h}': 2*w*h*1.5*20/1e6 for w,h in [(1344,760),(1928,1208)]}
  write_json(args.output, result)
  print(json.dumps(result, indent=2))


def prepare(args):
  """One NPZ per continuous segment; output contains consecutive training examples."""
  args.output.mkdir(parents=True, exist_ok=True)
  if any(args.output.iterdir()):
    raise ValueError('Output directory must be empty to prevent stale/mixed samples')
  with np.load(args.capture, allow_pickle=False) as archive:
    # NPZ members decompress on every access; load each camera array once.
    source = {key: archive[key] for key in archive.files}
    required = {'road_nv12', 'wide_nv12', 'road_times', 'wide_times', 'road_tfm', 'wide_tfm', 'route_id'}
    if not required <= set(source):
      raise ValueError(f'Missing capture fields: {required - set(source)}')
    route_id = str(source['route_id'].item())
    if not route_id:
      raise ValueError('route_id must identify the drive for route-level holdouts')
    count = len(source['road_times'])
    for key in required - {'route_id'}:
      if len(source[key]) != count:
        raise ValueError(f'{key} does not have {count} frames')
    period = float(source.get('frame_period', .05))
    pairs = temporal_pairs(source['road_times'], source['wide_times'], args.frame_skip, period=period)
    if not pairs:
      raise ValueError('No continuous synchronized temporal pairs')
    if source['road_tfm'].shape != (count, 3, 3) or source['wide_tfm'].shape != (count, 3, 3):
      raise ValueError('Transforms must have shape (N,3,3)')
    dims = {cam: source[f'{cam}_nv12'].shape for cam in ('road', 'wide')}
    for cam, shape in dims.items():
      if len(shape) != 3 or shape[1:] != (args.camera_height * 3 // 2, args.camera_width):
        raise ValueError(f'{cam}_nv12 must be tightly packed (N,H*3/2,W); depad hardware buffers first')
    motion_keys = {'pose_times', 'orientation_ned', 'velocity_device', 'pose_valid', 'calibration'}
    have_motion = motion_keys <= set(source)
    if motion_keys & set(source) and not have_motion:
      raise ValueError(f'Incomplete motion labels: {motion_keys - set(source)}')
    saved = 0
    for sample, (prev, now) in enumerate(pairs):
      motion = {}
      if have_motion:
        from openpilot.tools.full_frame.trajectory import motion_target, TIMES
        target = motion_target(source['pose_times'], source['orientation_ned'], source['velocity_device'],
                               source['pose_valid'], source['road_times'][now], source['calibration'][now])
        if target is None:
          continue
        motion = {'target_path': target[0][None], 'speed': target[1], 'path_times': TIMES}
      full, baseline, masks, affines, legacy_masks = [], {}, [], [], []
      for cam, key in [('road', 'img'), ('wide', 'big_img')]:
        history, teacher_history = [], []
        for idx in (prev, now):
          planes = unpack_nv12(source[f'{cam}_nv12'][idx], args.camera_width, args.camera_height)
          view, mask, affine = full_view(planes, args.width, args.height)
          history.append(view)
          teacher_history.append(teacher_view(planes, source[f'{cam}_tfm'][idx]))
          legacy_masks.append(legacy_visibility(source[f'{cam}_tfm'][idx], affine, args.width, args.height))
        full.extend(history)
        baseline[key] = np.concatenate(teacher_history, axis=0)[None]
        masks.append(mask)
        affines.append(affine)
      np.savez_compressed(args.output / f'{sample:06d}.npz', full=np.concatenate(full, axis=0)[None],
                          **baseline, valid_masks=np.stack(masks), source_to_canvas=np.stack(affines),
                          legacy_masks=np.stack(legacy_masks), **motion,
                          timestamps=source['road_times'][[prev, now]], source_indices=np.array([prev, now]))
      saved += 1
    if not saved:
      raise ValueError('No usable examples with complete valid future motion')
  write_json(args.output / 'manifest.json', {'schema': 1, 'route_id': route_id, 'capture_sha256': digest(args.capture),
    'camera_size': [args.camera_width, args.camera_height], 'canvas_size': [args.width, args.height],
    'frame_skip': args.frame_skip, 'samples': saved, 'labels': None,
    'frame_period': period, 'image_history_seconds': period * args.frame_skip,
    'motion_labels': '10s integrated valid livePose in calibrated frame; recorded motion, not obstacle annotations' if have_motion else None,
    'geometry': 'sensor view, aspect-preserving letterbox, pixel-centre bilinear, even YUV offsets',
    'teacher_geometry': 'per-frame supplied destination-to-source matrices, nearest, edge clamp'})
  print(f'Prepared {saved} synchronized examples from route {route_id}')


def label(args):
  from tinygrad import Tensor
  from tinygrad.nn.onnx import OnnxRunner
  from openpilot.selfdrive.modeld.get_model_metadata import make_metadata_dict
  metadata = make_metadata_dict(args.vision)
  feature_slice = metadata['output_slices'].get('hidden_state')
  if feature_slice is None:
    raise ValueError('Teacher has no hidden_state output slice')
  runner = OnnxRunner(args.vision)
  if set(runner.graph_inputs) != {'img', 'big_img'} or runner.graph_outputs != ('outputs',):
    raise ValueError('This prototype supports the audited split vision ABI (img, big_img -> outputs)')
  manifest = json.loads((args.dataset / 'manifest.json').read_text())
  started = time.monotonic()
  for path in sorted(args.dataset.glob('[0-9]*.npz')):
    with np.load(path, allow_pickle=False) as data:
      arrays = {k: data[k] for k in data.files}
    prediction = runner({k: Tensor(arrays[k]) for k in runner.graph_inputs})['outputs'].numpy().astype(np.float32)
    if not np.isfinite(prediction).all():
      raise ValueError(f'Non-finite teacher output: {path}')
    arrays['target'] = prediction[:, feature_slice]
    arrays['teacher_outputs'] = prediction
    np.savez_compressed(path, **arrays)
  manifest['labels'] = {'teacher_sha256': digest(args.vision), 'checkpoint': metadata['model_checkpoint'],
    'feature_slice': [feature_slice.start, feature_slice.stop], 'seconds': time.monotonic() - started}
  write_json(args.dataset / 'manifest.json', manifest)
  print(json.dumps(manifest['labels'], indent=2))


def load_dataset(path):
  manifest = json.loads((path / 'manifest.json').read_text())
  if not manifest['labels']:
    raise ValueError(f'{path} must be labeled first')
  samples = sorted(path.glob('[0-9]*.npz'))
  if not samples or len(samples) != manifest['samples']:
    raise ValueError('Dataset sample count differs from manifest')
  return manifest, samples


def read_sample(path):
  with np.load(path, allow_pickle=False) as data:
    image, target = data['full'], data['target']
  if not np.isfinite(target).all():
    raise ValueError(f'Non-finite target: {path}')
  return image, target


def train(args):
  from tinygrad import Tensor
  from tinygrad.helpers import Context
  from tinygrad.nn.optim import Adam
  from tinygrad.nn.state import get_parameters, get_state_dict, safe_save
  from openpilot.tools.full_frame.student import FullFrameEncoder
  train_manifest, train_files = load_dataset(args.train)
  val_manifest, val_files = load_dataset(args.validation)
  if train_manifest['route_id'] == val_manifest['route_id']:
    raise ValueError('Train and validation must come from different drives')
  for key in ('canvas_size', 'frame_skip'):
    if train_manifest[key] != val_manifest[key]:
      raise ValueError(f'Incompatible dataset {key}')
  if train_manifest.get('frame_period', .05) != val_manifest.get('frame_period', .05):
    raise ValueError('Incompatible dataset frame_period')
  if train_manifest['labels']['teacher_sha256'] != val_manifest['labels']['teacher_sha256']:
    raise ValueError('Teacher checkpoint differs between datasets')
  if args.steps < 1 or args.channels < 1:
    raise ValueError('Steps and channels must be positive')
  args.output.mkdir(parents=True, exist_ok=False)
  Tensor.manual_seed(args.seed)
  rng = np.random.default_rng(args.seed)
  width, height = train_manifest['canvas_size']
  output_dim = read_sample(train_files[0])[1].shape[1]
  mean_target = np.zeros((1, output_dim), dtype=np.float64)
  for path in train_files:
    mean_target += read_sample(path)[1] / len(train_files)
  mean_target = mean_target.astype(np.float32)
  np.save(args.output / 'mean_training_feature.npy', mean_target)
  model = FullFrameEncoder(height, width, output_dim, args.channels)
  optimizer = Adam(get_parameters(model), lr=args.lr)
  # Streaming single-sample training keeps host/device memory bounded on 8 GB GPUs.
  losses = []
  for step in range(args.steps):
    image, target = read_sample(train_files[int(rng.integers(len(train_files)))])
    with Context(TRAINING=1):
      optimizer.zero_grad()
      loss = (model(Tensor(image)) - Tensor(target)).square().mean()
      loss.backward()
      optimizer.step()
      value = float(loss.item())
    if not np.isfinite(value):
      raise ValueError('Non-finite training loss')
    losses.append(value)
    if step == 0 or (step+1) % 10 == 0:
      print(json.dumps({'step': step+1, 'feature_mse': value}), flush=True)
  safe_save(get_state_dict(model), str(args.output / 'student.safetensors'))
  config = {'width': width, 'height': height, 'output_dim': output_dim, 'channels': args.channels,
    'teacher_sha256': train_manifest['labels']['teacher_sha256'], 'frame_skip': train_manifest['frame_skip'],
    'frame_period': train_manifest.get('frame_period', .05),
    'train_route': train_manifest['route_id'], 'validation_route': val_manifest['route_id'],
    'steps': args.steps, 'seed': args.seed, 'lr': args.lr, 'training_feature_mse': losses,
    'scope': 'offline hidden-feature distillation; no perception heads, no driving-model export'}
  write_json(args.output / 'config.json', config)
  evaluate_paths(model, val_files, args.output / 'validation.json', mean_target)


def evaluate_paths(model, samples, output, mean_target):
  from tinygrad import Tensor
  errors, ablations, latencies, constant_errors, zero_errors, cosine = [], [], [], [], [], []
  for path in samples:
    image, target = read_sample(path)
    started = time.monotonic()
    prediction = model(Tensor(image)).numpy()
    latencies.append((time.monotonic() - started) * 1000)
    errors.append(float(np.mean((prediction - target)**2)))
    constant_errors.append(float(np.mean((mean_target - target)**2)))
    zero_errors.append(float(np.mean(target**2)))
    cosine.append(float(np.sum(prediction*target) / max(1e-9, np.linalg.norm(prediction)*np.linalg.norm(target))))
    # Full-view dependence probe, not a measure of driving improvement or safety.
    masked = image.copy()
    band = max(1, masked.shape[-1] // 8)
    masked[:, :, :, :band] = 128
    masked[:, :, :, -band:] = 128
    ablations.append(float(np.mean((model(Tensor(masked)).numpy() - prediction)**2)))
  result = {'samples': len(samples), 'feature_mse': float(np.mean(errors)),
    'constant_training_mean_feature_mse': float(np.mean(constant_errors)), 'zero_feature_mse': float(np.mean(zero_errors)),
    'mean_cosine_similarity': float(np.mean(cosine)),
    'outer_band_ablation_feature_change_mse': float(np.mean(ablations)),
    'inference_ms_including_first_compile_p50': float(np.percentile(latencies, 50)),
    'inference_ms_including_first_compile_p95': float(np.percentile(latencies, 95)),
    'limitations': 'Feature agreement and input sensitivity only; not driving quality. Includes host copies; no thermal or USB benchmark.'}
  write_json(output, result)
  print(json.dumps(result, indent=2))


def evaluate(args):
  from tinygrad.nn.state import safe_load, load_state_dict
  from openpilot.tools.full_frame.student import FullFrameEncoder
  config = json.loads((args.checkpoint / 'config.json').read_text())
  manifest, samples = load_dataset(args.dataset)
  if manifest['route_id'] == config['train_route']:
    raise ValueError('Evaluation requires a held-out drive')
  if (manifest['canvas_size'] != [config['width'], config['height']] or manifest['frame_skip'] != config['frame_skip'] or
      manifest.get('frame_period', .05) != config.get('frame_period', .05) or
      manifest['labels']['teacher_sha256'] != config['teacher_sha256']):
    raise ValueError('Evaluation dataset geometry, history, or teacher differs from training')
  model = FullFrameEncoder(config['height'], config['width'], config['output_dim'], config['channels'])
  load_state_dict(model, safe_load(str(args.checkpoint / 'student.safetensors')))
  evaluate_paths(model, samples, args.output, np.load(args.checkpoint / 'mean_training_feature.npy', allow_pickle=False))


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest='command', required=True)
  p = commands.add_parser('audit')
  p.add_argument('--vision', type=Path, default=ROOT / 'selfdrive/modeld/models/driving_vision.onnx')
  p.add_argument('--policy', type=Path, default=ROOT / 'selfdrive/modeld/models/driving_policy.onnx')
  p.add_argument('--output', type=Path, required=True)
  p.set_defaults(func=audit)
  p = commands.add_parser('prepare')
  p.add_argument('capture', type=Path)
  p.add_argument('--output', type=Path, required=True)
  p.add_argument('--camera-width', type=int, default=1344)
  p.add_argument('--camera-height', type=int, default=760)
  p.add_argument('--width', type=int, default=512)
  p.add_argument('--height', type=int, default=288)
  p.add_argument('--frame-skip', type=int, default=4)
  p.set_defaults(func=prepare)
  p = commands.add_parser('label')
  p.add_argument('dataset', type=Path)
  p.add_argument('--vision', type=Path, default=ROOT / 'selfdrive/modeld/models/driving_vision.onnx')
  p.set_defaults(func=label)
  p = commands.add_parser('train')
  p.add_argument('--train', type=Path, required=True)
  p.add_argument('--validation', type=Path, required=True)
  p.add_argument('--output', type=Path, required=True)
  p.add_argument('--steps', type=int, default=1000)
  p.add_argument('--channels', type=int, default=16)
  p.add_argument('--lr', type=float, default=.0001)
  p.add_argument('--seed', type=int, default=42)
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
