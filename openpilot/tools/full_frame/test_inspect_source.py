import hashlib
from pathlib import Path
import tempfile
import unittest

from openpilot.tools.full_frame.inspect_source import inspect


class TestSourceInspection(unittest.TestCase):
  def test_wrong_digest_rejected_before_graph_parsing(self):
    with tempfile.TemporaryDirectory() as directory:
      source = Path(directory) / 'model.onnx'
      source.write_bytes(b'not an ONNX')
      with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
        inspect(source, {'id': 'model', 'source_sha256': {'model.onnx': '0'*64}})

  def test_graph_contract_and_metadata_without_execution(self):
    import onnx
    from onnx import helper, TensorProto
    with tempfile.TemporaryDirectory() as directory:
      source = Path(directory) / 'model.onnx'
      graph = helper.make_graph([helper.make_node('Identity', ['state'], ['next_state'])], 'test',
        [helper.make_tensor_value_info('state', TensorProto.FLOAT, ['batch', 512])],
        [helper.make_tensor_value_info('next_state', TensorProto.FLOAT, ['batch', 512])])
      model = helper.make_model(graph)
      helper.set_model_props(model, {'output_slices': 'not deserialized'})
      onnx.save(model, source)
      result = inspect(source, {'id': 'model', 'source_sha256': {'model.onnx': hashlib.sha256(source.read_bytes()).hexdigest()}})
      self.assertEqual(result['state_pairs_by_name'], {'state': 'next_state'})
      self.assertEqual(result['inputs'][0]['shape'], ['batch', 512])
      self.assertIsNone(result['metadata']['output_slices']['value'])
      self.assertFalse(result['production_ready'])
