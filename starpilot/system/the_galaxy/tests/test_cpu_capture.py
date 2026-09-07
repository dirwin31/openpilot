import csv
import json
import sys
from types import ModuleType, SimpleNamespace

# cpu_capture -> starpilot_variables -> cereal.messaging pulls the native msgq
# extension, which won't load on non-device dev machines. Stub it so the pure
# helpers under test can be imported. run_capture_loop imports messaging lazily
# and is tested below with a fake SubMaster.
if "cereal.messaging" not in sys.modules:
  _messaging_stub = ModuleType("cereal.messaging")
  _messaging_stub.SubMaster = object
  sys.modules["cereal.messaging"] = _messaging_stub

import pytest

from openpilot.starpilot.system.the_galaxy import cpu_capture


def _proc(pid, name, cpu_user, cpu_system=0.0, **extra):
  return SimpleNamespace(pid=pid, name=name, cpuUser=cpu_user, cpuSystem=cpu_system, **extra)


def test_first_sample_seeds_totals_without_usage():
  procs = [_proc(1, "selfdrived", 10.0), _proc(2, "modeld", 5.0)]
  usage, totals = cpu_capture.compute_process_cpu(procs, {}, dt=None)
  assert usage == []
  assert totals == {(1, 0.0): 10.0, (2, 0.0): 5.0}


def test_second_sample_computes_sorted_percent():
  prev = {(1, 0.0): 10.0, (2, 0.0): 5.0, (3, 0.0): 0.0}
  # over 2s: selfdrived +1.2s cpu -> 60%, modeld +0.4s -> 20%, idle proc +0 -> dropped
  procs = [_proc(1, "selfdrived", 10.8, 0.4), _proc(2, "modeld", 5.4), _proc(3, "idle", 0.0)]
  usage, totals = cpu_capture.compute_process_cpu(procs, prev, dt=2.0)
  assert [proc["name"] for proc in usage] == ["selfdrived", "modeld"]
  assert usage[0]["cpu_pct"] == pytest.approx(60.0)
  assert usage[1]["cpu_pct"] == pytest.approx(20.0)
  assert totals[(1, 0.0)] == pytest.approx(11.2)


def test_percent_clamped_to_max():
  prev = {(1, 0.0): 0.0}
  procs = [_proc(1, "busy", 100.0)]
  usage, _ = cpu_capture.compute_process_cpu(procs, prev, dt=1.0, max_pct=400.0)
  assert usage[0]["cpu_pct"] == 400.0


def test_format_top_procs_limits_and_sanitizes():
  usage = [{"pid": i, "name": n, "cpu_pct": v} for i, (n, v) in enumerate([("a b", 31.4), ("c:d", 12.6), ("e", 5.0)])]
  assert cpu_capture.format_top_procs(usage, top_n=2) == "a_b[0]:31 cd[1]:13"


def test_build_capture_fields():
  ds = SimpleNamespace(
    cpuUsagePercent=[55, 38, 40, 35],
    cpuTempC=[60.1, 62.4, 61.0],
    gpuTempC=[58.0],
    memoryUsagePercent=48,
    started=True,
  )
  fields = cpu_capture.build_capture_fields("2026-09-04T12:00:00", ds, "selfdrived:31")
  assert fields == ["2026-09-04T12:00:00", 42, "[55,38,40,35]", 62.4, 58.0, 48, "onroad", "selfdrived:31"]


def test_build_capture_fields_handles_missing_data():
  ds = SimpleNamespace(cpuUsagePercent=[], cpuTempC=[], gpuTempC=None, memoryUsagePercent=None, started=False)
  fields = cpu_capture.build_capture_fields("t", ds, "")
  assert fields == ["t", "", "[]", "", "", "", "offroad", ""]


def test_append_row_writes_header_once_and_counts(tmp_path):
  path = tmp_path / "cap.csv"
  cpu_capture.append_capture_row(["t1", 10, "[10]", 50, 40, 20, "onroad", "a:1"], path=path)
  cpu_capture.append_capture_row(["t2", 20, "[20]", 51, 41, 21, "onroad", "b:2"], path=path)

  contents = path.read_text().splitlines()
  assert contents[0] == cpu_capture.CSV_HEADER
  assert len(contents) == 3

  status = cpu_capture.capture_status(path)
  assert status["exists"] is True
  assert status["rows"] == 2


def test_cores_with_commas_are_quoted(tmp_path):
  path = tmp_path / "cap.csv"
  cpu_capture.append_capture_row(["t", 42, "[55,38,40,35]", 60, 50, 30, "onroad", "x:1 y:2"], path=path)
  # Round-trips through csv without splitting the bracketed core list into extra columns.
  import csv
  with open(path, newline="") as f:
    rows = list(csv.reader(f))
  assert rows[1][2] == "[55,38,40,35]"
  assert len(rows[1]) == len(cpu_capture.CSV_HEADER.split(","))


