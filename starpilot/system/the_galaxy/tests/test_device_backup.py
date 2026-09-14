import io
import json
import zipfile

import pytest

from starpilot.system.the_galaxy.device_backup import create_backup, restore_backup


class Params:
  def __init__(self):
    self.values = {"IsMetric": b"1", "FLMActiveOverrides": b'\x00\xff'}

  def get(self, key):
    return self.values.get(key)

  def put(self, key, value):
    self.values[key] = value

  def remove(self, key):
    self.values.pop(key, None)


@pytest.fixture
def backup(tmp_path):
  root = tmp_path / "flm"
  root.mkdir()
  (root / "model.bin").write_bytes(b"model contents")
  params = Params()
  archive = io.BytesIO()
  create_backup(archive, {"flm": root}, params, {"IsMetric", "FLMActiveOverrides", "ScreenBrightness"})
  return archive, root, params


def test_round_trip(backup, tmp_path):
  archive, root, params = backup
  params.values = {"IsMetric": b"0", "ScreenBrightness": b"new"}
  (root / "model.bin").write_bytes(b"changed")
  assert restore_backup(archive, {"flm": root}, params, {"IsMetric", "FLMActiveOverrides", "ScreenBrightness"}, tmp_path, lambda: None) == (2, 1)
  assert params.values == {"IsMetric": b"1", "FLMActiveOverrides": b'\x00\xff'}
  assert (root / "model.bin").read_bytes() == b"model contents"


@pytest.mark.parametrize("name", ["flm/../../escape", "/flm/escape", "unknown/file", "flm/model.bin"])
def test_rejects_paths_and_corruption(backup, tmp_path, name):
  archive, root, params = backup
  with zipfile.ZipFile(archive) as source:
    manifest = json.loads(source.read("manifest.json"))
  metadata = manifest["files"].pop("flm/model.bin")
  manifest["files"][name] = metadata
  damaged = io.BytesIO()
  with zipfile.ZipFile(damaged, "w") as output:
    output.writestr("manifest.json", json.dumps(manifest))
    output.writestr(name, b"corrupt")
  with pytest.raises(ValueError):
    restore_backup(damaged, {"flm": root}, params, set(params.values), tmp_path, lambda: None)
  assert (root / "model.bin").read_bytes() == b"model contents"


def test_rolls_back_when_vehicle_leaves_offroad(backup, tmp_path):
  archive, root, params = backup
  (root / "model.bin").write_bytes(b"before restore")
  checks = 0

  def parked():
    nonlocal checks
    checks += 1
    if checks == 3:
      raise ValueError("Vehicle is onroad")

  with pytest.raises(ValueError, match="onroad"):
    restore_backup(archive, {"flm": root}, params, set(params.values), tmp_path, parked)
  assert (root / "model.bin").read_bytes() == b"before restore"


def test_rejects_symlink_destination(backup, tmp_path):
  archive, root, params = backup
  (root / "model.bin").unlink()
  other = tmp_path / "other"
  other.write_bytes(b"untouched")
  (root / "model.bin").symlink_to(other)
  with pytest.raises(ValueError, match="symbolic link"):
    restore_backup(archive, {"flm": root}, params, set(params.values), tmp_path, lambda: None)
  assert other.read_bytes() == b"untouched"


SENSITIVE_KEYS = {
  "StarPilotApiToken", "StarPilotDongleId", "GalaxyPaired", "GalaxyUploadPending",
  "DongleId", "StockDongleId", "KonikDongleId", "HardwareSerial", "AccessToken",
  "AssistNowToken", "WeatherToken", "MapboxSecretKey", "MapboxPublicKey", "AMapKey1", "AMapKey2",
  "SecOCKey", "SecOCKeys", "GithubSshKeys", "GithubUsername", "SentryModeWebhook", "SentryModeNtfyUrl",
  "IsOnroad", "IsEngaged", "GitCommit", "LastGPSPosition", "NavDestination", "FavoriteDestinations",
  "FutureUnknownToken",
}


