"""Jetlink borrows the small model's camera warp: the one JIT, its inputs and its output, read into host memory."""
import ctypes
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

from openpilot.starpilot.models import jetlink_adapter
from openpilot.starpilot.models.jetlink_warp import CatalogWarp

GEOMETRY = (16, 8)  # model_w, model_h
SHAPE = (2, 6, 4, 8)


class FakeBuffer:
  def __init__(self, live=True):
    self.mapped = np.zeros(int(np.prod(SHAPE)), dtype=np.uint8)
    self.nbytes = self.mapped.nbytes
    self.allocator = NS(dev=NS(synchronize=Mock()))
    self.live = live

  def as_memoryview(self, allow_zero_copy=False):
    assert allow_zero_copy
    # tinygrad's fallback without a host mapping is a one-off copy
    return memoryview(self.mapped if self.live else self.mapped.copy())


def output(buffer):
  return NS(uop=NS(base=NS(buffer=buffer)), shape=SHAPE)


def small_model(descriptor_shape=SHAPE, descriptor=True, live=True):
  calls, buffers = [], [FakeBuffer(live)]
  npy = {"tfm": np.zeros((3, 3), np.float32), "big_tfm": np.zeros((3, 3), np.float32)}

  def enqueue(**kw):
    calls.append({**kw, "tfm_at_call": npy["tfm"].copy(), "big_tfm_at_call": npy["big_tfm"].copy()})
    # a flat frame warps to itself: the output takes the road frame's first byte
    buffers[-1].mapped[:] = ctypes.c_uint8.from_address(kw["frame"][1]).value
    return output(buffers[-1])

  small = NS(camera_warp_descriptor=NS(output_shape=descriptor_shape, dtype=np.dtype(np.uint8)) if descriptor else None,
             warp_enqueue=enqueue, vision_input_names=("img", "big_img"), npy=npy,
             input_queues={"tfm": "tfm queue", "big_tfm": "big_tfm queue", "img_q": "history"},
             warp_input_keys=("tfm", "big_tfm"), _blob_cache={}, frame_buf_size=32, warp_device="QCOM")
  return small, calls, buffers


@pytest.fixture(autouse=True)
def blobs():
  with patch("tinygrad.Tensor.from_blob", side_effect=lambda ptr, shape, **kw: ("blob", ptr, shape, kw["device"])) as blob:
    yield blob


def frames(fill):
  return np.full(32, fill, np.uint8), np.full(32, fill + 1, np.uint8)


def test_warps_with_the_small_models_jit_inputs_and_cache():
  small, calls, buffers = small_model()
  warp = CatalogWarp(small, GEOMETRY)
  assert len(calls) == 2  # the warm-up and the live-read probe, before modeld goes realtime
  tfm, big_tfm = np.full((3, 3), 2, np.float32), np.full((3, 3), 3, np.float32)
  road, wide = frames(7)
  warp.start(road.ctypes.data, wide.ctypes.data, tfm, big_tfm)
  call = calls[-1]
  assert call["tfm"] == "tfm queue" and call["big_tfm"] == "big_tfm queue" and "img_q" not in call
  assert (call["tfm_at_call"] == tfm).all() and (call["big_tfm_at_call"] == big_tfm).all()
  assert call["frame"] == small._blob_cache[("img", road.ctypes.data)] == ("blob", road.ctypes.data, (32,), "QCOM")
  assert call["big_frame"] == small._blob_cache[("big_img", wide.ctypes.data)]
  warp.wait()
  buffers[-1].allocator.dev.synchronize.assert_called()
  assert warp.output.nbytes == int(np.prod(SHAPE)) and set(bytes(warp.output)) == {7}
  road2, wide2 = frames(9)
  warp.start(road2.ctypes.data, wide2.ctypes.data, tfm, big_tfm)  # what was sent holds until wait()
  assert set(bytes(warp.output)) == {7}
  warp.wait()
  assert set(bytes(warp.output)) == {9}


def test_rebinds_when_the_jit_returns_a_new_buffer():
  small, _, buffers = small_model()
  warp = CatalogWarp(small, GEOMETRY)
  buffers.append(FakeBuffer())
  road, wide = frames(5)
  warp.start(road.ctypes.data, wide.ctypes.data, np.eye(3), np.eye(3))
  warp.wait()
  assert set(bytes(warp.output)) == {5}


def test_refuses_an_output_it_cannot_read_live():
  small, _, _ = small_model(live=False)
  with pytest.raises(RuntimeError, match="stale frame"):
    CatalogWarp(small, GEOMETRY)


@pytest.mark.parametrize("kw", [{"descriptor": False}, {"descriptor_shape": (2, 6, 8, 16)}])
def test_refuses_a_warp_the_link_cannot_send(kw):
  small, _, _ = small_model(**kw)
  with pytest.raises(RuntimeError):
    CatalogWarp(small, GEOMETRY)


def adapter_warp(small, legacy):
  adapter = jetlink_adapter._adapter_class().__new__(jetlink_adapter._adapter_class())
  log = Mock()
  with patch.object(type(adapter), "log", log, create=True), \
       patch("openpilot.starpilot.models.runtime_bridge.runtime.selected_legacy", return_value=legacy), \
       patch("openpilot.starpilot.models.runner.CatalogModelState", NS):
    return adapter.model_warp(small, 1344, 760, GEOMETRY), log


def test_adapter_uses_the_small_models_warp_on_either_runtime():
  for legacy in (False, True):
    warp, _ = adapter_warp(small_model()[0], legacy)
    assert isinstance(warp, CatalogWarp)


def test_adapter_falls_back_to_the_stock_warp_only_under_legacy9d():
  small, _, _ = small_model(descriptor=False)
  warp, log = adapter_warp(small, legacy=True)
  assert warp is None
  log.exception.assert_called_once()
  with pytest.raises(RuntimeError, match="policy pipeline"):
    adapter_warp(small, legacy=False)
