"""Recorded-motion targets in the calibrated frame: x forward, y right, z down."""
import numpy as np

TIMES = 10 * (np.arange(33, dtype=np.float64) / 32)**2


def rotation(rpy):
  roll, pitch, yaw = np.asarray(rpy).T
  cr, sr, cp, sp, cy, sy = np.cos(roll), np.sin(roll), np.cos(pitch), np.sin(pitch), np.cos(yaw), np.sin(yaw)
  return np.stack((cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr,
                   sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr, -sp, cp*sr, cp*cr), axis=-1).reshape(-1, 3, 3)


def motion_target(pose_times, orientation_ned, velocity_device, valid, anchor, calibration):
  times = np.asarray(pose_times, dtype=np.float64)
  orientations, velocities = np.asarray(orientation_ned), np.asarray(velocity_device)
  valid = np.asarray(valid, dtype=bool)
  calibration = np.asarray(calibration)
  if (times.ndim != 1 or len(times) < 2 or orientations.shape != (len(times), 3) or
      velocities.shape != orientations.shape or valid.shape != times.shape or np.any(np.diff(times) <= 0) or
      calibration.shape != (3,) or not np.isfinite(calibration).all() or
      not np.isfinite(times).all() or not np.isfinite(orientations).all() or not np.isfinite(velocities).all()):
    raise ValueError('Invalid pose stream')
  start = np.searchsorted(times, anchor, side='right') - 1
  end = np.searchsorted(times, anchor + TIMES[-1], side='left')
  if start < 0 or end >= len(times) or anchor-times[start] > .1:
    return None
  window = slice(start, end+1)
  if not valid[window].all() or np.any(np.diff(times[window]) > .1):
    return None
  # Work locally in time to avoid accumulating irrelevant integration drift.
  ts, rot = times[window], rotation(orientations[window])
  world_velocity = np.einsum('nij,nj->ni', rot, velocities[window])
  position = np.concatenate((np.zeros((1, 3)), np.cumsum(
    .5*(world_velocity[1:]+world_velocity[:-1])*np.diff(ts)[:, None], axis=0)))
  future = np.stack([np.interp(anchor+TIMES, ts, position[:, axis]) for axis in range(3)], axis=1)
  origin = np.array([np.interp(anchor, ts, position[:, axis]) for axis in range(3)])
  # Use the last available orientation and velocity as inputs, never a future pose.
  world_from_calib = rot[0] @ rotation(np.asarray(calibration))[0]
  path = (future-origin) @ world_from_calib
  speed = float(np.linalg.norm(velocities[start]))
  return path[:, :2].astype(np.float32), np.array([[speed]], dtype=np.float32)
