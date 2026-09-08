"""Restrict live telemetry using the socket peer, before Flask handles a request.

FRP connects to 127.0.0.1:8082, including requests from galaxy.link. Never trust
Host or forwarded headers to distinguish that tunnel from a direct LAN client.
Install this outside any middleware that rewrites REMOTE_ADDR.

A peer is direct only when its address falls inside a subnet currently assigned
to an active local interface, so unusual IPv4 networks (carrier hotspots, public
ranges) and global-unicast IPv6 neighbours work without an allowlist. Loopback,
overlay/tunnel, container and cellular-modem interfaces are excluded, so a
Tailscale, WireGuard, ZeroTier, container or carrier-side peer is never mistaken
for direct local Wi-Fi. USB tethering and Ethernet stay eligible. Discovery
runs at most once per refresh interval, never for a peer that cannot be direct,
and fails closed.
"""
import ipaddress
import subprocess
import threading
import time

PATHS = frozenset(("/api/telematics/status", "/api/telematics/stream"))
REMOTE_ORIGINS = frozenset(("https://galaxy.link", "https://galaxy.firestar.link"))
INTERFACE_REFRESH_SEC = 15.0
INTERFACE_TIMEOUT_SEC = 0.5
MAX_INTERFACE_NETWORKS = 64

# Interfaces whose peers are remote even though the kernel calls them local: overlay and tunnel
# networks (Tailscale, WireGuard, ZeroTier, OpenVPN/tun), container plumbing (docker, bridges,
# veth) and the built-in cellular modem (tici hardware.py reads `wwan0`; `rmnet*` and `ppp*` are
# the other modem families), whose neighbours belong to the carrier, not to this LAN. USB tether
# (`usb*`, `rndis*`), Ethernet and `wlan*` are not listed and stay eligible.
# the_galaxy/utilities.py excludes the same overlay prefixes when it picks the device's LAN address.
REMOTE_INTERFACE_PREFIXES = ("tailscale", "tun", "docker", "br-", "veth", "zt", "wg",
                             "wwan", "rmnet", "ppp")
LOOPBACK_INTERFACE = "lo"


def _local_interface(name):
  """False for loopback and for remote overlay/tunnel/container/cellular interfaces."""
  # `ip` prints `veth9a1@if7` and `wwan0.1@wwan0`: reject if either the device or its parent link
  # is remote, so a VLAN or macvlan stacked on a tunnel or on the modem cannot pass as local Wi-Fi.
  labels = [label.strip().rstrip(":").lower() for label in str(name).rstrip(":").split("@")]
  if not any(labels):
    return False
  for device in labels:
    # A dotted VLAN suffix rides its parent: `tun0.5` is still the tunnel, `wwan0.1` is still the
    # modem, `lo.5` is still loopback.
    if device.startswith(REMOTE_INTERFACE_PREFIXES) or device.split(".", 1)[0] == LOOPBACK_INTERFACE:
      return False
  return True


def _usable_network(value):
  # Networks reachable only through this device (loopback) can never identify a direct client.
  try:
    network = ipaddress.ip_network(value, strict=False)
  except (ValueError, TypeError):
    return None
  address = network.network_address
  if network.prefixlen == 0 or address.is_loopback or address.is_multicast or address.is_unspecified:
    return None
  return network


def parse_ip_addr(text):
  """Turn `ip -o addr show up` output into the subnets of active local interfaces."""
  # Each line is `<index>: <device>    <family> <address>/<prefix> ...`.
  networks = []
  for line in text.splitlines():
    parts = line.split()
    if len(parts) < 4 or parts[2] not in ("inet", "inet6") or not _local_interface(parts[1]):
      continue
    network = _usable_network(parts[3].split("%", 1)[0])
    if network is not None:
      networks.append(network)
  return networks


def discover_interface_networks():
  result = subprocess.run(["ip", "-o", "addr", "show", "up"], check=False, capture_output=True,
                          text=True, timeout=INTERFACE_TIMEOUT_SEC)
  if result.returncode != 0:
    raise OSError(f"ip addr show up failed with {result.returncode}")
  return parse_ip_addr(result.stdout)


class ActiveNetworks:
  """Bounded, thread-safe cache of the local interface subnets, refreshed on a timer."""

  def __init__(self, discover=discover_interface_networks, refresh_sec=INTERFACE_REFRESH_SEC,
               limit=MAX_INTERFACE_NETWORKS, monotonic=time.monotonic):
    self._discover = discover
    self._refresh_sec = refresh_sec
    self._limit = limit
    self._monotonic = monotonic
    self._lock = threading.Lock()
    self._networks = None
    self._updated_at = None

  def current(self):
    """Active subnets, or None when discovery failed and every peer must be refused."""
    now = self._monotonic()
    # The lock is held across discovery so a burst of requests cannot spawn a burst of commands.
    with self._lock:
      if self._updated_at is not None and now - self._updated_at < self._refresh_sec:
        return self._networks
      try:
        networks = []
        for value in self._discover():
          network = _usable_network(value)
          if network is not None and network not in networks:
            networks.append(network)
          if len(networks) >= self._limit:
            break
        self._networks = tuple(networks) or None
      except Exception:
        self._networks = None
      self._updated_at = now
      return self._networks


def peer_address(address):
  """The comparable peer address, or None when it can never be a direct client."""
  try:
    peer = ipaddress.ip_address(str(address).strip().split("%", 1)[0])
  except ValueError:
    return None
  if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped:
    peer = peer.ipv4_mapped
  # Loopback is the FRP tunnel; the rest cannot be the source of an accepted connection.
  if peer.is_loopback or peer.is_multicast or peer.is_unspecified:
    return None
  return peer


def peer_in_networks(peer, networks):
  """Match an already parsed peer, so a rejected peer never costs an interface discovery."""
  if peer is None or not networks:
    return False
  return any(peer.version == network.version and peer in network for network in networks)


def is_lan_peer(address, networks):
  return peer_in_networks(peer_address(address), networks)


class LanTelemetryAccess:
  def __init__(self, app, networks=None):
    self.app = app
    self.networks = ActiveNetworks() if networks is None else networks

  def __call__(self, environ, start_response):
    if environ.get("PATH_INFO") not in PATHS:
      return self.app(environ, start_response)
    # Parse the socket peer first: the galaxy.link FRP loopback path, and anything unparsable,
    # unspecified or multicast, is refused without ever running interface discovery.
    peer = peer_address(environ.get("REMOTE_ADDR", ""))
    if peer is None or not peer_in_networks(peer, self.networks.current()):
      body = b'{"available":false,"error":"Connect directly to the comma on local Wi-Fi."}'
      start_response("403 Forbidden", [("Content-Type", "application/json"), ("Cache-Control", "no-store"),
                                        ("Content-Length", str(len(body)))])
      return [body]
    origin = environ.get("HTTP_ORIGIN")
    cors = [("Access-Control-Allow-Origin", origin), ("Vary", "Origin")] if origin in REMOTE_ORIGINS else []
    if environ.get("REQUEST_METHOD") == "OPTIONS" and cors:
      start_response("204 No Content", cors + [("Access-Control-Allow-Methods", "GET"), ("Content-Length", "0")])
      return [b""]

    def respond(status, headers, exc_info=None):
      return start_response(status, headers + cors, exc_info)
    return self.app(environ, respond)
