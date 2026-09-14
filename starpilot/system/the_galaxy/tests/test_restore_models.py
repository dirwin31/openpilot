import pytest

from starpilot.system.the_galaxy.restore_models import download_saved_models


def entry(value="a", **fields):
  return {"value": value, "version": "1", "installed": False, "modelLabArtifactAvailable": True,
          "modelLabArtifactInstalled": False, **fields}


def run_job(*, catalog=None, saved=None, fail=(), onroad=False, timeout=False, busy_before=False, refreshed=None):
  entries = catalog if catalog is not None else [entry()]
  models = saved if saved is not None else [{"key": "a", "version": "1", "standard": True, "lab": True}]
  calls = []
  active = ["user download"] if busy_before else []
  ticks = [0]

  def queue(key, variant):
    calls.append((key, variant))
    active.append((key, variant))
    return (key, variant)

  def sleep(_seconds):
    ticks[0] += 1
    if timeout:
      return
    key, variant = active.pop()
    if key not in fail:
      model = next(model for model in entries if model["value"] == key)
      model["installed" if variant == "standard" else "modelLabArtifactInstalled"] = True

  def parked():
    if onroad and calls:
      raise ValueError("Vehicle is onroad")

  def refresh():
    calls.append("refresh")
    entries.extend(refreshed or [])

  error = None
  try:
    download_saved_models(models, catalog=lambda: entries, queue=queue, cancel=lambda token: calls.append("cancel"), owns=lambda token: token in active,
                          busy=lambda: bool(active), progress=lambda: "offline", check_parked=parked,
                          reboot=lambda note: calls.append(("reboot", note)), report=lambda _message: None,
                          refresh=refresh if refreshed is not None else None, sleep=sleep, monotonic=lambda: ticks[0], timeout=3)
  except ValueError as exc:
    error = str(exc)
  return calls, error


def test_downloads_each_missing_variant_before_reboot():
  assert run_job() == ([("a", "standard"), ("a", "lab"), ("reboot", "")], None)


def test_skips_installed_models_and_reboots():
  assert run_job(catalog=[entry(installed=True, modelLabArtifactInstalled=True)]) == ([("reboot", "")], None)


def test_newer_catalog_version_is_still_downloaded():
  calls, error = run_job(catalog=[entry(version="2")])
  assert error is None
  assert calls[:2] == [("a", "standard"), ("a", "lab")]


def test_unavailable_models_are_skipped_without_blocking_the_rest():
  saved = [{"key": "gone", "version": "1", "standard": True, "lab": False},
           {"key": "gpu", "version": "1", "standard": True, "lab": False},
           {"key": "a", "version": "1", "standard": True, "lab": False}]
  calls, error = run_job(catalog=[entry("gpu", requiresGpu=True, gpuAvailable=False), entry()], saved=saved)
  assert error is None
  assert calls[0] == ("a", "standard")
  assert calls[1][0] == "reboot"
  assert "gone (no longer offered)" in calls[1][1] and "gpu (needs a detected external GPU)" in calls[1][1]


def test_empty_catalog_is_refreshed_before_skipping():
  calls, error = run_job(catalog=[], refreshed=[entry()])
  assert error is None
  assert calls == ["refresh", ("a", "standard"), ("a", "lab"), ("reboot", "")]


def test_failed_download_continues_then_blocks_reboot():
  saved = [{"key": "a", "version": "1", "standard": True, "lab": False},
           {"key": "b", "version": "1", "standard": True, "lab": False}]
  calls, error = run_job(catalog=[entry(), entry("b")], saved=saved, fail={"a"})
  assert calls == [("a", "standard"), ("b", "standard")]
  assert "Could not download 'a' (offline)" in error


@pytest.mark.parametrize("options", [{"onroad": True}, {"timeout": True}])
def test_interrupted_download_cancels_only_its_own_job_and_never_reboots(options):
  calls, error = run_job(**options)
  assert error
  assert calls[-1] == "cancel"
  assert not any(call[0] == "reboot" for call in calls if isinstance(call, tuple))


def test_another_active_download_is_not_cancelled():
  calls, error = run_job(busy_before=True)
  assert "Another model download is active" in error
  assert calls == []


def test_no_saved_models_reboots_without_downloading():
  assert run_job(saved=[]) == ([("reboot", "")], None)


def test_builtin_placeholder_does_not_prevent_catalog_refresh():
  calls, error = run_job(catalog=[entry("stock", builtin=True, installed=True, modelLabArtifactAvailable=False)], refreshed=[entry()])
  assert error is None
  assert calls[0] == "refresh"
  assert ("a", "standard") in calls


def test_unloaded_catalog_does_not_silently_skip_every_model_and_reboot():
  calls, error = run_job(catalog=[entry("stock", builtin=True, installed=True, modelLabArtifactAvailable=False)], refreshed=[])
  assert "catalog is unavailable" in error
  assert calls == ["refresh"]


def test_download_that_replaces_our_finished_request_is_never_cancelled():
  token = object()
  active = [token]
  cancelled = []
  entries = [entry()]

  def sleep(_):
    entries[0]["installed"] = True
    active[0] = object()  # User starts another request between completion and our next poll.

  with pytest.raises(ValueError, match="Another download started"):
    download_saved_models([{"key": "a", "standard": True, "lab": False}], catalog=lambda: entries,
                          queue=lambda *_: token, cancel=cancelled.append, owns=lambda owner: active[0] is owner,
                          busy=lambda: active[0] is not token, progress=lambda: "", check_parked=lambda: None,
                          reboot=lambda _: pytest.fail("must not reboot over a user's download"), report=lambda _: None, sleep=sleep)
  assert not cancelled
