#!/usr/bin/env python3
"""Offline Mountain Dew v3 full-view feature branch; never installs a model.

Keep the original calibrated image path. Add both full camera views through a
small encoder into the feature tensor consumed by policy and recurrent memory.
A zero projection initializes the new branch to leave original features alone.
This graph construction alone does not train or qualify the driving response.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

import numpy as np

SOURCE_SHA256 = '1cfd2c45353d876b5b257b24d77537de58ab90a44ae595f15b1f64b6ea3fd770'


def buffer_feature_fusion(model):
  """Put explicit tensor buffers around the FP16 residual sum in the graph.

  The native Metal control with eager buffers restored exact baseline parity.
  These portable tinygrad Contiguous nodes still need saved-graph replay:
  a lazy graph barrier is not assumed equivalent to the eager control.
  """
  from onnx import helper
  matches = [i for i,n in enumerate(model.graph.node)
             if list(n.output)==['view_39'] and n.op_type=='Add' and list(n.input)==['ff_base_features','ff_residual']]
  if len(matches) != 1:
    raise ValueError('Expected exactly one unbuffered research feature sum')
  i = matches[0]
  replacement = [helper.make_node('Contiguous',['ff_base_features'],['ff_base_buffer'],
                                  name='ff_base_buffer',domain='org.tinygrad'),
                 helper.make_node('Contiguous',['ff_residual'],['ff_residual_buffer'],
                                  name='ff_residual_buffer',domain='org.tinygrad'),
                 helper.make_node('Add',['ff_base_buffer','ff_residual_buffer'],['ff_sum'],name='ff_sum'),
                 helper.make_node('Contiguous',['ff_sum'],['view_39'],name='ff_fused_buffer',domain='org.tinygrad')]
  original = list(model.graph.node)
  del model.graph.node[:]
  model.graph.node.extend(original[:i]+replacement+original[i+1:])
  if not any(v.domain=='org.tinygrad' for v in model.opset_import):
    model.opset_import.append(helper.make_opsetid('org.tinygrad',1))
  return model


def add_full_view_branch(source, seed=42, buffered=True):
  import onnx
  from onnx import helper, numpy_helper, TensorProto
  model = copy.deepcopy(source)
  producers = [i for i,n in enumerate(model.graph.node) if 'view_39' in n.output]
  if len(producers) != 1:
    raise ValueError('Expected exactly one audited Mountain Dew feature tap')
  if any(v.name == 'full_img' for v in model.graph.input):
    raise ValueError('Full-view input already exists')
  index = producers[0]
  tap = model.graph.node[index]
  if tap.op_type != 'Reshape':
    raise ValueError('Audited feature tap must be a Reshape')
  tensors = {t.name: t for t in model.graph.initializer}
  if tap.input[1] not in tensors:
    raise ValueError('Feature reshape must use an explicit audited constant')
  shape = numpy_helper.to_array(tensors[tap.input[1]]).tolist()
  if shape not in ([1, 32, 512], [1, -1, 512], [-1, 32, 512]):
    raise ValueError(f'Feature shape differs from proposed 32x512 branch: {shape}')
  # The original report identifies an FP16 encoder head. Do not silently cast
  # another model's feature tap or assume an output ABI from its filename.
  head = tensors.get('model.vision.encoder.head.fc.weight')
  if head is None or head.data_type != TensorProto.FLOAT16 or list(head.dims)[:1] != [512]:
    raise ValueError('Unexpected original encoder feature dtype or width')
  nodes, parameters = [], 0
  rng = np.random.default_rng(seed)
  def constant(name, value):
    model.graph.initializer.append(numpy_helper.from_array(np.asarray(value), name))
    return name
  def node(op, inputs, output, **attributes):
    nodes.append(helper.make_node(op, inputs, [output], name=output, **attributes))
    return output
  x = node('Reshape', ['full_img', constant('ff_shape', np.array([1,12,128,256],np.int64))], 'ff_packed')
  x = node('Cast', [x], 'ff_float', to=TensorProto.FLOAT)
  x = node('Div', [x, constant('ff_scale', np.float32(127.5))], 'ff_scaled')
  x = node('Sub', [x, constant('ff_one', np.float32(1))], 'ff_normalized')
  channels = [12,16,32,64]
  for i,(inc,outc) in enumerate(zip(channels,channels[1:])):
    weight = (rng.standard_normal((outc,inc,3,3))*np.sqrt(2/(inc*9))).astype(np.float32)
    bias = np.zeros(outc,np.float32)
    parameters += weight.size+bias.size
    x = node('Conv', [x,constant(f'ff_conv{i}_weight',weight),constant(f'ff_conv{i}_bias',bias)],
             f'ff_conv{i}',strides=[2,2],pads=[1,1,1,1])
    x = node('Relu', [x], f'ff_relu{i}')
  x = node('AveragePool', [x], 'ff_grid', kernel_shape=[4,4], strides=[4,4])
  weight, bias = np.zeros((512,64,1,1),np.float32), np.zeros(512,np.float32)
  parameters += weight.size+bias.size
  x = node('Conv', [x,constant('ff_projection_weight',weight),constant('ff_projection_bias',bias)], 'ff_projection')
  x = node('Transpose', [x], 'ff_tokens_grid', perm=[0,2,3,1])
  x = node('Reshape', [x,constant('ff_tokens_shape',np.array([1,32,512],np.int64))], 'ff_tokens')
  x = node('Cast', [x], 'ff_residual', to=TensorProto.FLOAT16)
  node('Add', ['ff_base_features',x], 'view_39')
  tap.output[list(tap.output).index('view_39')] = 'ff_base_features'
  original_nodes = list(model.graph.node)
  del model.graph.node[:]
  model.graph.node.extend(original_nodes[:index+1]+nodes+original_nodes[index+1:])
  model.graph.input.append(helper.make_tensor_value_info('full_img',TensorProto.UINT8,[2,6,128,256]))
  if buffered:
    buffer_feature_fusion(model)
  contract = {'original_source_sha256':SOURCE_SHA256,'full_camera_canvas':[512,256],
              'input':'full_img: two complete road/wide views, aspect-preserving YUV420 letterbox',
              'feature_tap':'view_39','residual_shape':[1,32,512], 'trainable_branch_parameters':parameters,
              'original_image_path_retained':True, 'zero_projection_initialized':True,
              'action_loss_required':True,'all_original_outputs_and_state_pairs_retained':True,
              'feature_fusion_revision':2 if buffered else 1,
              'feature_fusion':'three explicit tinygrad Contiguous buffers around FP16 addition' if buffered else 'unbuffered FP16 addition',
              'trained':False,'native_output_parity_verified':False,'modeld_ready':False}
  props = {p.key:p.value for p in model.metadata_props}
  props['full_view_research_contract'] = json.dumps(contract)
  helper.set_model_props(model, props)
  onnx.checker.check_model(model)
  return model, contract


def verify_native(source_path, adapted_path, device, frames=3):
  """Compare all outputs and recurrent states on synthetic inputs, never driving."""
  if device == 'AUTO':
    import platform
    device = 'METAL' if platform.system() == 'Darwin' and platform.machine() == 'arm64' else 'CPU'
  print(f'Native replay backend: {device}',flush=True)
  os.environ['DEV'] = device
  from tinygrad import Tensor
  from tinygrad.nn.onnx import OnnxRunner
  import gc
  def replay(path, full_view):
    runner = OnnxRunner(path)
    state = {name:Tensor.zeros(*spec.shape,dtype=spec.dtype).realize()
             for name,spec in runner.graph_inputs.items() if 'next_'+name in runner.graph_outputs}
    results = []
    for frame in range(frames):
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
      print(f'{"Full-view branch" if full_view else "Original"}: synthetic frame {frame+1}/{frames}',flush=True)
      values = runner(inputs)
      Tensor.realize(*values.values())
      results.append({name:value.numpy().copy() for name,value in values.items()})
      state = {name:values['next_'+name].contiguous().realize() for name in state}
    return results
  original = replay(source_path,False)
  gc.collect()
  adapted = replay(adapted_path,True)
  differences, passed = {}, True
  for base,full in zip(original,adapted):
    if base.keys() != full.keys():
      raise ValueError('Output ABI changed')
    for name,value in base.items():
      if value.shape != full[name].shape or value.dtype != full[name].dtype:
        raise ValueError(f'Output shape or dtype changed: {name}')
      if not np.isfinite(value).all() or not np.isfinite(full[name]).all():
        raise ValueError(f'Nonfinite model output: {name}')
      diff = float(np.max(np.abs(value.astype(np.float64)-full[name].astype(np.float64))))
      differences[name] = max(differences.get(name,0),diff)
      passed = passed and bool(np.allclose(value,full[name],atol=1e-4,rtol=1e-4))
  return {'device':device,'frames':frames,'all_outputs_and_states_match':passed,
          'comparison_executed':True,
          'atol':1e-4,'rtol':1e-4,'max_abs_difference':differences,
          'scope':'Synthetic native zero-branch replay parity only; no training, real-camera replay or driving qualification',
          'modeld_ready':False}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('source',type=Path)
  parser.add_argument('--output',type=Path,required=True)
  parser.add_argument('--verify-device',help='Optional native synthetic replay parity on e.g. METAL or CPU; requires pinned tinygrad')
  parser.add_argument('--verify-only',action='store_true',help='Retry native comparison using an existing research graph and contract')
  args = parser.parse_args()
  if args.output.resolve() == args.source.resolve():
    parser.error('Choose a new output file; original source is preserved')
  if args.verify_only:
    if not args.verify_device or not args.output.is_file() or not args.output.with_suffix('.contract.json').is_file():
      parser.error('--verify-only requires --verify-device and an existing research graph and contract')
  elif args.output.exists():
    parser.error('Choose a new output file, or use --verify-only to retry the existing graph')
  print('Verifying original Mountain Dew v3 source...',flush=True)
  digest = hashlib.sha256()
  with args.source.open('rb') as stream:
    for block in iter(lambda:stream.read(4*1024*1024),b''):digest.update(block)
  if digest.hexdigest() != SOURCE_SHA256:
    parser.error('Source SHA-256 is not the audited Mountain Dew v3')
  if args.verify_only:
    contract = json.loads(args.output.with_suffix('.contract.json').read_text())
    if contract.get('original_source_sha256') != SOURCE_SHA256 or contract.get('modeld_ready') is not False:
      parser.error('Existing contract does not identify this untrained Mountain Dew research graph')
    print('Source hash verified. Reusing existing research graph.',flush=True)
  else:
    import onnx
    print('Source hash verified. Building full-view research graph...',flush=True)
    source = onnx.load(args.source,load_external_data=False)
    if any(t.data_location==onnx.TensorProto.EXTERNAL for t in source.graph.initializer):
      parser.error('External tensor sources are not supported')
    model, contract = add_full_view_branch(source)
    print('Graph checked. Writing a separate research ONNX (about 793 MB)...',flush=True)
    onnx.save(model,args.output)
    args.output.with_suffix('.contract.json').write_text(json.dumps(contract,indent=2)+'\n')
    del source, model
  if args.verify_device:
    import gc
    gc.collect()
    try:
      verification = verify_native(args.source,args.output,args.verify_device)
    except Exception as exc:
      verification = {'all_outputs_and_states_match':False,'error':f'{type(exc).__name__}: {exc}',
                      'comparison_executed':False,'requested_device':args.verify_device,'modeld_ready':False}
    args.output.with_suffix('.parity.json').write_text(json.dumps(verification,indent=2)+'\n')
    contract['native_output_parity_verified'] = verification['all_outputs_and_states_match']
  args.output.with_suffix('.contract.json').write_text(json.dumps(contract,indent=2)+'\n')
  print(f'Saved untrained full-view research graph to {args.output}; not ready for modeld')
  if args.verify_device:
    print(json.dumps(verification,indent=2))


if __name__ == '__main__':
  main()
