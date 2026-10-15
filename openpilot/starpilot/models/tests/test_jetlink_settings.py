"""Jetlink connection choices do not change the local model catalog."""
import re
from types import SimpleNamespace
import tempfile
from pathlib import Path
import unittest

from openpilot.starpilot.models.jetlink_settings import JetlinkSettings
from openpilot.starpilot.models.manager import ModelManager, preferences


REGISTERED = frozenset(re.findall(r'^\s*\{"(\w+)",', (Path(__file__).resolve().parents[3] / "common/params_keys.h").read_text(), re.M))


class MemoryParams:
  # Like Params, an unregistered key raises: the device's UnknownKeyName broke the whole page.
  def __init__(self):
    self.values = {"JetlinkMode": 0, "JetlinkChargePhone": False}

  def get(self, key):
    assert key in REGISTERED, key
    return self.values.get(key)

  def get_bool(self, key):
    assert key in REGISTERED, key
    return self.values.get(key) is True

  def put(self, key, value, *, block):
    assert key in REGISTERED, key
    assert block
    self.values[key] = value

  put_bool = put

  def remove(self, key):
    self.values.pop(key, None)


class FakeRefresher:
  def __init__(self):
    self.starts = []
    self.running = False
    self.error = ""

  def start(self, params, *, force=False):
    self.starts.append(force)
    return True


def bundle(ref, name, index):
  from jetlink.registry.catalog import REQUIRED_SELECTOR_VERSION
  return {"ref": ref, "display_name": name, "index": index, "minimum_selector_version": str(REQUIRED_SELECTOR_VERSION)}


