"""NumPy reference preprocessing; matrices map destination pixels to source pixels.

Packed YUV order deliberately follows compile_modeld.frames_to_tensor, rather than
the older models/README description. This is a reference, not the production warp.
"""
import numpy as np


def unpack_nv12(frame, width, height, stride=None, y_height=None):
  stride, y_height = stride or width, y_height or height
  if width <= 0 or height <= 0 or width % 2 or height % 2 or stride < width or y_height < height:
    raise ValueError("NV12 needs positive even dimensions and sufficient stride/y_height")
  flat = np.asarray(frame)
  if flat.dtype != np.uint8 or flat.size < stride * (y_height + height // 2):
    raise ValueError("NV12 buffer has the wrong dtype or is too short")
  flat = flat.reshape(-1)
  y = flat[:stride * height].reshape(height, stride)[:, :width]
  uv = flat[stride * y_height:stride * (y_height + height // 2)].reshape(height // 2, stride)[:, :width]
  return y, uv[:, 0::2], uv[:, 1::2]


def pack_yuv(y, u, v):
  h, w = y.shape
  if h % 2 or w % 2 or u.shape != (h // 2, w // 2) or v.shape != u.shape:
    raise ValueError("Invalid YUV420 plane dimensions")
  return np.stack((y[0::2, 0::2], y[1::2, 0::2], y[0::2, 1::2], y[1::2, 1::2], u, v))


def warp_plane(plane, matrix, width, height):
  matrix = np.asarray(matrix, dtype=np.float64)
  if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-12:
    raise ValueError("Expected a finite invertible 3x3 destination-to-source matrix")
  yy, xx = np.indices((height, width))
  points = matrix @ np.stack((xx.ravel(), yy.ravel(), np.ones(width * height)))
  if np.any(np.abs(points[2]) < 1e-12):
    raise ValueError("Projection crosses infinity")
  sx, sy = np.rint(points[:2] / points[2]).astype(np.int64)
  # Matches the current tinygrad warp's nearest sampling and edge clamping.
  return plane[sy.clip(0, plane.shape[0] - 1), sx.clip(0, plane.shape[1] - 1)].reshape(height, width)


def teacher_view(planes, matrix, width=512, height=256):
  uv_matrix = np.asarray(matrix) * np.array([[1, 1, .5], [1, 1, .5], [2, 2, 1]])
  return pack_yuv(warp_plane(planes[0], matrix, width, height),
                  warp_plane(planes[1], uv_matrix, width // 2, height // 2),
                  warp_plane(planes[2], uv_matrix, width // 2, height // 2))


def resize_plane(plane, width, height):
  """Bilinear reference resize using pixel centres (no source crop)."""
  sx = ((np.arange(width) + .5) * plane.shape[1] / width - .5).clip(0, plane.shape[1] - 1)
  sy = ((np.arange(height) + .5) * plane.shape[0] / height - .5).clip(0, plane.shape[0] - 1)
  x0, y0 = np.floor(sx).astype(int), np.floor(sy).astype(int)
  x1, y1 = (x0 + 1).clip(max=plane.shape[1] - 1), (y0 + 1).clip(max=plane.shape[0] - 1)
  dx, dy = sx - x0, (sy - y0)[:, None]
  row0 = plane[y0[:, None], x0] * (1 - dx) + plane[y0[:, None], x1] * dx
  row1 = plane[y1[:, None], x0] * (1 - dx) + plane[y1[:, None], x1] * dx
  return np.rint(row0 * (1 - dy) + row1 * dy).clip(0, 255).astype(np.uint8)


def full_view(planes, width=512, height=288):
  """Aspect-preserving fit, even YUV offsets, neutral padding, and valid mask."""
  if width <= 0 or height <= 0 or width % 32 or height % 32:
    raise ValueError("Output dimensions must be positive multiples of 32")
  src_h, src_w = planes[0].shape
  scale = min(width / src_w, height / src_h)
  fit_w = max(2, min(width, int(round(src_w * scale / 2)) * 2))
  fit_h = max(2, min(height, int(round(src_h * scale / 2)) * 2))
  left, top = ((width - fit_w) // 4) * 2, ((height - fit_h) // 4) * 2
  out = []
  for i, plane in enumerate(planes):
    divisor = 1 if i == 0 else 2
    dst = np.full((height // divisor, width // divisor), 16 if i == 0 else 128, dtype=np.uint8)
    x, y, w, h = left // divisor, top // divisor, fit_w // divisor, fit_h // divisor
    dst[y:y+h, x:x+w] = resize_plane(plane, w, h)
    out.append(dst)
  mask = np.zeros((height // 2, width // 2), dtype=np.uint8)
  mask[top // 2:(top + fit_h) // 2, left // 2:(left + fit_w) // 2] = 1
  # Approximate intrinsics can be transformed by this source-to-destination affine.
  affine = np.array([[fit_w / src_w, 0, left + (fit_w / src_w - 1) / 2],
                     [0, fit_h / src_h, top + (fit_h / src_h - 1) / 2], [0, 0, 1]])
  return pack_yuv(*out), mask, affine


def legacy_visibility(matrix, affine, width, height):
  """Map the old model's visible region into the new full-view packed grid."""
  yy, xx = np.indices((height // 2, width // 2))
  points = np.stack(((2*xx+.5).ravel(), (2*yy+.5).ravel(), np.ones(xx.size)))
  old = np.linalg.inv(matrix) @ np.linalg.inv(affine) @ points
  old = old[:2] / old[2]
  return ((old[0] >= 0) & (old[0] <= 511) & (old[1] >= 0) & (old[1] <= 255)).reshape(xx.shape)


def temporal_pairs(road_times, wide_times, frame_skip=4, period=.05, tolerance=.01):
  road_times, wide_times = np.asarray(road_times), np.asarray(wide_times)
  if frame_skip < 1 or road_times.ndim != 1 or road_times.shape != wide_times.shape:
    raise ValueError("Invalid timestamps or frame skip")
  if not np.isfinite(road_times).all() or not np.isfinite(wide_times).all():
    raise ValueError("Non-finite timestamps")
  if np.any(np.diff(road_times) <= 0) or np.any(np.diff(wide_times) <= 0):
    raise ValueError("Timestamps must be strictly increasing")
  pairs = []
  for now in range(frame_skip, len(road_times)):
    prev = now - frame_skip
    rt, wt = road_times[prev:now+1], wide_times[prev:now+1]
    if (np.all(np.abs(np.diff(rt) - period) <= tolerance) and
        np.all(np.abs(np.diff(wt) - period) <= tolerance) and np.all(np.abs(rt - wt) <= tolerance)):
      pairs.append((prev, now))
  return pairs
