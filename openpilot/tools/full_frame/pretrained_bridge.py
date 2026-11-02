"""Offline driving-loss experiment through the bundled pretrained vision/policy.

Cache the frozen vision trunk for a 5-second full-view sequence, then optimize
the original vision summarizer through the original temporal policy's plan loss.
This is a one-example gradient/overfit check, never a driving-quality benchmark.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def unpack_packed(image):
  h,w=image.shape[1:]
  y=np.empty((h*2,w*2),dtype=np.uint8)
  for i,(row,col) in enumerate([(0,0),(1,0),(0,1),(1,1)]):y[row::2,col::2]=image[i]
  return y,image[4],image[5]


def main():
  from tinygrad import Tensor
  from tinygrad.helpers import Context
  from tinygrad.nn.onnx import OnnxRunner
  from tinygrad.nn.optim import Adam
  from tinygrad.nn.state import safe_save
  from openpilot.tools.full_frame.geometry import full_view
  from openpilot.tools.full_frame.reference_metadata import make_metadata_dict
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument('segment',type=Path)
  parser.add_argument('--end-sample',type=int,default=210)
  parser.add_argument('--reference-models',type=Path,required=True,help='Directory of trusted historical driving_vision.onnx and driving_policy.onnx; not Mountain Dew v3')
  parser.add_argument('--steps',type=int,default=20)
  parser.add_argument('--reuse-trunk',type=Path,help='Verified trunk cache from a previous run on the same window')
  parser.add_argument('--output',type=Path,required=True)
  args=parser.parse_args()
  args.output.mkdir(parents=True,exist_ok=False)
  vision_path=args.reference_models/'driving_vision.onnx'
  policy_path=args.reference_models/'driving_policy.onnx'
  vm=make_metadata_dict(vision_path);pm=make_metadata_dict(policy_path)
  vision=OnnxRunner(vision_path);policy=OnnxRunner(policy_path)
  if vm['input_shapes']!={'img':(1,12,128,256),'big_img':(1,12,128,256)}:
    raise ValueError('This experiment supports only the audited bundled split ABI')
  if pm['input_shapes']['features_buffer']!=(1,25,512):raise ValueError('Unexpected policy history')
  files=sorted(args.segment.glob('[0-9]*.npz'))
  window=files[args.end_sample-24:args.end_sample+1]
  if len(window)!=25:raise ValueError('Need 25 continuous examples')
  features=[];times=[];target=None;image_history=[]
  cache=None
  if args.reuse_trunk:
    provenance=json.loads((args.reuse_trunk.parent/'result.json').read_text())
    if provenance['vision_sha256']!=hashlib.sha256(vision_path.read_bytes()).hexdigest():
      raise ValueError('Trunk cache belongs to a different vision checkpoint')
    with np.load(args.reuse_trunk,allow_pickle=False) as data:cache={k:data[k]for k in data.files}
    if cache['trunk'].shape!=(25,512):raise ValueError('Unexpected cached feature dimensions')
  tap='/summarizer/resblock/final_relu/Relu_output_0'
  for i,path in enumerate(window):
    with np.load(path,allow_pickle=False) as sample:
      image=sample['full'][0];times.append(float(sample['timestamps'][-1]));target=sample['target_path']
    prepared=[]
    for start in (0,6,12,18):
      planes=unpack_packed(image[start:start+6])
      # Preserve the complete already-prepared view, with a 512x256 letterbox
      # matching the pretrained ABI. This is a second reference resize.
      view,_,_=full_view(planes,512,256);prepared.append(view)
    inputs={'img':Tensor(np.concatenate(prepared[:2],axis=0)[None]),
            'big_img':Tensor(np.concatenate(prepared[2:],axis=0)[None])}
    image_history.append({name:tensor.numpy() for name,tensor in inputs.items()})
    if cache is None:
      vision(inputs)['outputs'].realize()
      features.append(vision.graph_values[tap].numpy().astype(np.float32))
    else:
      features.append(cache['trunk'][i:i+1])
    print(json.dumps({'cached_full_view_frame':i+1,'total':25}),flush=True)
  if not np.all(np.abs(np.diff(times)-.2)<.01):raise ValueError('History is not continuous at 5 Hz')
  if cache is not None:
    np.testing.assert_allclose(cache['timestamps'],times,atol=1e-6,rtol=0)
    np.testing.assert_array_equal(cache['target'],target)
  trunk=np.concatenate(features,axis=0)
  np.savez_compressed(args.output/'full_view_trunk.npz',trunk=trunk,target=target,timestamps=times)
  weight=Tensor(vision.graph_values['summarizer._mu.weight'].numpy().astype(np.float32))
  bias=Tensor(vision.graph_values['summarizer._mu.bias'].numpy().astype(np.float32))
  optimizer=Adam([weight,bias],lr=1e-5)
  feature_tensor=Tensor(trunk)
  target_tensor=Tensor(target)
  gemm_node=next(n for n in vision.graph_nodes if n.outputs==('/summarizer/_mu/Gemm_output_0',))
  norm_node=next(n for n in vision.graph_nodes if n.outputs==('/summarizer/ReduceL2_output_0',))
  gemm_op=vision._select_op('Gemm',gemm_node.opset_id)
  norm_op=vision._select_op('ReduceL2',norm_node.opset_id)
  def prediction():
    rows=[]
    # Match per-frame FP16 inference, including the original ReduceL2's FP32
    # accumulation and FP16 result. Batched FP32 adaptation gave an avoidable
    # numerical mismatch when the head was exported back to the original graph.
    for i in range(25):
      row=gemm_op(feature_tensor[i:i+1].cast('float16'),weight.cast('float16'),bias.cast('float16'),**gemm_node.opts)
      row=row/norm_op(row,**norm_node.opts).maximum(1e-12)
      rows.append(row)
    hidden=Tensor.cat(*rows,dim=0)
    raw=policy({'features_buffer':hidden.reshape(1,25,512).cast('float16'),
                'desire_pulse':Tensor.zeros(1,25,8,dtype='float16'),
                'traffic_convention':Tensor([[1.,0.]],dtype='float16')})['outputs'].cast('float32')
    # Audited plan is 495 means then 495 log-standard deviations, 33x15 each.
    return raw[:,:495].reshape(1,33,15)[:,:,:2]
  def objective(pred):
    error=((pred-target_tensor)/Tensor([10.,2.])).abs()[:,1:]
    q=error.minimum(1.)
    return (.5*q.square()+error-q).mean()
  initial=prediction().numpy();losses=[];gradient_norm=None
  for i in range(args.steps):
    with Context(TRAINING=1):
      optimizer.zero_grad();pred=prediction();loss=objective(pred)
      # Only compute gradients for the explicitly trainable summarizer; frozen
      # policy and cached vision trunk are not optimizer parameters.
      weight.grad,bias.grad=loss.gradient(weight,bias)
      if i==0:gradient_norm=float(np.linalg.norm(weight.grad.numpy()))
      optimizer.step();value=float(loss.item())
    if not np.isfinite(value):raise ValueError('Non-finite driving loss')
    losses.append(value);print(json.dumps({'step':i+1,'pretrained_policy_driving_loss':value}),flush=True)
  final=prediction().numpy()
  safe_save({'summarizer._mu.weight':weight,'summarizer._mu.bias':bias},str(args.output/'summarizer.safetensors'))
  np.savez_compressed(args.output/'plans.npz',initial=initial,adapted=final,target=target)
  result={'experiment':'single-example driving-loss gradient/overfit check',
    'vision_sha256':hashlib.sha256(vision_path.read_bytes()).hexdigest(),
    'policy_sha256':hashlib.sha256(policy_path.read_bytes()).hexdigest(),
    'input_geometry':'complete prepared sensor view resized again to a 512x256 letterbox; 25 cached trunk features at 5 Hz',
    'trainable_parameters':['summarizer._mu.weight','summarizer._mu.bias'],
    'frozen':'pretrained vision trunk and original temporal policy',
    'head_forward_precision':'original per-frame FP16 ONNX Gemm and ReduceL2; FP32 optimizer master weights',
    'driving_loss_gradient_l2':gradient_norm,'training_losses':losses,
    'initial_plan_loss':float(objective(Tensor(initial)).item()),'adapted_plan_loss':float(objective(Tensor(final)).item()),
    'scope':'not a held-out evaluation; no stop-sign response claim; no vehicle deployment',
    'limitations':['Zero desire history and assumed right-hand traffic; not exact runtime replay',
                   'Uses bundled ONNX weights, not verified as the selected on-device model',
                   'Cached trunk is frozen; only the original summarizer is adapted',
                   'Other vision heads are not trained or validated for the new geometry']}
  # Preserve the actual model graph and output ABI, replacing only the trained
  # summarizer. This artifact requires a new runtime preprocessing contract;
  # the current modeld warp still crops and must not silently load it as-is.
  import onnx
  from onnx import numpy_helper
  exported=onnx.load(vision_path)
  updated={'summarizer._mu.weight':weight.numpy(),'summarizer._mu.bias':bias.numpy()}
  for initializer in exported.graph.initializer:
    if initializer.name in updated:
      dtype=numpy_helper.to_array(initializer).dtype
      initializer.CopyFrom(numpy_helper.from_array(updated[initializer.name].astype(dtype),initializer.name))
  property=exported.metadata_props.add();property.key='research_input_geometry';property.value='full_view_letterbox_512x256'
  property=exported.metadata_props.add();property.key='research_modeld_ready';property.value='false'
  for property in exported.metadata_props:
    if property.key=='model_checkpoint':property.value+=':full-view-summarizer-overfit-research'
  onnx.checker.check_model(exported)
  path=args.output/'research-full-view-vision.onnx';onnx.save(exported,path)
  imported=OnnxRunner(path)
  replay=[]
  for inputs in image_history:
    values=imported({k:Tensor(v)for k,v in inputs.items()})['outputs'].numpy()
    replay.append(values[:,1064:1576])
  replay_features=np.concatenate(replay,axis=0).reshape(1,25,512)
  replay_raw=policy({'features_buffer':Tensor(replay_features),
                    'desire_pulse':Tensor.zeros(1,25,8,dtype='float16'),
                    'traffic_convention':Tensor([[1.,0.]],dtype='float16')})['outputs'].numpy()
  replay_plan=replay_raw[:,:495].reshape(1,33,15)[:,:,:2].astype(np.float32)
  if not np.isfinite(replay_plan).all():raise ValueError('Exported pretrained path produced non-finite plan')
  result['export']={'onnx_checker':'passed','modeld_ready':False,
     'replayed_original_temporal_policy':True,
     'plan_max_abs_difference_after_fp16_export_m':float(np.abs(replay_plan-final).max()),
     'plan_numerical_match_within_1mm':bool(np.max(np.abs(replay_plan-final))<=.001),
     'replayed_driving_loss':float(objective(Tensor(replay_plan)).item())}
  np.savez_compressed(args.output/'plans.npz',initial=initial,adapted=final,target=target,exported_replay=replay_plan)
  (args.output/'MODEL-CONTRACT.json').write_text(json.dumps({'input_geometry':'full_view_letterbox_512x256',
     'image_history':'two frames 200 ms apart per camera; 25 policy features at 5 Hz',
     'outputs':'original bundled vision ABI, original unchanged temporal policy',
     'modeld_ready':False,'required_before_runtime':'explicit full-view warp contract, complete output validation and closed-loop evaluation'},indent=2)+'\n')
  (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
  print(json.dumps(result,indent=2))


if __name__=='__main__':main()
