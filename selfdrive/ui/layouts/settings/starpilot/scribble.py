from __future__ import annotations
import math
import pyray as rl
from openpilot.system.ui.lib.application import gui_app

# Icons are authored on a 60x60 canvas and rasterised into a cached texture at
# this multiple, then box-filtered down, which gives clean anti-aliased edges.
ICON_SUPERSAMPLE = 4

_Point = tuple[float, float]


def _draw_custom_icon_geometry(key: str, x: float, y: float, s: float, color: rl.Color):
  """Draw icon `key` with its 60x60 canvas at (x, y), scaled by s.

  Every shape is drawn so that overlapping parts share one colour, which lets
  the cache render it opaque and apply translucency once when compositing.
  """

  def v(px: float, py: float) -> rl.Vector2:
    return rl.Vector2(x + px * s, y + py * s)

  def dot(px: float, py: float, r: float, col: rl.Color = color):
    rl.draw_circle_v(v(px, py), r * s, col)

  def stroke(points: list[_Point], thick: float, closed: bool = False, col: rl.Color = color):
    """Polyline with round caps and joins."""
    pts = points + [points[0]] if closed else points
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
      rl.draw_line_ex(v(x0, y0), v(x1, y1), thick * s, col)
    for px, py in points:
      dot(px, py, thick / 2.0, col)

  def arc(cx: float, cy: float, r: float, start: float, end: float, thick: float, caps: bool = True):
    """Circular arc; angles in degrees, clockwise from +x (screen space)."""
    segments = max(24, int(abs(end - start) / 3))
    rl.draw_ring(v(cx, cy), (r - thick / 2.0) * s, (r + thick / 2.0) * s, start, end, segments, color)
    if caps:
      for a in (start, end):
        dot(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)), thick / 2.0)

  def ellipse(cx: float, cy: float, a: float, b: float, start: float, end: float, n: int = 96) -> list[_Point]:
    return [(cx + a * math.cos(math.radians(start + (end - start) * i / n)),
             cy + b * math.sin(math.radians(start + (end - start) * i / n))) for i in range(n + 1)]

  def quad(p0: _Point, p1: _Point, p2: _Point, n: int = 32) -> list[_Point]:
    out = []
    for i in range(n + 1):
      t = i / n
      out.append(((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0],
                  (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]))
    return out

  def cubic(p0: _Point, p1: _Point, p2: _Point, p3: _Point, t0: float = 0.0, t1: float = 1.0, n: int = 48) -> list[_Point]:
    out = []
    for i in range(n + 1):
      t = t0 + (t1 - t0) * i / n
      a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
      out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out

  def fill(points: list[_Point]):
    """Convex polygon fill. Raylib culls clockwise triangles, so fix winding."""
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1], strict=True))
    pts = points if area < 0 else points[::-1]
    for i in range(1, len(pts) - 1):
      rl.draw_triangle(v(*pts[0]), v(*pts[i]), v(*pts[i + 1]), color)

  def round_rect(rx: float, ry: float, rw: float, rh: float, r: float, col: rl.Color = color):
    rl.draw_rectangle_rounded(rl.Rectangle(x + rx * s, y + ry * s, rw * s, rh * s), min(1.0, 2.0 * r / min(rw, rh)), 16, col)

  def round_rect_outline(rx: float, ry: float, rw: float, rh: float, r: float, thick: float):
    """Stroke centred on the rectangle edge, built from non-overlapping pieces."""
    h = thick / 2.0
    for cx, cy, a in ((rx + r, ry + r, 180), (rx + rw - r, ry + r, 270), (rx + rw - r, ry + rh - r, 0), (rx + r, ry + rh - r, 90)):
      arc(cx, cy, r, a, a + 90, thick, caps=False)
    for sx, sy, sw, sh in ((rx + r, ry - h, rw - 2 * r, thick), (rx + r, ry + rh - h, rw - 2 * r, thick),
                           (rx - h, ry + r, thick, rh - 2 * r), (rx + rw - h, ry + r, thick, rh - 2 * r)):
      rl.draw_rectangle_rec(rl.Rectangle(x + sx * s, y + sy * s, sw * s, sh * s), color)

  if key == "sound":
    # Sounds & Alerts: solid speaker with three sound waves
    t = 3.8
    round_rect(10.0, 23.0, 9.0, 14.0, 2.0)
    fill([(18.0, 23.0), (29.0, 14.0), (29.0, 46.0), (18.0, 37.0)])
    stroke([(29.0, 14.0), (29.0, 46.0)], t)
    for r, span in ((8.5, 34.0), (16.0, 44.0), (23.5, 52.0)):
      arc(29.0, 30.0, r, -span, span, t)

  elif key == "steering":
    # Driving Controls: sports steering wheel
    arc(30.0, 30.0, 20.0, 0.0, 360.0, 5.0, caps=False)
    round_rect(23.0, 26.0, 14.0, 11.0, 4.0)
    stroke([(23.5, 30.5), (12.0, 32.5)], 4.6)
    stroke([(36.5, 30.5), (48.0, 32.5)], 4.6)
    stroke([(27.5, 36.0), (28.5, 47.0)], 3.6)
    stroke([(32.5, 36.0), (31.5, 47.0)], 3.6)

  elif key == "navigate":
    # Map Data: location pin over a ground ellipse
    t = 3.8
    cx, cy, r, tip = 30.0, 21.0, 10.5, 41.0
    swing = math.degrees(math.acos(r / (tip - cy)))
    a0, a1 = 90.0 + swing, 450.0 - swing
    arc(cx, cy, r, a0, a1, t, caps=False)
    for a in (a0, a1):
      stroke([(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))), (cx, tip)], t)
    dot(cx, cy, 3.6)
    stroke(ellipse(cx, 46.0, 14.0, 4.5, 0.0, 360.0)[:-1], 2.6, closed=True)

  elif key == "system":
    # System Settings: two meshing gears
    def gear(cx: float, cy: float, r_hole: float, r_body: float, r_tip: float, teeth: int, base_w: float, tip_w: float, offset: float):
      arc(cx, cy, (r_hole + r_body) / 2.0, 0.0, 360.0, r_body - r_hole, caps=False)
      for i in range(teeth):
        a = math.radians(offset + i * 360.0 / teeth)
        ca, sa = math.cos(a), math.sin(a)
        rb = r_body - 0.8
        fill([(cx + ca * rb - sa * base_w / 2, cy + sa * rb + ca * base_w / 2),
              (cx + ca * r_tip - sa * tip_w / 2, cy + sa * r_tip + ca * tip_w / 2),
              (cx + ca * r_tip + sa * tip_w / 2, cy + sa * r_tip - ca * tip_w / 2),
              (cx + ca * rb + sa * base_w / 2, cy + sa * rb - ca * base_w / 2)])

    # The big gear leaves a gap facing the small one (45 deg) and the small
    # gear points a tooth back into it (225 deg).
    gear(23.0, 23.0, 4.8, 11.0, 15.5, 8, 6.2, 4.2, 22.5)
    gear(39.5, 39.5, 3.2, 7.4, 10.8, 6, 4.6, 3.2, 45.0)

  elif key == "display":
    # Appearance: three tuning sliders
    dim = rl.Color(color[0], color[1], color[2], 75) if isinstance(color, tuple) else rl.Color(color.r, color.g, color.b, 75)
    for tx, knob_y in ((15.0, 36.0), (30.0, 22.0), (45.0, 31.0)):
      round_rect(tx - 2.0, 12.0, 4.0, 36.0, 2.0, dim)
      round_rect(tx - 2.0, knob_y, 4.0, 48.0 - knob_y, 2.0)
      round_rect(tx - 7.0, knob_y - 3.5, 14.0, 7.0, 3.5)

  elif key == "vehicle":
    # Vehicle Settings: fastback sports car in profile
    t = 3.6
    w1, w2, wy, tire_r = 15.5, 44.5, 40.0, 6.2
    body = (quad((5.5, 34.5), (10.0, 30.0), (17.0, 28.5))
            + quad((17.0, 28.5), (21.0, 19.5), (25.5, 18.5))[1:]
            + [(37.5, 18.5)]
            + quad((37.5, 18.5), (48.5, 23.5), (54.5, 35.0))[1:])
    stroke(body, t)
    stroke([(5.5, 34.5), (5.5, wy), (w1 - tire_r - 1.5, wy)], t)
    stroke([(w1 + tire_r + 1.5, wy), (w2 - tire_r - 1.5, wy)], t)
    stroke([(w2 + tire_r + 1.5, wy), (54.5, wy), (54.5, 35.0)], t)
    stroke([(20.0, 27.5), (42.0, 27.5)], 2.4)
    for wx in (w1, w2):
      arc(wx, wy, (tire_r + 3.0) / 2.0, 0.0, 360.0, tire_r - 3.0, caps=False)
      dot(wx, wy, 1.3)

  elif key == "road":
    # Curvy Road: winding road with a dashed centre line
    c = ((30.0, 54.0), (14.0, 39.0), (46.0, 25.0), (30.0, 11.0))
    half = (17.0, 12.0, 8.0, 5.0)
    for side in (-1.0, 1.0):
      edge = [(px + side * w, py) for (px, py), w in zip(c, half, strict=True)]
      stroke(cubic(*edge), 3.8)
    for i in range(4):
      stroke(cubic(*c, t0=(i + 0.2) / 4, t1=(i + 0.6) / 4, n=12), 3.0)

  elif key == "aicar":
    # Driving Model: front view of a car with forward perception arcs
    t = 3.6
    sensor_y = 22.0
    for r, span in ((9.0, 85.0), (17.0, 80.0), (25.0, 75.0)):
      arc(30.0, sensor_y, r, -90.0 - span / 2, -90.0 + span / 2, t)
    dot(30.0, sensor_y, 3.0)
    stroke([(12.0, 47.0), (12.0, 33.0), (20.0, 23.0), (40.0, 23.0), (48.0, 33.0), (48.0, 47.0)], t, closed=True)
    stroke([(12.0, 34.0), (7.5, 33.0)], 2.5)
    stroke([(48.0, 34.0), (52.5, 33.0)], 2.5)
    stroke([(14.5, 38.5), (22.0, 38.5)], 3.5)
    stroke([(38.0, 38.5), (45.5, 38.5)], 3.5)
    stroke([(20.0, 43.5), (40.0, 43.5)], 2.2)
    round_rect(9.5, 43.5, 5.0, 7.5, 2.0)
    round_rect(45.5, 43.5, 5.0, 7.5, 2.0)

  elif key == "first_aid":
    # First Aid Kit
    t = 2.6
    stroke(ellipse(30.0, 20.0, 6.0, 4.5, 180.0, 360.0, 32), t)
    round_rect_outline(12.0, 20.0, 36.0, 25.0, 3.5, t)
    stroke([(30.0, 26.0), (30.0, 39.0)], 3.6)
    stroke([(23.5, 32.5), (36.5, 32.5)], 3.6)


