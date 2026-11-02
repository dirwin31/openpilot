#!/usr/bin/env python3
"""Isolate native zero-branch parity failures without editing model files.

Identity and buffering variants are runtime diagnostic controls. An identity
bypass is not a full-frame model and cannot qualify the residual branch.
"""
import argparse
from dataclasses import replace
import gc
import hashlib
import json
import os
from pathlib import Path
import platform

import numpy as np

SOURCE_SHA256 = '1cfd2c45353d876b5b257b24d77537de58ab90a44ae595f15b1f64b6ea3fd770'
SECTIONS = {'geometry_and_meta':(0,917),'plan':(917,1907),'lead':(1907,2054),
            'desire_state':(2054,2062),'action_means':(2062,2064),'action_uncertainty':(2064,2066),
            'hidden_state':(2066,18450),'pad':(18450,18452)}


def compare(reference, candidate):
  if len(reference) != len(candidate):
    raise ValueError('Replay lengths differ')
  per_frame, passed, maxima = [], True, {}
  for baseline, full in zip(reference,candidate):
    if baseline['values'].keys() != full['values'].keys():
      raise ValueError('Model output names differ')
    differences = {}
    for name,value in baseline['values'].items():
      other = full['values'][name]
      if value.shape != other.shape or value.dtype != other.dtype:
        raise ValueError(f'Output contract differs: {name}')
      if not np.isfinite(value).all() or not np.isfinite(other).all():
        raise ValueError(f'Nonfinite values: {name}')
      difference = float(np.max(np.abs(value.astype(np.float64)-other.astype(np.float64))))
      differences[name] = difference
      maxima[name] = max(maxima.get(name,0),difference)
      passed = passed and bool(np.allclose(value,other,atol=1e-4,rtol=1e-4))
    sections = {}
    base_out, full_out = baseline['values']['outputs'], full['values']['outputs']
    for name,(start,stop) in SECTIONS.items():
      if base_out.shape[-1] >= stop:
        a,b = base_out[:,start:stop],full_out[:,start:stop]
        sections[name] = {'max_abs_difference':float(np.max(np.abs(a.astype(np.float64)-b.astype(np.float64)))),
                          'matches':bool(np.allclose(a,b,atol=1e-4,rtol=1e-4))}
        if name == 'action_means':
          sections[name]['original'] = a.tolist()
          sections[name]['candidate'] = b.tolist()
    taps = {}
    for name in sorted(baseline['taps'].keys() & full['taps'].keys()):
      a,b = baseline['taps'][name],full['taps'][name]
      taps[name] = float(np.max(np.abs(a.astype(np.float64)-b.astype(np.float64))))
    per_frame.append({'max_abs_difference':differences,'output_sections':sections,'tap_max_abs_difference':taps,
                      'candidate_residual_max_abs':full['residual_max_abs'],
                      'candidate_feature_change_max_abs':full['feature_change_max_abs']})
  return {'all_outputs_and_states_match':passed,'max_abs_difference':maxima,'per_frame':per_frame}


