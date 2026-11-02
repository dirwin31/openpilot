"""Calibrated motion and explicit kinematic action proxies for offline research.

These are measured-motion imitation proxies, not comma's original training
loss or automatically correct expert labels. Future times must be aligned to
the model's separate action_t delays before using the helper.
"""
import numpy as np

from openpilot.tools.full_frame.trajectory import rotation


def calibrated_motion(velocity_device,angular_velocity_device,acceleration_device,rpy_calib):
  values = [np.asarray(v,dtype=np.float64) for v in
            (velocity_device,angular_velocity_device,acceleration_device,rpy_calib)]
  if any(v.shape!=(3,) or not np.isfinite(v).all() for v in values):
    raise ValueError('Need finite three-component motion vectors and calibration')
  matrix = rotation(values[-1])[0].T
  return tuple(matrix@v for v in values[:3])


def action_proxy(anchor_speed, future_velocity_calib, future_angular_velocity_calib,
                 future_longitudinal_accel_calib, min_lateral_speed=2.):
  """Encode future measured curvature in Dom v16's current-speed action units.

  First components use the lateral action horizon; longitudinal acceleration
  uses its own horizon. Below min_lateral_speed, the lateral target is masked,
  not taught to be straight. Longitudinal stationary labels may still be valid.
  """
  v,w = np.asarray(future_velocity_calib),np.asarray(future_angular_velocity_calib)
  if (not np.isfinite(anchor_speed) or anchor_speed<0 or v.shape!=(3,) or w.shape!=(3,) or
      not np.isfinite(v).all() or not np.isfinite(w).all() or
      not np.isfinite(future_longitudinal_accel_calib) or min_lateral_speed<=0):
    raise ValueError('Invalid action-proxy input')
  lateral_ok = bool(anchor_speed>=min_lateral_speed and v[0]>=min_lateral_speed)
  # Runtime divides this first action mean by max(1, current vEgo)^2.
  # Encode the desired measured future curvature using that same anchor speed.
  lateral = w[2]/v[0]*max(1.,anchor_speed)**2 if lateral_ok else 0.
  return np.array([[lateral,future_longitudinal_accel_calib]],np.float32),np.array([[lateral_ok,True]])


def aligned_future(times,values,valid,eligible,anchor,delay,max_gap=.1):
  """Interpolate only inside a fully valid eligible interval through the delay.

  eligible must include the caller's assistance/gear/calibration/quality mask.
  This prevents targets crossing engagement transitions or extrapolating past
  the recorded data. It does not itself certify human demonstration quality.
  """
  times,values = np.asarray(times),np.asarray(values)
  valid,eligible = np.asarray(valid,dtype=bool),np.asarray(eligible,dtype=bool)
  if (times.ndim!=1 or len(times)<2 or len(values)!=len(times) or valid.shape!=times.shape or
      eligible.shape!=times.shape or not np.isfinite(times).all() or not np.isfinite(values).all() or
      np.any(np.diff(times)<=0) or not np.isfinite(anchor) or not np.isfinite(delay) or delay<=0 or max_gap<=0):
    raise ValueError('Invalid delayed signal stream')
  first = int(np.searchsorted(times,anchor,side='right')-1)
  last = int(np.searchsorted(times,anchor+delay,side='left'))
  if first<0 or last>=len(times) or anchor-times[first]>max_gap:
    return None
  window = slice(first,last+1)
  if not (valid[window]&eligible[window]).all() or np.any(np.diff(times[window])>max_gap+1e-9):
    return None
  low = max(first,last-1)
  if times[last]==anchor+delay:
    return values[last].copy()
  fraction = (anchor+delay-times[low])/(times[last]-times[low])
  return values[low]*(1-fraction)+values[last]*fraction
