import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
UI_ROOT = REPO_ROOT / "starpilot/system/the_galaxy/assets/mobile"

# Discovery runs `ip` on the comma; tests inject subnets so they never depend on the test host.
HOME_SUBNETS = ("192.168.1.0/24", "172.20.10.0/28", "10.0.0.0/24", "fd00::/64", "fe80::/64")


def load_file(name, path):
  spec = importlib.util.spec_from_file_location(name, REPO_ROOT / path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def load_access(name="lan_access_test"):
  return load_file(name, "starpilot/system/the_galaxy/lan_access.py")


def test_tunnel_boundary_and_scoped_cors():
  access = load_access()
  calls = []

  def application(environ, start_response):
    calls.append(environ)
    start_response("200 OK", [("Content-Type", "text/event-stream")])
    return [b"live"]

  app = access.LanTelemetryAccess(application, access.ActiveNetworks(discover=lambda: HOME_SUBNETS))

  def request(peer, path="/api/telematics/stream", origin="https://galaxy.link", method="GET", host="galaxy.link"):
    response = {}

    def start(status, headers, exc_info=None):
      response.update(status=status, headers=dict(headers))
    body = b"".join(app({"PATH_INFO": path, "REMOTE_ADDR": peer, "REQUEST_METHOD": method,
                         "HTTP_ORIGIN": origin, "HTTP_HOST": host, "HTTP_X_FORWARDED_FOR": "192.168.1.8",
                         "HTTP_X_FORWARDED_HOST": "starpilot-comma.local"}, start))
    return response, body

  for path in access.PATHS:
    for peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "8.8.8.8", "", "bad-address"):
      for host in ("galaxy.link", "example.devices.local", "example.devices.local.", "192.168.1.5"):
        response, body = request(peer, path, host=host)
        assert response["status"].startswith("403")
        assert b"live" not in body
  assert not calls, "Tunnel requests must never reach the producer"
  for peer in ("192.168.1.8", "172.20.10.2", "10.0.0.2", "::ffff:192.168.1.8", "fd00::2", "fe80::2"):
    response, body = request(peer)
    assert body == b"live"
    assert response["headers"]["Access-Control-Allow-Origin"] == "https://galaxy.link"
  response, _ = request("192.168.1.8", origin="https://galaxy.link.evil.example")
  assert "Access-Control-Allow-Origin" not in response["headers"]
  response, _ = request("192.168.1.8", method="OPTIONS")
  assert response["status"].startswith("204")
  assert response["headers"]["Access-Control-Allow-Methods"] == "GET"
  response, _ = request("127.0.0.1", path="/api/device/status")
  assert response["status"].startswith("200"), "Existing tunnel features are unaffected"


def test_active_interface_subnets_decide_direct_peers():
  access = load_access("lan_access_subnets")
  # Wi-Fi on public/CGNAT space and a native IPv6 prefix: neither fits an RFC1918 allowlist.
  subnets = ("203.0.113.16/28", "100.86.4.0/22", "2001:db8:abcd:1::5/64", "fe80::a1/64", "127.0.0.0/8")
  networks = access.ActiveNetworks(discover=lambda: subnets)
  for peer in ("203.0.113.20", "100.86.5.9", "2001:db8:abcd:1::9", "fe80::b2", "fe80::b2%wlan0",
               "::ffff:203.0.113.20", "::ffff:100.86.5.9"):
    assert access.is_lan_peer(peer, networks.current()), peer
  for peer in ("203.0.113.40", "203.0.114.20", "100.86.8.9", "8.8.8.8", "192.168.1.8", "2001:db8:abcd:2::9",
               "127.0.0.1", "::1", "::ffff:127.0.0.1", "127.0.0.5", "0.0.0.0", "::", "224.0.0.1", "ff02::1",
               "", "  ", "bad-address", "192.168.1.8/24", None):
    assert not access.is_lan_peer(peer, networks.current()), peer
  # A loopback subnet must never be usable, however it reaches the cache.
  assert access.ActiveNetworks(discover=lambda: ("127.0.0.0/8", "::1/128")).current() is None


