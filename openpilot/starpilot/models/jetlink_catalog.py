"""Jetlink's big-model catalog and parked pick, for Galaxy's Jetlink picker.

StarPilot's own catalog lists Chestnut builds. Jetlink runs a comma commit's
ONNX on the remote device, so it keeps sunnypilot's big-model catalogs, as
jetlink.registry fetches them, in JetlinkCatalog and the pick in JetlinkBigModel.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

CATALOG_KEY = "JetlinkCatalog"
PICK_KEY = "JetlinkBigModel"
POINTERS_KEY = "JetlinkPointers"
REFRESH_INTERVAL = 6 * 3600.0
RETRY_INTERVAL = 300.0


def _selector() -> int:
  from jetlink.registry.catalog import REQUIRED_SELECTOR_VERSION
  return REQUIRED_SELECTOR_VERSION


def _bundles(catalog) -> list[dict]:
  from jetlink.registry.catalog import is_ref
  bundles = catalog.get("bundles") if isinstance(catalog, dict) else None
  found = []
  for bundle in bundles if isinstance(bundles, list) else []:
    try:
      selector = int(bundle.get("minimum_selector_version", 0))
      index = int(bundle.get("index", 0) or 0)
    except (AttributeError, TypeError, ValueError):
      continue
    if isinstance(bundle, dict) and is_ref(bundle.get("ref")) and selector == _selector():
      found.append((index, bundle))
  found.sort(key=lambda item: item[0], reverse=True)
  return [bundle for _, bundle in found]


def usable(catalog) -> bool:
  return bool(_bundles(catalog))


def default_catalog() -> dict:
  """Jetlink's default model alone: what runs before any catalog has been fetched."""
  from jetlink.registry.catalog import DEFAULT_BIG_MODEL_NAME, DEFAULT_BIG_MODEL_REF
  return {"bundles": [{"ref": DEFAULT_BIG_MODEL_REF, "display_name": DEFAULT_BIG_MODEL_NAME, "index": 0,
                       "minimum_selector_version": str(_selector()), "is_big": True, "models": []}]}


def effective_catalog(catalog) -> dict:
  return catalog if usable(catalog) else default_catalog()


def picked_ref(params) -> str:
  pick = params.get(PICK_KEY)
  ref = pick.get("ref") if isinstance(pick, dict) else None
  return ref if isinstance(ref, str) else ""


def running_ref(rows: list[dict], pick: str) -> str:
  """The ref Jetlink prepares: the pick, else its default when listed, else the newest."""
  from jetlink.registry.catalog import DEFAULT_BIG_MODEL_REF
  if pick:
    return pick
  refs = [row["ref"] for row in rows]
  return DEFAULT_BIG_MODEL_REF if DEFAULT_BIG_MODEL_REF in refs or not refs else refs[0]


def downloaded(params, model_root: Path, ref: str) -> bool:
  pointers = params.get(POINTERS_KEY)
  pointer = pointers.get(ref) if isinstance(pointers, dict) else None
  oid, size = (pointer.get("oid"), pointer.get("size")) if isinstance(pointer, dict) else (None, None)
  if not isinstance(oid, str) or not oid:
    return False
  path = model_root / "jetlink" / f"{oid[:16]}.onnx"
  try:
    return path.is_file() and path.stat().st_size == int(size)
  except (OSError, TypeError, ValueError):
    return False


def rows(params, *, model_root: Path, ready: bool, model_state=None) -> list[dict]:
  """The picker's rows, newest first: {ref, name, state, selected, running, default}."""
  from jetlink.registry.catalog import DEFAULT_BIG_MODEL_REF
  pick = picked_ref(params)
  listed, seen = [], set()
  for bundle in _bundles(effective_catalog(params.get(CATALOG_KEY))):
    if bundle["ref"] not in seen:
      seen.add(bundle["ref"])
      listed.append({"ref": bundle["ref"], "name": str(bundle.get("display_name") or bundle["ref"][:10])})
  running = running_ref(listed, pick)
  for row in listed:
    state = None
    if model_state is not None:
      try:
        state = model_state(row["ref"])
      except Exception:
        state = None
    elif row["ref"] == running and ready:
      state = "ready"
    if state is None and downloaded(params, model_root, row["ref"]):
      state = "downloaded"
    row.update(state=state, selected=row["ref"] == pick, running=row["ref"] == running,
               default=row["ref"] == DEFAULT_BIG_MODEL_REF)
  return listed


def select(params, ref: str) -> str:
  """Save the pick by catalog ref; '' returns to Jetlink's default. Returns its name."""
  if ref == "":
    params.remove(PICK_KEY)
    return "the default model"
  row = next((b for b in _bundles(effective_catalog(params.get(CATALOG_KEY))) if b["ref"] == ref), None)
  if row is None:
    raise ValueError("Unknown Jetlink model")
  name = str(row.get("display_name") or ref[:10])
  params.put(PICK_KEY, {"ref": ref, "displayName": name}, block=True)
  return name


class CatalogRefresher:
  """Fetches sunnypilot's catalogs off the request thread; a failed fetch keeps the last list."""

  def __init__(self, fetch=None, clock=time.monotonic):
    self._fetch = fetch
    self._clock = clock
    self._lock = threading.Lock()
    self._thread: threading.Thread | None = None
    self._last_attempt: float | None = None
    self.error = ""

  @property
  def running(self) -> bool:
    return self._thread is not None and self._thread.is_alive()

  def due(self, params) -> bool:
    if self._last_attempt is None:
      return True
    interval = REFRESH_INTERVAL if usable(params.get(CATALOG_KEY)) else RETRY_INTERVAL
    return self._clock() - self._last_attempt >= interval

  def start(self, params, *, force=False) -> bool:
    with self._lock:
      if self.running or not (force or self.due(params)):
        return False
      self._last_attempt = self._clock()
      self._thread = threading.Thread(target=self._run, args=(params,), name="jetlink-catalog", daemon=True)
      self._thread.start()
      return True

  def _run(self, params) -> None:
    try:
      from jetlink.registry.catalog import fetch_catalogs, merge_catalogs, pinned_pointers
      stored = params.get(CATALOG_KEY)
      stored = stored if usable(stored) else {}
      fetched = (self._fetch or fetch_catalogs)()
      merged = merge_catalogs([stored, fetched] if stored else [fetched], selector=_selector())
      if not usable(merged):
        raise ValueError("the fetched catalog lists no Jetlink models")
      pins = pinned_pointers(fetched)
      if pins:
        known = params.get(POINTERS_KEY)
        known = known if isinstance(known, dict) else {}
        updated = {**known, **pins}
        if updated != known:
          params.put(POINTERS_KEY, updated, block=True)
      if merged != stored:
        params.put(CATALOG_KEY, merged, block=True)
      self.error = ""
    except Exception as error:
      logging.getLogger(__name__).warning("Jetlink catalog refresh failed: %s", error)
      self.error = "Could not check for Jetlink models. The last list is kept."
