import asyncio
from types import SimpleNamespace

import pytest

import openpilot.starpilot.system.uniden_r4 as uniden_r4
import openpilot.starpilot.system.uniden_radar_d as radar_d
from openpilot.starpilot.system.bluetooth import protocol
from openpilot.starpilot.system.bluetooth.protocol import BluetoothDevice, BluetoothStatus

DETECTOR = "AA:BB:CC:DD:EE:01"
PHONE = "AA:BB:CC:DD:EE:02"
OTHER_DETECTOR = "AA:BB:CC:DD:EE:03"


class Stop(BaseException):
  """Escapes the daemon's `except Exception` to end the loop."""


@pytest.fixture
def state(monkeypatch):
  shm, params, history = {}, {}, []

  def set_shm_param(key, value):
    shm[key] = value
    history.append((key, value))

  for module in (uniden_r4, radar_d):
    monkeypatch.setattr(module, "set_shm_param", set_shm_param)
    monkeypatch.setattr(module, "get_shm_param", lambda key, default=None: shm.get(key, default))
    monkeypatch.setattr(module, "get_param", lambda key, default: params.get(key, default))
    monkeypatch.setattr(module, "set_param", lambda key, value: params.__setitem__(key, value))
  return SimpleNamespace(shm=shm, params=params, history=history)


def managerd(monkeypatch, *devices, **status):
  status = {"enabled": True, "powered": True, **status}
  monkeypatch.setattr(protocol, "BluetoothClient", lambda: SimpleNamespace(status=lambda: BluetoothStatus(devices=devices, **status)))


def detector(address=DETECTOR, **changes):
  return BluetoothDevice(address=address, name="R4@1234", uniden=True, **changes)


def test_saved_detector_survives_unreachable_bluetooth_managerd(monkeypatch, state):
  state.params["UnidenR4Mac"] = DETECTOR

  def unreachable():
    raise ConnectionRefusedError
  monkeypatch.setattr(protocol, "BluetoothClient", unreachable)

  assert uniden_r4.discover_uniden_device() == DETECTOR
  assert state.params["UnidenR4Mac"] == DETECTOR


def test_saved_detector_survives_radio_still_starting_at_boot(monkeypatch, state):
  state.params["UnidenR4Mac"] = DETECTOR
  managerd(monkeypatch, powered=False)

  assert uniden_r4.discover_uniden_device() == DETECTOR
  assert state.params["UnidenR4Mac"] == DETECTOR


def test_forgotten_detector_is_cleared_once_the_radio_is_up(monkeypatch, state):
  state.params["UnidenR4Mac"] = DETECTOR
  managerd(monkeypatch, BluetoothDevice(address=PHONE, name="iPhone", paired=True))

  assert uniden_r4.discover_uniden_device() == ""
  assert state.params["UnidenR4Mac"] == ""


def test_only_a_paired_detector_is_adopted(monkeypatch, state):
  managerd(monkeypatch, detector(OTHER_DETECTOR), detector(paired=True))

  assert uniden_r4.discover_uniden_device() == DETECTOR
  assert state.params["UnidenR4Mac"] == DETECTOR


def bluez_device(address, name="R4@1234", paired=True):
  return {"org.bluez.Device1": {"Address": address, "Name": name, "Alias": name, "Paired": paired}}


def test_daemon_prefers_the_saved_detector():
  objects = {"/dev_other": bluez_device(OTHER_DETECTOR), "/dev_saved": bluez_device(DETECTOR)}

  assert radar_d._select_detector(objects, DETECTOR.lower())[0] == "/dev_saved"


def test_daemon_falls_back_to_a_paired_detector_by_name():
  objects = {
    "/dev_scanned": bluez_device(OTHER_DETECTOR, paired=False),
    "/dev_phone": bluez_device(PHONE, name="iPhone"),
    "/dev_detector": bluez_device(DETECTOR, name="R9@5678"),
  }

  assert radar_d._select_detector(objects, "")[0] == "/dev_detector"
  assert radar_d._select_detector({"/dev_scanned": bluez_device(DETECTOR, paired=False)}, DETECTOR) is None


