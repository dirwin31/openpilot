"""First real-weight MDv3 adapter action-loss experiment, offline and unqualified.

Replay a contiguous manual clip at 20 Hz; update only the adapter on a short
braking window. This is a development overfit check on one route, not evaluation.
"""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import platform

import numpy as np


def main():
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--source',type=Path,required=True)
  parser.add_argument('--graph',type=Path,required=True)
  parser.add_argument('--segments',type=Path,required=True)
  parser.add_argument('--schema-root',type=Path,required=True)
  parser.add_argument('--output',type=Path,required=True)
  parser.add_argument('--device',default='AUTO')
  parser.add_argument('--steps',type=int,default=20)
  parser.add_argument('--learning-rate',type=float,default=1e-4)
  args=parser.parse_args()
  if args.output.exists() or args.steps<1 or not 0<args.learning_rate<=.001:
    parser.error('Need a new output directory, positive step count and learning rate <= .001')
  device=args.device
  if device=='AUTO':device='METAL' if platform.system()=='Darwin' and platform.machine()=='arm64' else 'CPU'
  os.environ['DEV']=device
  from tinygrad import Tensor
  from tinygrad.nn.onnx import OnnxRunner
  from tinygrad.nn.optim import Adam
  from tinygrad.nn.state import safe_save
  from openpilot.tools.full_frame.mdv3_adapter import SOURCE_SHA256
  from openpilot.tools.full_frame.mdv3_data import build_clip,decoded_frames
  from openpilot.tools.full_frame.mdv3_sequence import SequenceGuard
  from openpilot.tools.full_frame.mdv3_training import adapter_parameters,action_step,PARAMETER_NAMES
  digest=hashlib.sha256()
  with args.source.open('rb') as stream:
    for block in iter(lambda:stream.read(4*1024*1024),b''):digest.update(block)
  if digest.hexdigest()!=SOURCE_SHA256:parser.error('Original source SHA-256 mismatch')
  contract=json.loads(args.graph.with_suffix('.contract.json').read_text())
  if (contract.get('trained') is not False or contract.get('native_output_parity_verified') is not True or
      contract.get('feature_fusion_revision')!=2):
    parser.error('Start from the verified untrained revision-2 graph')
  clip=build_clip(args.segments,schema_root=args.schema_root)
  args.output.mkdir(parents=True)
  print(f'Backend {device}; route {clip["route_id"]}; {len(clip["frames"])} native-cadence frames',flush=True)
  teacher=OnnxRunner(args.source);student=OnnxRunner(args.graph)
  frozen={k:v for k,v in teacher.graph_values.items() if v is not None}
  def fingerprint(value):return hashlib.sha256(value.data().tobytes()).hexdigest()
  print('Verifying and sharing frozen tensors...',flush=True)
  initial_frozen={}
  for name,value in frozen.items():
    other=student.graph_values.get(name)
    if other is None or value.shape!=other.shape or value.dtype!=other.dtype:
      raise ValueError(f'Frozen tensor contract changed: {name}')
    expected=fingerprint(value)
    if fingerprint(other)!=expected:raise ValueError(f'Frozen tensor bytes changed: {name}')
    initial_frozen[name]=expected;student.graph_values[name]=value
  gc.collect()
  optimizer=Adam(adapter_parameters(student),lr=args.learning_rate)
  guard=SequenceGuard();teacher_state=student_state=None;history=[];zero_checks=[];last_inputs=None;last_teacher=None;last_record=None
  def zero_state(runner):
    return {name:Tensor.zeros(*spec.shape,dtype=spec.dtype).realize()for name,spec in runner.graph_inputs.items()
            if 'next_'+name in runner.graph_outputs}
  def forward(runner,inputs):
    output=runner(inputs);Tensor.realize(*output.values())
    state={name:output['next_'+name].detach().contiguous().realize()for name in runner.graph_inputs if 'next_'+name in output}
    return output,state
  def check_zero_branch(reference,actual,seconds):
    differences={}
    for name,value in reference.items():
      a,b=value.numpy(),actual[name].numpy()
      differences[name]=float(np.max(np.abs(a.astype(np.float64)-b.astype(np.float64))))
      if not np.allclose(a,b,atol=1e-4,rtol=1e-4):
        raise ValueError(f'Real-camera zero-branch parity failed at {seconds:.3f}s: {name}, {differences[name]}')
    zero_checks.append({'route_seconds':seconds,'max_abs_difference':differences})
  reader=decoded_frames(args.segments,clip)
  try:
    for i,(record,cameras) in enumerate(reader):
      sequence=guard.update(clip['route_id'],record['frame_id'],record['timestamp'])
      if sequence.reset:teacher_state=zero_state(teacher);student_state=zero_state(student)
      common={'new_img':Tensor(cameras['new_img']),'desire':Tensor.zeros(8),
              'action_t':Tensor(record['action_t']),'traffic_convention':Tensor(record['traffic_convention'])}
      teacher_output,teacher_state=forward(teacher,common|teacher_state)
      baseline=teacher_output['outputs'].numpy().copy()
      inputs=common|student_state|{'full_img':Tensor(cameras['full_img'])}
      supervise=sequence.ready and 57.<=record['route_seconds']<=63. and i%4==0 and record['mask'].any()
      if supervise:
        result=action_step(student,optimizer,inputs,record['target'],record['mask'],reference_aux=baseline[:,887:917])
        student_state=result['next_state']
        if not history:
          check_zero_branch(teacher_output,{name:student.graph_values[name]for name in student.graph_outputs},record['route_seconds'])
        row={'step':len(history)+1,'route_seconds':record['route_seconds'],'loss':result['loss'],
             'gradient_l2':result['adapter_gradient_l2'],'target':record['target'].tolist(),
             'mask':record['mask'].tolist(),'action_before_update':result['action_before_update'].tolist()}
        history.append(row);print(json.dumps(row),flush=True)
        last_inputs,last_teacher,last_record=inputs,baseline,record
        if len(history)>=args.steps:break
      else:
        observed,student_state=forward(student,inputs)
        if not history and i in (0,132):check_zero_branch(teacher_output,observed,record['route_seconds'])
      if i%40==0:print(f'Replayed {i+1} frames; {record["route_seconds"]:.1f}s; updates {len(history)}',flush=True)
  finally:reader.close()
  if not history:raise ValueError('No eligible supervised examples after warm-up')
  unchanged=all(fingerprint(student.graph_values[name])==value for name,value in initial_frozen.items())
  if not unchanged:raise ValueError('Original model tensors changed during adapter optimization')
  checkpoint={name:student.graph_values[name]for name in PARAMETER_NAMES}
  safe_save(checkpoint,str(args.output/'adapter.safetensors'))
  # Compare pre/post-update predictions at the same last input/state. This is a
  # training-sample probe, not an independent or closed-loop driving metric.
  final=student(last_inputs);Tensor.realize(*final.values())
  final_values={name:value.numpy().copy()for name,value in final.items()}
  np.savez_compressed(args.output/'last-step-reference.npz',**final_values)
  normalized=np.abs((final_values['outputs'][:,2062:2064]-last_record['target'])/np.array([[3.,2.]]))
  q=np.minimum(normalized,1.)
  comparable_loss=float(((.5*q*q+normalized-q)*last_record['mask']).sum()/last_record['mask'].sum()+
                        .1*np.square(final_values['outputs'][:,887:917]-last_teacher[:,887:917]).mean())
  result={'device':device,'route_id':clip['route_id'],'steps':history,'original_initializers_checked':len(initial_frozen),
          'original_initializers_unchanged':unchanged,'real_camera_zero_branch_checks':zero_checks,'trainable_parameters':58160,
          'last_action_after_update':final_values['outputs'][:,2062:2064].tolist(),
          'last_original_action':last_teacher[:,2062:2064].tolist(),
          'last_target':last_record['target'].tolist(),'last_sample_loss_before_update':history[-1]['loss'],
          'last_sample_loss_after_update':comparable_loss,'last_auxiliary_max_abs_drift':float(np.max(np.abs(final_values['outputs'][:,887:917]-last_teacher[:,887:917]))),
          'scope':'One-route development overfit and gradient check with kinematic proxy labels; no held-out driving improvement',
          'trained_adapter_checkpoint_saved':True,'trained_model_exported':False,'modeld_ready':False}
  (args.output/'training-report.json').write_text(json.dumps(result,indent=2)+'\n')
  print(f'Saved {args.output/"training-report.json"}; offline research only',flush=True)


if __name__=='__main__':main()
