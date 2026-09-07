"""Background CPU capture logger.

Samples ``deviceState`` (per-core CPU %, temps, memory) and ``procLog``
(per-process CPU) every few seconds while the device is running and appends a
compact, human-readable CSV row to a single rolling file. The file is capped so
a multi-hour drive stays small, and can be downloaded from the Galaxy dashboard
afterwards for offline analysis.

The pure helpers (row building, per-process percentage math, size cap) have no
dependency on ``cereal`` so they can be unit tested without the native msgq
extension. ``run_capture_loop`` imports ``messaging`` lazily for the same reason.
"""

import csv
import json
import os
import time
import uuid
from datetime import datetime
from io import StringIO
from pathlib import Path

from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import PC
from openpilot.system.hardware.hw import Paths


def _is_comma_device_runtime(
  marker_paths=("/TICI", "/AGNOS"),
  model_path="/sys/firmware/devicetree/base/model",
) -> bool:
  """Match the service's device check, including edge boot/update states."""
  if not PC:
    return True
  if any(os.path.isfile(path) for path in marker_paths):
    return True
  try:
    with open(model_path, encoding="utf-8") as f:
      model = f.read().strip("\x00").lower()
    return "comma " in model
  except OSError:
    return False


# Mirrors _FP_DATA_ROOT in starpilot_variables, but avoids that module's heavy
# import chain so this stays cheap to import (it runs in the always-on service
# and is imported by utilities/tests).
if _is_comma_device_runtime():
  _DATA_ROOT = Path("/data")
else:
  _DATA_ROOT = Path(Paths.comma_home()) / "starpilot" / "data"
CPU_CAPTURES_PATH = _DATA_ROOT / "cpu_captures"

# How often a row is written. A single rolling file is trimmed once it grows
# past the cap, dropping the oldest rows first.
CAPTURE_INTERVAL_S = 3.0
# Number of hottest processes recorded per row.
TOP_PROCS = 8
# Rolling file cap, including the detailed process records.
MAX_CAPTURE_BYTES = 5 * 1024 * 1024
# After a trim, keep roughly this fraction of the cap so trims are infrequent.
TRIM_TARGET_RATIO = 0.8

CAPTURE_FILENAME = "cpu_capture.csv"
CAPTURE_FILE = CPU_CAPTURES_PATH / CAPTURE_FILENAME

LEGACY_CSV_HEADER = "timestamp,cpu_pct,cpu_cores,cpu_temp_c,gpu_temp_c,mem_pct,state,top_procs"
CSV_HEADER = LEGACY_CSV_HEADER + ",device_type,git_commit,capture_session,monotonic_s,proc_sample_ns,proc_interval_s,processes"
DRIVE_MARKER_STATE = "drive_start"


def _max_temp(values):
  try:
    temps = [float(value) for value in (values or [])]
  except (TypeError, ValueError):
    return ""
  if not temps:
    return ""
  return round(max(temps), 1)


def compute_process_cpu(procs, prev_totals, dt, max_pct=None):
  """Return sorted process records and CPU totals keyed by (PID, start time).

  CPU is own user + system time, with 100% meaning one busy core. ``dt`` must
  span the two procLog timestamps, not the CSV write times. Keep mapd records
  even when idle or newly observed (unknown CPU is None, not zero).
  """
  totals = {}
  usage = []
  for proc in procs or []:
    try:
      pid = int(getattr(proc, "pid", 0) or 0)
    except (TypeError, ValueError):
      continue
    cmdline = list(getattr(proc, "cmdline", []) or [])
    name = " ".join(cmdline) or str(getattr(proc, "exe", "") or getattr(proc, "name", "") or f"pid{pid}")
    start_time = float(getattr(proc, "startTime", 0.0))
    identity = (pid, start_time)
    total = float(getattr(proc, "cpuUser", 0.0) or 0.0) + float(getattr(proc, "cpuSystem", 0.0) or 0.0)
    totals[identity] = total

    prev = prev_totals.get(identity)
    value = None
    if dt and dt > 0 and prev is not None and total >= prev:
      value = (total - prev) / dt * 100.0
      if max_pct is not None:
        value = min(value, max_pct)
    is_mapd = getattr(proc, "name", "") == "mapd" or any(
      Path(arg).name in ("mapd", "mapd_wrapper.py", "starpilot.navigation.mapd_wrapper", "openpilot.starpilot.navigation.mapd_wrapper")
      for arg in cmdline
    )
    if not value and not is_mapd:
      continue
    usage.append({
      "pid": pid, "start_time_s": start_time, "name": name,
      "cpu_pct": round(value, 2) if value is not None else None,
      "rss_bytes": int(getattr(proc, "memRss", 0)),
      "threads": int(getattr(proc, "numThreads", 0)),
      "processor": int(getattr(proc, "processor", 0)),
      "ppid": int(getattr(proc, "ppid", 0)), "mapd": is_mapd,
    })

  usage.sort(key=lambda item: item["cpu_pct"] or 0, reverse=True)
  return usage, totals


