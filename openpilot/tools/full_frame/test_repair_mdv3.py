from pathlib import Path
import tempfile
import unittest

from openpilot.tools.full_frame.repair_mdv3 import publish_candidate


class TestResearchGraphPublication(unittest.TestCase):
  def test_failed_or_unexecuted_comparison_preserves_existing_graph(self):
    for result in ({'comparison_executed':True,'all_outputs_and_states_match':False},
                   {'comparison_executed':False,'all_outputs_and_states_match':True}):
      with tempfile.TemporaryDirectory() as directory:
        graph,candidate = Path(directory)/'graph.onnx',Path(directory)/'candidate.onnx'
        graph.write_bytes(b'preserve');candidate.write_bytes(b'rejected')
        self.assertFalse(publish_candidate(candidate,graph,result))
        self.assertEqual(graph.read_bytes(),b'preserve')
        self.assertFalse(candidate.exists())

  def test_completed_successful_comparison_publishes_candidate(self):
    with tempfile.TemporaryDirectory() as directory:
      graph,candidate = Path(directory)/'graph.onnx',Path(directory)/'candidate.onnx'
      graph.write_bytes(b'old');candidate.write_bytes(b'passed')
      self.assertTrue(publish_candidate(candidate,graph,{'comparison_executed':True,'all_outputs_and_states_match':True}))
      self.assertEqual(graph.read_bytes(),b'passed')