def test_interface_subnets_refresh_and_fail_closed():
  access = load_access("lan_access_refresh")
  clock = [1000.0]
  subnets = [["192.168.4.7/24"]]
  discoveries = []

  def discover():
    discoveries.append(clock[0])
    if subnets[0] is None:
      raise OSError("no ip command")
    return subnets[0]

  networks = access.ActiveNetworks(discover=discover, refresh_sec=15.0, monotonic=lambda: clock[0])
  for _ in range(5):
    assert access.is_lan_peer("192.168.4.9", networks.current())
  assert len(discoveries) == 1, "Requests must not spawn discovery every time"

  subnets[0] = ["10.31.0.4/16"]
  clock[0] += 14.0
  assert access.is_lan_peer("192.168.4.9", networks.current()), "Cached subnets stay valid until they expire"
  clock[0] += 2.0
  assert access.is_lan_peer("10.31.7.2", networks.current()), "A new interface subnet is picked up"
  assert not access.is_lan_peer("192.168.4.9", networks.current()), "A removed subnet stops being accepted"

  subnets[0] = None
  clock[0] += 20.0
  assert networks.current() is None
  for _ in range(5):
    assert not access.is_lan_peer("192.168.4.9", networks.current())
    assert not access.is_lan_peer("10.31.7.2", networks.current())
  assert discoveries == [1000.0, 1016.0, 1036.0], "A failing discovery must also be rate limited"
  assert access.ActiveNetworks(discover=lambda: []).current() is None, "No interfaces means no direct peers"


def test_interface_discovery_parses_ip_output_and_is_bounded():
  access = load_access("lan_access_discovery")
  output = "\n".join((
    "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever",
    "1: lo    inet6 ::1/128 scope host \\       valid_lft forever preferred_lft forever",
    "3: wlan0    inet 100.86.5.4/22 brd 100.86.7.255 scope global dynamic wlan0\\       valid_lft 3521sec",
    "3: wlan0    inet6 2001:db8:abcd:1::5/64 scope global dynamic mngtmpaddr \\       valid_lft 6997sec",
    "3: wlan0    inet6 fe80::a00:27ff:fe4e:66a1/64 scope link \\       valid_lft forever",
    # A USB tether is a real local link; the built-in cellular modem is not, on either side of the `@`.
    "4: usb0    inet 192.168.42.129/24 brd 192.168.42.255 scope global usb0\\       valid_lft forever",
    "5: wwan0.1@wwan0    inet 203.0.113.20/28 scope global wwan0.1\\       valid_lft forever",
    "6: rmnet_data0    inet 10.62.4.7/29 scope global rmnet_data0\\       valid_lft forever",
    "7: usb0    inet6 ff02::1/16 scope global \\       valid_lft forever",
    "8: ppp0    inet 100.64.7.9/32 scope global ppp0\\       valid_lft forever",
    "9: broken    inet",
    # Overlay, tunnel and container interfaces are local to the kernel but their peers are remote.
    "11: tailscale0    inet 100.101.102.103/32 scope global tailscale0\\       valid_lft forever",
    "12: tailscale0    inet6 fd7a:115c:a1e0::1/128 scope global \\       valid_lft forever",
    "13: tun0    inet 10.8.0.6/24 scope global tun0\\       valid_lft forever",
    "14: wg0    inet 10.9.0.2/24 scope global wg0\\       valid_lft forever",
    "15: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever",
    "16: br-4f1c9a2b7d31    inet 172.18.0.1/16 scope global br-4f1c9a2b7d31\\       valid_lft forever",
    "17: veth9a1c2f3@if16    inet6 fe80::1c9a:2fff:fe3d:41/64 scope link \\       valid_lft forever",
    "18: zt7nnjcaqa    inet 10.147.17.42/24 scope global zt7nnjcaqa\\       valid_lft forever",
    # A VLAN or macvlan stacked on a tunnel is still the tunnel, on either side of the `@`.
    "19: tun0.5@tun0    inet 10.8.5.6/24 scope global tun0.5\\       valid_lft forever",
    "20: vlan7@wg0    inet 10.9.7.2/24 scope global vlan7\\       valid_lft forever",
    "21: lo.9    inet 127.0.0.9/8 scope host \\       valid_lft forever",
    "",
  ))
  parsed = [str(network) for network in access.parse_ip_addr(output)]
  assert parsed == ["100.86.4.0/22", "2001:db8:abcd:1::/64", "fe80::/64", "192.168.42.0/24"]
  # Every overlay/tunnel/container/cellular subnet above must be absent, so their peers can never
  # be direct: a carrier-side neighbour in the modem's prefix is not a LAN client.
  networks = access.ActiveNetworks(discover=lambda: access.parse_ip_addr(output)).current()
  for excluded in ("100.101.102.103/32", "fd7a:115c:a1e0::1/128", "10.8.0.0/24", "10.9.0.0/24",
                   "172.17.0.0/16", "172.18.0.0/16", "10.147.17.0/24", "10.8.5.0/24", "10.9.7.0/24",
                   "203.0.113.16/28", "10.62.4.0/29", "100.64.7.9/32"):
    assert excluded not in parsed, excluded
  for peer in ("100.101.102.103", "fd7a:115c:a1e0::1", "10.8.0.9", "10.9.0.9", "172.17.0.4",
               "172.18.0.4", "10.147.17.9", "10.8.5.9", "10.9.7.9", "127.0.0.9",
               "203.0.113.20", "10.62.4.2", "100.64.7.9"):
    assert not access.is_lan_peer(peer, networks), peer
  # Wi-Fi (including a CGNAT lease), its IPv6 neighbours and the USB tether stay reachable.
  for peer in ("100.86.5.9", "2001:db8:abcd:1::9", "fe80::b2", "192.168.42.140"):
    assert access.is_lan_peer(peer, networks), peer
  bounded = access.ActiveNetworks(discover=lambda: [f"10.{index}.0.1/24" for index in range(200)], limit=8)
  assert len(bounded.current()) == 8


