"""Offline adapter-only action-loss step; no device/modeld or deployment interface.

Callers are responsible for calibrated delayed targets, real 20 Hz state replay,
route splits and demonstration quality. This is not a complete route trainer.
"""
import numpy as np

PARAMETER_NAMES = tuple([f'ff_conv{i}_{kind}' for i in range(3) for kind in ('weight','bias')]+
                        ['ff_projection_weight','ff_projection_bias'])


def adapter_parameters(runner):
  from tinygrad import dtypes
  values = runner.graph_values
  if not all(name in values for name in PARAMETER_NAMES):
    raise ValueError('Runner is missing the full-view branch parameters')
  parameters = [values[name] for name in PARAMETER_NAMES]
  if sum(value.numel() for value in parameters)!=58160 or any(value.dtype!=dtypes.float32 for value in parameters):
    raise ValueError('Unexpected adapter parameter contract')
  return parameters


def action_step(runner,optimizer,inputs,target,mask,*,reference_aux=None,aux_weight=.1,
                action_slice=(2062,2064),aux_slice=(887,917)):
  """One-frame truncated action step, with detached pre-update recurrent state.

  A real MDv3 training step requires original auxiliary-head references. The
  fixture can set aux_weight=0 to test the mechanics on a small graph.
  Compute gradients before realizing inference buffers: realization discards
  their forward graph in the pinned tinygrad runtime.
  """
  from tinygrad import Tensor
  from tinygrad.helpers import Context
  parameters = adapter_parameters(runner)
  if len(optimizer.params)!=len(parameters) or {id(p)for p in optimizer.params}!={id(p)for p in parameters}:
    raise ValueError('Optimizer must contain exactly the full-view parameters')
  target,mask = np.asarray(target,dtype=np.float32),np.asarray(mask,dtype=bool)
  if target.shape!=(1,2) or mask.shape!=(1,2) or not mask.any() or not np.isfinite(target).all():
    raise ValueError('Need finite two-axis targets and at least one valid axis')
  if aux_weight<0 or (aux_weight>0 and reference_aux is None):
    raise ValueError('Provide baseline auxiliary-head references for regularized training')
  if action_slice[1]-action_slice[0]!=2:
    raise ValueError('Action slice must contain exactly the two action means')
  with Context(TRAINING=1):
    optimizer.zero_grad()
    output = runner(inputs)
    raw = output['outputs'].cast('float32')
    if raw.ndim!=2 or raw.shape[0]!=1 or raw.shape[1]<action_slice[1]:
      raise ValueError('Unexpected raw action output')
    action = raw[:,action_slice[0]:action_slice[1]]
    error = (action-Tensor(target))/Tensor([[3.,2.]])
    absolute = error.abs()
    quadratic = absolute.minimum(1.)
    loss = ((.5*quadratic.square()+absolute-quadratic)*Tensor(mask.astype(np.float32))).sum()/int(mask.sum())
    if aux_weight:
      reference_aux = np.asarray(reference_aux,dtype=np.float32)
      if reference_aux.shape!=(1,aux_slice[1]-aux_slice[0]) or not np.isfinite(reference_aux).all():
        raise ValueError('Invalid baseline auxiliary reference')
      loss = loss+aux_weight*(raw[:,aux_slice[0]:aux_slice[1]]-Tensor(reference_aux)).square().mean()
    gradients = loss.gradient(*parameters)
    # Freeze the step's outputs/state and gradients before optimizer mutation.
    Tensor.realize(loss,action,*gradients,*output.values())
    value = float(loss.item())
    arrays = [g.numpy() for g in gradients]
    if not np.isfinite(value) or not all(np.isfinite(g).all() for g in arrays):
      raise ValueError('Nonfinite loss or adapter gradient')
    for parameter,gradient in zip(parameters,gradients):
      parameter.grad = gradient
    next_state = {name:output['next_'+name].detach().contiguous().realize()
                  for name in runner.graph_inputs if 'next_'+name in runner.graph_outputs}
    action_value = action.numpy().copy()
    optimizer.step()
  return {'loss':value,'action_before_update':action_value,
          'adapter_gradient_l2':float(np.sqrt(sum(float(np.square(g.astype(np.float64)).sum())for g in arrays))),
          'next_state':next_state,'scope':'One-frame truncated action supervision; not a driving-quality result'}
