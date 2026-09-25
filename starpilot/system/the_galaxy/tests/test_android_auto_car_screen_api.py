import json
from pathlib import Path

import test_navigation_params as nav
from openpilot.starpilot.system.android_auto import car_screen

JS_ROOT = Path(__file__).resolve().parent.parent / "assets" / "mobile" / "js"


def _client(monkeypatch, tmp_path):
  path = tmp_path / "car_screen.json"
  monkeypatch.setattr(car_screen, "CAR_SCREEN_PATH", path)
  client, _ = nav._params_client(monkeypatch, {"IsOffroad": True}, "mici")
  return client, path


def test_defaults_then_partial_updates_are_saved_for_car_ui(monkeypatch, tmp_path):
  client, path = _client(monkeypatch, tmp_path)
  response = client.get("/api/android_auto/car_screen")
  assert response.status_code == 200 and response.headers["Cache-Control"].startswith("no-store")
  assert response.get_json()["settings"] == {"onroad_view": "split", "map_side": "right", "camera": True}

  assert client.post("/api/android_auto/car_screen", json={"map_side": "left"}).get_json()["settings"]["map_side"] == "left"
  saved = client.post("/api/android_auto/car_screen", json={"camera": False}).get_json()["settings"]
  assert saved == {"onroad_view": "split", "map_side": "left", "camera": False}, "earlier changes are kept"
  assert json.loads(path.read_text()) == saved
  assert car_screen.load(path) == saved


def test_invalid_values_are_rejected_without_saving(monkeypatch, tmp_path):
  client, path = _client(monkeypatch, tmp_path)
  assert client.post("/api/android_auto/car_screen", json={"onroad_view": "sideways"}).status_code == 400
  assert client.post("/api/android_auto/car_screen", json={"camera": "off"}).status_code == 400
  assert client.post("/api/android_auto/car_screen", data="nope", content_type="application/json").status_code == 400
  assert not path.exists()


def test_car_screen_settings_live_in_the_android_auto_tab():
  navigation = (JS_ROOT / "views" / "Navigation.js").read_text()
  panel = (JS_ROOT / "components" / "AndroidAutoCarScreenPanel.js").read_text()
  assert "AndroidAutoCarScreenPanel" in navigation and 'title="Car Screen"' in navigation
  for value in ('"split"', '"driving"', '"map"', "map_side", "camera"):
    assert value in panel
  assert "fetch(" not in panel
