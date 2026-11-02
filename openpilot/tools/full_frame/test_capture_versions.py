import unittest

import numpy as np

from openpilot.tools.full_frame.capture import event_contract,pose_row


class TestCaptureVersions(unittest.TestCase):
  def test_current_and_historical_message_names_are_explicit(self):
    current = event_contract({'deviceMotion','extrinsicsCalibration','narrowRoadCameraState','narrowRoadEncodeIdx'})
    old = event_contract({'livePose','liveCalibration','roadCameraState','roadEncodeIdx'})
    self.assertEqual(current['pose'],'deviceMotion')
    self.assertEqual(old['pose'],'livePose')
    self.assertEqual(current['road_index'],'narrowRoadEncodeIdx')

  def test_current_pose_uses_sensor_time_not_publication_time(self):
    from openpilot.cereal import log
    event = log.Event.new_message(logMonoTime=1025000000,valid=True)
    pose = event.init('deviceMotion')
    pose.timestamp = 1000000000
    pose.inputsOK=pose.sensorsOK=pose.posenetOK=True
    pose.orientationNED.valid=pose.velocityDevice.valid=True
    pose.velocityDevice.x=12.
    t,_,velocity,good = pose_row(event,'deviceMotion')
    self.assertEqual(t,1.)
    self.assertTrue(good)
    self.assertEqual(velocity[0],12.)

  def test_unknown_sensor_time_cannot_become_a_valid_target(self):
    from openpilot.cereal import log
    event = log.Event.new_message(logMonoTime=1025000000,valid=True)
    pose = event.init('deviceMotion')
    pose.inputsOK=pose.sensorsOK=pose.posenetOK=True
    pose.orientationNED.valid=pose.velocityDevice.valid=True
    self.assertFalse(pose_row(event,'deviceMotion')[3])

  def test_nonfinite_vectors_remain_explicitly_invalid(self):
    from openpilot.cereal import log
    event = log.Event.new_message(logMonoTime=1025000000,valid=True)
    pose = event.init('deviceMotion')
    pose.timestamp=1000000000
    pose.inputsOK=pose.sensorsOK=pose.posenetOK=True
    pose.orientationNED.valid=pose.velocityDevice.valid=True
    pose.velocityDevice.x=float('nan')
    row = pose_row(event,'deviceMotion')
    self.assertFalse(row[3])
    self.assertTrue(np.isfinite(row[2]).all())
