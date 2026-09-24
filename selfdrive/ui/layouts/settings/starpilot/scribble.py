from __future__ import annotations
import math
import pyray as rl
from openpilot.system.ui.lib.vector_icon import Pen, draw_vector_icon


def _draw_custom_icon_geometry(key: str, x: float, y: float, s: float, color: rl.Color):
  """Draw icon `key` with its 60x60 canvas at (x, y), scaled by s."""
  p = Pen(x, y, s, color)

  if key == "sound":
    # Sounds & Alerts: solid speaker with three sound waves
    t = 3.8
    p.round_rect(10.0, 23.0, 9.0, 14.0, 2.0)
    p.fill([(18.0, 23.0), (29.0, 14.0), (29.0, 46.0), (18.0, 37.0)])
    p.stroke([(29.0, 14.0), (29.0, 46.0)], t)
    for r, span in ((8.5, 34.0), (16.0, 44.0), (23.5, 52.0)):
      p.arc(29.0, 30.0, r, -span, span, t)

  elif key == "steering":
    # Driving Controls: sports steering wheel
    p.arc(30.0, 30.0, 20.0, 0.0, 360.0, 5.0, caps=False)
    p.round_rect(23.0, 26.0, 14.0, 11.0, 4.0)
    p.stroke([(23.5, 30.5), (12.0, 32.5)], 4.6)
    p.stroke([(36.5, 30.5), (48.0, 32.5)], 4.6)
    p.stroke([(27.5, 36.0), (28.5, 47.0)], 3.6)
    p.stroke([(32.5, 36.0), (31.5, 47.0)], 3.6)

  elif key == "navigate":
    # Map Data: location pin over a ground ellipse
    t = 3.8
    cx, cy, r, tip = 30.0, 21.0, 10.5, 41.0
    swing = math.degrees(math.acos(r / (tip - cy)))
    a0, a1 = 90.0 + swing, 450.0 - swing
    p.arc(cx, cy, r, a0, a1, t, caps=False)
    for a in (a0, a1):
      p.stroke([(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))), (cx, tip)], t)
    p.dot(cx, cy, 3.6)
    p.stroke(Pen.ellipse(cx, 46.0, 14.0, 4.5, 0.0, 360.0)[:-1], 2.6, closed=True)

  elif key == "system":
    # System Settings: two meshing gears
    def gear(cx: float, cy: float, r_hole: float, r_body: float, r_tip: float, teeth: int, base_w: float, tip_w: float, offset: float):
      p.arc(cx, cy, (r_hole + r_body) / 2.0, 0.0, 360.0, r_body - r_hole, caps=False)
      for i in range(teeth):
        a = math.radians(offset + i * 360.0 / teeth)
        ca, sa = math.cos(a), math.sin(a)
        rb = r_body - 0.8
        p.fill([(cx + ca * rb - sa * base_w / 2, cy + sa * rb + ca * base_w / 2),
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
      p.round_rect(tx - 2.0, 12.0, 4.0, 36.0, 2.0, dim)
      p.round_rect(tx - 2.0, knob_y, 4.0, 48.0 - knob_y, 2.0)
      p.round_rect(tx - 7.0, knob_y - 3.5, 14.0, 7.0, 3.5)

  elif key == "vehicle":
    # Vehicle Settings: fastback sports car in profile
    t = 3.6
    w1, w2, wy, tire_r = 15.5, 44.5, 40.0, 6.2
    body = (Pen.quad((5.5, 34.5), (10.0, 30.0), (17.0, 28.5))
            + Pen.quad((17.0, 28.5), (21.0, 19.5), (25.5, 18.5))[1:]
            + [(37.5, 18.5)]
            + Pen.quad((37.5, 18.5), (48.5, 23.5), (54.5, 35.0))[1:])
    p.stroke(body, t)
    p.stroke([(5.5, 34.5), (5.5, wy), (w1 - tire_r - 1.5, wy)], t)
    p.stroke([(w1 + tire_r + 1.5, wy), (w2 - tire_r - 1.5, wy)], t)
    p.stroke([(w2 + tire_r + 1.5, wy), (54.5, wy), (54.5, 35.0)], t)
    p.stroke([(20.0, 27.5), (42.0, 27.5)], 2.4)
    for wx in (w1, w2):
      p.arc(wx, wy, (tire_r + 3.0) / 2.0, 0.0, 360.0, tire_r - 3.0, caps=False)
      p.dot(wx, wy, 1.3)

  elif key == "road":
    # Curvy Road: winding road with a dashed centre line
    c = ((30.0, 54.0), (14.0, 39.0), (46.0, 25.0), (30.0, 11.0))
    half = (17.0, 12.0, 8.0, 5.0)
    for side in (-1.0, 1.0):
      edge = [(px + side * w, py) for (px, py), w in zip(c, half, strict=True)]
      p.stroke(Pen.cubic(*edge), 3.8)
    for i in range(4):
      p.stroke(Pen.cubic(*c, t0=(i + 0.2) / 4, t1=(i + 0.6) / 4, n=12), 3.0)

  elif key == "aicar":
    # Driving Model: front view of a car with forward perception arcs
    t = 3.6
    sensor_y = 22.0
    for r, span in ((9.0, 85.0), (17.0, 80.0), (25.0, 75.0)):
      p.arc(30.0, sensor_y, r, -90.0 - span / 2, -90.0 + span / 2, t)
    p.dot(30.0, sensor_y, 3.0)
    p.stroke([(12.0, 47.0), (12.0, 33.0), (20.0, 23.0), (40.0, 23.0), (48.0, 33.0), (48.0, 47.0)], t, closed=True)
    p.stroke([(12.0, 34.0), (7.5, 33.0)], 2.5)
    p.stroke([(48.0, 34.0), (52.5, 33.0)], 2.5)
    p.stroke([(14.5, 38.5), (22.0, 38.5)], 3.5)
    p.stroke([(38.0, 38.5), (45.5, 38.5)], 3.5)
    p.stroke([(20.0, 43.5), (40.0, 43.5)], 2.2)
    p.round_rect(9.5, 43.5, 5.0, 7.5, 2.0)
    p.round_rect(45.5, 43.5, 5.0, 7.5, 2.0)

  elif key == "first_aid":
    # First Aid Kit
    t = 2.6
    p.stroke(Pen.ellipse(30.0, 20.0, 6.0, 4.5, 180.0, 360.0, 32), t)
    p.round_rect_outline(12.0, 20.0, 36.0, 25.0, 3.5, t)
    p.stroke([(30.0, 26.0), (30.0, 39.0)], 3.6)
    p.stroke([(23.5, 32.5), (36.5, 32.5)], 3.6)


def draw_custom_icon(key: str, x: float, y: float, s: float, color: rl.Color):
  """Draw a custom icon, caching its static vector geometry on the GPU."""
  draw_vector_icon(f"aethergrid:{key}", x, y, s, color,
                   lambda ix, iy, i_s, icolor: _draw_custom_icon_geometry(key, ix, iy, i_s, icolor))