def diagnose(source_path, adapted_path, device, frames=3):
  if device == 'AUTO':
    device = 'METAL' if platform.system()=='Darwin' and platform.machine()=='arm64' else 'CPU'
  print(f'Diagnostic backend: {device}',flush=True)
  os.environ['DEV'] = device
  from tinygrad import Tensor, dtypes
  from tinygrad.nn.onnx import OnnxRunner

  def buffered_add(a,b):
    # Force actual feature buffers before/after summation; do not just change
    # tolerances or silently compare a bypassed full-frame branch.
    return (a.contiguous().realize()+b.contiguous().realize()).contiguous().realize()

  def fp32_add(a,b):
    return (a.cast(dtypes.float32)+b.cast(dtypes.float32)).cast(a.dtype)

  def fingerprints(runner,names=None):
    return {name:(tuple(value.shape),str(value.dtype),hashlib.sha256(value.data().tobytes()).hexdigest())
            for name,value in runner.graph_values.items() if value is not None and (names is None or name in names)}

  def replay(runner, label):
    state = {name:Tensor.zeros(*spec.shape,dtype=spec.dtype).realize()
             for name,spec in runner.graph_inputs.items() if 'next_'+name in runner.graph_outputs}
    records = []
    for frame in range(frames):
      print(f'{label}: frame {frame+1}/{frames}',flush=True)
      inputs = dict(state)
      for name,spec in runner.graph_inputs.items():
        if name in inputs:
          continue
        if name in ('new_img','full_img'):
          rng = np.random.default_rng(42+frame+(100 if name=='full_img' else 0))
          inputs[name] = Tensor(rng.integers(16,236,tuple(spec.shape),dtype=np.uint8))
        elif name == 'traffic_convention':
          inputs[name] = Tensor([[0.,1.]])
        elif name == 'action_t':
          inputs[name] = Tensor([[.3,.3]])
        else:
          inputs[name] = Tensor.zeros(*spec.shape,dtype=spec.dtype)
      values = runner(inputs)
      Tensor.realize(*values.values())
      # Capture authoritative outputs before inspecting intermediate tensors.
      record = {'values':{name:value.numpy().copy() for name,value in values.items()},'taps':{},
                'residual_max_abs':None,'feature_change_max_abs':None}
      for name in ('add_35','permute_81','view_39'):
        if name in runner.graph_values:
          record['taps'][name] = runner.graph_values[name].numpy().copy()
      if 'ff_base_features' in runner.graph_values:
        base = runner.graph_values['ff_base_features'].numpy().copy()
        record['taps']['base_features'] = base
        record['feature_change_max_abs'] = float(np.max(np.abs(base.astype(np.float32)-record['taps']['view_39'].astype(np.float32))))
        record['residual_max_abs'] = float(np.max(np.abs(runner.graph_values['ff_residual'].numpy())))
      else:
        record['taps']['base_features'] = record['taps']['view_39']
      records.append(record)
      state = {name:values['next_'+name].contiguous().realize() for name in state}
    return records

  source = OnnxRunner(source_path)
  print('Checking original initializer fingerprints...',flush=True)
  original_weights = fingerprints(source)
  original = replay(source,'Original')
  repeat = compare(original,replay(source,'Original repeated'))
  del source
  gc.collect()
  runner = OnnxRunner(adapted_path)
  adapted_weights = fingerprints(runner,original_weights)
  differing_weights = sorted(name for name,value in original_weights.items() if adapted_weights.get(name)!=value)
  nodes, ops = runner.graph_nodes, dict(runner.onnx_ops)
  fusion = [n for n in nodes if n.outputs==('view_39',) and n.op=='Add' and n.inputs[0]=='ff_base_features']
  if len(fusion) != 1:
    raise ValueError('Expected exactly one audited residual fusion node')
  variants = {}
  controls = [('residual',None,None),('identity_control','Identity',None),
              ('buffered_add_control','FFBufferedAdd',buffered_add),('fp32_add_control','FFFloat32Add',fp32_add)]
  for label,op,function in controls:
    runner.graph_nodes = tuple(replace(n,op=op,inputs=(n.inputs[0],) if op=='Identity' else n.inputs,opts={})
                               if op and n is fusion[0] else n for n in nodes)
    runner.onnx_ops = dict(ops)
    if function:
      runner.onnx_ops[op] = function
    variants[label] = compare(original,replay(runner,label))
  del runner
  gc.collect()
  return {'device':device,'frames':frames,'comparison_executed':True,'atol':1e-4,'rtol':1e-4,
          'all_outputs_and_states_match':(variants['residual']['all_outputs_and_states_match']
                                         and repeat['all_outputs_and_states_match'] and not differing_weights),
          'max_abs_difference':variants['residual']['max_abs_difference'],
          'original_initializers_checked':len(original_weights),
          'original_initializers_identical':not differing_weights,
          'differing_original_initializers':differing_weights,
          'original_repeatability':repeat,'variants':variants,
          'scope':'Synthetic diagnostic controls only. Identity disables the new input and is not a full-frame solution. Buffered/fp32 variants are in-memory controls, not exported or qualified models.',
          'intermediate_taps_observed_after_outputs':True,'modeld_ready':False}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  downloads = Path.home()/'Downloads'
  parser.add_argument('--source',type=Path,default=downloads/'mountaindewv3_driving_supercombo.onnx')
  parser.add_argument('--graph',type=Path,default=downloads/'mdv3-full-view-research.onnx')
  parser.add_argument('--output',type=Path)
  parser.add_argument('--device',default='AUTO')
  args = parser.parse_args()
  output = args.output or args.graph.with_suffix('.parity.json')
  digest = hashlib.sha256()
  with args.source.open('rb') as stream:
    for block in iter(lambda:stream.read(4*1024*1024),b''):digest.update(block)
  if digest.hexdigest() != SOURCE_SHA256:
    parser.error('Original source does not match audited Mountain Dew v3')
  try:
    result = diagnose(args.source,args.graph,args.device)
  except Exception as exc:
    import traceback
    result = {'comparison_executed':False,'error':f'{type(exc).__name__}: {exc}',
              'traceback':traceback.format_exc(),'modeld_ready':False,'all_outputs_and_states_match':False}
  output.write_text(json.dumps(result,indent=2)+'\n')
  print(f'Saved diagnostics to {output}',flush=True)


if __name__ == '__main__':
  main()