def format_top_procs(usage, top_n=TOP_PROCS):
  """Format full commands and PIDs as space-separated name[pid]:pct entries."""
  parts = []
  for proc in usage[:top_n]:
    if not proc["cpu_pct"]:
      continue
    clean = "_".join(proc["name"].split()).replace(":", "").replace(",", "")
    parts.append(f"{clean}[{proc['pid']}]:{round(proc['cpu_pct'])}")
  return " ".join(parts)


def build_capture_fields(now, device_state, top_procs):
  """Build one CSV row (list of column values) from a deviceState snapshot."""
  try:
    cores = [int(value) for value in (getattr(device_state, "cpuUsagePercent", None) or [])]
  except (TypeError, ValueError):
    cores = []

  cpu_avg = round(sum(cores) / len(cores)) if cores else ""
  cores_str = "[" + ",".join(str(value) for value in cores) + "]"

  mem = getattr(device_state, "memoryUsagePercent", None)
  started = bool(getattr(device_state, "started", False))
  timestamp = now.isoformat(timespec="seconds") if isinstance(now, datetime) else str(now)

  return [
    timestamp,
    cpu_avg,
    cores_str,
    _max_temp(getattr(device_state, "cpuTempC", None)),
    _max_temp(getattr(device_state, "gpuTempC", None)),
    mem if mem is not None else "",
    "onroad" if started else "offroad",
    top_procs,
  ]


def _csv_line(fields):
  buffer = StringIO()
  csv.writer(buffer, lineterminator="\n").writerow(fields)
  return buffer.getvalue()


def _enforce_size_cap(path, header, max_bytes, trim_ratio=TRIM_TARGET_RATIO):
  try:
    size = path.stat().st_size
  except OSError:
    return
  if max_bytes <= 0 or size <= max_bytes:
    return

  try:
    with open(path, encoding="utf-8", newline="") as f:
      lines = f.readlines()
  except OSError:
    return

  header_line = (header + "\n").encode("utf-8")
  body = lines[1:] if lines and lines[0].rstrip("\r\n") == header else lines

  target = int(max_bytes * trim_ratio)
  running = len(header_line)
  kept = []
  for line in reversed(body):
    line_size = len(line.encode("utf-8"))
    if running + line_size > target:
      break
    kept.append(line)
    running += line_size
  kept.reverse()

  tmp = path.with_name(path.name + ".tmp")
  with open(tmp, "w", encoding="utf-8", newline="") as f:
    f.write(header + "\n")
    f.writelines(kept)
  os.replace(tmp, path)


def _append_raw(text, path, header, max_bytes):
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  write_header = not path.exists() or path.stat().st_size == 0
  if not write_header and header == CSV_HEADER:
    # Preserve old captures when adding columns; their new fields are unknown.
    with open(path, encoding="utf-8", newline="") as f:
      if f.readline().rstrip("\r\n") == LEGACY_CSV_HEADER:
        tmp = path.with_name(path.name + ".tmp")
        columns = header.split(",")
        with open(tmp, "w", encoding="utf-8", newline="") as out:
          writer = csv.writer(out, lineterminator="\n")
          writer.writerow(columns)
          for row in csv.reader(f):
            writer.writerow(row + [""] * (len(columns) - len(row)))
        os.replace(tmp, path)
  with open(path, "a", encoding="utf-8", newline="") as f:
    if write_header:
      f.write(header + "\n")
    f.write(text)
  _enforce_size_cap(path, header, max_bytes)


def append_capture_row(fields, path=CAPTURE_FILE, header=CSV_HEADER, max_bytes=MAX_CAPTURE_BYTES):
  """Append a row to the rolling capture file, trimming oldest rows if capped."""
  _append_raw(_csv_line(fields + [""] * (len(header.split(",")) - len(fields))), path, header, max_bytes)


