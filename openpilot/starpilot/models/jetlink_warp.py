"""Jetlink's warp (jetlink.openpilot.warp.Warp's interface) over the small model's own camera warp.

The stock driving warp Jetlink ships with is a legacy9d pickle, which a native02e modeld cannot load. A catalog
model on the policy image pipeline already runs the same warp every frame, compiled for whichever runtime it was
selected under, and makes the frame the link sends: both cameras as (2, 6, model_h/2, model_w/2) uint8, the same
YUV layout as examples/openpilot/compile_warp.py's yuv420 warp. Reusing it keeps the link on the user's small
model's runtime and loads no second warp.

Only one of the small and the large model runs a frame (JoiningModelState), so the warp JIT, its inputs and its
output buffer are never in use by both at once. The small model rewrites its transforms every frame it runs.
"""
from __future__ import annotations

import numpy as np

PROBE = 77


class CatalogWarp:
  def __init__(self, small, geometry: tuple[int, int]):
    from tinygrad import Tensor
    descriptor = small.camera_warp_descriptor
    if descriptor is None:
      raise RuntimeError("the small model's image history is not on the policy pipeline: it has no shared warp")
    model_w, model_h = geometry
    shape = (2, 6, model_h // 2, model_w // 2)
    if tuple(descriptor.output_shape) != shape or descriptor.dtype.itemsize != 1:
      raise RuntimeError(f"the small model's warp makes {descriptor.output_shape} {descriptor.dtype}, the link sends {shape} uint8")
    self._small = small
    self._enqueue = small.warp_enqueue
    self._names = small.vision_input_names
    self._tfm, self._big_tfm = small.npy["tfm"], small.npy["big_tfm"]
    self._queues = {key: small.input_queues[key] for key in small.warp_input_keys}
    self._Tensor = Tensor
    # warm up on blank frames, as the small model's own warmup does, before modeld goes realtime: the JIT is
    # captured already, so this replays it and finds the output buffer every frame writes
    self._blank = [np.zeros(small.frame_buf_size, dtype=np.uint8) for _ in self._names]
    eye = np.eye(3, dtype=np.float32)
    frame, big_frame = self._blank
    self._run(frame.ctypes.data, big_frame.ctypes.data, eye, eye)
    self._bind()
    self.output = memoryview(self._host)
    # as_memoryview() hands back a one-off copy where it finds no mapping of the buffer, which would send the
    # warm-up's frame forever: a flat frame under the identity samples to itself, so `output` must read it back
    self._probe = [np.full(small.frame_buf_size, PROBE, dtype=np.uint8) for _ in self._names]
    frame, big_frame = self._probe
    self.start(frame.ctypes.data, big_frame.ctypes.data, eye, eye)
    self.wait()
    if not (self._host == PROBE).all():
      raise RuntimeError("the small model's warp output is not read live: the link would send a stale frame")

  def _frame(self, name: str, pointer: int):
    # the small model's own cache and arguments, so the JIT sees exactly the inputs it was captured with
    cache = self._small._blob_cache
    key = (name, pointer)
    if key not in cache:
      cache[key] = self._Tensor.from_blob(pointer, (self._small.frame_buf_size,), dtype="uint8",
                                          device=self._small.warp_device)
    return cache[key]

  def _run(self, frame: int, big_frame: int, tfm, big_tfm) -> None:
    self._tfm[:] = tfm
    self._big_tfm[:] = big_tfm
    self._out = self._enqueue(**self._queues, frame=self._frame(self._names[0], frame),
                              big_frame=self._frame(self._names[1], big_frame))

  def _bind(self) -> None:
    self._buffer = self._out.uop.base.buffer
    self._device = self._buffer.allocator.dev
    # the GPU's mapping of the output, read once a frame into host memory: the link sends from `output`
    self._mapped = np.frombuffer(self._buffer.as_memoryview(allow_zero_copy=True), dtype=np.uint8)
    expected = int(np.prod(self._out.shape))
    if self._buffer.nbytes != expected or self._mapped.nbytes < expected:
      raise RuntimeError(f"the warp's output is {self._buffer.nbytes} bytes ({self._mapped.nbytes} mapped), not {expected}")
    self._mapped = self._mapped[:expected]
    self._host = np.empty(expected, dtype=np.uint8)

  def start(self, frame: int, big_frame: int, tfm, big_tfm) -> None:
    """Warp the camera buffers at these addresses under their transforms; `output` holds it once wait() returns."""
    self._run(frame, big_frame, tfm, big_tfm)
    if self._out.uop.base.buffer is not self._buffer:
      self._bind()
      self.output = memoryview(self._host)

  def wait(self) -> None:
    self._device.synchronize()
    np.copyto(self._host, self._mapped)
