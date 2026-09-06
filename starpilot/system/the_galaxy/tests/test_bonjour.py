import sys
from types import SimpleNamespace

from openpilot.starpilot.system.the_galaxy.bonjour import GalaxyBonjourAdvertiser, _service_hostname, _service_label, _valid_ipv4


def test_bonjour_service_label_is_stable_and_dns_safe():
  assert _service_label("comma-ab_cd.example") == "StarPilot comma-ab-cd-example"
  assert _service_label("---") == "StarPilot comma"
  assert _service_hostname("comma-ab_cd.example") == "starpilot-comma-ab-cd-example.local"


def test_bonjour_only_advertises_non_loopback_ipv4_addresses():
  assert _valid_ipv4("192.168.50.22") == "192.168.50.22"
  assert _valid_ipv4("127.0.0.1") is None
  assert _valid_ipv4("fe80::1") is None
  assert _valid_ipv4(None) is None


def test_bonjour_advertises_https_port(monkeypatch):
  captured = {}

  class ServiceInfo:
    def __init__(self, *args, **kwargs):
      captured.update(kwargs)
      self.name = args[1]

  class Zeroconf:
    def __init__(self, **kwargs):
      pass

    def register_service(self, service_info):
      captured["registered"] = service_info

    def close(self):
      pass

  fake = SimpleNamespace(
    IPVersion=SimpleNamespace(V4Only=4),
    InterfaceChoice=SimpleNamespace(Default="default"),
    ServiceInfo=ServiceInfo,
    Zeroconf=Zeroconf,
  )
  monkeypatch.setitem(sys.modules, "zeroconf", fake)
  advertiser = GalaxyBonjourAdvertiser(lambda: "192.168.1.2", identifier="comma-test")
  advertiser._register("192.168.1.2")

  assert captured["properties"]["https_port"] == "8443"
  advertiser._unregister()
