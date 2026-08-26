import os
import time
import threading
import logging
import json
from pathlib import Path
import pytest

from cereal import log
from openpilot.system.hardware.hw import Paths

from openpilot.common.swaglog import cloudlog
from openpilot.system.loggerd.uploader import AUTO_UPLOAD_FULL_LOGS_ENABLED_AT_PARAM, Uploader, clear_locks, main, UPLOAD_ATTR_NAME, UPLOAD_ATTR_VALUE
from openpilot.system.loggerd.xattr_cache import getxattr

from openpilot.system.loggerd.tests.loggerd_tests_common import UploaderTestCase


class FakeLogHandler(logging.Handler):
  def __init__(self):
    logging.Handler.__init__(self)
    self.reset()

  def reset(self):
    self.upload_order = list()
    self.upload_ignored = list()

  def emit(self, record):
    try:
      j = json.loads(record.getMessage())
      if j["event"] == "upload_success":
        self.upload_order.append(j["key"])
      if j["event"] == "upload_ignored":
        self.upload_ignored.append(j["key"])
    except Exception:
      pass

log_handler = FakeLogHandler()
cloudlog.addHandler(log_handler)


def test_clear_locks_missing_root(tmp_path):
  clear_locks(str(tmp_path / "missing"))


class TestUploader(UploaderTestCase):
  def setup_method(self):
    super().setup_method()
    log_handler.reset()

  def start_thread(self):
    self.end_event = threading.Event()
    self.up_thread = threading.Thread(target=main, args=[self.end_event])
    self.up_thread.daemon = True
    self.up_thread.start()

  def join_thread(self):
    self.end_event.set()
    self.up_thread.join()

  def gen_files(self, lock=False, xattr: bytes = None, boot=True) -> list[Path]:
    f_paths = []
    for t in ["qlog", "rlog", "dcamera.hevc", "fcamera.hevc"]:
      f_paths.append(self.make_file_with_data(self.seg_dir, t, 1, lock=lock, upload_xattr=xattr))

    if boot:
      f_paths.append(self.make_file_with_data("boot", f"{self.seg_dir}", 1, lock=lock, upload_xattr=xattr))
    return f_paths

  def gen_order(self, seg1: list[int], seg2: list[int], boot=True) -> list[str]:
    keys = []
    if boot:
      keys += [f"boot/{self.seg_format.format(i)}.zst" for i in seg1]
      keys += [f"boot/{self.seg_format2.format(i)}.zst" for i in seg2]
    keys += [f"{self.seg_format.format(i)}/qlog.zst" for i in seg1]
    keys += [f"{self.seg_format2.format(i)}/qlog.zst" for i in seg2]
    return keys

  def test_upload(self):
    self.gen_files(lock=False)

    self.start_thread()
    # allow enough time that files could upload twice if there is a bug in the logic
    time.sleep(1)
    self.join_thread()

    exp_order = self.gen_order([self.seg_num], [])

    assert len(log_handler.upload_ignored) == 0, "Some files were ignored"
    assert not len(log_handler.upload_order) < len(exp_order), "Some files failed to upload"
    assert not len(log_handler.upload_order) > len(exp_order), "Some files were uploaded twice"
    for f_path in exp_order:
      assert getxattr((Path(Paths.log_root()) / f_path).with_suffix(""), UPLOAD_ATTR_NAME) == UPLOAD_ATTR_VALUE, "All files not uploaded"

    assert log_handler.upload_order == exp_order, "Files uploaded in wrong order"

  def test_upload_with_wrong_xattr(self):
    self.gen_files(lock=False, xattr=b'0')

    self.start_thread()
    # allow enough time that files could upload twice if there is a bug in the logic
    time.sleep(1)
    self.join_thread()

    exp_order = self.gen_order([self.seg_num], [])

    assert len(log_handler.upload_ignored) == 0, "Some files were ignored"
    assert not len(log_handler.upload_order) < len(exp_order), "Some files failed to upload"
    assert not len(log_handler.upload_order) > len(exp_order), "Some files were uploaded twice"
    for f_path in exp_order:
      assert getxattr((Path(Paths.log_root()) / f_path).with_suffix(""), UPLOAD_ATTR_NAME) == UPLOAD_ATTR_VALUE, "All files not uploaded"

    assert log_handler.upload_order == exp_order, "Files uploaded in wrong order"

  def test_upload_ignored(self):
    self.set_ignore()
    self.gen_files(lock=False)

    self.start_thread()
    # allow enough time that files could upload twice if there is a bug in the logic
    time.sleep(1)
    self.join_thread()

    exp_order = self.gen_order([self.seg_num], [])

    assert len(log_handler.upload_order) == 0, "Some files were not ignored"
    assert not len(log_handler.upload_ignored) < len(exp_order), "Some files failed to ignore"
    assert not len(log_handler.upload_ignored) > len(exp_order), "Some files were ignored twice"
    for f_path in exp_order:
      assert getxattr((Path(Paths.log_root()) / f_path).with_suffix(""), UPLOAD_ATTR_NAME) == UPLOAD_ATTR_VALUE, "All files not ignored"

    assert log_handler.upload_ignored == exp_order, "Files ignored in wrong order"

  def test_upload_files_in_create_order(self):
    seg1_nums = [0, 1, 2, 10, 20]
    for i in seg1_nums:
      self.seg_dir = self.seg_format.format(i)
      self.gen_files(boot=False)
    seg2_nums = [5, 50, 51]
    for i in seg2_nums:
      self.seg_dir = self.seg_format2.format(i)
      self.gen_files(boot=False)

    exp_order = self.gen_order(seg1_nums, seg2_nums, boot=False)

    self.start_thread()
    # allow enough time that files could upload twice if there is a bug in the logic
    time.sleep(1)
    self.join_thread()

    assert len(log_handler.upload_ignored) == 0, "Some files were ignored"
    assert not len(log_handler.upload_order) < len(exp_order), "Some files failed to upload"
    assert not len(log_handler.upload_order) > len(exp_order), "Some files were uploaded twice"
    for f_path in exp_order:
      assert getxattr((Path(Paths.log_root()) / f_path).with_suffix(""), UPLOAD_ATTR_NAME) == UPLOAD_ATTR_VALUE, "All files not uploaded"

    assert log_handler.upload_order == exp_order, "Files uploaded in wrong order"

  def test_no_upload_with_lock_file(self):
    self.start_thread()

    time.sleep(0.25)
    f_paths = self.gen_files(lock=True, boot=False)

    # allow enough time that files should have been uploaded if they would be uploaded
    time.sleep(1)
    self.join_thread()

    for f_path in f_paths:
      fn = f_path.with_suffix(f_path.suffix.replace(".zst", ""))
      uploaded = getxattr(fn, UPLOAD_ATTR_NAME) == UPLOAD_ATTR_VALUE
      assert not uploaded, "File upload when locked"

  def test_no_upload_with_xattr(self):
    self.gen_files(lock=False, xattr=UPLOAD_ATTR_VALUE)

    self.start_thread()
    # allow enough time that files could upload twice if there is a bug in the logic
    time.sleep(1)
    self.join_thread()

    assert len(log_handler.upload_order) == 0, "File uploaded again"

  def test_clear_locks_on_startup(self):
    f_paths = self.gen_files(lock=True, boot=False)
    self.start_thread()
    time.sleep(0.25)
    self.join_thread()

    for f_path in f_paths:
      lock_path = f_path.with_suffix(f_path.suffix + ".lock")
      assert not lock_path.is_file(), "File lock not cleared on startup"

  def _step(self, uploader, network_type, network_metered):
    # mirrors how main() derives step()'s arguments
    metered = network_metered and not self.params.get_bool("AlwaysAllowUploads")
    return uploader.step(network_type, metered, network_metered)

  @pytest.mark.parametrize("log_name", ["rlog", "rlog.zst"])
  @pytest.mark.parametrize(("enabled", "network_type", "network_metered", "expected_upload"), [
    (False, log.DeviceState.NetworkType.wifi, False, False),
    (True, log.DeviceState.NetworkType.wifi, False, True),
    # a tethered hotspot reports wifi but costs cellular data
    (True, log.DeviceState.NetworkType.wifi, True, False),
    (True, log.DeviceState.NetworkType.cell4G, True, False),
    (True, log.DeviceState.NetworkType.cell4G, False, False),
    (True, log.DeviceState.NetworkType.none, False, False),
  ])
  def test_auto_upload_full_logs_requires_enabled_unmetered_wifi(self, log_name, enabled, network_type, network_metered, expected_upload):
    if enabled:
      self.params.put_int(AUTO_UPLOAD_FULL_LOGS_ENABLED_AT_PARAM, time.time_ns())
    self.params.put_bool("AutoUploadFullLogsOnWifi", enabled)
    log_path = self.make_file_with_data(self.seg_dir, log_name)
    uploader = Uploader("0000000000000000", Paths.log_root())

    result = self._step(uploader, network_type, network_metered)

    if expected_upload:
      assert result is True
      assert log_handler.upload_order == [f"{self.seg_dir}/rlog.zst"]
      assert getxattr(log_path, UPLOAD_ATTR_NAME) == UPLOAD_ATTR_VALUE
    else:
      assert result is None
      assert log_handler.upload_order == []
      assert getxattr(log_path, UPLOAD_ATTR_NAME) != UPLOAD_ATTR_VALUE

  # AlwaysAllowUploads clears the throttling flag, but must not open a paid connection to full logs
  @pytest.mark.parametrize("network_type", [log.DeviceState.NetworkType.cell4G, log.DeviceState.NetworkType.wifi])
  def test_always_allow_uploads_does_not_auto_upload_full_logs_on_metered(self, network_type):
    self.params.put_int(AUTO_UPLOAD_FULL_LOGS_ENABLED_AT_PARAM, time.time_ns())
    self.params.put_bool("AutoUploadFullLogsOnWifi", True)
    self.params.put_bool("AlwaysAllowUploads", True)
    log_path = self.make_file_with_data(self.seg_dir, "rlog.zst")
    uploader = Uploader("0000000000000000", Paths.log_root())

    assert self._step(uploader, network_type, True) is None
    assert getxattr(log_path, UPLOAD_ATTR_NAME) != UPLOAD_ATTR_VALUE

  @pytest.mark.parametrize("log_name", ["rlog", "rlog.zst"])
  def test_auto_upload_full_logs_only_includes_drives_after_enabling(self, log_name):
    old_log_path = self.make_file_with_data(self.seg_dir, log_name)
    self.params.put_bool("AutoUploadFullLogsOnWifi", True)
    uploader = Uploader("0000000000000000", Paths.log_root())

    assert self._step(uploader, log.DeviceState.NetworkType.wifi, False) is None
    enabled_at = self.params.get_int(AUTO_UPLOAD_FULL_LOGS_ENABLED_AT_PARAM)
    assert enabled_at > old_log_path.stat().st_mtime_ns
    assert getxattr(old_log_path, UPLOAD_ATTR_NAME) != UPLOAD_ATTR_VALUE

    new_seg_dir = self.seg_format.format(self.seg_num + 1)
    new_log_path = self.make_file_with_data(new_seg_dir, log_name)

    assert self._step(uploader, log.DeviceState.NetworkType.wifi, False) is True
    assert log_handler.upload_order == [f"{new_seg_dir}/rlog.zst"]
    assert getxattr(old_log_path, UPLOAD_ATTR_NAME) != UPLOAD_ATTR_VALUE
    assert getxattr(new_log_path, UPLOAD_ATTR_NAME) == UPLOAD_ATTR_VALUE
