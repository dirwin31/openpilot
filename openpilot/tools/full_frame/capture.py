#!/usr/bin/env python3
"""Export a short, synchronized local rlog + road/wide HEVC segment to NPZ.

Requires cereal schemas, pycapnp, zstandard, NumPy, ffmpeg, and ffprobe.
Uses logged calibration with nominal camera intrinsics and zero custom camera
offset. Decode indexes come from EncodeIndex.segmentId, never video frame guesses.
"""
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np


def decode(path, indices):
  probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
    '-show_entries', 'stream=width,height', '-of', 'json', str(path)]))['streams'][0]
  width, height = probe['width'], probe['height']
  size = width * height * 3 // 2
  selected, wanted = {}, set(indices)
  with subprocess.Popen(['ffmpeg', '-v', 'error', '-threads', '1', '-i', str(path), '-frames:v', str(max(wanted)+1),
                         '-vsync', '0', '-f', 'rawvideo', '-pix_fmt', 'nv12', '-'], stdout=subprocess.PIPE) as proc:
    for idx in range(max(wanted)+1):
      chunks, remaining = [], size
      while remaining:
        chunk = proc.stdout.read(remaining)
        if not chunk:
          raise ValueError(f'Video ended before encoded frame {idx}: {path}')
        chunks.append(chunk)
        remaining -= len(chunk)
      if idx in wanted:
        selected[idx] = np.frombuffer(b''.join(chunks), dtype=np.uint8).reshape(height*3//2, width)
    if proc.wait() != 0:
      raise ValueError(f'ffmpeg failed: {path}')
  return np.stack([selected[i] for i in indices]), width, height


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--schema-root', type=Path, help='Historical cereal schema directory matching the recorded software')
  parser.add_argument('--rlog', type=Path, required=True)
  parser.add_argument('--future-rlog', type=Path, help='Next segment of the same route, supplying future motion across the boundary')
  parser.add_argument('--road', type=Path, required=True)
  parser.add_argument('--wide', type=Path, required=True)
  parser.add_argument('--route-id', required=True, help='Whole drive ID; use the same ID for every segment of that drive')
  parser.add_argument('--max-frames', type=int, default=200)
  parser.add_argument('--frame-step', type=int, default=1, help='Keep every Nth synchronized pair; 4 gives 5 Hz across the segment')
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  if args.max_frames < 5 or args.frame_step < 1 or args.output.exists():
    raise ValueError('Need at least five frames and a new output path')
  from openpilot.tools.full_frame.local_log import read_events, log_schema
  if 'livePose' not in log_schema(args.schema_root).Event.schema.fields:
    raise ValueError('Capture currently supports historical livePose recordings only; pass --schema-root matching that recording. Current deviceMotion labels require a separate adapter.')
  from openpilot.tools.full_frame.calibration import nominal_warp
  calibration, device, sensor = None, None, None
  streams = {'roadEncodeIdx': [], 'wideRoadEncodeIdx': []}
  segments = set()
  poses = []
  for msg in sorted(read_events(args.rlog, args.schema_root), key=lambda m: m.logMonoTime):
    kind = msg.which()
    if (kind == 'liveCalibration' and msg.valid and len(msg.liveCalibration.rpyCalib) == 3 and
        str(msg.liveCalibration.calStatus) == 'calibrated'):
      calibration = np.array(msg.liveCalibration.rpyCalib)
    elif kind == 'deviceState':
      device = str(msg.deviceState.deviceType)
    elif kind == 'roadCameraState':
      sensor = str(msg.roadCameraState.sensor)
    elif kind == 'livePose':
      pose = msg.livePose
      good = (msg.valid and pose.inputsOK and pose.sensorsOK and pose.posenetOK and
              pose.orientationNED.valid and pose.velocityDevice.valid)
      poses.append((msg.logMonoTime / 1e9, [pose.orientationNED.x, pose.orientationNED.y, pose.orientationNED.z],
                    [pose.velocityDevice.x, pose.velocityDevice.y, pose.velocityDevice.z], good))
    elif kind in streams and msg.valid and calibration is not None and device is not None and sensor is not None:
      event = getattr(msg, kind)
      segments.add(int(event.segmentNum))
      if not event.timestampSof:
        continue
      wide = kind == 'wideRoadEncodeIdx'
      matrix = nominal_warp(calibration, device, sensor, wide)
      streams[kind].append((int(event.frameId), int(event.segmentId), event.timestampSof/1e9, matrix, calibration.copy()))
  if len(segments) != 1:
    raise ValueError('Supply exactly one segment rlog with its matching two camera files')
  if args.future_rlog:
    if args.future_rlog.parent.name.rsplit('--', 1)[0] != args.rlog.parent.name.rsplit('--', 1)[0]:
      raise ValueError('Future-motion log must belong to the same route')
    for msg in read_events(args.future_rlog, args.schema_root):
      if msg.which() != 'livePose':
        continue
      pose = msg.livePose
      good = (msg.valid and pose.inputsOK and pose.sensorsOK and pose.posenetOK and
              pose.orientationNED.valid and pose.velocityDevice.valid)
      poses.append((msg.logMonoTime/1e9, [pose.orientationNED.x, pose.orientationNED.y, pose.orientationNED.z],
                    [pose.velocityDevice.x, pose.velocityDevice.y, pose.velocityDevice.z], good))
    poses.sort(key=lambda row: row[0])
    if any(poses[i][0] >= poses[i+1][0] for i in range(len(poses)-1)):
      raise ValueError('Motion logs overlap or have duplicated timestamps')
  road = sorted(streams['roadEncodeIdx'], key=lambda x: x[2])
  wide = {x[0]: x for x in streams['wideRoadEncodeIdx']}
  if len(wide) != len(streams['wideRoadEncodeIdx']) or len({x[0] for x in road}) != len(road):
    raise ValueError('Repeated encoded frame IDs; capture requires one unambiguous stream per camera')
  pairs = [(r, wide[r[0]]) for r in road if r[0] in wide and abs(r[2]-wide[r[0]][2]) <= .01][::args.frame_step][:args.max_frames]
  if len(pairs) < 5:
    raise ValueError('Too few synchronized frames with logged calibration/device/sensor metadata')
  road_data, width, height = decode(args.road, [r[1] for r, _ in pairs])
  wide_data, wide_w, wide_h = decode(args.wide, [w[1] for _, w in pairs])
  if (width, height) != (wide_w, wide_h):
    raise ValueError('Road/wide resolutions differ')
  if not poses:
    raise ValueError('No livePose stream for recorded-motion labels')
  np.savez_compressed(args.output, road_nv12=road_data, wide_nv12=wide_data,
    road_times=np.array([r[2] for r, _ in pairs]), wide_times=np.array([w[2] for _, w in pairs]),
    road_tfm=np.stack([r[3] for r, _ in pairs]), wide_tfm=np.stack([w[3] for _, w in pairs]),
    calibration=np.stack([r[4] for r, _ in pairs]), pose_times=np.array([p[0] for p in poses]),
    orientation_ned=np.array([p[1] for p in poses]), velocity_device=np.array([p[2] for p in poses]),
    pose_valid=np.array([p[3] for p in poses]),
    route_id=np.array(args.route_id), frame_period=np.array(.05*args.frame_step))
  print(f'Exported {len(pairs)} pairs at {width}x{height}; baseline uses nominal calibration and zero custom camera offset')


if __name__ == '__main__':
  main()
