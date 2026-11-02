import unittest

import numpy as np

from openpilot.tools.full_frame.mdv3_sequence import camera_inputs,traffic_convention,DesirePulse,SequenceGuard


class TestMountainDewSequence(unittest.TestCase):
  def test_full_views_include_edges_outside_the_retained_calibrated_crop(self):
    road = np.full((512,1024),100,np.uint8);road[:,:64]=17;road[:,-64:]=230
    wide = np.full((512,1024),110,np.uint8);wide[:,:64]=40;wide[:,-64:]=210
    uv = np.full((256,512),128,np.uint8)
    crop = np.array([[1,0,256],[0,1,128],[0,0,1]])
    inputs = camera_inputs((road,uv,uv),(wide,uv,uv),crop,crop)
    self.assertEqual(inputs['new_img'].shape,(2,6,128,256))
    self.assertEqual(inputs['full_img'].dtype,np.uint8)
    np.testing.assert_array_equal(inputs['new_img'][0,:4],100)
    np.testing.assert_array_equal(inputs['full_img'][0,:4,:,0],17)
    np.testing.assert_array_equal(inputs['full_img'][0,:4,:,-1],230)
    np.testing.assert_array_equal(inputs['full_img'][1,:4,:,0],40)
    np.testing.assert_array_equal(inputs['full_img'][1,:4,:,-1],210)

  def test_real_20hz_history_reaches_warmup_and_route_change_resets(self):
    guard = SequenceGuard()
    for i in range(132):
      step = guard.update('route-a',i,100+i*.05)
      self.assertEqual(step.ready,i==131)
    step = guard.update('route-b',0,200.)
    self.assertTrue(step.reset)
    self.assertFalse(step.ready)

  def test_decimated_5hz_samples_cannot_silently_warm_a_20hz_model(self):
    guard = SequenceGuard()
    for i in range(200):
      self.assertFalse(guard.update('route',i*4,i*.2).ready)

  def test_timestamp_gap_resets_even_with_consecutive_frame_ids(self):
    guard = SequenceGuard(warmup_frames=5)
    for i in range(5):
      step = guard.update('route',i,i*.05)
    self.assertTrue(step.ready)
    step = guard.update('route',5,.5)
    self.assertTrue(step.reset)
    self.assertEqual(step.reason,'not_20hz')

  def test_desire_pulses_are_rising_edges_and_reset_with_the_route(self):
    pulse = DesirePulse()
    self.assertEqual(pulse.update(3)[3],1)
    self.assertEqual(pulse.update(3).sum(),0)
    self.assertEqual(pulse.update(0).sum(),0)
    self.assertEqual(pulse.update(3)[3],1)
    pulse.reset()
    self.assertEqual(pulse.update(3)[3],1)

  def test_traffic_convention_matches_runtime_rhd_index(self):
    np.testing.assert_array_equal(traffic_convention(False),[[1,0]])
    np.testing.assert_array_equal(traffic_convention(True),[[0,1]])
    with self.assertRaises(ValueError):
      traffic_convention(None)