class TestJetlinkSettings(unittest.TestCase):
  def setUp(self):
    self.params = MemoryParams()
    self.chestnut = False
    self.device = "tizi"
    self.runtime = {"state": "unavailable", "active": False}
    self.status = SimpleNamespace(enabled=True, present=True, ready=True, reason=None,
                                  progress={"stage": "ready"}, model="remote", transport="USB")
    self.refresher = FakeRefresher()
    self.root = Path(tempfile.mkdtemp())
    self.owner = JetlinkSettings(params=self.params, device_type=lambda: self.device,
                                chestnut=lambda: self.chestnut, runtime=lambda: self.runtime, link=lambda: SimpleNamespace(status=lambda: self.status),
                                refresher=self.refresher, model_root=self.root)

  def test_prepared_is_not_execution(self):
    self.owner.configure({"mode": "usb"})
    view = self.owner.snapshot()
    self.assertEqual(view["state"], "prepared")
    self.assertTrue(view["prepared"])
    self.assertFalse(view["active"])
    self.runtime = {"state": "running", "active": True}
    self.assertEqual(self.owner.snapshot()["state"], "active")
    self.runtime = {"state": "unavailable", "active": False}
    self.status.ready = False
    self.status.progress = {"stage": "joining", "msg": "Connecting"}
    self.assertEqual(self.owner.snapshot()["state"], "joining")

  def test_default_model_is_listed_before_any_catalog(self):
    from jetlink.registry.catalog import DEFAULT_BIG_MODEL_REF
    view = self.owner.snapshot()
    self.assertFalse(view["catalogFetched"])
    self.assertEqual([row["ref"] for row in view["models"]], [DEFAULT_BIG_MODEL_REF])
    self.assertEqual(view["runningRef"], DEFAULT_BIG_MODEL_REF)
    self.assertEqual(view["selectedRef"], "")
    self.assertEqual(view["models"][0]["state"], "ready")

  def test_pick_from_catalog_and_back_to_default(self):
    from jetlink.registry.catalog import DEFAULT_BIG_MODEL_REF
    newer, older = "a" * 40, DEFAULT_BIG_MODEL_REF
    self.params.values["JetlinkCatalog"] = {"bundles": [bundle(older, "Default (Sep 17, 2026)", 1), bundle(newer, "Newer (Oct 1, 2026)", 2),
                                                        {**bundle("b" * 40, "Wrong runtime", 3), "minimum_selector_version": "20"}]}
    view = self.owner.snapshot()
    self.assertEqual([row["ref"] for row in view["models"]], [newer, older])
    self.assertEqual(view["runningRef"], older)
    self.status.ready = False
    self.owner.configure({"model": newer})
    self.assertEqual(self.params.values["JetlinkBigModel"], {"ref": newer, "displayName": "Newer (Oct 1, 2026)"})
    view = self.owner.snapshot()
    self.assertEqual(view["selectedRef"], newer)
    self.assertEqual([row["selected"] for row in view["models"]], [True, False])
    self.assertEqual(view["runningRef"], newer)
    for payload in ({"model": "b" * 40}, {"model": "c" * 40}, {"model": 1}, {"refreshCatalog": False}):
      with self.assertRaises(ValueError):
        self.owner.configure(payload)
    self.owner.configure({"model": ""})
    self.assertNotIn("JetlinkBigModel", self.params.values)

  def test_downloaded_model_is_marked_from_its_pointer(self):
    from jetlink.registry.catalog import DEFAULT_BIG_MODEL_REF
    self.status.ready = False
    oid = "d" * 64
    (self.root / "jetlink").mkdir()
    (self.root / "jetlink" / f"{oid[:16]}.onnx").write_bytes(b"x" * 5)
    self.params.values["JetlinkPointers"] = {DEFAULT_BIG_MODEL_REF: {"oid": oid, "size": 5}}
    self.assertEqual(self.owner.snapshot()["models"][0]["state"], "downloaded")

  def test_catalog_refreshes_only_while_parked_and_enabled(self):
    self.owner.snapshot()
    self.assertEqual(self.refresher.starts, [])
    self.params.values["JetlinkMode"] = 2
    self.params.values["IsOffroad"] = False
    self.owner.snapshot()
    self.assertEqual(self.refresher.starts, [])
    self.params.values["IsOffroad"] = True
    self.owner.snapshot()
    self.assertEqual(self.refresher.starts, [False])
    self.owner.configure({"refreshCatalog": True})
    self.assertEqual(self.refresher.starts[-1], True)

  def test_standin_is_prepared_while_selected_model_builds(self):
    self.owner.configure({"mode": "ios"})
    self.status.ready = False
    self.status.standin = "previous-model"
    self.status.progress = {"stage": "building", "msg": "Preparing selected model"}
    view = self.owner.snapshot()
    self.assertTrue(view["prepared"])
    self.assertFalse(view["active"])
    self.assertEqual(view["model"], "remote")
    self.assertEqual(view["standinModel"], "previous-model")
    self.assertEqual(view["state"], "fallback model prepared")

  def test_any_jetlink_failure_keeps_the_model_manager_page(self):
    class Broken(Exception):
      pass

    def broken():
      raise Broken("unknown key")
    self.owner._link = broken
    with self.assertLogs("openpilot.starpilot.models.jetlink_settings", "ERROR"):
      view = self.owner.snapshot()
    self.assertEqual(view["state"], "unavailable")
    self.assertEqual(view["models"], [])

  def test_exact_choices_and_conflicts(self):
    for payload in ({"mode": 1}, {"mode": "other"}, {"chargePhone": 1}, {"unknown": True}):
      with self.assertRaises(ValueError):
        self.owner.configure(payload)
    self.chestnut = True
    with self.assertRaisesRegex(ValueError, "Chestnut"):
      self.owner.configure({"mode": "usb"})
    self.owner.configure({"mode": "off"})
    self.chestnut = False
    self.device = "tici"
    with self.assertRaisesRegex(ValueError, "3X"):
      self.owner.configure({"mode": "ios"})
    self.owner.configure({"mode": "off"})
    self.device = "mici"
    self.owner.configure({"mode": "ios", "chargePhone": True})
    self.assertEqual(self.params.values, {"JetlinkMode": 2, "JetlinkChargePhone": True})

  def test_manager_parked_guard_and_catalog_preserved(self):
    with tempfile.TemporaryDirectory() as tmp:
      parked = [True]
      manager = ModelManager(root=Path(tmp), parked=lambda: parked[0], gpu_present=lambda: False, jetlink=self.owner)
      before = preferences(Path(tmp))
      manager.action("jetlink", {"mode": "usb"})
      self.assertEqual(preferences(Path(tmp)), before)
      parked[0] = False
      with self.assertRaisesRegex(ValueError, "vehicle off"):
        manager.action("jetlink", {"mode": "off"})
      self.assertEqual(self.params.values["JetlinkMode"], 1)