def test_rolling_cap_drops_oldest(tmp_path):
  path = tmp_path / "cap.csv"
  # Tiny cap forces frequent trims; oldest rows should fall off, header stays.
  for i in range(200):
    cpu_capture.append_capture_row([f"t{i}", i, "[0]", 0, 0, 0, "onroad", ""], path=path, max_bytes=400)

  assert path.stat().st_size <= 400
  lines = path.read_text().splitlines()
  assert lines[0] == cpu_capture.CSV_HEADER
  # Newest row retained, an early row evicted.
  assert any(line.startswith("t199,") for line in lines[1:])
  assert not any(line.startswith("t0,") for line in lines[1:])


def test_rolling_cap_drops_row_that_cannot_fit(tmp_path):
  path = tmp_path / "cap.csv"
  cpu_capture.append_capture_row(["t", 42, "[0]", 0, 0, 0, "onroad", "x" * 500], path=path, max_bytes=200)

  assert path.stat().st_size <= 200
  assert path.read_text() == f"{cpu_capture.CSV_HEADER}\n"


def test_drive_marker_written_and_excluded_from_row_count(tmp_path):
  path = tmp_path / "cap.csv"
  cpu_capture.append_drive_marker("2026-09-04T12:00:00", path=path)
  cpu_capture.append_capture_row(["t1", 10, "[10]", 50, 40, 20, "onroad", "a:1"], path=path)
  cpu_capture.append_capture_row(["t2", 20, "[20]", 51, 41, 21, "onroad", "b:2"], path=path)

  with open(path, newline="") as f:
    rows = list(csv.reader(f))
  assert rows[0] == cpu_capture.CSV_HEADER.split(",")
  assert all(len(row) == len(rows[0]) for row in rows)
  assert rows[1][0] == "2026-09-04T12:00:00"
  assert rows[1][6] == cpu_capture.DRIVE_MARKER_STATE

  # Marker row is valid CSV but is not counted as a sample.
  assert cpu_capture.capture_status(path)["rows"] == 2

  # Every physical row remains rectangular for ordinary CSV readers.
  data_rows = [row for row in rows[1:] if row[6] != cpu_capture.DRIVE_MARKER_STATE]
  assert len(data_rows) == 2


def test_marker_survives_rolling_trim(tmp_path):
  path = tmp_path / "cap.csv"
  cpu_capture.append_drive_marker("t0", path=path, max_bytes=400)
  for i in range(200):
    cpu_capture.append_capture_row([f"t{i}", i, "[0]", 0, 0, 0, "onroad", ""], path=path, max_bytes=400)

  # Trim keeps the header first and never corrupts it, even with a marker present.
  lines = path.read_text().splitlines()
  assert lines[0] == cpu_capture.CSV_HEADER
  assert path.stat().st_size <= 400


def test_capture_status_handles_legacy_comment_marker(tmp_path):
  path = tmp_path / "cap.csv"
  path.write_text(f"{cpu_capture.CSV_HEADER}\n# ==== NEW DRIVE 2026-09-04T12:00:00\nt1,10,[10],50,40,20,onroad,a:1\n")

  assert cpu_capture.capture_status(path)["rows"] == 1


def test_capture_status_missing_file(tmp_path):
  status = cpu_capture.capture_status(tmp_path / "nope.csv")
  assert status["exists"] is False
  assert status["rows"] == 0
  assert status["intervalS"] == cpu_capture.CAPTURE_INTERVAL_S


def test_device_path_detection_handles_agnos_marker(monkeypatch, tmp_path):
  monkeypatch.setattr(cpu_capture, "PC", True)
  agnos = tmp_path / "AGNOS"
  agnos.touch()

  assert cpu_capture._is_comma_device_runtime(marker_paths=(agnos,), model_path=tmp_path / "model")


def test_full_commands_distinguish_truncated_names_and_pid_reuse():
  procs = [
    _proc(1, "starpilot.syste", 12, startTime=10, cmdline=["python3", "-m", "starpilot.system.speed_limit_vision"]),
    _proc(2, "starpilot.syste", 11, startTime=20, cmdline=["starpilot.system.adj_spot_monitor_vision"]),
    _proc(3, "reused", 40, startTime=99),
  ]
  usage, totals = cpu_capture.compute_process_cpu(procs, {(1, 10): 10, (2, 20): 10, (3, 30): 10}, 2)
  summary = cpu_capture.format_top_procs(usage)
  assert "starpilot.system.speed_limit_vision[1]:100" in summary
  assert "starpilot.system.adj_spot_monitor_vision[2]:50" in summary
  assert [p["pid"] for p in usage] == [1, 2]
  assert (3, 30) not in totals and totals[(3, 99)] == 40


def test_mapd_unknown_idle_and_reset_cpu_preserve_memory():
  proc = _proc(7, "mapd", 1, startTime=2, memRss=123456, numThreads=4, processor=5, ppid=6)
  for previous, expected in [({}, None), ({(7, 2): 1}, 0), ({(7, 2): 10}, None)]:
    usage, _ = cpu_capture.compute_process_cpu([proc], previous, 2)
    assert usage[0]["cpu_pct"] == expected
    assert usage[0]["rss_bytes"] == 123456
    assert usage[0]["threads"] == 4
    assert usage[0]["processor"] == 5
    assert usage[0]["ppid"] == 6
    assert usage[0]["mapd"] is True


