from dataclasses import replace
from types import SimpleNamespace

import pytest

import openpilot.starpilot.system.uniden_r4 as uniden_r4
from openpilot.starpilot.system.bluetooth import protocol
from openpilot.starpilot.system.bluetooth.protocol import BluetoothDevice, BluetoothStatus

DETECTOR = "AA:BB:CC:DD:EE:01"
PHONE = "AA:BB:CC:DD:EE:02"


class FakeDaemon:
  """bluetooth_managerd as seen through BluetoothClient."""

  def __init__(self, detector=None, pair_error="", connect_after_pair=True, connect_error=None, enabled=True):
    phone = BluetoothDevice(address=PHONE, name="iPhone", paired=True, trusted=True, connected=True)
    self.devices = [phone] + ([detector] if detector is not None else [])
    self.enabled = enabled
    self.discovering = False
    self.pairing_address = ""
    self.error = ""
    self.pair_error = pair_error
    self.connect_after_pair = connect_after_pair
    self.connect_error = connect_error
    self.pair_polls = 0
    self.calls = []

  def status(self):
    if self.pairing_address:
      self.pair_polls += 1
      if self.pair_polls >= 2:
        self._finish_pair()
    return BluetoothStatus(enabled=self.enabled, discovering=self.discovering, pairing_address=self.pairing_address,
                           devices=tuple(self.devices), error=self.error)

  def _finish_pair(self):
    index = next(i for i, device in enumerate(self.devices) if device.address == self.pairing_address)
    if self.pair_error:
      self.error = self.pair_error
    else:
      self.devices[index] = replace(self.devices[index], paired=True, trusted=True, connected=self.connect_after_pair)
    self.pairing_address = ""
    self.discovering = False

  def start_scan(self):
    self.calls.append("start_scan")
    self.discovering = True

  def stop_scan(self):
    self.calls.append("stop_scan")
    self.discovering = False

  def pair(self, address):
    self.calls.append(("pair", address))
    self.pairing_address = address

  def connect(self, address):
    self.calls.append(("connect", address))
    if self.connect_error is not None:
      raise self.connect_error


@pytest.fixture
def harness(monkeypatch):
  clock = [0.0]
  shm, params = {}, {}
  monkeypatch.setattr(uniden_r4, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s)))
  monkeypatch.setattr(uniden_r4, "set_shm_param", lambda key, value: shm.__setitem__(key, value))
  monkeypatch.setattr(uniden_r4, "get_shm_param", lambda key, default=None: shm.get(key, default))
  monkeypatch.setattr(uniden_r4, "set_param", lambda key, value: params.__setitem__(key, value))

  def run(daemon):
    monkeypatch.setattr(protocol, "BluetoothClient", lambda: daemon)
    uniden_r4._pairing_worker()
    return SimpleNamespace(state=shm.get("UnidenPairState"), message=shm.get("UnidenPairMessage", ""), shm=shm, params=params)

  return run


def detector(**changes):
  return BluetoothDevice(address=DETECTOR, name="R4@1234", uniden=True, **changes)


def test_new_detector_pairs_through_bluetooth_managerd(harness):
  daemon = FakeDaemon(detector())
  result = harness(daemon)

  assert result.state == "success"
  # Pairing and the post-bond connect both belong to bluetooth_managerd.
  assert daemon.calls == ["start_scan", ("pair", DETECTOR)]
  assert result.params["UnidenR4Mac"] == DETECTOR
  assert result.shm["UnidenManualConnectTrigger"] is True


def test_detector_matched_by_name_when_daemon_does_not_flag_it(harness):
  daemon = FakeDaemon(BluetoothDevice(address=DETECTOR, name="R9@5678"))
  assert harness(daemon).state == "success"
  assert ("pair", DETECTOR) in daemon.calls


def test_already_bonded_detector_only_connects(harness):
  daemon = FakeDaemon(detector(paired=True, trusted=True))
  result = harness(daemon)

  assert result.state == "success"
  assert daemon.calls == ["start_scan", "stop_scan", ("connect", DETECTOR)]


def test_bonded_but_unreachable_keeps_mac(harness):
  daemon = FakeDaemon(detector(paired=True, trusted=True), connect_error=RuntimeError("Bluetooth device did not connect"))
  result = harness(daemon)

  assert result.state == "unreachable"
  assert result.params["UnidenR4Mac"] == DETECTOR


def test_new_bond_without_services_reports_unreachable_without_a_second_connect(harness):
  daemon = FakeDaemon(detector(), connect_after_pair=False)
  result = harness(daemon)

  assert result.state == "unreachable"
  assert not any(call[0] == "connect" for call in daemon.calls if isinstance(call, tuple))


def test_pair_failure_reports_daemon_error(harness):
  daemon = FakeDaemon(detector(), pair_error="Authentication Failed")
  result = harness(daemon)

  assert result.state == "failed"
  assert "Authentication Failed" in result.message
  assert "UnidenR4Mac" not in result.params


def test_missing_detector_rescans_then_fails(harness):
  daemon = FakeDaemon(None)
  original_status = daemon.status

  def status():
    # bluetooth_managerd ends each scan after 20s.
    daemon.discovering = False
    return original_status()

  daemon.status = status
  result = harness(daemon)

  assert result.state == "failed"
  assert daemon.calls.count("start_scan") > 1
  assert daemon.calls[-1] == "stop_scan"


def test_bluetooth_off_fails_without_scanning(harness):
  daemon = FakeDaemon(detector(), enabled=False)
  result = harness(daemon)

  assert result.state == "failed"
  assert daemon.calls == []


def test_setup_refusal_is_reported(harness):
  daemon = FakeDaemon(detector())

  def refuse():
    raise RuntimeError("Bluetooth setup requires offroad or a stationary vehicle in Park")

  daemon.start_scan = refuse
  result = harness(daemon)

  assert result.state == "failed"
  assert "stationary vehicle in Park" in result.message


def test_select_detector_prefers_saved_mac_then_paired():
  from openpilot.starpilot.system.uniden_radar_d import _select_detector

  other = BluetoothDevice(address="AA:BB:CC:DD:EE:09", name="R8@9999", uniden=True, paired=True)
  saved = BluetoothDevice(address=DETECTOR, name="R4@1234", uniden=True, paired=True)
  status = BluetoothStatus(devices=(other, saved))

  assert _select_detector(status, DETECTOR).address == DETECTOR
  assert _select_detector(status, "").address == "AA:BB:CC:DD:EE:09"

  no_detector = BluetoothStatus(devices=(BluetoothDevice(address=PHONE, name="iPhone", paired=True),))
  assert _select_detector(no_detector, "") is None

