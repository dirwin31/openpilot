import tempfile
from pathlib import Path
import unittest

import numpy as np

from openpilot.tools.full_frame.mdv3_adapter import add_full_view_branch, verify_native


def fixture():
  from onnx import helper, numpy_helper, TensorProto
  rng = np.random.default_rng(4)
  initializers = [numpy_helper.from_array(rng.normal(size=(1,32,512)).astype(np.float16),'features'),
                  numpy_helper.from_array(np.array([1,32,512],np.int64),'feature_shape'),
                  numpy_helper.from_array(np.zeros((512,1,1,1),np.float16),'model.vision.encoder.head.fc.weight'),
                  numpy_helper.from_array(rng.normal(size=(512,4)).astype(np.float32)*.01,'action_weight')]
  nodes = [helper.make_node('Add',['features','state_feat_q'],['base_with_history']),
           helper.make_node('Reshape',['base_with_history','feature_shape'],['view_39']),
           helper.make_node('ReduceMean',['view_39'],['pooled'],axes=[1],keepdims=0),
           helper.make_node('Cast',['pooled'],['pooled_float'],to=TensorProto.FLOAT),
           helper.make_node('MatMul',['pooled_float','action_weight'],['outputs']),
           helper.make_node('Identity',['view_39'],['next_state_feat_q'])]
  graph = helper.make_graph(nodes,'contract-fixture',[helper.make_tensor_value_info('state_feat_q',TensorProto.FLOAT16,[1,32,512])],
    [helper.make_tensor_value_info('outputs',TensorProto.FLOAT,[1,4]),
     helper.make_tensor_value_info('next_state_feat_q',TensorProto.FLOAT16,[1,32,512])],initializers)
  return helper.make_model(graph,opset_imports=[helper.make_opsetid('',17)])


class TestMountainDewAdapter(unittest.TestCase):
  def test_zero_branch_preserves_output_and_memory_then_action_loss_reaches_encoder(self):
    import onnx
    from tinygrad import Tensor
    from tinygrad.nn.onnx import OnnxRunner
    from tinygrad.helpers import Context
    original = fixture()
    adapted, contract = add_full_view_branch(original)
    with tempfile.TemporaryDirectory() as directory:
      base_path, full_path = Path(directory)/'base.onnx', Path(directory)/'full.onnx'
      onnx.save(original,base_path);onnx.save(adapted,full_path)
      base, full = OnnxRunner(base_path), OnnxRunner(full_path)
      state = Tensor(np.zeros((1,32,512),np.float16))
      expected = base({'state_feat_q':state})
      image = np.random.default_rng(2).integers(16,236,(2,6,128,256),dtype=np.uint8)
      output = full({'full_img':Tensor(image),'state_feat_q':state})
      for name in ('outputs','next_state_feat_q'):
        np.testing.assert_array_equal(output[name].numpy(),expected[name].numpy())
      # Build a fresh lazy training forward before differentiating; realizing
      # inference buffers discards their graph in this tinygrad revision.
      output = full({'full_img':Tensor(image),'state_feat_q':state})
      # Projection is the first trainable stage at zero initialization.
      loss = (output['outputs']-1).square().mean()
      projection = full.graph_values['ff_projection_weight']
      gradient = loss.gradient(projection)[0].numpy()
      self.assertTrue(np.isfinite(gradient).all())
      self.assertGreater(np.linalg.norm(gradient),0)
      # A nonzero projection opens the gradient to the full-view image encoder.
      full.graph_values['ff_projection_weight'] = Tensor((-gradient*.01).astype(np.float32))
      with Context(TRAINING=1):
        changed = full({'full_img':Tensor(image),'state_feat_q':state})
        loss = (changed['outputs']-1).square().mean()
        gradient = loss.gradient(full.graph_values['ff_conv0_weight'])[0].numpy()
      self.assertTrue(np.isfinite(gradient).all())
      self.assertGreater(np.linalg.norm(gradient),0)
      peripheral = image.copy();peripheral[:,:,:,:48]=128
      masked = full({'full_img':Tensor(peripheral),'state_feat_q':state})
      self.assertGreater(float(np.max(np.abs(changed['outputs'].numpy()-masked['outputs'].numpy()))),0)
      parity = verify_native(base_path,full_path,'CPU',frames=3)
      self.assertTrue(parity['all_outputs_and_states_match'])
      self.assertIn('next_state_feat_q',parity['max_abs_difference'])
    self.assertFalse(contract['modeld_ready'])
    self.assertEqual([v.name for v in original.graph.output],[v.name for v in adapted.graph.output])
    self.assertNotIn('full_img',[v.name for v in original.graph.input])

  def test_unexpected_feature_tap_rejected(self):
    source = fixture()
    source.graph.node[1].output[0] = 'other_features'
    with self.assertRaisesRegex(ValueError,'feature tap'):
      add_full_view_branch(source)