def test_legacy_capture_migration_preserves_rows(tmp_path):
  path = tmp_path / "capture.csv"
  with path.open("w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(cpu_capture.LEGACY_CSV_HEADER.split(","))
    writer.writerow(["old", 10, "[10,10]", 50, 40, 20, "onroad", "old:1"])
  cpu_capture.append_capture_row(["new", 20, "[20,20]", 50, 40, 20, "onroad", "new:2"], path=path)
  with path.open(newline="") as f:
    rows = list(csv.DictReader(f))
  assert [r["timestamp"] for r in rows] == ["old", "new"]
  assert rows[0]["cpu_cores"] == "[10,10]"
  assert rows[0]["processes"] == ""
  assert cpu_capture.capture_status(path)["rows"] == 2


def test_capture_uses_proc_timestamps_and_keeps_mapd_outside_top_eight(monkeypatch, tmp_path):
  stop = SimpleNamespace(stopped=False)
  stop.is_set = lambda: stop.stopped
  snapshots = [(1, 10, True), (5, 12, True), (5, 12, True), (7, 13, True), (9, 14, False), (11, 15, True)]

  class FakeSubMaster:
    def __init__(self, services):
      self.index = -1
      self.valid = dict.fromkeys(services, True)
      self.alive = dict.fromkeys(services, True)
      self.logMonoTime = {}

    def update(self, _timeout_ms):
      self.index += 1
      stamp, self.total, alive = snapshots[self.index]
      self.alive["procLog"] = alive
      self.logMonoTime["procLog"] = stamp * 10**9
      stop.stopped = self.index == len(snapshots) - 1

    def __getitem__(self, service):
      if service == "deviceState":
        return SimpleNamespace(started=True, deviceType="mici")
      busy = [_proc(i, f"busy{i}", self.total, startTime=1) for i in range(1, 10)]
      return SimpleNamespace(procs=busy + [
        _proc(20, "mapd", 10, startTime=2, memRss=987654),
        _proc(21, "python3", 10, startTime=2, cmdline=["python3", "-m", "starpilot.navigation.mapd_wrapper"]),
      ])

  monkeypatch.setitem(sys.modules, "cereal.messaging", SimpleNamespace(SubMaster=FakeSubMaster))
  ticks = iter(range(0, 21, 3))
  monkeypatch.setattr(cpu_capture.time, "monotonic", lambda: next(ticks))
  path = tmp_path / "cap.csv"
  cpu_capture.run_capture_loop(stop_event=stop, path=path)
  with path.open(newline="") as f:
    rows = [r for r in csv.DictReader(f) if r["state"] == "onroad"]
  assert len(rows) == 6
  assert rows[1]["proc_interval_s"] == "4.0"
  assert rows[3]["proc_interval_s"] == "2.0"
  for index in (1, 3):
    records = json.loads(rows[index]["processes"])
    assert records[0]["cpu_pct"] == 50.0  # actual 4s / 2s intervals, not CSV's 3s
    assert len(records) == 10  # hottest eight plus the native child and wrapper
    assert [p["pid"] for p in records if p["mapd"]] == [20, 21]
    assert records[-2]["rss_bytes"] == 987654
  assert rows[2]["processes"] == rows[4]["processes"] == ""  # repeated/stale samples are unknown
  assert rows[5]["proc_interval_s"] == ""  # re-seed after stale telemetry
  assert json.loads(rows[5]["processes"])[0]["cpu_pct"] is None
  assert len({r["capture_session"] for r in rows}) == 1
  assert rows[0]["device_type"] == "mici"
  assert [float(r["monotonic_s"]) for r in rows] == [3, 6, 9, 12, 15, 18]


def test_capture_loop_ignores_stale_device_state(monkeypatch, tmp_path):
  class Stop:
    stopped = False

    def is_set(self):
      return self.stopped

  stop = Stop()

  class FakeSubMaster:
    def __init__(self, services):
      assert services == ["deviceState", "procLog"]
      self.valid = {"deviceState": True, "procLog": True}
      self.alive = {"deviceState": False, "procLog": False}

    def update(self, _timeout_ms):
      stop.stopped = True

  monkeypatch.setitem(sys.modules, "cereal.messaging", SimpleNamespace(SubMaster=FakeSubMaster))
  monkeypatch.setattr(cpu_capture, "append_capture_row", lambda *args, **kwargs: pytest.fail("stale device state was captured"))
  monkeypatch.setattr(cpu_capture, "append_drive_marker", lambda *args, **kwargs: pytest.fail("stale drive marker was captured"))

  path = tmp_path / "cap.csv"
  cpu_capture.run_capture_loop(stop_event=stop, path=path)

  assert not path.exists()