def draw_custom_icon(key: str, x: float, y: float, s: float, color: rl.Color):
  """Draw a custom icon, caching its static vector geometry on the GPU."""
  cache = getattr(gui_app, "cached_render_texture", None)
  if cache is None:
    _draw_custom_icon_geometry(key, x, y, s, color)
    return

  if hasattr(color, "r"):
    r, g, b, a = color.r, color.g, color.b, color.a
  else:
    components = tuple(color)
    r, g, b = components[:3]
    a = components[3] if len(components) > 3 else 255

  # A few strokes extend beyond the authored 60x60 canvas. Padding prevents
  # those edges from being clipped by the render texture.
  padding = max(2, int(math.ceil(6.0 * s)))
  icon_size = max(1, int(round(60.0 * s)))
  width = icon_size + 2 * padding
  height = width
  # The cached geometry is opaque; alpha is applied once as a tint below, so
  # overlapping strokes never show darker seams and fades reuse one texture.
  opaque = rl.Color(r, g, b, 255)
  cache_key = f"aethergrid-icon:{key}:{round(s, 4)}:{width}:{height}:{(r, g, b)}"
  texture = cache(cache_key, width, height,
                  lambda: _draw_custom_icon_geometry(key, padding, padding, s, opaque),
                  supersample=ICON_SUPERSAMPLE)
  if texture is None:
    _draw_custom_icon_geometry(key, x, y, s, color)
    return

  # Render targets contain premultiplied alpha after drawing onto transparent
  # black. Use the matching blend mode (and a premultiplied tint). Snapping to
  # whole pixels keeps the texture 1:1 with the screen so edges stay sharp.
  rl.begin_blend_mode(rl.BlendMode.BLEND_ALPHA_PREMULTIPLY)
  try:
    rl.draw_texture_pro(
      texture,
      rl.Rectangle(0, 0, width, -height),
      rl.Rectangle(round(x) - padding, round(y) - padding, width, height),
      rl.Vector2(0, 0),
      0.0,
      rl.Color(a, a, a, a),
    )
  finally:
    rl.end_blend_mode()
