"""Dump a drive's rlogs to the JSONL that mapd's Go replay harness reads (mapd_repo/MATCHING.md, "Replaying a drive").

One row per line, keyed by "k", in time order:
  start  first logMonoTime of the drive and the dump format version
  ext    gpsLocationExternal fix (comma ublox, mapd gpsSource 2)
  int    gpsLocation fix (device qcom GNSS, gpsSource 1)
  ph     phone fix forwarded in starpilotNavigation.phoneGps (gpsSource 4); "src" is its sourceMonoTime
  map    the mapdOut frame the car published: status, source, fix time, way, limit, junctions
  cs     carState every 50 ms: wheel speed, acceleration, steering angle, yaw rate, standstill
  dm     deviceMotion every 100 ms: gyro rates in the device frame
  nav    starpilotNavigation guidance: status, instruction text and distance
Fix rows carry every accuracy field mapd reads (horizontal, bearing and speed accuracy).
"""
import argparse
import json
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

FORMAT_VERSION = 2
CAR_STATE_EVERY_NS = 50_000_000
DEVICE_MOTION_EVERY_NS = 100_000_000


def gps_row(kind, gps, t, **extra):
  return {'k': kind, 'lm': t, 'fix': bool(gps.hasFix), 'lat': gps.latitude, 'lon': gps.longitude, 'v': gps.speed,
          'b': gps.bearingDeg, 'ha': gps.horizontalAccuracy, 'ba': gps.bearingAccuracyDeg, 'sa': gps.speedAccuracy,
          'alt': gps.altitude, **extra}


def map_row(t, road):
  return {'k': 'map', 'lm': t, 'ver': road.sampleVersion, 'st': str(road.roadStatus), 'gs': road.gpsSource.raw,
          'gm': road.gpsMonoTime, 'cm': road.computedMonoTime, 'way': road.wayId, 'road': road.roadName, 'lim': road.speedLimit,
          'br': [{'id': b.wayId, 'n': b.roadName, 'd': b.distanceMeters, 'a': b.angleDegrees} for b in road.navigationBranches]}


def rows_from_messages(messages):
  """Harness rows from one segment's events, in log order."""
  rows, last_car, last_motion = [], -CAR_STATE_EVERY_NS, -DEVICE_MOTION_EVERY_NS
  for message in messages:
    kind, t = message.which(), message.logMonoTime
    if kind == 'gpsLocationExternal':
      rows.append(gps_row('ext', message.gpsLocationExternal, t, valid=bool(message.valid)))
    elif kind == 'gpsLocation':
      rows.append(gps_row('int', message.gpsLocation, t, valid=bool(message.valid)))
    elif kind == 'starpilotNavigation':
      nav = message.starpilotNavigation
      gps = nav.phoneGps
      if gps.sourceMonoTime:
        rows.append(gps_row('ph', gps, t, src=gps.sourceMonoTime, valid=bool(message.valid)))
      guidance = nav.navigation
      rows.append({'k': 'nav', 'lm': t, 'status': guidance.status, 'text': guidance.instruction.text,
                   'dist': guidance.instruction.distanceMeters, 'type': guidance.instruction.maneuverType,
                   'sess': guidance.sessionId})
    elif kind == 'mapdOut':
      rows.append(map_row(t, message.mapdOut))
    elif kind == 'carState' and t - last_car >= CAR_STATE_EVERY_NS:
      last_car = t
      car = message.carState
      rows.append({'k': 'cs', 'lm': t, 'v': car.vEgo, 'a': car.aEgo, 'sa': car.steeringAngleDeg, 'yr': car.yawRate,
                   'ss': car.standstill})
    elif kind == 'deviceMotion' and t - last_motion >= DEVICE_MOTION_EVERY_NS:
      last_motion = t
      w = message.deviceMotion.angularVelocityDevice
      rows.append({'k': 'dm', 'lm': t, 'wx': w.x, 'wy': w.y, 'wz': w.z})
  return rows


def dump_segment(path):
  from openpilot.tools.lib.logreader import LogReader
  return rows_from_messages(LogReader(str(path)))


def segment_number(path):
  match = re.search(r'--(\d+)$', Path(path).parent.name)
  return int(match[1]) if match else -1


def write_dump(segments, out, jobs=8):
  segments = sorted(segments, key=segment_number)
  if jobs <= 1:
    parts = [dump_segment(segment) for segment in segments]
  else:
    with ProcessPoolExecutor(jobs) as pool:
      parts = list(pool.map(dump_segment, segments))
  rows = [row for part in parts for row in part]
  if not rows:
    raise SystemExit('no rows: no readable rlog.zst under the given folder')
  with open(out, 'w') as f:
    f.write(json.dumps({'k': 'start', 'lm': min(row['lm'] for row in rows), 'format': FORMAT_VERSION}) + '\n')
    for row in rows:
      f.write(json.dumps(row) + '\n')
  return len(rows)


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('root', help='folder of <route>--<segment>/rlog.zst for one drive')
  parser.add_argument('out', help='output JSONL, kept outside the repository')
  parser.add_argument('--route', default='', help='only segments whose folder name contains this')
  parser.add_argument('--jobs', type=int, default=8)
  args = parser.parse_args()
  segments = [p for p in Path(args.root).glob('*/rlog.zst') if args.route in p.parent.name]
  routes = {p.parent.name.rsplit('--', 1)[0] for p in segments}
  if len(routes) > 1:
    raise SystemExit(f'more than one route under {args.root}; pass --route ({", ".join(sorted(routes))})')
  print(args.out, write_dump(segments, args.out, args.jobs))


if __name__ == '__main__':
  main()
