import bz2
from pathlib import Path
import tempfile
import unittest

import numpy as np

from openpilot.tools.full_frame.calibration import nominal_warp


class TestCaptureSupport(unittest.TestCase):
  def test_nominal_road_centre(self):
    matrix = nominal_warp([0, 0, 0], 'mici', 'os04c10')
    np.testing.assert_allclose(matrix @ [256, 47.6, 1], [672, 380, 1], atol=1e-10)
    self.assertAlmostEqual(matrix[0, 0], 1141.5/910)

  def test_nominal_wide_centre(self):
    matrix = nominal_warp([0, 0, 0], 'mici', 'os04c10', True)
    np.testing.assert_allclose(matrix @ [256, 151.8, 1], [672, 380, 1], atol=1e-10)
    self.assertAlmostEqual(matrix[0, 0], 425.25/455)

  def test_unknown_sensor_rejected(self):
    with self.assertRaises(ValueError):
      nominal_warp([0, 0, 0], 'mici', 'unknown-sensor')

  def test_local_log_raw_bz2_zstd(self):
    from cereal import log
    import zstandard
    from openpilot.tools.full_frame.local_log import read_events
    event = log.Event.new_message()
    event.logMonoTime = 12345
    event.init('livePose')
    data = event.to_bytes()
    with tempfile.TemporaryDirectory() as directory:
      for filename, contents in [('rlog', data), ('rlog.bz2', bz2.compress(data)), ('rlog.zst', zstandard.compress(data))]:
        path = Path(directory) / filename
        path.write_bytes(contents)
        events = list(read_events(path))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].logMonoTime, 12345)
        self.assertEqual(events[0].which(), 'livePose')


if __name__ == '__main__':
  unittest.main()