def test_disconnect_pauses_auto_connect_until_connect(monkeypatch, state):
  managerd(monkeypatch, detector(paired=True))
  bluetoothctl = []
  monkeypatch.setattr(uniden_r4.subprocess, "run", lambda cmd, **kwargs: bluetoothctl.append(cmd))

  uniden_r4.trigger_action("disconnect")
  assert state.shm["UnidenAutoConnectPaused"] is True
  assert bluetoothctl == [["bluetoothctl", "disconnect", DETECTOR]]

  uniden_r4.trigger_action("connect")
  assert state.shm["UnidenAutoConnectPaused"] is False
  assert state.shm["UnidenManualConnectTrigger"] is True
  assert len(bluetoothctl) == 1  # uniden_radar_d owns the connection


@pytest.fixture
def daemon(monkeypatch, state):
  """Drive run_uniden_daemon with a fake clock, BlueZ, and detector until `stop(sleeps)` is true."""
  clock = [0.0]
  sleeps = []
  attempts = []
  params = {"BluetoothEnabled": True, "IsOnroad": False}

  class FakeClient:
    outcomes = []

    def __init__(self, device, timeout):
      self.is_connected = False

    async def connect(self):
      outcome = FakeClient.outcomes.pop(0) if FakeClient.outcomes else "connect"
      attempts.append((clock[0], outcome))
      if outcome == "timeout":  # detector off - BlueZ gives up after the full attempt
        clock[0] += radar_d.CONNECT_TIMEOUT_SEC
        raise TimeoutError
      if outcome == "refused":  # e.g. adapter still powering on
        raise RuntimeError("org.bluez.Error.NotReady")
      self.is_connected = True

    async def start_notify(self, uuid, callback):
      pass

    async def write_gatt_char(self, uuid, data, response=False):
      pass

    async def disconnect(self):
      self.is_connected = False

  async def find_detector():
    return SimpleNamespace(address=DETECTOR)

  def run(stop, outcomes=()):
    FakeClient.outcomes = list(outcomes)

    async def sleep(seconds):
      sleeps.append(seconds)
      clock[0] += seconds
      if stop(sleeps):
        raise Stop

    monkeypatch.setattr(radar_d, "Params", lambda: SimpleNamespace(get_bool=lambda key: params.get(key, False)))
    monkeypatch.setattr(radar_d, "bleak", SimpleNamespace(BleakClient=FakeClient))
    monkeypatch.setattr(radar_d, "_find_detector", find_detector)
    monkeypatch.setattr(radar_d, "asyncio", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(radar_d, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    with pytest.raises(Stop):
      asyncio.run(radar_d.run_uniden_daemon())
    return SimpleNamespace(attempts=attempts, sleeps=sleeps, history=state.history)

  return run


def test_detector_powering_on_while_comma_is_offroad_connects(daemon):
  # Detector off for two attempts, then powers on - all while the car is offroad.
  result = daemon(lambda sleeps: sleeps.count(0.5) >= 2, outcomes=["timeout", "timeout"])

  assert [outcome for _, outcome in result.attempts] == ["timeout", "timeout", "connect"]
  # Next attempt starts a second after a timed-out one, so the pending connection has almost no gaps.
  assert result.sleeps[:2] == [radar_d.RETRY_DELAY_SEC, radar_d.RETRY_DELAY_SEC]
  assert ("UnidenRadarConnected", True) in result.history


def test_refused_attempts_back_off_instead_of_spinning(daemon):
  result = daemon(lambda sleeps: len(sleeps) >= 2, outcomes=["refused", "refused"])

  assert result.sleeps == [radar_d.FAST_FAIL_DELAY_SEC, radar_d.FAST_FAIL_DELAY_SEC]


def test_paused_daemon_waits_for_connect_button(daemon, state):
  state.shm["UnidenAutoConnectPaused"] = True

  def stop(sleeps):
    if len(sleeps) == 3:
      state.shm["UnidenManualConnectTrigger"] = True
    return sleeps.count(0.5) >= 1

  result = daemon(stop)

  assert len(result.attempts) == 1
  assert result.attempts[0][0] == sum(result.sleeps[:3])  # nothing tried while paused
  assert state.shm["UnidenAutoConnectPaused"] is False
