from unittest.mock import patch
import unittest

import numpy as np

from openpilot.tools.full_frame.mdv3_data import asof,decoded_frames


class TestRecordedFrameSelection(unittest.TestCase):
  def test_asof_does_not_mark_future_or_stale_records_valid(self):
    rows=np.array([[1.,4.],[1.1,5.]])
    values,valid=asof(rows,[.9,1.05,1.3],.1)
    np.testing.assert_array_equal(valid,[False,True,False])
    self.assertEqual(values[1,0],4.)

  def test_logged_presentation_indexes_override_decoder_fps_metadata(self):
    closed=[]
    def reader(path,**kwargs):
      try:
        yield {'size':(1344,760),'fps':25.}
        for value in (17,18,19):
          pixels=np.full((1140,1344),128,np.uint8);pixels[:760]=value
          yield pixels.tobytes()
      finally:closed.append(path)
    matrix=np.eye(3)
    frames=[{'segment':'route--0','road_index':i,'wide_index':i,'timestamp':i*.05,
             'road_transform':matrix,'wide_transform':matrix} for i in (0,2)]
    with patch('imageio_ffmpeg.read_frames',side_effect=reader):
      output=list(decoded_frames('/unused',{'frames':frames,'camera_size':(1344,760)}))
    self.assertEqual(len(output),2)
    self.assertEqual(output[0][1]['new_img'][0,0,0,0],17)
    self.assertEqual(output[1][1]['new_img'][0,0,0,0],19)
    self.assertEqual(len(closed),2)
