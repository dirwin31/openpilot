import threading
from types import SimpleNamespace

import pytest

from openpilot.starpilot.system.android_auto import car_ui, headless_egl, supervisor
from openpilot.starpilot.system.android_auto.frame_source import FrameConsumer, FrameProducer, FrameRequest


def test_capture_wait_matches_the_pacing_deadline():
  producer = FrameProducer()
  request = FrameRequest(4, 2, 0, 0, 100_000)
  producer._next_capture_ns = 1_100_000_000
  assert producer.capture_delay(request, 1_000_000_000) == pytest.approx(0.075)
  assert not producer.due(request, 1_074_999_999)
  assert producer.due(request, 1_075_000_000)
  assert producer.capture_delay(request, 1_500_000_000) == 0


def test_renderer_can_initialize_without_focus_and_resume_demand(tmp_path):
  path = str(tmp_path / "frames")
  consumer = FrameConsumer(path)
  producer = FrameProducer(path)
  request = FrameRequest(4, 2, 0, 0, 33333)
  try:
    consumer.configure(request)
    assert car_ui.wait_for_request(producer) == request
    assert producer.pending_request() is None
    consumer.demand()
    assert producer.pending_request() == request
    consumer.release_demand()
    assert producer.pending_request() is None
    consumer.demand()
    assert producer.pending_request() == request
  finally:
    producer._close()
    consumer.close()


def test_readback_reuses_storage_and_published_frames_are_independent(monkeypatch, tmp_path):
  calls = []

  class Function:
    def __init__(self, fn):
      self.fn = fn

    def __call__(self, *args):
      return self.fn(*args)

  value = [17]

  def read_pixels(x, y, width, height, format_, type_, pixels):
    assert (x, y, width, height, format_, type_) == (0, 0, 4, 2, 0x1908, 0x1401)
    pixels[:] = [value[0]] * len(pixels)

  gl = SimpleNamespace(glBindFramebuffer=Function(lambda *args: calls.append(args)), glReadPixels=Function(read_pixels))
  monkeypatch.setattr(headless_egl.C, "CDLL", lambda _: gl)
  readback = headless_egl.RgbaReadback(4, 2)
  path = str(tmp_path / "frames")
  consumer = FrameConsumer(path)
  producer = FrameProducer(path)
  request = FrameRequest(4, 2, 0, 0, 33333)
  try:
    consumer.configure(request)
    consumer.demand()
    assert producer.pending_request() == request
    first = readback.read(42)
    producer.publish(request, first, 1)
    frame = consumer.latest()
    value[0] = 31
    assert readback.read(42) is first
    assert bytes(first) == bytes([31]) * 32
    assert frame.data == bytes([17]) * 32
    assert calls == [(0x8D40, 42), (0x8D40, 0)] * 2
    def fail_read(*args):
      raise RuntimeError("read failed")

    gl.glReadPixels.fn = fail_read
    with pytest.raises(RuntimeError, match="read failed"):
      readback.read(42)
    assert calls[-1] == (0x8D40, 0)
  finally:
    producer._close()
    consumer.close()


@pytest.mark.parametrize("scenario", ["ready", "idle", "blocked", "unfocused", "input_burst"])
def test_stream_waits_only_when_needed_and_preserves_flow_control(monkeypatch, scenario):
  clock = [100.0]
  monkeypatch.setattr(supervisor.time, "monotonic", lambda: clock[0])
  monkeypatch.setattr(supervisor.time, "sleep", lambda _: pytest.fail("stream must wait on input, not sleep"))
  stop = threading.Event()
  waits, sent, demands, touches = [], [], [], []
  pending_input = [40 if scenario == "input_burst" else 0]

  class Session:
    focused = scenario != "unfocused"
    needs_keyframe = True
    touch_events = []
    blocked = scenario == "blocked"

    def can_send(self):
      return self.focused and not self.blocked

    def pump(self, timeout):
      waits.append(timeout)
      clock[0] += timeout
      if pending_input[0]:
        pending_input[0] -= 1
        self.touch_events.append(pending_input[0])
        return True
      if len(waits) == 2:
        self.focused = True
        self.blocked = False
      return False

    def check_progress(self):
      pass

    def send_frame(self, data, timestamp, *, keyframe):
      assert self.can_send()
      sent.append(clock[0])
      self.needs_keyframe = False
      if len(sent) == 3:
        stop.set()

    def stats(self):
      return {}

  session = Session()

  class Source:
    frames = 0
    label = "car"

    def demand(self, seconds):
      demands.append(True)

    def release_demand(self):
      demands.append(False)

    def latest(self):
      if scenario == "idle" and len(waits) == 1:
        return None
      self.frames += 1
      return SimpleNamespace(data=b"rgba", captured_ns=int(clock[0] * 1e9))

    def send_touches(self, events):
      touches.extend(events)

    def check(self, now, *, focused):
      pass

  def encode(data, *, keyframe):
    clock[0] += 0.035
    return b"h264", keyframe

  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  sup._stop = stop
  sup._status = {"state": "streaming"}
  sup._stage = lambda _: None
  sup._set = lambda **values: sup._status.update(values)
  sup.log = lambda *args, **kwargs: None
  sup._stream(session, SimpleNamespace(encode_rgba=encode, last_encode_ms=35), Source(),
              SimpleNamespace(still_connected=lambda: True), 1 / 30)
  assert len(sent) == 3
  assert sent[1] - sent[0] == pytest.approx(0.035)
  assert sent[2] - sent[1] == pytest.approx(0.035)
  if scenario == "ready":
    assert waits == [0.0] * 3
  elif scenario == "idle":
    assert waits[1] == pytest.approx(1 / 120)
  elif scenario in ("blocked", "unfocused"):
    assert waits[:2] == [0.05, 0.05]
    assert demands[0] is (scenario == "blocked")
    assert demands[-1]
  elif scenario == "input_burst":
    assert len(touches) == 40
    assert len(waits) == 41  # video still progresses after each bounded batch
