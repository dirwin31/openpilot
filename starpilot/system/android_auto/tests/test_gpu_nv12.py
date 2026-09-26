"""Pixel-level GPU regression checks; run with AA_GL_TEST=1 on a desktop or comma.

The expected image uses the original RGBA composition pass, followed by the CPU
NV12 reference. This catches flipped rows, odd-margin placement and chroma blocks
that straddle the padding without duplicating the shader's coordinate logic.
"""

import ctypes as C
import os
import sys

import numpy as np
import pytest

from openpilot.starpilot.system.android_auto import gpu_nv12, headless_egl


@pytest.mark.parametrize("margins", [(-1, 0), (32, 0), (0, -1), (0, 18)])
def test_invalid_margins_leave_no_gpu_resources(margins):
  with pytest.raises(ValueError, match="margins"):
    gpu_nv12.Nv12Converter(32, 18, margin_w=margins[0], margin_h=margins[1])


@pytest.fixture(scope="module")
def gpu():
  if os.getenv("AA_GL_TEST") != "1":
    pytest.skip("AA_GL_TEST=1 enables tests requiring a real graphics context")
  import pyray as rl
  with pytest.MonkeyPatch.context() as patch:
    if sys.platform == "darwin":
      gl = C.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")
      # A windowless CGL context also works in a terminal without a Cocoa app.
      gl.CGLChoosePixelFormat.argtypes = [C.POINTER(C.c_int), C.POINTER(C.c_void_p), C.POINTER(C.c_int)]
      gl.CGLCreateContext.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)]
      gl.CGLSetCurrentContext.argtypes = [C.c_void_p]
      gl.CGLDestroyContext.argtypes = [C.c_void_p]
      gl.CGLDestroyPixelFormat.argtypes = [C.c_void_p]
      pixel_format, context, count = C.c_void_p(), C.c_void_p(), C.c_int()
      # kCGLPFAOpenGLProfile, kCGLOGLPVersion_3_2_Core, kCGLPFAColorSize
      assert gl.CGLChoosePixelFormat((C.c_int * 5)(99, 0x3200, 8, 24, 0), C.byref(pixel_format), C.byref(count)) == 0
      assert gl.CGLCreateContext(pixel_format, None, C.byref(context)) == 0
      gl.CGLDestroyPixelFormat(pixel_format)
      assert gl.CGLSetCurrentContext(context) == 0
      patch.setattr(headless_egl.C, "CDLL", lambda _: gl)
      loader_type = C.CFUNCTYPE(C.c_void_p, C.c_char_p)
      loader = loader_type(lambda name: C.cast(getattr(gl, name.decode(), None), C.c_void_p).value)
      rl.rl_load_extensions(rl.ffi.cast("void *", C.cast(loader, C.c_void_p).value))
      rl.rlgl_init(64, 64)
      rl.rl_set_framebuffer_width(64)
      rl.rl_set_framebuffer_height(64)

      def close():
        rl.rlgl_close()
        gl.CGLSetCurrentContext(None)
        gl.CGLDestroyContext(context)
    else:
      context = headless_egl.HeadlessContext(64, 64)
      close = context.close
    try:
      yield rl
    finally:
      close()


def read_frame(size, regions, asynchronous=False):
  readback = headless_egl.FrameReadback(size, asynchronous=asynchronous)
  try:
    readback.start(regions)
    return bytes(readback.finish())
  finally:
    readback.close()


@pytest.mark.parametrize("margin_w,margin_h", [(0, 0), (4, 2), (6, 6), (3, 5), (1, 1), (31, 17)])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("opaque", [False, True])
def test_fused_composition_matches_original_rgba_pass(gpu, margin_w, margin_h, asynchronous, opaque):
  rl = gpu
  width, height = 32, 18
  visible_w, visible_h = width - margin_w, height - margin_h
  pixels = np.random.default_rng(42).integers(0, 256, (visible_h, visible_w, 4), dtype=np.uint8)
  if opaque:
    pixels[..., 3] = 255
  buffer = rl.ffi.from_buffer(pixels)
  texture_id = rl.rl_load_texture(rl.ffi.cast("void *", buffer), visible_w, visible_h, 7, 1)
  source = rl.Texture(texture_id, visible_w, visible_h, 1, 7)
  output = rl.load_render_texture(width, height)
  converter = gpu_nv12.Nv12Converter(width, height, margin_w=margin_w, margin_h=margin_h, compose=True)
  try:
    rl.begin_texture_mode(output)
    rl.clear_background(rl.Color(6, 6, 15, 255))
    rl.draw_texture_pro(source, rl.Rectangle(0, 0, visible_w, visible_h),
                        rl.Rectangle(margin_w // 2, margin_h // 2, visible_w, visible_h), rl.Vector2(0, 0), 0, rl.WHITE)
    rl.end_texture_mode()
    rgba = read_frame(width * height * 4, [(output.id, width, height, 0)])
    expected = gpu_nv12.reference_nv12(rgba, width, height)
    regions = converter.convert(source)
    assert read_frame(width * height * 3 // 2, regions, asynchronous) == expected
  finally:
    converter.close()
    rl.unload_render_texture(output)
    rl.rl_unload_texture(texture_id)


def test_default_conversion_keeps_raw_texture_row_order(gpu):
  rl = gpu
  width, height = 32, 18
  pixels = np.random.default_rng(7).integers(0, 256, (height, width, 4), dtype=np.uint8)
  pixels[..., 3] = 255
  buffer = rl.ffi.from_buffer(pixels)
  texture_id = rl.rl_load_texture(rl.ffi.cast("void *", buffer), width, height, 7, 1)
  source = rl.Texture(texture_id, width, height, 1, 7)
  converter = gpu_nv12.Nv12Converter(width, height)
  try:
    regions = converter.convert(source)
    assert read_frame(width * height * 3 // 2, regions) == gpu_nv12.reference_nv12(pixels.tobytes(), width, height)
    with pytest.raises(ValueError, match="source dimensions"):
      converter.convert(rl.Texture(texture_id, width - 1, height, 1, 7))
  finally:
    converter.close()
    rl.rl_unload_texture(texture_id)