def test_rejected_peers_never_run_interface_discovery():
  access = load_access("lan_access_no_discovery")
  discoveries = []

  def discover():
    # The galaxy.link tunnel must be refused from the socket peer alone, never by asking the kernel.
    discoveries.append(True)
    raise AssertionError("interface discovery ran for a peer that can never be direct")

  def forbidden_app(environ, start_response):
    raise AssertionError("a rejected peer reached the telemetry application")

  app = access.LanTelemetryAccess(forbidden_app, access.ActiveNetworks(discover=discover))
  # Forged headers must not change the decision, or make discovery worth running.
  hostile = {"HTTP_HOST": "192.168.1.5", "HTTP_X_FORWARDED_FOR": "192.168.1.8",
             "HTTP_X_FORWARDED_HOST": "starpilot-comma.local", "HTTP_X_REAL_IP": "192.168.1.8",
             "HTTP_ORIGIN": "https://galaxy.link"}

  def status_for(environ):
    response = {}
    body = b"".join(app(dict(hostile, PATH_INFO="/api/telematics/stream", REQUEST_METHOD="GET", **environ),
                        lambda status, headers, exc_info=None: response.update(status=status)))
    return response["status"], body

  peers = ("127.0.0.1", "127.0.0.5", "::1", "::ffff:127.0.0.1", "0.0.0.0", "::", "224.0.0.1", "ff02::1",
           "", "   ", "bad-address", "192.168.1.8/24", "192.168.1.8:41234")
  for peer in peers:
    status, body = status_for({"REMOTE_ADDR": peer})
    assert status.startswith("403") and body != b"live", peer
  status, _ = status_for({})  # A missing REMOTE_ADDR is unparsable too.
  assert status.startswith("403")
  assert not discoveries, "Loopback and malformed peers must be rejected before discovery"

  # A peer that could be direct still triggers discovery, so the guard is a short circuit, not a bypass.
  probed = access.LanTelemetryAccess(forbidden_app, access.ActiveNetworks(discover=lambda: discoveries.append(True) or []))
  probed({"PATH_INFO": "/api/telematics/stream", "REQUEST_METHOD": "GET", "REMOTE_ADDR": "192.168.1.8"},
         lambda status, headers, exc_info=None: None)
  assert discoveries == [True]


def test_forged_headers_cannot_grant_or_deny_access():
  access = load_access("lan_access_headers")
  seen = []

  def application(environ, start_response):
    seen.append(environ["REMOTE_ADDR"])
    start_response("200 OK", [("Content-Type", "text/event-stream")])
    return [b"live"]

  app = access.LanTelemetryAccess(application, access.ActiveNetworks(discover=lambda: ("192.168.1.0/24",)))
  hostile = {"HTTP_HOST": "192.168.1.5", "HTTP_ORIGIN": "http://192.168.1.5",
             "HTTP_X_FORWARDED_FOR": "192.168.1.8, 10.0.0.4", "HTTP_X_FORWARDED_HOST": "starpilot-comma.local",
             "HTTP_X_REAL_IP": "192.168.1.8", "HTTP_FORWARDED": "for=192.168.1.8"}

  def status_for(peer):
    response = {}
    body = b"".join(app(dict(hostile, PATH_INFO="/api/telematics/stream", REQUEST_METHOD="GET", REMOTE_ADDR=peer),
                        lambda status, headers, exc_info=None: response.update(status=status)))
    return response["status"], body

  for peer in ("127.0.0.1", "::ffff:127.0.0.1", "203.0.113.20"):
    status, body = status_for(peer)
    assert status.startswith("403") and body != b"live", peer
  assert not seen, "Forwarded headers must never admit a tunnel request"
  assert status_for("192.168.1.9") == ("200 OK", b"live"), "Only the socket peer decides"
  assert seen == ["192.168.1.9"]


