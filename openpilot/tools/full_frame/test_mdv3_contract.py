import base64
import pickle
import unittest

from openpilot.tools.full_frame.mdv3_contract import slices


class TestMountainDewContract(unittest.TestCase):
  def test_actual_output_slice_encoding_is_decoded_with_slice_only_reader(self):
    raw = base64.b64encode(pickle.dumps({'action':slice(2062,2066)})).decode()
    self.assertEqual(slices(raw,18452),{'action':[2062,2066]})

  def test_non_slice_globals_are_rejected(self):
    raw = base64.b64encode(pickle.dumps(eval)).decode()
    with self.assertRaisesRegex(ValueError,'Only slice'):
      slices(raw,18452)

  def test_out_of_range_section_is_rejected(self):
    raw = base64.b64encode(pickle.dumps({'action':slice(2062,99999)})).decode()
    with self.assertRaisesRegex(ValueError,'Invalid output slice'):
      slices(raw,18452)