class TestCatalogRefresher(unittest.TestCase):
  def run_refresh(self, refresher, params):
    self.assertTrue(refresher.start(params, force=True))
    refresher._thread.join(5)

  def test_fetch_is_kept_and_a_failure_keeps_the_last_list(self):
    from openpilot.starpilot.models.jetlink_catalog import CatalogRefresher, RETRY_INTERVAL, REFRESH_INTERVAL
    params = MemoryParams()
    now = [100.]
    fetched = {"bundles": [bundle("a" * 40, "A", 1)]}
    refresher = CatalogRefresher(fetch=lambda: fetched, clock=lambda: now[0])
    self.assertTrue(refresher.due(params))
    self.run_refresh(refresher, params)
    self.assertEqual([b["ref"] for b in params.values["JetlinkCatalog"]["bundles"]], ["a" * 40])
    self.assertEqual(refresher.error, "")
    now[0] += RETRY_INTERVAL
    self.assertFalse(refresher.due(params))
    now[0] += REFRESH_INTERVAL
    self.assertTrue(refresher.due(params))

    def fail():
      raise OSError("offline")
    refresher._fetch = fail
    self.run_refresh(refresher, params)
    self.assertEqual([b["ref"] for b in params.values["JetlinkCatalog"]["bundles"]], ["a" * 40])
    self.assertIn("last list is kept", refresher.error)

  def test_empty_catalog_retries_sooner(self):
    from openpilot.starpilot.models.jetlink_catalog import CatalogRefresher, RETRY_INTERVAL
    params = MemoryParams()
    now = [0.]
    refresher = CatalogRefresher(fetch=lambda: {"bundles": []}, clock=lambda: now[0])
    self.run_refresh(refresher, params)
    self.assertNotIn("JetlinkCatalog", params.values)
    now[0] = RETRY_INTERVAL
    self.assertTrue(refresher.due(params))

  def test_new_pin_replaces_old_identity_even_when_catalog_is_unchanged(self):
    from openpilot.starpilot.models.jetlink_catalog import CatalogRefresher
    params = MemoryParams()
    ref, other = "a" * 40, "b" * 40
    catalog = {"bundles": [bundle(ref, "Mountain Dew Model", 1)]}
    old = {"oid": "1" * 64, "size": 10}
    new = {"oid": "2" * 64, "size": 20}
    params.values["JetlinkPointers"] = {ref: old, other: old}
    refresher = CatalogRefresher(fetch=lambda: catalog)
    self.run_refresh(refresher, params)
    unchanged = params.get("JetlinkCatalog")
    refresher._fetch = lambda: {**catalog, "pointers": {ref: new, "c" * 40: {"oid": "invalid", "size": 1}}}
    self.run_refresh(refresher, params)
    self.assertEqual(refresher.error, "")
    self.assertEqual(params.get("JetlinkPointers"), {ref: new, other: old})
    self.assertEqual(params.get("JetlinkCatalog"), unchanged)
    self.assertNotIn("pointers", params.get("JetlinkCatalog"))

  def test_pins_stay_out_of_a_first_or_previously_polluted_catalog(self):
    from openpilot.starpilot.models.jetlink_catalog import CatalogRefresher
    params = MemoryParams()
    ref = "a" * 40
    pin = {"oid": "2" * 64, "size": 20}
    fetched = {"bundles": [bundle(ref, "Mountain Dew Model", 1)], "pointers": {ref: pin}}
    refresher = CatalogRefresher(fetch=lambda: fetched)
    self.run_refresh(refresher, params)
    self.assertEqual(refresher.error, "")
    self.assertEqual(params.get("JetlinkPointers"), {ref: pin})
    self.assertNotIn("pointers", params.get("JetlinkCatalog"))

    params.values["JetlinkCatalog"] = {**params.get("JetlinkCatalog"), "pointers": {ref: pin}}
    self.run_refresh(refresher, params)
    self.assertNotIn("pointers", params.get("JetlinkCatalog"))
