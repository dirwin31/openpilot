import pytest

from starpilot.system.the_galaxy.restore_models import download_saved_models


def run_job(*, catalog=None, saved=None, fail=False, onroad=False, timeout=False):
  entries = catalog if catalog is not None else [{"value": "a", "version": "1", "installed": False,
                                                 "modelLabArtifactAvailable": True, "modelLabArtifactInstalled": False}]
  models = saved if saved is not None else [{"key": "a", "version": "1", "standard": True, "lab": True}]
  calls = []
  active = []
  ticks = [0]

  def queue(key, variant):
    calls.append((key, variant))
    active.append(variant)

  def sleep(_seconds):
    ticks[0] += 1
    if timeout:
      return
    variant = active.pop()
    if not fail:
      entries[0]["installed" if variant == "standard" else "modelLabArtifactInstalled"] = True

  def parked():
    if onroad and calls:
      raise ValueError("Vehicle is onroad")

  error = None
  try:
    download_saved_models(models, catalog=lambda: entries, queue=queue, busy=lambda: bool(active),
                          progress=lambda: "offline", check_parked=parked, reboot=lambda: calls.append("reboot"),
                          report=lambda _message: None, sleep=sleep, monotonic=lambda: ticks[0], timeout=3)
  except ValueError as exc:
    error = str(exc)
  return calls, error


def test_downloads_each_missing_variant_before_reboot():
  assert run_job() == ([("a", "standard"), ("a", "lab"), "reboot"], None)


def test_skips_installed_models_and_reboots():
  assert run_job(catalog=[{"value": "a", "version": "1", "installed": True, "modelLabArtifactInstalled": True}]) == (["reboot"], None)


@pytest.mark.parametrize("options", [{"fail": True}, {"onroad": True}, {"timeout": True}, {"catalog": []},
                                     {"catalog": [{"value": "a", "version": "2"}]}])
def test_errors_never_reboot(options):
  calls, error = run_job(**options)
  assert error
  assert "reboot" not in calls


def test_no_saved_models_reboots_without_downloading():
  assert run_job(saved=[]) == (["reboot"], None)
