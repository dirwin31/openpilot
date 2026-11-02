import unittest

import numpy as np

from openpilot.tools.full_frame.geometry import full_view, pack_yuv, teacher_view, temporal_pairs, unpack_nv12, warp_plane


class TestGeometry(unittest.TestCase):
  def test_packed_order_matches_compiler(self):
    y = np.arange(16, dtype=np.uint8).reshape(4, 4)
    packed = pack_yuv(y, np.full((2, 2), 99), np.full((2, 2), 101))
    np.testing.assert_array_equal(packed[:4, 0, 0], [0, 4, 1, 5])
    np.testing.assert_array_equal(packed[4:, 0, 0], [99, 101])

  def test_nv12_depadding_and_chroma(self):
    data = np.full((9, 8), 255, dtype=np.uint8)
    data[:4, :4] = np.arange(16).reshape(4, 4)
    data[6:8, :4] = [10, 20, 11, 21]
    y, u, v = unpack_nv12(data.ravel(), 4, 4, stride=8, y_height=6)
    np.testing.assert_array_equal(y, np.arange(16).reshape(4, 4))
    np.testing.assert_array_equal(u, [[10, 11], [10, 11]])
    np.testing.assert_array_equal(v, [[20, 21], [20, 21]])

  def test_full_frame_keeps_both_edges(self):
    y = np.tile(np.arange(32, dtype=np.uint8), (16, 1))
    packed, mask, affine = full_view((y, np.full((8, 16), 100), np.full((8, 16), 140)), 64, 64)
    self.assertEqual(mask.sum(), 64*32//4)
    self.assertEqual(packed[0, 8, 0], 0)
    self.assertEqual(packed[2, 8, -1], 31)
    self.assertEqual(packed[4, 8, 0], 100)
    self.assertEqual(packed[5, 8, 0], 140)
    np.testing.assert_allclose(affine, [[2, 0, .5], [0, 2, 16.5], [0, 0, 1]])

  def test_identity_teacher_and_chroma(self):
    planes = (np.arange(64, dtype=np.uint8).reshape(8, 8),
              np.arange(16, dtype=np.uint8).reshape(4, 4), np.full((4, 4), 80, dtype=np.uint8))
    np.testing.assert_array_equal(teacher_view(planes, np.eye(3), 8, 8), pack_yuv(*planes))

  def test_edge_clamp_and_reject_bad_projection(self):
    matrix = np.array([[1, 0, -10], [0, 1, 0], [0, 0, 1]])
    np.testing.assert_array_equal(warp_plane(np.arange(4).reshape(2, 2), matrix, 2, 2), [[0, 0], [2, 2]])
    with self.assertRaises(ValueError):
      warp_plane(np.ones((2, 2)), np.zeros((3, 3)), 2, 2)

  def test_temporal_skip_and_sync(self):
    times = np.arange(10) * .05
    self.assertEqual(temporal_pairs(times, times + .001), [(i, i+4) for i in range(6)])
    self.assertEqual(temporal_pairs(times, times + .03), [])

  def test_drop_does_not_cross_history(self):
    times = np.r_[np.arange(4)*.05, np.arange(5, 11)*.05]
    self.assertEqual(temporal_pairs(times, times), [(4, 8), (5, 9)])

  def test_bad_inputs_rejected(self):
    with self.assertRaises(ValueError):
      temporal_pairs([0, 0], [0, 0])
    with self.assertRaises(ValueError):
      unpack_nv12(np.zeros(24, dtype=np.uint8), 3, 4)
    with self.assertRaises(ValueError):
      full_view((np.zeros((4, 4)),)*3, 63, 32)


if __name__ == '__main__':
  unittest.main()
