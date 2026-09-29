from openpilot.system.loggerd.uploader import StarpilotAutoMonitor, STARPILOT_AUTO_CONNECTED_STATES


class FakeClient:
  def __init__(self, state="idle", available=True, error=None):
    self.state, self.available, self.error, self.calls = state, available, error, 0

  def status(self):
    self.calls += 1
    if self.error:
      raise self.error
    return {"state": self.state}


def test_connected_states():
  for state in ("streaming", "suspended", "negotiating", "joining_wifi"):
    assert StarpilotAutoMonitor(FakeClient(state)).is_connected(0.)
  for state in ("idle", "backoff", "error", "connecting_bluetooth", "discovering", "stopping"):
    assert state not in STARPILOT_AUTO_CONNECTED_STATES
    assert not StarpilotAutoMonitor(FakeClient(state)).is_connected(0.)


def test_status_is_cached_between_checks():
  client = FakeClient("streaming")
  monitor = StarpilotAutoMonitor(client, interval=5.)
  assert monitor.is_connected(10.)
  client.state = "idle"
  assert monitor.is_connected(14.9) and client.calls == 1
  assert not monitor.is_connected(15.) and client.calls == 2


def test_daemon_missing_or_failing_means_not_connected():
  assert not StarpilotAutoMonitor(FakeClient("streaming", available=False)).is_connected(0.)
  assert not StarpilotAutoMonitor(FakeClient("streaming", error=OSError("socket timed out"))).is_connected(0.)