def append_drive_marker(now, path=CAPTURE_FILE, header=CSV_HEADER, max_bytes=MAX_CAPTURE_BYTES):
  """Append a valid CSV row marking the start of a new drive."""
  timestamp = now.isoformat(timespec="seconds") if isinstance(now, datetime) else str(now)
  append_capture_row([timestamp, "", "", "", "", "", DRIVE_MARKER_STATE, ""], path, header, max_bytes)


def capture_status(path=CAPTURE_FILE):
  """Summary for the dashboard/API: existence, size, row count, mtime."""
  path = Path(path)
  status = {
    "exists": False,
    "filename": path.name,
    "sizeBytes": 0,
    "rows": 0,
    "modified": None,
    "intervalS": CAPTURE_INTERVAL_S,
  }
  try:
    stat = path.stat()
  except OSError:
    return status
  try:
    with open(path, encoding="utf-8", newline="") as f:
      reader = csv.reader(f)
      next(reader, None)  # header
      rows = 0
      for row in reader:
        if not row or not row[0].strip() or row[0].lstrip().startswith("#"):
          continue
        if len(row) > 6 and row[6].strip() == DRIVE_MARKER_STATE:
          continue
        rows += 1
  except (OSError, UnicodeError, csv.Error):
    rows = 0
  status.update(exists=True, sizeBytes=stat.st_size, rows=rows, modified=stat.st_mtime)
  return status


def run_capture_loop(stop_event=None, path=CAPTURE_FILE, interval_s=CAPTURE_INTERVAL_S):
  """Sample deviceState/procLog forever, writing a row every ``interval_s``.

  Intended to run in a daemon thread inside the always-on Galaxy service so it
  captures the whole drive without a live connection to the device.
  """
  from cereal import messaging  # lazy: native msgq extension isn't importable everywhere
  from openpilot.common.basedir import BASEDIR
  from openpilot.common.git import get_commit

  sm = messaging.SubMaster(["deviceState", "procLog"])
  num_cores = os.cpu_count() or 8
  max_pct = 100.0 * num_cores

  prev_totals = {}
  prev_ts = None
  prev_started = None
  capture_session = uuid.uuid4().hex
  git_commit = get_commit(BASEDIR)
  next_emit = time.monotonic()

  cloudlog.info(f"cpu_capture writing to {path} every {interval_s}s")

  while stop_event is None or not stop_event.is_set():
    try:
      sm.update(1000)
      now = time.monotonic()
      if now < next_emit:
        continue
      next_emit = now + interval_s

      if not sm.valid.get("deviceState") or not sm.alive.get("deviceState"):
        continue
      device_state = sm["deviceState"]

      # Mark the start of a new drive (offroad -> onroad, or first onroad sample
      # after a mid-drive service restart) so drives are separable in the log.
      started = bool(getattr(device_state, "started", False))
      if started and not prev_started:
        append_drive_marker(datetime.now(), path=path)
      prev_started = started

      top_procs = ""
      proc_sample_ns = ""
      proc_interval_s = ""
      processes = ""
      if sm.valid.get("procLog") and sm.alive.get("procLog"):
        sample_ns = sm.logMonoTime["procLog"]
        if prev_ts is None or sample_ns != prev_ts:
          dt = None if prev_ts is None or sample_ns < prev_ts else (sample_ns - prev_ts) / 1e9
          usage, prev_totals = compute_process_cpu(sm["procLog"].procs, prev_totals, dt, max_pct)
          prev_ts = sample_ns
          proc_sample_ns = sample_ns
          proc_interval_s = dt if dt is not None else ""
          top_procs = format_top_procs(usage)
          selected = [proc for i, proc in enumerate(usage) if i < TOP_PROCS or proc["mapd"]]
          processes = json.dumps(selected, separators=(",", ":"))
      else:
        prev_totals = {}
        prev_ts = None

      fields = build_capture_fields(datetime.now(), device_state, top_procs)
      fields += [str(getattr(device_state, "deviceType", "")), git_commit, capture_session,
                 round(now, 3), proc_sample_ns, proc_interval_s, processes]
      append_capture_row(fields, path=path)
    except Exception:
      cloudlog.exception("cpu_capture iteration failed")
      time.sleep(interval_s)
