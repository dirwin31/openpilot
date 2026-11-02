import tempfile
from pathlib import Path
import unittest

import numpy as np

from openpilot.tools.full_frame.mdv3_adapter import add_full_view_branch
from openpilot.tools.full_frame.test_mdv3_adapter import fixture
from openpilot.tools.full_frame.diagnose_mdv3 import diagnose


class TestMountainDewDiagnostics(unittest.TestCase):
  def test_identity_control_cannot_qualify_a_nonzero_residual_model(self):
    import onnx
    from onnx import numpy_helper
    source = fixture()
    graph,_ = add_full_view_branch(source,buffered=False)
    bias = next(t for t in graph.graph.initializer if t.name=='ff_projection_bias')
    bias.CopyFrom(numpy_helper.from_array(np.full((512,),.03,np.float32),'ff_projection_bias'))
    with tempfile.TemporaryDirectory() as directory:
      original,full = Path(directory)/'original.onnx',Path(directory)/'full.onnx'
      onnx.save(source,original);onnx.save(graph,full)
      report = diagnose(original,full,'CPU',frames=2)
    self.assertTrue(report['original_repeatability']['all_outputs_and_states_match'])
    self.assertTrue(report['original_initializers_identical'])
    self.assertGreater(report['original_initializers_checked'],0)
    self.assertFalse(report['all_outputs_and_states_match'])
    self.assertFalse(report['variants']['residual']['all_outputs_and_states_match'])
    self.assertTrue(report['variants']['identity_control']['all_outputs_and_states_match'])
    self.assertFalse(report['variants']['buffered_add_control']['all_outputs_and_states_match'])
    self.assertGreater(report['variants']['residual']['per_frame'][0]['candidate_residual_max_abs'],0)
    self.assertFalse(report['modeld_ready'])

  def test_exported_buffered_graph_is_supported(self):
    import onnx
    source = fixture()
    graph,_ = add_full_view_branch(source)
    with tempfile.TemporaryDirectory() as directory:
      original,full = Path(directory)/'original.onnx',Path(directory)/'full.onnx'
      onnx.save(source,original);onnx.save(graph,full)
      report = diagnose(original,full,'CPU',frames=2)
    self.assertTrue(report['all_outputs_and_states_match'])
    self.assertTrue(report['original_initializers_identical'])