def test_tunnel_socket_cannot_open_live_stream():
  # FRP uses this exact loopback socket path. Host/header rewrites cannot affect it.
  import http.client
  import threading
  from wsgiref.simple_server import WSGIRequestHandler, make_server

  access = load_file("lan_access_socket", "starpilot/system/the_galaxy/lan_access.py")

  def forbidden_app(environ, start_response):
    raise AssertionError("Tunnel reached the telemetry application")

  class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
      pass

  server = make_server("127.0.0.1", 0, access.LanTelemetryAccess(forbidden_app), handler_class=QuietHandler)
  worker = threading.Thread(target=server.serve_forever, daemon=True)
  worker.start()
  try:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    connection.request("GET", "/api/telematics/stream", headers={"Host": "starpilot-comma.local", "X-Forwarded-For": "192.168.1.8"})
    response = connection.getresponse()
    assert response.status == 403
    response.read()
    connection.close()
  finally:
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


def test_lan_frames_metadata_queue_and_source_freshness(monkeypatch):
  fixture_module = load_file("live_test_helpers", "starpilot/system/the_galaxy/tests/test_live_frames.py")
  live = fixture_module._live_module(monkeypatch)
  monkeypatch.setitem(sys.modules, "openpilot.starpilot.system.bluetooth.live", live)
  identity = load_file("telemetry_identity_test", "starpilot/system/bluetooth/identity.py")
  monkeypatch.setitem(sys.modules, "openpilot.starpilot.system.bluetooth.identity", identity)
  lan = load_file("lan_test", "starpilot/system/bluetooth/lan.py")
  clock = [100.0]

  class Params:
    def get(self, *args, **kwargs):
      return None

    def get_bool(self, *args):
      return False

  class Publisher:
    starts = 0

    def __init__(self, callback, *args):
      self.callback = callback
      self.current = {"alert": {"id": 1, "text1": "Original alert"}, "speed_limit_source": "Map"}

    def start(self):
      if not getattr(self, "running", False):
        Publisher.starts += 1
      self.running = True

    def is_running(self):
      return getattr(self, "running", False)

    def close(self):
      pass

    def details(self):
      return self.current

    def source_status(self):
      return {"source_age_sec": 0.1, "fresh": True}

  manager = lan.LanTelemetryManager(Params(), Params(), publisher_factory=Publisher, monotonic=lambda: clock[0])
  stream = manager.stream()
  initial = next(stream)
  second = manager.stream()
  next(second)
  assert Publisher.starts == 1
  assert initial.startswith(b"event: metadata\n")
  publisher = manager._publisher
  # Changing alerts/source while model revision stays fixed must deliver metadata.
  publisher.current = {"alert": {"id": 2, "text1": "New alert"}, "speed_limit_source": "Vision"}
  frame = live.LiveSnapshot(metadata_revision=1).pack(1, 100000)
  publisher.callback(frame)
  assert b"New alert" in next(stream)
  event = next(stream).decode()
  payload = json.loads(event.split("data: ", 1)[1])
  import base64
  assert base64.b64decode(payload["data"]) == frame
  assert payload["fresh"] is True
  publisher.current = {"alert": {"id": 3, "text1": "Latest alert"}, "speed_limit_source": "Offline"}
  for index in range(20):
    publisher.callback(live.LiveSnapshot(metadata_revision=1).pack(index + 2, 100100 + index * 100))
  assert all(subscriber.qsize() <= lan.LAN_STREAM_QUEUE_SIZE for subscriber in manager._subscribers)
  assert b"Latest alert" in next(stream), "Queue eviction must preserve current metadata"
  assert b"Latest alert" in next(second)
  stream.close()
  second.close()
  assert not manager._subscribers
  manager.close()
  assert manager.status()["running"] is False

  # A fresh publisher timestamp cannot make frozen cereal inputs fresh.
  producer = live.LiveTelemetryPublisher(lambda frame: None, Params(), Params(), monotonic=lambda: clock[0])
  producer._cached_params = lambda now: {"IsOffroad": True}
  monkeypatch.setattr(live, "build_live_snapshot", lambda *args: live.LiveSnapshot())
  sm = SimpleNamespace(recv_time={"deviceState": 99.5}, valid={"deviceState": True})
  producer.publish_once(sm)
  assert producer.source_status() == {"fresh": True, "source_age_sec": 0.5}
  clock[0] += 3
  producer.publish_once(sm)
  assert producer.source_status() == {"fresh": False, "source_age_sec": 3.5}


@pytest.mark.skipif(shutil.which("node") is None, reason="no node.js runtime available")
def test_lan_client_and_connection_controller(tmp_path):
  for relative in ("js/ble/live_frames.js", "js/lan/live_lan.js", "js/lan/connection.js"):
    source = UI_ROOT / relative
    body = source.read_text().replace('"../ble/live_frames.js"', '"./live_frames.mjs"').replace('"./live_lan.js"', '"./live_lan.mjs"')
    (tmp_path / (source.stem + ".mjs")).write_text(body)
  script = (Path(__file__).with_name("lan_telematics_test.mjs")).read_text()
  result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], cwd=tmp_path, capture_output=True, text=True, timeout=20)
  assert result.returncode == 0, result.stderr
