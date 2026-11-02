from pathlib import Path
import tempfile
import unittest

import numpy as np

from openpilot.tools.full_frame.mdv3_adapter import add_full_view_branch
from openpilot.tools.full_frame.mdv3_training import adapter_parameters,action_step
from openpilot.tools.full_frame.test_mdv3_adapter import fixture


class TestAdapterOnlyTraining(unittest.TestCase):
  def test_action_update_preserves_frozen_weights_and_detaches_state(self):
    import onnx
    from tinygrad import Tensor
    from tinygrad.nn.onnx import OnnxRunner
    from tinygrad.nn.optim import Adam
    source,_ = add_full_view_branch(fixture())
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory)/'fixture.onnx';onnx.save(source,path)
      runner = OnnxRunner(path)
      parameters = adapter_parameters(runner)
      before = {name:value.numpy().copy() for name,value in runner.graph_values.items() if value is not None}
      optimizer = Adam(parameters,lr=.001)
      rng = np.random.default_rng(3)
      inputs = {'full_img':Tensor(rng.integers(16,235,(2,6,128,256),dtype=np.uint8)),
                'state_feat_q':Tensor(np.zeros((1,32,512),np.float16))}
      first = action_step(runner,optimizer,inputs,[[.2,-.3]],[[True,True]],
                          aux_weight=0,action_slice=(0,2))
      self.assertGreater(first['adapter_gradient_l2'],0)
      self.assertIn('state_feat_q',first['next_state'])
      changed = []
      for name,old in before.items():
        if not np.array_equal(old,runner.graph_values[name].numpy()):
          changed.append(name)
      self.assertTrue(changed)
      self.assertTrue(all(name.startswith('ff_projection_') for name in changed))
      # Following a projection update, gradients reach and update the encoder.
      action_step(runner,optimizer,inputs,[[.2,-.3]],[[True,True]],aux_weight=0,action_slice=(0,2))
      self.assertFalse(np.array_equal(before['ff_conv0_weight'],runner.graph_values['ff_conv0_weight'].numpy()))
      np.testing.assert_array_equal(before['action_weight'],runner.graph_values['action_weight'].numpy())

  def test_frozen_model_parameter_cannot_be_added_to_optimizer(self):
    import onnx
    from tinygrad.nn.onnx import OnnxRunner
    from tinygrad.nn.optim import Adam
    source,_ = add_full_view_branch(fixture())
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory)/'fixture.onnx';onnx.save(source,path)
      runner = OnnxRunner(path)
      optimizer = Adam(adapter_parameters(runner)+[runner.graph_values['action_weight']],lr=.001)
      with self.assertRaisesRegex(ValueError,'exactly the full-view'):
        action_step(runner,optimizer,{},[[0,0]],[[True,True]],aux_weight=0,action_slice=(0,2))
