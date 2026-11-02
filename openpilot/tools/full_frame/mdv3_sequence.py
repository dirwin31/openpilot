"""Full-view MDv3 input reference and 20 Hz replay bookkeeping.

This does not replace the production warp. Callers supply the calibrated
baseline transforms and must verify their provenance against the device.
"""
from dataclasses import dataclass

import numpy as np

from openpilot.tools.full_frame.geometry import teacher_view, full_view


def camera_inputs(road_planes, wide_planes, road_transform, wide_transform):
  baseline, full, masks, affines = [], [], [], []
  for planes, matrix in ((road_planes,road_transform),(wide_planes,wide_transform)):
    if len(planes)!=3 or any(np.asarray(plane).dtype!=np.uint8 for plane in planes):
      raise ValueError('Need unsigned-byte Y/U/V planes for each camera')
    baseline.append(teacher_view(planes,matrix,512,256))
    image,mask,affine = full_view(planes,512,256)
    full.append(image);masks.append(mask);affines.append(affine)
  return {'new_img':np.stack(baseline),'full_img':np.stack(full),
          'full_valid_masks':np.stack(masks),'full_source_to_canvas':np.stack(affines)}


def traffic_convention(is_rhd):
  if type(is_rhd) is not bool:
    raise ValueError('Provide a known logged right-hand-drive flag')
  return np.array([[not is_rhd,is_rhd]],dtype=np.float32)


class DesirePulse:
  """Match catalog runner rising-edge desire pulses; keep-lane slot stays zero."""
  def __init__(self):
    self.previous = np.zeros(8,np.float32)

  def reset(self):
    self.previous.fill(0)

  def update(self, desire):
    if type(desire) is not int or not 0<=desire<8:
      raise ValueError('Need a known desire enum in [0,7]')
    current = np.zeros(8,np.float32)
    if desire:
      current[desire] = 1
    pulse = np.where(current-self.previous>.99,current,0).astype(np.float32)
    self.previous = current
    return pulse


@dataclass(frozen=True)
class SequenceStep:
  reset: bool
  ready: bool
  continuous_frames: int
  reason: str


class SequenceGuard:
  """Keep real 20 Hz history. A gap, restart or route change resets all states."""
  def __init__(self,warmup_frames=132):
    if type(warmup_frames) is not int or warmup_frames<5:
      raise ValueError('Warm-up must cover at least the image queue')
    self.warmup = warmup_frames
    self.last = None
    self.count = 0

  def update(self,route_id,frame_id,timestamp):
    if not route_id or type(frame_id) is not int or frame_id<0 or not np.isfinite(timestamp):
      raise ValueError('Need route ID, nonnegative frame ID and finite timestamp')
    reset,reason = self.last is None,'first_frame'
    if self.last is not None:
      route,frame,when = self.last
      if route!=route_id:
        reset,reason = True,'route_change'
      elif timestamp<=when:
        raise ValueError('Camera timestamps must increase within one route')
      elif frame_id!=frame+1:
        reset,reason = True,'frame_gap_or_restart'
      elif abs((timestamp-when)-.05)>.01:
        reset,reason = True,'not_20hz'
      else:
        reason = 'continuous'
    self.count = 1 if reset else self.count+1
    self.last = route_id,frame_id,float(timestamp)
    return SequenceStep(reset,self.count>=self.warmup,self.count,reason)