def test_export_filters_credentials_in_params_and_nested_profiles(tmp_path):
  params = Params()
  params.values.update(dict.fromkeys(SENSITIVE_KEYS, b"SECRET"))
  profiles = tmp_path / "profiles"
  auto = profiles / "2026-09-13_auto"
  auto.mkdir(parents=True)
  for key, value in params.values.items():
    (auto / key).write_bytes(value)
  slot = {"format": "starpilot-params-profile", "version": 1, "slot": "a", "settings": {
    "StarPilotApiToken": {"type": 1, "value": "SECRET"}, "IsMetric": {"type": 1, "value": "1"},
  }}
  (profiles / ".params-profile-a.json").write_text(json.dumps(slot))
  flm = tmp_path / "flm"
  flm.mkdir()
  (flm / "glxysession").write_text("SECRET")
  output = io.BytesIO()
  create_backup(output, {"profiles": profiles, "flm": flm}, params, set(params.values))
  with zipfile.ZipFile(output) as archive:
    manifest = json.loads(archive.read("manifest.json"))
    assert not set(manifest["params"]) & SENSITIVE_KEYS
    assert "FLMActiveOverrides" in manifest["params"]
    assert "flm/glxysession" not in archive.namelist()
    assert not any(name.split("/")[-1] in SENSITIVE_KEYS for name in archive.namelist())
    restored_slot = json.loads(archive.read("profiles/.params-profile-a.json"))
    assert set(restored_slot["settings"]) == {"IsMetric"}
    assert restored_slot["settingsCount"] == 1
    assert all(b"SECRET" not in archive.read(name) for name in archive.namelist())


def test_old_archive_cannot_restore_or_clear_credentials(tmp_path):
  import base64
  import hashlib

  params = Params()
  params.values.update(dict.fromkeys(SENSITIVE_KEYS, b"current"))
  profiles = tmp_path / "profiles"
  profiles.mkdir()
  flm = tmp_path / "flm"
  flm.mkdir()
  (flm / "glxysession").write_bytes(b"current-session")
  manifest = {"format": "starpilot-device-backup", "version": 1, "params": {
    key: base64.b64encode(b"old").decode() for key in SENSITIVE_KEYS | {"IsMetric"}
  }, "files": {}}
  # A prior broad archive can contain credentials both in raw auto backups and JSON slots.
  files = {"profiles/2026-09-13_auto/StarPilotApiToken": b"old-secret", "flm/glxysession": b"old-session",
           "profiles/.params-profile-a.json": json.dumps({"format": "starpilot-params-profile", "settings": {
             "StarPilotApiToken": {"value": "old-secret"}, "IsMetric": {"value": "1"},
           }}).encode()}
  output = io.BytesIO()
  with zipfile.ZipFile(output, "w") as archive:
    for name, content in files.items():
      archive.writestr(name, content)
      manifest["files"][name] = {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    archive.writestr("manifest.json", json.dumps(manifest))
  restore_backup(output, {"profiles": profiles, "flm": flm}, params, set(params.values), tmp_path, lambda: None)
  assert all(params.get(key) == b"current" for key in SENSITIVE_KEYS)
  assert params.get("IsMetric") == b"old"
  assert (flm / "glxysession").read_bytes() == b"current-session"
  assert not (profiles / "2026-09-13_auto/StarPilotApiToken").exists()
  assert set(json.loads((profiles / ".params-profile-a.json").read_text())["settings"]) == {"IsMetric"}


def test_audited_policy_preserves_tunings_and_excludes_unreviewed_keys():
  from starpilot.system.the_galaxy.device_backup import BACKUP_KEYS
  assert not BACKUP_KEYS & SENSITIVE_KEYS
  assert {"FLMActiveOverrides", "FLMActiveProfileId", "FLMTrialBaseline", "FLMTrialApplied",
          "LongitudinalPersonalityProfiles", "SafeModeBackup", "ModelLabConfig", "CalibrationParams",
          "GalaxyMobileDefault", "GalaxyDeveloperMode"} <= BACKUP_KEYS


@pytest.mark.parametrize("ready,parked,busy,download,status", [
  (False, True, False, False, 409), (True, False, False, False, 403), (True, True, True, False, 409),
  (True, True, False, False, 200), (True, True, False, True, 200), (True, True, False, None, 400),
])
def test_restore_reboot_requires_completed_restore_and_parked_state(ready, parked, busy, download, status):
  # Execute the real route body without importing the device hardware/server stack.
  import ast
  from pathlib import Path
  import threading

  tree = ast.parse(Path(__file__).parents[1].joinpath("the_galaxy.py").read_text())
  route = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "reboot_after_device_restore")
  route.decorator_list = []
  lock = threading.Lock()
  if busy:
    lock.acquire()
  writes = []

  class RebootParams:
    def put_bool(self, key, value):
      writes.append((key, value))

  from types import SimpleNamespace
  workers = []

  class FakeThread:
    def __init__(self, **kwargs):
      workers.append(kwargs)

    def start(self):
      pass

  scope = {"device_backup_lock": lock, "device_restore_ready": ready, "_params_raw": RebootParams(),
           "_personality_settings_write_locked": lambda: not parked, "jsonify": lambda **kwargs: kwargs,
           "request": SimpleNamespace(get_json=lambda **kwargs: {"downloadModels": download}),
           "threading": SimpleNamespace(Thread=FakeThread), "run_restore_model_downloads": lambda: None,
           "restore_model_download_busy": lambda: False, "device_restore_state": {},
           "request_restore_reboot": lambda: writes.append(("DoReboot", True))}
  exec(compile(ast.Module(body=[route], type_ignores=[]), "<reboot-route>", "exec"), scope)
  result = scope["reboot_after_device_restore"]()
  assert (result[1] if isinstance(result, tuple) else 200) == status
  assert writes == ([("DoReboot", True)] if status == 200 and not download else [])
  worker_started = status == 200 and download
  assert bool(workers) == bool(worker_started)
  assert lock.locked() == bool(busy or worker_started)
  if lock.locked():
    lock.release()


