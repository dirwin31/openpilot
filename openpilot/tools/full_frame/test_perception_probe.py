import unittest

import numpy as np

from openpilot.tools.full_frame.perception_probe import nms, proposals


class TestPerceptionProbe(unittest.TestCase):
  def test_greedy_nms_preserves_nonoverlap(self):
    boxes=np.array([[0.,0,10,10],[1,1,11,11],[30,30,40,40]])
    self.assertEqual(nms(boxes,np.array([.9,.8,.7])),[0,2])

  def test_pixel_mapping_and_class_identity(self):
    raw=np.zeros((1,84,1),dtype=np.float32)
    raw[0,:4,0]=[50,60,20,10]
    raw[0,15,0]=.8
    result=proposals(raw,(.5,.5,0,10),200,100)
    self.assertEqual(result[0]['class'],'stop sign')
    np.testing.assert_allclose(result[0]['box_xyxy_pixels'],[80,90,120,100])

  def test_low_scores_do_not_become_labels(self):
    self.assertEqual(proposals(np.zeros((1,84,1)),(1,1,0,0),100,100),[])
