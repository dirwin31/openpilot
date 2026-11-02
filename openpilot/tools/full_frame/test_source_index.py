from io import BytesIO
import json
import unittest
from unittest.mock import patch

from openpilot.tools.full_frame.source_index import inventory


class Response(BytesIO):
  def __init__(self, rows, link=''):
    super().__init__(json.dumps(rows).encode())
    self.headers = {'Link': link}


class TestSourceIndex(unittest.TestCase):
  def test_follows_pagination_and_keeps_only_sources(self):
    pages = [Response([{'type': 'file', 'path': 'models/a.pkl'}], '<https://huggingface.co/page2>; rel="next"'),
             Response([{'type': 'file', 'path': 'onnx/mountaindewv3/driving_supercombo.onnx', 'size': 10}])]
    with patch('openpilot.tools.full_frame.source_index.urlopen', side_effect=pages):
      result = inventory()
    self.assertTrue(result['complete'])
    self.assertEqual(result['pages'], 2)
    self.assertEqual(len(result['onnx_objects']), 1)

  def test_partial_inventory_is_not_reported_as_complete(self):
    with patch('openpilot.tools.full_frame.source_index.urlopen', side_effect=[
        Response([], '<https://huggingface.co/page2>; rel="next"'), OSError('unavailable')]):
      with self.assertRaises(OSError):
        inventory()
