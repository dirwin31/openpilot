from pathlib import Path

import test_navigation_params as nav
from openpilot.starpilot.navigation.offline_maps import OfflineMaps

JS_ROOT = Path(__file__).resolve().parent.parent / "assets" / "mobile" / "js"
ROUTE = [[36.10 + i * 0.001, -115.20] for i in range(30)]


def _client(monkeypatch, tmp_path, position=(36.1, -115.2)):
  monkeypatch.setattr(nav.the_galaxy, "OfflineMaps", lambda: OfflineMaps(tmp_path))
  monkeypatch.setattr(nav.the_galaxy, "_get_navigation_last_position",
                      lambda: {"latitude": position[0], "longitude": position[1]} if position else None)
  client, fake_params = nav._params_client(monkeypatch, {"MapboxPublicKey": "pk.test", "IsMetric": False}, "mici")
  return client, OfflineMaps(tmp_path)


def test_summary_reports_position_key_presets_and_a_stopped_service(monkeypatch, tmp_path):
  client, _ = _client(monkeypatch, tmp_path)
  response = client.get("/api/android_auto/offline")
  assert response.status_code == 200 and response.headers["Cache-Control"].startswith("no-store")
  payload = response.get_json()
  assert payload["items"] == [] and payload["mapboxPublic"] == "pk.test" and payload["isMetric"] is False
  assert payload["position"] == {"latitude": 36.1, "longitude": -115.2}
  assert [p["max_zoom"] for p in payload["presets"]] == [16, 15, 14, 13]
  assert payload["service_running"] is False, "no status file means navtilesd hasn't run"


def test_area_estimate_then_save(monkeypatch, tmp_path):
  client, maps = _client(monkeypatch, tmp_path)
  presets = client.post("/api/android_auto/offline/estimate", json={"latitude": 36.1, "longitude": -115.2}).get_json()["presets"]
  assert len(presets) == 4 and all(p["tiles"] > 0 and p["bytes"] > 0 and p["fits"] for p in presets)

  response = client.post("/api/android_auto/offline/areas", json={"name": "Home", "latitude": 36.1, "longitude": -115.2, "radius_km": 10, "max_zoom": 16})
  assert response.status_code == 201
  [area] = maps.areas()
  assert (area.name, area.kind, area.radius_km, area.max_zoom) == ("Home", "area", 10.0, 16)
  item = client.get("/api/android_auto/offline").get_json()["items"][0]
  assert item["state"] == "queued" and item["id"] == area.id


def test_area_rejects_unlisted_sizes_and_bad_points(monkeypatch, tmp_path):
  client, maps = _client(monkeypatch, tmp_path)
  assert client.post("/api/android_auto/offline/areas", json={"latitude": 36.1, "longitude": -115.2, "radius_km": 500, "max_zoom": 18}).status_code == 400
  assert client.post("/api/android_auto/offline/areas", json={"latitude": 95, "longitude": 0, "radius_km": 10, "max_zoom": 16}).status_code == 400
  assert client.post("/api/android_auto/offline/estimate", json={}).status_code == 400
  assert maps.areas() == []


def test_route_estimate_then_make_available_offline(monkeypatch, tmp_path):
  client, maps = _client(monkeypatch, tmp_path)
  estimate = client.post("/api/android_auto/offline/estimate", json={"points": ROUTE}).get_json()
  assert estimate["tiles"] > 0 and estimate["fits"]

  body = {"name": "Bellagio", "origin_name": "Home", "points": ROUTE, "distance_m": 3300, "duration_s": 420}
  assert client.post("/api/android_auto/offline/routes", json=body).status_code == 201
  [route] = maps.areas()
  assert route.kind == "route" and route.name == "Bellagio" and route.origin_name == "Home"
  assert len(route.tiles()) == estimate["tiles"]
  item = client.get("/api/android_auto/offline").get_json()["items"][0]
  assert item["kind"] == "route" and item["distance_m"] == 3300 and len(item["points"]) == len(ROUTE)


def test_route_rejects_garbage(monkeypatch, tmp_path):
  client, maps = _client(monkeypatch, tmp_path)
  assert client.post("/api/android_auto/offline/routes", json={"points": [[36.1, -115.2]]}).status_code == 400
  assert client.post("/api/android_auto/offline/routes", json={"points": [["x", 1], [2, 3]]}).status_code == 400
  assert client.post("/api/android_auto/offline/estimate", json={"points": "nope"}).status_code == 400
  assert maps.areas() == []


def test_saves_are_refused_when_offline_storage_is_full(monkeypatch, tmp_path):
  client, maps = _client(monkeypatch, tmp_path)
  monkeypatch.setattr(nav.the_galaxy, "OFFLINE_MAX_BYTES", 1)
  assert client.post("/api/android_auto/offline/routes", json={"points": ROUTE}).status_code == 409
  assert client.post("/api/android_auto/offline/areas", json={"latitude": 36.1, "longitude": -115.2, "radius_km": 10, "max_zoom": 16}).status_code == 409
  assert client.post("/api/android_auto/offline/estimate", json={"points": ROUTE}).get_json()["fits"] is False
  assert maps.areas() == []


def test_update_download_now_and_delete(monkeypatch, tmp_path):
  client, maps = _client(monkeypatch, tmp_path)
  area = maps.add_area("Home", 36.1, -115.2, 10.0, 16)
  assert client.post(f"/api/android_auto/offline/{area.id}/update").status_code == 200
  assert maps.get(area.id).update_requested > 0
  assert client.post(f"/api/android_auto/offline/{area.id}/download_now").status_code == 200
  assert maps.get(area.id).allow_metered is True
  assert client.post(f"/api/android_auto/offline/{area.id}/explode").status_code == 400

  assert client.delete(f"/api/android_auto/offline/{area.id}").status_code == 200
  assert maps.get(area.id).deleted, "navtilesd removes the tiles, then the record"
  assert client.get("/api/android_auto/offline").get_json()["items"][0]["state"] == "removing"
  assert client.delete("/api/android_auto/offline/missing").status_code == 404
  assert client.post("/api/android_auto/offline/missing/update").status_code == 404


def test_android_auto_lives_in_navigation_not_vehicle():
  navigation = (JS_ROOT / "views" / "Navigation.js").read_text()
  vehicle = (JS_ROOT / "views" / "Vehicle.js").read_text()
  assert 'auto: "Android Auto"' in navigation and 'auto: "auto"' in navigation
  assert "AndroidAutoOfflinePanel" in navigation and "AndroidAutoIdentityPanel" in navigation
  assert "AndroidAutoIdentityPanel" not in vehicle and "/navigation/auto" in vehicle
  panel = (JS_ROOT / "components" / "AndroidAutoOfflinePanel.js").read_text()
  assert "fetch(" not in panel, "network calls go through api.js"
