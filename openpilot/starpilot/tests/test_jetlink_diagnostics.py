import json
from unittest.mock import patch

from openpilot.starpilot.system.jetlink.diagnostics import RuntimeDiagnostics


def test_diagnostics_throttle_transitions_and_rotation(tmp_path):
  path = tmp_path / 'runtime.jsonl'
  diagnostics = RuntimeDiagnostics(path)
  record = {'state': 'joining', 'handovers': 0, 'mono_ns': 1_000_000_000,
            'remote_output_mono_ns': 0, 'session': 'test'}
  diagnostics.record(record, False)
  diagnostics.record(record | {'mono_ns': 2_000_000_000}, False)
  diagnostics.record(record | {'mono_ns': 3_000_000_000}, True)
  running = record | {'state': 'running', 'handovers': 1, 'mono_ns': 4_000_000_000,
                      'remote_output_mono_ns': 3_950_000_000}
  diagnostics.record(running, True)
  diagnostics.record(running | {'mono_ns': 14_000_000_000}, True)
  rows = [json.loads(line) for line in path.read_text().splitlines()]
  assert len(rows) == 4
  assert rows[0]['remote_output_age_ms'] is None
  assert rows[2]['remote_output_age_ms'] == 50
  assert rows[1]['android_auto_enabled'] is True
  diagnostics.handler.maxBytes = 1
  diagnostics.record(running | {'state': 'unavailable'}, True)
  diagnostics.close()
  assert path.with_suffix('.jsonl.1').exists()


def test_diagnostics_failure_does_not_escape(tmp_path):
  diagnostics = RuntimeDiagnostics(tmp_path / 'runtime.jsonl')
  with patch('openpilot.starpilot.system.jetlink.diagnostics.RotatingFileHandler', side_effect=OSError('full')):
    diagnostics.record({'state': 'joining', 'handovers': 0, 'mono_ns': 1, 'remote_output_mono_ns': 0}, False)
  diagnostics.close()


def test_android_auto_bundle_includes_bounded_jetlink_logs(tmp_path):
  import io
  import zipfile
  from openpilot.starpilot.system.android_auto import compat_report

  logs = tmp_path / 'jetlink'
  logs.mkdir()
  (logs / 'jetlink-runtime.jsonl').write_text('runtime sample\n')
  (logs / 'jetlink-owner.log').write_text('owner sample\n')
  (logs / 'jetlink-owner.log.1').symlink_to(logs / 'jetlink-owner.log')
  sessions = tmp_path / 'sessions'
  sessions.mkdir()
  with patch.object(compat_report, 'JETLINK_LOG_DIR', logs):
    data = compat_report.bundle(sessions, tmp_path / 'missing-config.json', snapshot=None)
  with zipfile.ZipFile(io.BytesIO(data)) as archive:
    assert archive.read('jetlink/jetlink-runtime.jsonl') == b'runtime sample\n'
    assert archive.read('jetlink/jetlink-owner.log') == b'owner sample\n'
    assert 'jetlink/jetlink-owner.log.1' not in archive.namelist()


def test_processing_metrics_are_published_off_frame_thread():
  from types import SimpleNamespace
  from unittest.mock import Mock
  from openpilot.starpilot.models import jetlink_adapter

  joined = SimpleNamespace(run=Mock(return_value={}), client=None, chestnut=False,
                           big_model_state='joining', handovers=0, frame_drop_ratio=.02, close=Mock())
  op = Mock()
  with patch.object(jetlink_adapter, 'adapter', return_value=op), \
       patch('openpilot.starpilot.system.jetlink.diagnostics.RuntimeDiagnostics') as diagnostics:
    model = jetlink_adapter.JetlinkModel(joined, object())
    try:
      with patch.object(jetlink_adapter.time, 'monotonic_ns', side_effect=[1_000_000_000, 1_060_000_000, 1_060_000_000]):
        model.run({}, {}, {})
    finally:
      model.close()
      model._runtime_thread.join(1)
    record = op.put.call_args.args[1]
    assert record['processing'] == {'runs': 1, 'total_ms': 60, 'max_ms': 60,
                                    'over_50ms': 1, 'frame_drop_ratio': .02}
    diagnostics.return_value.record.assert_called()
    diagnostics.return_value.close.assert_called_once()


def test_usb_receive_steering_avoids_camera_and_driving_cores(tmp_path):
  import os
  from pathlib import Path
  import subprocess
  from jetlink.comma.root import SCRIPT

  gadget = tmp_path / 'gadget'
  function = gadget / 'functions' / 'ncm.usb0'
  function.mkdir(parents=True)
  (function / 'ifname').write_text('usb1')
  net = tmp_path / 'net'
  mask = net / 'usb1' / 'queues' / 'rx-0' / 'rps_cpus'
  mask.parent.mkdir(parents=True)
  mask.write_text('00')
  env = {**os.environ, 'JETLINK_GADGET': str(gadget), 'JETLINK_SYS_NET': str(net)}
  result = subprocess.run(['bash', str(SCRIPT), 'rps'], env=env, capture_output=True, text=True, timeout=2)
  assert result.returncode == 0, result.stderr
  assert mask.read_text().strip() == '08'
  mask.unlink()
  mask.symlink_to(Path(os.devnull))
  result = subprocess.run(['bash', str(SCRIPT), 'rps'], env=env, capture_output=True, text=True, timeout=2)
  assert result.returncode == 1
  assert 'reads nothing, not 08' in result.stderr
