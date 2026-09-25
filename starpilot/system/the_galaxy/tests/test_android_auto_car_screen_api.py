import json
from pathlib import Path

import test_navigation_params as nav
from openpilot.starpilot.system.android_auto import car_screen

JS_ROOT = Path(__file__).resolve().parent.parent / "assets" / "mobile" / "js"


def _client(monkeypatch, tmp_path):
  path = tmp_path / "car_screen.json"
  monkeypatch.setattr(car_screen, "CAR_SCREEN_PATH", path)
  client, _ = nav._params_client(monkeypatch, {"IsOffroad": True, "AndroidAutoEnabled": True}, "mici")
  return client, path


def test_defaults_then_partial_updates_are_saved_for_car_ui(monkeypatch, tmp_path):
  client, path = _client(monkeypatch, tmp_path)
  response = client.get("/api/android_auto/car_screen")
  assert response.status_code == 200 and response.headers["Cache-Control"].startswith("no-store")
  assert response.get_json()["settings"] == car_screen.DEFAULTS

  assert client.post("/api/android_auto/car_screen", json={"map_side": "left"}).get_json()["settings"]["map_side"] == "left"
  blind_spot = client.post("/api/android_auto/car_screen", json={"blind_spot_monitors": False, "blind_spot_min_speed_ms": 8.0}).get_json()["settings"]
  assert not blind_spot["blind_spot_monitors"] and blind_spot["blind_spot_min_speed_ms"] == 8.0
  saved = client.post("/api/android_auto/car_screen", json={"camera": False}).get_json()["settings"]
  assert saved == {**car_screen.DEFAULTS, "map_side": "left", "camera": False,
                   "blind_spot_monitors": False, "blind_spot_min_speed_ms": 8.0}, "earlier changes are kept"
  assert json.loads(path.read_text()) == saved
  assert car_screen.load(path) == saved


def test_invalid_values_are_rejected_without_saving(monkeypatch, tmp_path):
  client, path = _client(monkeypatch, tmp_path)
  assert client.post("/api/android_auto/car_screen", json={"onroad_view": "sideways"}).status_code == 400
  assert client.post("/api/android_auto/car_screen", json={"camera": "off"}).status_code == 400
  assert client.post("/api/android_auto/car_screen", json={"blind_spot_monitors": "off"}).status_code == 400
  assert client.post("/api/android_auto/car_screen", json={"blind_spot_min_speed_ms": -1}).status_code == 400
  assert client.post("/api/android_auto/car_screen", data="nope", content_type="application/json").status_code == 400
  assert not path.exists()


def test_car_screen_settings_live_under_vehicle_toggle():
  settings = (JS_ROOT / "views" / "Settings.js").read_text()
  panel = (JS_ROOT / "components" / "AndroidAutoCarScreenPanel.js").read_text()
  assert "AndroidAutoCarScreenPanel" in settings and "activeSection.name === 'Vehicle' && values.AndroidAutoEnabled" in settings
  assert 'title="Android Auto"' in settings and 'p.key === "AndroidAutoEnabled"' in settings
  assert 'v-else-if="error"' in panel and "attempt < 3" in panel and "@click=\"load\"" in panel
  for value in ('"split"', '"driving"', '"map"', "map_side", "camera", "blind_spot_monitors", "blind_spot_min_speed_ms"):
    assert value in panel
  assert "fetch(" not in panel


def test_disabled_blocks_layout_and_identity_but_not_live_ui(monkeypatch):
  client, _ = nav._params_client(monkeypatch, {"IsOffroad": True, "AndroidAutoEnabled": False}, "mici")
  for path in ("car_screen", "identity", "identity/download", "identity/upload"):
    assert client.post("/api/android_auto/" + path, json={}).status_code == 403
  assert client.get("/api/android_auto/identity").status_code == 403
  assert client.get("/api/android_auto/car_screen").status_code == 403
  monkeypatch.setattr(nav.the_galaxy.utilities, "get_ui_stream_port", lambda: 8091)
  memory = nav.WritableFakeParams()
  monkeypatch.setattr(nav.the_galaxy, "params_memory", memory)
  response = client.post("/api/ui_stream/start")
  assert response.status_code == 200
  assert memory.values["UiStreamRequested"] is True