def test_backup_records_inventory_without_model_files(tmp_path):
  from starpilot.system.the_galaxy.device_backup import saved_models
  models = tmp_path / "models"
  models.mkdir()
  (models / "large_model.pkl").write_bytes(b"MODEL BINARY CONTENT")
  inventory = saved_models([
    {"value": "stock", "builtin": True, "installed": True},
    {"value": "model-a", "version": "1", "installed": True, "modelLabArtifactInstalled": True},
    {"value": "not-installed", "installed": False},
  ])
  output = io.BytesIO()
  create_backup(output, {"models": models}, Params(), {"IsMetric"}, models=inventory)
  with zipfile.ZipFile(output) as archive:
    assert archive.namelist() == ["manifest.json"]
    assert json.loads(archive.read("manifest.json"))["models"] == [
      {"key": "model-a", "version": "1", "standard": True, "lab": True},
    ]
  restored_models = []
  restore_backup(output, {}, Params(), {"IsMetric"}, tmp_path, lambda: None, models_out=restored_models)
  assert restored_models == inventory


def test_model_inventory_validation_precedes_restore(tmp_path):
  output = io.BytesIO()
  with zipfile.ZipFile(output, "w") as archive:
    archive.writestr("manifest.json", json.dumps({"format": "starpilot-device-backup", "version": 2,
      "files": {}, "params": {}, "models": [{"key": "https://untrusted/model", "standard": True, "lab": False}]}))
  params = Params()
  with pytest.raises(ValueError, match="model identifier"):
    restore_backup(output, {}, params, {"IsMetric"}, tmp_path, lambda: None)
  assert params.get("IsMetric") == b"1"


def test_typed_params_round_trip_uses_serialized_store(tmp_path):
  class TypedParams:
    types = {"IsMetric": 1, "ScreenBrightness": 2, "FLMActiveOverrides": 5}
    values = {"IsMetric": True, "ScreenBrightness": 70, "FLMActiveOverrides": {"tune": 1}}

    def get_param_path(self, key):
      return str(tmp_path / key)

    def get_type(self, key):
      return self.types[key]

    def get(self, key):
      return self.values.get(key)

    def put(self, key, value):
      assert type(value) is {1: bool, 2: int, 5: dict}[self.types[key]]
      self.values[key] = value

    def remove(self, key):
      self.values.pop(key, None)

  params = TypedParams()
  for key, value in {"IsMetric": b"1", "ScreenBrightness": b"70", "FLMActiveOverrides": b'{"tune":1}'}.items():
    (tmp_path / key).write_bytes(value)
  output = io.BytesIO()
  create_backup(output, {}, params, set(params.types))
  params.values = {}
  restore_backup(output, {}, params, set(params.types), tmp_path, lambda: None)
  assert params.values == {"IsMetric": True, "ScreenBrightness": 70, "FLMActiveOverrides": {"tune": 1}}
