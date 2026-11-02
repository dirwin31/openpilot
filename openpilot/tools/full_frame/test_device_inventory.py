import json
from pathlib import Path
import tempfile
import unittest

from openpilot.tools.full_frame.device_inventory import collect


class TestDeviceInventory(unittest.TestCase):
  def test_selection_and_source_identity_without_drives_or_weights(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      (root / 'preferences.json').write_text(json.dumps({'big': 'mountaindewv3', 'small': 'rdf43', 'unrelated': 'private'}))
      row = {'id': 'mountaindewv3', 'source_sha256': {'model.onnx': 'a'*64}, 'uses_external_gpu': True}
      (root / 'catalog.json').write_text(json.dumps({'models': [row, {'id': 'other'}]}))
      (root / 'mountaindewv3').mkdir()
      (root / 'mountaindewv3/model.pkl').write_bytes(b'not read')
      result = collect(root)
      self.assertTrue(result['mountain_dew_v3_found'])
      self.assertEqual(result['selected_catalog_rows'], [row])
      self.assertEqual(result['installed_files']['mountaindewv3'], [{'name': 'model.pkl', 'bytes': 8}])
      self.assertNotIn('unrelated', result['preferences'])

  def test_missing_catalog_is_not_mistaken_for_model_identity(self):
    with tempfile.TemporaryDirectory() as directory:
      result = collect(directory)
      self.assertIn('catalog', result['errors'])
      self.assertNotIn('mountain_dew_v3_found', result)
