import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np


class TestOfflineTrainingDriver(unittest.TestCase):
  def test_one_update_preserves_frozen_tensors_and_writes_checkpoint(self):
    import onnx
    from onnx import helper,numpy_helper,TensorProto
    from openpilot.tools.full_frame.test_mdv3_adapter import fixture
    from openpilot.tools.full_frame.mdv3_adapter import add_full_view_branch
    from openpilot.tools.full_frame.mdv3_fit import main
    original=fixture()
    for node in original.graph.node:
      if list(node.output)==['outputs']:node.output[0]='action_raw'
    original.graph.initializer.extend([numpy_helper.from_array(np.zeros((1,2062),np.float32),'head_padding'),
                                      numpy_helper.from_array(np.zeros((1,2),np.float32),'tail_padding'),
                                      numpy_helper.from_array(np.array([1,16384],np.int64),'flat_shape')])
    original.graph.node.extend([helper.make_node('Reshape',['view_39','flat_shape'],['flat_half']),
                               helper.make_node('Cast',['flat_half'],['flat'],to=TensorProto.FLOAT),
                               helper.make_node('Concat',['head_padding','action_raw','flat','tail_padding'],['outputs'],axis=1)])
    original.graph.output[0].CopyFrom(helper.make_tensor_value_info('outputs',TensorProto.FLOAT,[1,18452]))
    graph,contract=add_full_view_branch(original)
    records=[{'frame_id':i,'timestamp':45+i*.05,'route_seconds':45+i*.05,
              'action_t':np.array([[.4,.5]],np.float32),'traffic_convention':np.array([[1,0]],np.float32),
              'target':np.array([[.2,-.3]],np.float32),'mask':np.array([[True,True]])}for i in range(241)]
    pixels=np.full((2,6,128,256),100,np.uint8)
    def decoded(*args):
      for row in records:yield row,{'new_img':pixels,'full_img':pixels}
    with tempfile.TemporaryDirectory() as directory:
      root=Path(directory);source=root/'source.onnx';adapted=root/'adapted.onnx';output=root/'run'
      onnx.save(original,source);onnx.save(graph,adapted)
      sha=hashlib.sha256(source.read_bytes()).hexdigest()
      contract['native_output_parity_verified']=True
      adapted.with_suffix('.contract.json').write_text(json.dumps(contract))
      args=['mdv3_fit','--source',str(source),'--graph',str(adapted),'--segments',str(root),
            '--schema-root',str(root),'--output',str(output),'--device','CPU','--steps','1']
      with patch.object(sys,'argv',args),patch('openpilot.tools.full_frame.mdv3_adapter.SOURCE_SHA256',sha),\
           patch('openpilot.tools.full_frame.mdv3_data.build_clip',return_value={'route_id':'fixture','frames':records}),\
           patch('openpilot.tools.full_frame.mdv3_data.decoded_frames',side_effect=decoded):
        main()
      result=json.loads((output/'training-report.json').read_text())
      self.assertEqual(len(result['steps']),1)
      self.assertEqual(len(result['real_camera_zero_branch_checks']),3)
      self.assertTrue(result['original_initializers_unchanged'])
      self.assertTrue((output/'adapter.safetensors').is_file())
      self.assertFalse(result['modeld_ready'])
