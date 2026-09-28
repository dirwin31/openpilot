import pytest

import test_navigation_params as nav


class FakeAndroidAutoClient:
  calls = []
  is_available = True
  failure = ""
  status_value = {
    "state": "idle",
    "label": "Ready",
    "detail": "",
    "error": "",
    "running": False,
    "receiver_address": "AA:BB:CC:DD:EE:FF",
    "receiver_name": "Test Car",
    "configured_view": "car",
    "connection": "wireless",
    "auto_connect": True,
    "auto_paused": False,
    "stats": {},
  }
  devices_value = [
    {"address": "AA:BB:CC:DD:EE:FF", "name": "Test Car", "paired": True, "connected": False, "android_auto": True},
  ]

  def __init__(self, timeout=0):
    self.timeout = timeout

  @property
  def available(self):
    return self.is_available

  def _record(self, operation, **payload):
    if self.failure:
      raise RuntimeError(self.failure)
    self.calls.append((operation, payload))

  def status(self):
    if not self.is_available:
      raise RuntimeError("Android Auto service is not running")
    return dict(self.status_value)

  def devices(self):
    self._record("devices")
    return [dict(device) for device in self.devices_value]

  def start(self): self._record("start")
  def stop(self): self._record("stop")
  def set_auto_connect(self, enabled): self._record("set_auto_connect", enabled=enabled)
  def select_receiver(self, address, name=""): self._record("select_receiver", address=address, name=name)
  def set_view(self, view): self._record("set_view", view=view)
  def set_connection(self, connection): self._record("set_connection", connection=connection)
  def prepare_pairing(self): self._record("prepare_pairing")


@pytest.fixture(autouse=True)
def reset_fake():
  FakeAndroidAutoClient.calls = []
  FakeAndroidAutoClient.is_available = True
  FakeAndroidAutoClient.failure = ""
  FakeAndroidAutoClient.status_value = {**FakeAndroidAutoClient.status_value, "connection": "wireless", "error": ""}


def _client(monkeypatch, *, enabled=True, offroad=True):
  client, _ = nav._params_client(monkeypatch, {"AndroidAutoEnabled": enabled, "IsOffroad": offroad}, "mici")
  monkeypatch.setattr(nav.the_galaxy, "AndroidAutoClient", FakeAndroidAutoClient)
  return client


def test_connection_status_includes_controls_cars_and_shared_help(monkeypatch):
  client = _client(monkeypatch)

  response = client.get("/api/android_auto/connection")

  assert response.status_code == 200
  assert response.headers["Cache-Control"].startswith("no-store")
  payload = response.get_json()
  assert payload["status"]["receiver_name"] == "Test Car"
  assert payload["devices"] == FakeAndroidAutoClient.devices_value
  assert payload["offroad"] is True
  assert "wireless Android Auto" in payload["setup_help"]
  assert payload["recovery_hint"] == ""


def test_wired_status_skips_bluetooth_inventory_and_returns_usb_help(monkeypatch):
  client = _client(monkeypatch)
  monkeypatch.setattr(FakeAndroidAutoClient, "status_value", {
    **FakeAndroidAutoClient.status_value,
    "connection": "wired",
    "receiver_address": "",
    "receiver_name": "",
  })

  response = client.get("/api/android_auto/connection")

  assert response.status_code == 200
  assert response.get_json()["devices"] == []
  assert "data-capable cable" in response.get_json()["setup_help"]
  assert ("devices", {}) not in FakeAndroidAutoClient.calls


@pytest.mark.parametrize(("operation", "body", "expected"), [
  ("start", {}, ("start", {})),
  ("stop", {}, ("stop", {})),
  ("set_auto_connect", {"enabled": False}, ("set_auto_connect", {"enabled": False})),
  ("select_receiver", {"address": "11:22:33:44:55:66", "name": "Other Car"},
   ("select_receiver", {"address": "11:22:33:44:55:66", "name": "Other Car"})),
  ("set_view", {"view": "mirror"}, ("set_view", {"view": "mirror"})),
  ("set_connection", {"connection": "wired"}, ("set_connection", {"connection": "wired"})),
  ("prepare_pairing", {}, ("prepare_pairing", {})),
])
def test_connection_operations_proxy_to_android_autod(monkeypatch, operation, body, expected):
  client = _client(monkeypatch)

  response = client.post(f"/api/android_auto/connection/{operation}", json=body)

  assert response.status_code == 200
  assert expected in FakeAndroidAutoClient.calls
  assert response.get_json()["status"]["receiver_name"] == "Test Car"


@pytest.mark.parametrize(("operation", "body"), [
  ("set_auto_connect", {"enabled": "yes"}),
  ("set_connection", {"connection": "bluetooth"}),
  ("set_view", {"view": "dashboard"}),
  ("select_receiver", {"address": ""}),
])
def test_connection_operations_validate_values(monkeypatch, operation, body):
  client = _client(monkeypatch)
  assert client.post(f"/api/android_auto/connection/{operation}", json=body).status_code == 400
  assert FakeAndroidAutoClient.calls == []


def test_connection_pairing_requires_offroad(monkeypatch):
  client = _client(monkeypatch, offroad=False)
  response = client.post("/api/android_auto/connection/prepare_pairing", json={})
  assert response.status_code == 409
  assert FakeAndroidAutoClient.calls == []


def test_connection_routes_require_android_auto_and_report_service_failures(monkeypatch):
  disabled = _client(monkeypatch, enabled=False)
  assert disabled.get("/api/android_auto/connection").status_code == 403
  assert disabled.post("/api/android_auto/connection/start", json={}).status_code == 403

  enabled = _client(monkeypatch)
  FakeAndroidAutoClient.is_available = False
  assert enabled.get("/api/android_auto/connection").status_code == 503
  assert enabled.post("/api/android_auto/connection/start", json={}).status_code == 503
  assert enabled.post("/api/android_auto/connection/nope", json={}).status_code == 404


def test_car_inventory_failure_keeps_projection_status_available(monkeypatch):
  client = _client(monkeypatch)
  FakeAndroidAutoClient.failure = "Bluetooth inventory unavailable"

  response = client.get("/api/android_auto/connection")

  assert response.status_code == 200
  assert response.get_json()["devices"] == []
  assert response.get_json()["devices_error"] == "Bluetooth inventory unavailable"
