"""Audit local recorded action-label signals and assistance, without inventing targets."""
import argparse
import json
from pathlib import Path

import numpy as np

from openpilot.tools.full_frame.local_log import read_events


def summarize(path, schema_root):
  car, pose, control, frames = [], [], [], []
  for msg in read_events(path,schema_root):
    kind = msg.which()
    t = msg.logMonoTime/1e9
    if kind == 'carState':
      x = msg.carState
      car.append((t,float(x.vEgo),float(x.aEgo),float(x.yawRate),bool(msg.valid and x.canValid)))
    elif kind == 'carControl':
      x = msg.carControl
      control.append((t,bool(x.latActive),bool(x.longActive),bool(msg.valid)))
    elif kind == 'livePose':
      x = msg.livePose
      ok = bool(msg.valid and x.inputsOK and x.posenetOK and x.sensorsOK)
      pose.append((t,float(x.angularVelocityDevice.z),float(x.accelerationDevice.x),
                   bool(ok and x.angularVelocityDevice.valid),bool(ok and x.accelerationDevice.valid)))
    elif kind == 'roadEncodeIdx' and msg.valid and msg.roadEncodeIdx.timestampSof:
      frames.append(msg.roadEncodeIdx.timestampSof/1e9)
  def match(rows):
    if len(rows)==0:
      return np.zeros(len(frames),bool),np.zeros(len(frames),int)
    times = np.asarray(rows)[:,0]
    indices = np.searchsorted(times,frames,side='right')-1
    return (indices>=0)&(np.asarray(frames)-times[np.maximum(indices,0)]<=.1),np.maximum(indices,0)
  car = np.asarray(car);control = np.asarray(control);pose = np.asarray(pose)
  good_car, ci = match(car)
  good_control, ai = match(control)
  good_pose, pi = match(pose)
  if len(car):
    good_car &= car[ci,4].astype(bool)&np.isfinite(car[ci,1:4]).all(axis=1)
  if len(control):
    good_control &= control[ai,3].astype(bool)
  valid = good_car&good_control
  moving = good_car&(car[ci,1]>2) if len(car) else good_car
  unassisted = valid&~(control[ai,1].astype(bool)|control[ai,2].astype(bool)) if len(control) else valid
  moving_manual = moving&unassisted
  moving_assisted = moving&valid&~unassisted
  yaw_ok = good_pose&pose[pi,3].astype(bool)&np.isfinite(pose[pi,1]) if len(pose) else good_pose
  accel_ok = good_pose&pose[pi,4].astype(bool)&np.isfinite(pose[pi,2]) if len(pose) else good_pose
  def span(values):
    return [float(np.min(values)),float(np.max(values))] if len(values) else None
  result = {'segment':path.parent.name,'camera_frames':len(frames),'matched_valid_car_state':int(good_car.sum()),
            'matched_valid_control_state':int(good_control.sum()),'moving_frames_speed_over_2mps':int(moving.sum()),
            'moving_both_axes_unassisted':int(moving_manual.sum()),'moving_at_least_one_axis_assisted':int(moving_assisted.sum()),
            'matched_valid_pose_yaw_rate':int(yaw_ok.sum()),'matched_valid_pose_acceleration':int(accel_ok.sum()),
            'can_yaw_rate_range_radps':span(car[ci[moving],3]) if len(car) else None,
            'pose_yaw_rate_range_radps':span(pose[pi[yaw_ok],1]) if len(pose) else None,
            'can_acceleration_range_mps2':span(car[ci[good_car],2]) if len(car) else None,
            'first_valid_pose_seconds_after_first_camera':float(pose[pi[yaw_ok][0],0]-frames[0]) if yaw_ok.any() else None,
            'candidate_moving_unassisted_with_pose':int((moving_manual&yaw_ok&accel_ok).sum())}
  return result


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('segments',type=Path)
  parser.add_argument('--schema-root',type=Path,required=True)
  parser.add_argument('--output',type=Path,required=True)
  args = parser.parse_args()
  if args.output.exists():
    parser.error('Use a new output report')
  segments = []
  for path in sorted(args.segments.glob('*/rlog.zst')):
    row = summarize(path,args.schema_root)
    segments.append(row)
    print(json.dumps(row),flush=True)
  if not segments:
    parser.error('No segment rlog.zst files found')
  result = {'segments':segments,'scope':'Signal availability audit only, not expert driving labels or a training benchmark',
            'lateral_units':'Raw CAN/pose yaw rates are inspected, not silently converted to calibrated action targets',
            'assistance':'Both axes unassisted is a candidate demonstration interval, not a quality label',
            'stop_sign_example':'The previously inspected route49 segment0 approach is not a correct-stop demonstration'}
  args.output.write_text(json.dumps(result,indent=2)+'\n')


if __name__ == '__main__':
  main()
