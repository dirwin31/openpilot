"""Actual LAN addresses for direct Galaxy access, independent of the tunnel."""

from datetime import datetime, timezone
from ipaddress import IPv4Address, ip_network
import json
import re
import subprocess
import threading
import time


LAN = tuple(ip_network(value) for value in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16'))


# Virtual links can carry RFC1918 addresses too; they are not a local Wi-Fi/Ethernet address.
TUNNEL_KINDS = frozenset(('tun', 'wireguard', 'gre', 'gretap', 'ipip', 'sit', 'vti', 'vti6', 'vxlan', 'geneve'))
TUNNEL_PREFIXES = ('tun', 'tap', 'wg', 'tailscale', 'zt', 'docker', 'veth', 'br-', 'p2p', 'usb', 'rndis', 'ppp', 'wwan', 'rmnet')


class LocalAccess:
  def __init__(self, port=8082, *, run=subprocess.run, clock=time.monotonic):
    self.port, self.run, self.clock = port, run, clock
    self.lock = threading.Lock()
    self.cached, self.expiry = None, 0.0

  def snapshot(self):
    with self.lock:
      if self.cached is not None and self.clock() < self.expiry:
        return self.cached
      addresses = []
      reason = 'Connect to the Galaxy hotspot, or put the comma and phone on the same Wi-Fi or Ethernet network.'
      try:
        result = self.run(['ip', '-j', '-4', 'address', 'show', 'up'], capture_output=True, timeout=1, check=True)
        if len(result.stdout) > 65536:
          raise ValueError('Network inventory too large')
        interfaces = json.loads(result.stdout)
        if not isinstance(interfaces, list):
          raise ValueError('Invalid network inventory')
        for interface in interfaces[:64]:
          if len(addresses) >= 32:
            break
          if type(interface) is not dict:
            continue
          name = interface.get('ifname', '')
          flags = interface.get('flags', [])
          info_rows = interface.get('addr_info', [])
          if (not isinstance(name, str) or not name or type(flags) is not list or
              'UP' not in flags or 'LOOPBACK' in flags or type(info_rows) is not list or
              name.lower().startswith(TUNNEL_PREFIXES) or
              interface.get('operstate') in ('DOWN', 'LOWERLAYERDOWN', 'NOTPRESENT') or
              isinstance(interface.get('linkinfo'), dict) and
              interface['linkinfo'].get('info_kind') in TUNNEL_KINDS):
            continue
          for info in info_rows[:16]:
            if len(addresses) >= 32:
              break
            if type(info) is not dict:
              continue
            if (info.get('family') != 'inet' or info.get('scope') != 'global' or
                info.get('valid_life_time') == 0 or info.get('preferred_life_time') == 0 or
                info.get('tentative') or info.get('dadfailed')):
              continue
            address = IPv4Address(info['local'])
            if not any(address in network for network in LAN):
              continue
            url = f'http://{address}:{self.port}/'
            if any(row['url'] == url for row in addresses):
              continue
            addresses.append({'interface': name[:64], 'label': f'{address} ({name[:64]})', 'url': url})
        addresses.sort(key=lambda row: (row['interface'], row['url']))
      except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        reason = 'The comma could not read its network addresses. Check its Network settings and refresh.'
        addresses = []
      self.cached = {'available': bool(addresses), 'addresses': addresses,
                     'reason': '' if addresses else reason}
      self.expiry = self.clock() + 5.0
      return self.cached


DISCOVERY_UNIT = 'galaxy-lan-discovery.service'
DISCOVERY_MESSAGE = re.compile(
  r'(Galaxy Wi-Fi discovery available on [0-9]{1,3} local interface\(s\)|'
  r'Wi-Fi discovery unavailable: [A-Za-z][A-Za-z0-9_]{0,63}|LAN publisher cleanup failed)\Z',
)


def connection_capture(addresses, *, run=subprocess.run):
  lines = ['StarPilot connection capture', f'Captured at (UTC): {datetime.now(timezone.utc).isoformat(timespec="seconds")}',
           'Galaxy endpoint: HTTP port 8082', 'Bonjour service: _sp-galaxy._tcp.local.', '', 'Local addresses']
  lines.extend(f'{row["interface"]}: {row["url"]}' for row in addresses['addresses'])
  if not addresses['available']:
    lines.append(addresses['reason'] or 'No eligible local address')
  try:
    result = run(['systemctl', 'is-active', DISCOVERY_UNIT], capture_output=True, timeout=1)
    raw = result.stdout.decode('ascii', errors='replace').strip()
    state = raw if raw in {'active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading'} else 'unavailable'
  except (OSError, subprocess.SubprocessError):
    state = 'unavailable'
  lines.extend(['', f'Bonjour advertiser: {state}', 'Recent advertiser events (last 30 minutes)'])
  try:
    result = run(['journalctl', '--no-pager', '--quiet', '-u', DISCOVERY_UNIT, '--since', '-30min',
                  '-n', '24', '-o', 'json', '--output-fields=MESSAGE'],
                 capture_output=True, timeout=2, check=True)
    events = []
    for raw in result.stdout.splitlines()[-24:]:
      if len(raw) > 8192:
        continue
      try:
        entry = json.loads(raw)
        message = entry.get('MESSAGE') if isinstance(entry, dict) else None
        if isinstance(message, str) and DISCOVERY_MESSAGE.fullmatch(message):
          stamp = entry.get('__REALTIME_TIMESTAMP', '')
          if isinstance(stamp, str) and re.fullmatch(r'[0-9]{1,16}', stamp):
            timestamp = datetime.fromtimestamp(int(stamp) / 1e6, timezone.utc).isoformat(timespec='seconds')
            message = f'{timestamp} {message}'
          events.append(message)
      except (ValueError, OSError, OverflowError):
        continue
    lines.extend(events or ['No matching events'])
  except (OSError, subprocess.SubprocessError):
    lines.append('Advertiser journal unavailable')
  return {'schemaVersion': 1, 'text': '\n'.join(lines) + '\n'}
