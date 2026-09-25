import json
import time

import pytest

from openpilot.selfdrive.ui.layouts.settings.starpilot import offline_maps as page_module
from openpilot.selfdrive.ui.layouts.settings.starpilot.navigation import SearchResult
from openpilot.starpilot.navigation.offline_maps import AREA_PRESETS, OfflineMaps


class FakeParams:
  def __init__(self, values=None):
    self.values = dict(values or {})

  def get(self, key, encoding=None, default=None, **kwargs):
    return self.values.get(key, default)

  def put(self, key, value):
    self.values[key] = value

  def remove(self, key):
    self.values.pop(key, None)


class FakeSearchClient:
  def __init__(self):
    self.results = []

  def reverse(self, latitude, longitude, public_token, language=""):
    return "Las Vegas"

  def search(self, query, public_token, session_token, **kwargs):
    return self.results


GPS = json.dumps({"latitude": 36.1, "longitude": -115.2, "hasFix": True})
DESTINATION = json.dumps({"name": "Office", "place_name": "Office", "latitude": 36.2, "longitude": -115.1})


@pytest.fixture
def page(tmp_path):
  params = FakeParams({"MapboxPublicKey": "pk"})
  layout = page_module.StarPilotOfflineMapsLayout(offline=OfflineMaps(tmp_path), params=params)
  layout._search_client = FakeSearchClient()
  layout.params = params
  layout.refresh()
  return layout


def wait_for(layout, condition, timeout=5.0):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    layout._consume_pending()
    if condition():
      return
    time.sleep(0.01)
  raise AssertionError("timed out")


def kinds(layout):
  return [kind for kind, _, _ in layout.layout_rows()]


def test_empty_page_leads_with_storage_and_adding(page):
  assert kinds(page) == ["storage", "add_header", "add_buttons", "saved_header", "empty"]
  assert [target for target, _ in page.add_buttons()] == ["add:here", "add:search"]
  text, _ = page.connection_text()
  assert "not running" in text


def test_around_me_needs_gps(page):
  page.activate("add:here")
  assert "GPS" in page.message and "message" in kinds(page)


def test_save_an_area_around_me(page):
  page.params.values["LastGPSPosition"] = GPS
  page.activate("add:here")
  assert kinds(page)[2] == "estimating"
  wait_for(page, lambda: page.chooser["estimates"] is not None)
  assert page.chooser["name"] == "Las Vegas"
  assert kinds(page).count("preset") == len(AREA_PRESETS)
  page.activate("preset:0")
  assert page.chooser is None
  assert [area.name for area in page.areas] == ["Las Vegas"]
  assert "area" in kinds(page) and "empty" not in kinds(page)


def test_destination_button_and_current_route(page):
  page.params.values["NavDestination"] = DESTINATION
  assert [target for target, _ in page.add_buttons()] == ["add:here", "add:destination", "add:search"]
  assert "route" in kinds(page)
  page.activate("add:destination")
  assert page.chooser["name"] == "Office"


def test_search_a_place_then_choose_its_size(page):
  page._search_client.results = [SearchResult("Reno", "Nevada", 39.5, -119.8)]
  page._search("reno")
  wait_for(page, lambda: page.search_results)
  assert "result" in kinds(page)
  page.activate("result:0")
  assert page.chooser["name"] == "Reno" and page.search_results == []


def test_manage_a_saved_area(page, monkeypatch):
  area = page._offline.add_area("Home area", 36.1, -115.2, 10, 16)
  page.refresh()
  page.activate(f"area:{area.id}")
  assert "area_actions" in kinds(page)
  page.activate("area_action:update")
  assert page._offline.get(area.id).update_requested > 0
  page.activate(f"area:{area.id}")
  assert page.selected_area_id is None, "tapping again closes it"

  page.activate(f"area:{area.id}")
  dialogs = []
  monkeypatch.setattr(page_module, "ConfirmDialog", lambda text, label, callback: dialogs.append(callback) or text)
  monkeypatch.setattr(page_module.gui_app, "push_widget", lambda widget: None)
  page.activate("area_action:delete")
  dialogs[0](page_module.DialogResult.CONFIRM)
  assert page._offline.get(area.id).deleted and page.selected_area_id is None
