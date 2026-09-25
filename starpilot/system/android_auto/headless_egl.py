"""EGL pbuffer context for rendering the StarPilot UI without a window or display ownership.

The Qualcomm EGL on AGNOS needs a GBM native display (``EGL_DEFAULT_DISPLAY`` is
not supported). Opening the DRM *render* node does not take DRM master or touch
the comma's own scanout, so the normal UI keeps the physical screen.

Adapted from yummydirtx/openpilot ``tools/android_auto/native_egl.py`` (MIT),
pinned at 672a16f6183567c0ada53654f8527d97e1a483fa.
"""

from __future__ import annotations

import ctypes as C
import os
import time

RENDER_NODE = "/dev/dri/renderD128"
EGL_OPENGL_ES_API = 0x30A0


class RgbaReadback:
  """Read an existing RGBA framebuffer into storage reused for the session.

  Raylib's GLES texture readback creates/deletes a temporary framebuffer and
  allocates/frees an image every call. The car renderer already owns the output
  framebuffer, and publish copies the pixels before the next read.
  """

  def __init__(self, width: int, height: int):
    self.width, self.height = width, height
    self.gl = C.CDLL("libGLESv2.so")
    self.gl.glBindFramebuffer.argtypes = [C.c_uint, C.c_uint]
    self.gl.glBindFramebuffer.restype = None
    self.gl.glReadPixels.argtypes = [C.c_int] * 4 + [C.c_uint, C.c_uint, C.c_void_p]
    self.gl.glReadPixels.restype = None
    self._storage = (C.c_ubyte * (width * height * 4))()
    self.pixels = memoryview(self._storage).cast("B")

  def read(self, framebuffer: int) -> memoryview:
    # Called after end_texture_mode(), with the default framebuffer bound.
    # RGBA rows are multiples of eight bytes (negotiated sizes are even), so
    # all GLES pack alignments work. Output is already flipped by the GPU.
    self.gl.glBindFramebuffer(0x8D40, framebuffer)  # GL_FRAMEBUFFER
    try:
      self.gl.glReadPixels(0, 0, self.width, self.height, 0x1908, 0x1401, self._storage)  # RGBA, UNSIGNED_BYTE
    finally:
      self.gl.glBindFramebuffer(0x8D40, 0)
    return self.pixels


class HeadlessContext:
  def __init__(self, width: int, height: int):
    self.egl = C.CDLL("libEGL.so")
    signatures = {
      "eglGetDisplay": (C.c_void_p, [C.c_void_p]),
      "eglInitialize": (C.c_uint, [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)]),
      "eglBindAPI": (C.c_uint, [C.c_uint]),
      "eglChooseConfig": (C.c_uint, [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_void_p), C.c_int, C.POINTER(C.c_int)]),
      "eglCreatePbufferSurface": (C.c_void_p, [C.c_void_p, C.c_void_p, C.POINTER(C.c_int)]),
      "eglCreateContext": (C.c_void_p, [C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_int)]),
      "eglMakeCurrent": (C.c_uint, [C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]),
      "eglDestroySurface": (C.c_uint, [C.c_void_p, C.c_void_p]),
      "eglDestroyContext": (C.c_uint, [C.c_void_p, C.c_void_p]),
      "eglTerminate": (C.c_uint, [C.c_void_p]),
      "eglGetError": (C.c_int, []),
    }
    for name, (result, args) in signatures.items():
      function = getattr(self.egl, name)
      function.restype, function.argtypes = result, args
    self.gbm = C.CDLL("libgbm.so")
    self.gbm.gbm_create_device.argtypes = [C.c_int]
    self.gbm.gbm_create_device.restype = C.c_void_p
    self.gbm.gbm_device_destroy.argtypes = [C.c_void_p]
    self.display = self.context = self.surface = None
    self.drm_fd = os.open(RENDER_NODE, os.O_RDWR | os.O_CLOEXEC)
    self.gbm_device = self.gbm.gbm_create_device(self.drm_fd)
    self._check(self.gbm_device, "GBM device")
    self.display = self.egl.eglGetDisplay(self.gbm_device)
    major, minor, count, config = C.c_int(), C.c_int(), C.c_int(), C.c_void_p()
    self._check(self.egl.eglInitialize(self.display, C.byref(major), C.byref(minor)), "initialize")
    self._check(self.egl.eglBindAPI(EGL_OPENGL_ES_API), "bind GLES")
    # SURFACE_TYPE=PBUFFER, RENDERABLE_TYPE=ES3, RGBA8888
    attrs = (C.c_int * 13)(0x3033, 1, 0x3040, 0x0040, 0x3024, 8, 0x3023, 8, 0x3022, 8, 0x3021, 8, 0x3038)
    self._check(self.egl.eglChooseConfig(self.display, attrs, C.byref(config), 1, C.byref(count)) and count.value, "config")
    self.surface = self.egl.eglCreatePbufferSurface(self.display, config, (C.c_int * 5)(0x3057, width, 0x3056, height, 0x3038))
    self._check(self.surface, "pbuffer")
    self.context = self.egl.eglCreateContext(self.display, config, None, (C.c_int * 3)(0x3098, 3, 0x3038))
    self._check(self.context, "context")
    self._check(self.egl.eglMakeCurrent(self.display, self.surface, self.surface, self.context), "make current")

    import pyray as rl
    self.rl = rl
    rl.rl_load_extensions(rl.ffi.cast("void *", C.cast(self.egl.eglGetProcAddress, C.c_void_p).value))
    rl.rlgl_init(width, height)
    rl.rl_set_framebuffer_width(width)
    rl.rl_set_framebuffer_height(height)
    texture = rl.Texture(rl.rl_get_texture_id_default(), 1, 1, 1, 7)
    rl.set_shapes_texture(texture, rl.Rectangle(0, 0, 1, 1))
    # No raylib window means no raylib clock; widgets only need monotonic time.
    started = time.monotonic()
    rl.get_time = lambda: time.monotonic() - started
    rl.get_frame_time = lambda: 1 / 30
    rl.get_fps = lambda: 30

  def _check(self, value, operation: str) -> None:
    if not value:
      raise RuntimeError(f"Headless EGL {operation} failed: {self.egl.eglGetError():#x}")

  def close(self) -> None:
    try:
      self.rl.rlgl_close()
    except Exception:
      pass
    if self.display:
      self.egl.eglMakeCurrent(self.display, None, None, None)
      if self.context:
        self.egl.eglDestroyContext(self.display, self.context)
      if self.surface:
        self.egl.eglDestroySurface(self.display, self.surface)
      self.egl.eglTerminate(self.display)
    if self.gbm_device:
      self.gbm.gbm_device_destroy(self.gbm_device)
    os.close(self.drm_fd)
