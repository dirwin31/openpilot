"""The outputs the Jetson add-on answers for each detector are every graph output of the bundled model."""
import numpy as np
import pytest

from openpilot.starpilot.speed_limits.vision import model, traffic_signs
from openpilot.starpilot.speed_limits.vision.traffic_signs import JETSON_OUTPUTS, TrafficSignModelCore


@pytest.mark.parametrize("name", sorted(JETSON_OUTPUTS))
def test_declared_outputs_are_the_whole_graph(name):
  if model.cv2 is None:
    pytest.skip("OpenCV DNN is not available on this host")
  net = model.cv2.dnn.readNetFromONNX(str(TrafficSignModelCore.ASSET_DIR / name))
  declared = JETSON_OUTPUTS[name]
  assert sorted(net.getUnconnectedOutLayersNames()) == sorted(n for n, _ in declared)
  net.setInput(np.zeros((1, 3, 256, 256), np.float32))
  outputs = net.forward([n for n, _ in declared])
  assert [tuple(o.shape) for o in outputs] == [shape for _, shape in declared]


def test_detectors_go_to_the_jetson_as_scaled_u8(monkeypatch):
  calls = []
  monkeypatch.setattr("openpilot.starpilot.models.jetlink_addons.with_jetson",
                      lambda net, addon, sha, outputs, **kw: calls.append((addon, kw)) or net)
  net = object()
  assert traffic_signs._with_jetson("traffic_signs_us_stop_detector.onnx", "0" * 64, net) is net
  assert calls == [("signs", {"scaled_u8": True})]
