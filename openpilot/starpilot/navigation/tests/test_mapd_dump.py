import json

from openpilot.cereal import log
from openpilot.starpilot.navigation.replay.mapd_dump import FORMAT_VERSION, rows_from_messages, write_dump


def event(t, which, **fields):
  msg = log.Event.new_message(logMonoTime=t, valid=True)
  msg.init(which)
  body = getattr(msg, which)
  for path, value in fields.items():
    target = body
    *parents, leaf = path.split('.')
    for name in parents:
      target = getattr(target, name)
    setattr(target, leaf, value)
  return msg


def test_rows_carry_fix_accuracy_car_speed_and_guidance():
  messages = [
    event(1_000, 'gpsLocationExternal', hasFix=True, latitude=35.1, longitude=-97.9, speed=12.5, bearingDeg=90.0,
          horizontalAccuracy=1.5, bearingAccuracyDeg=2.0, speedAccuracy=0.3),
    event(2_000, 'starpilotNavigation', **{'phoneGps.hasFix': True, 'phoneGps.latitude': 35.2, 'phoneGps.sourceMonoTime': 1_500,
                                          'phoneGps.horizontalAccuracy': 4.0, 'navigation.status': 'guiding',
                                          'navigation.instruction.text': 'Turn left onto Elm St',
                                          'navigation.instruction.distanceMeters': 120.0}),
    event(3_000, 'carState', vEgo=12.0, steeringAngleDeg=-3.0, standstill=False),
    event(3_010, 'carState', vEgo=12.1),
    event(60_000_000, 'carState', vEgo=12.2),
    event(4_000, 'mapdOut', roadStatus='matchedLimit', wayId=42, speedLimit=20.0, gpsMonoTime=1_000, gpsSource='external'),
  ]
  rows = rows_from_messages(messages)
  kinds = [row['k'] for row in rows]
  assert kinds == ['ext', 'ph', 'nav', 'cs', 'cs', 'map'], kinds
  ext, phone, nav, car, _, frame = rows
  assert (ext['lm'], ext['fix'], ext['ha'], ext['ba'], ext['sa']) == (1_000, True, 1.5, 2.0, 0.30000001192092896)
  assert (phone['src'], phone['ha']) == (1_500, 4.0)
  assert (nav['status'], nav['text'], nav['dist']) == ('guiding', 'Turn left onto Elm St', 120.0)
  assert (car['v'], car['sa'], car['yr']) == (12.0, -3.0, 0.0)
  assert (frame['st'], frame['gs'], frame['gm'], frame['way'], frame['lim']) == ('matchedLimit', 2, 1_000, 42, 20.0)


def test_write_dump_orders_segments_and_starts_with_header(tmp_path, monkeypatch):
  from openpilot.starpilot.navigation.replay import mapd_dump
  monkeypatch.setattr(mapd_dump, 'dump_segment', lambda path: [{'k': 'cs', 'lm': int(path.split('--')[-1].split('/')[0]) * 10 + 5, 'v': 1.0}])
  segments = [f'{tmp_path}/route--{n}/rlog.zst' for n in (10, 2, 1)]
  out = tmp_path / 'dump.jsonl'
  assert write_dump(segments, out, jobs=1) == 3
  lines = [json.loads(line) for line in out.read_text().splitlines()]
  assert lines[0] == {'k': 'start', 'lm': 15, 'format': FORMAT_VERSION}
  assert [row['lm'] for row in lines[1:]] == [15, 25, 105]
