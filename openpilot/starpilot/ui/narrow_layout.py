"""Default widget arrangement for a narrow road: the comma half beside a phone screen."""
from __future__ import annotations

from collections.abc import Callable

MARGIN = 30
GAP = 15
SPEED_ROW = 345  # below the 300 px current speed


def arrange_narrow(placements: dict, size: Callable[[str, dict], tuple[float, float]], width: float, height: float,
                   boxed: Callable[[str], dict | None] = lambda _key: None) -> dict:
  """Rearrange default placements in place so the main widgets fit a road about half as wide as usual."""
  def place(key, x, y):
    if key not in placements:
      return None
    placed = placements[key]
    w, h = size(key, placed)
    placed['x'] = max(MARGIN, min(round(x), round(width - MARGIN - w)))
    placed['y'] = max(MARGIN, min(round(y), round(height - MARGIN - h)))
    return placed['x'], placed['y'], w, h

  def resize(key, w):
    box = boxed(key)
    if box is None or key not in placements:
      return
    w = max(box['minWidth'], min(box['maxWidth'], round(w)))
    h = max(box['minHeight'], min(box['maxHeight'], round(w / box['aspect'])))
    placements[key].update(width=w, height=h)

  for key, placed in placements.items():
    w, h = size(key, placed)
    placed['x'] = max(MARGIN, min(round(placed['x']), round(width - MARGIN - w)))
    placed['y'] = max(MARGIN, min(round(placed['y']), round(height - MARGIN - h)))

  place('current_speed', MARGIN, MARGIN)
  if 'steering_wheel' in placements:
    place('steering_wheel', width - MARGIN - size('steering_wheel', placements['steering_wheel'])[0], 75)
  cruise = place('cruise_limits', MARGIN, SPEED_ROW)
  if cruise is not None:
    cx, cy, cw, _ = cruise
    place('speed_limit_actions', cx + 24, cy + 250)
    place('rear_fog', cx + 77, cy + 219)
    right = cx + cw + GAP
  else:
    right = MARGIN

  resize('nav_card', width - MARGIN - right)
  card = None
  if 'nav_card' in placements:
    card = place('nav_card', width - MARGIN - size('nav_card', placements['nav_card'])[0], SPEED_ROW)
  if 'nav_map' in placements and card is not None:
    nx, ny, nw, nh = card
    resize('nav_map', max(nw, 280))
    map_w, map_h = size('nav_map', placements['nav_map'])
    place('nav_map', nx + nw - map_w, ny + nh + GAP)

  if 'cem_gauge' in placements and 'speed_limit_actions' in placements:
    ax, ay, aw, _ = place('speed_limit_actions', placements['speed_limit_actions']['x'], placements['speed_limit_actions']['y'])
    place('cem_gauge', ax + aw + GAP, ay - 35)

  for key in ('pip_left', 'pip_right'):
    resize(key, (width - 2 * MARGIN - GAP) / 2)
  if 'pip_left' in placements:
    w, h = size('pip_left', placements['pip_left'])
    place('pip_left', MARGIN, height - MARGIN - h)
  if 'pip_right' in placements:
    w, h = size('pip_right', placements['pip_right'])
    place('pip_right', width - MARGIN - w, height - MARGIN - h)

  if 'uniden_alerts' in placements:
    w, h = size('uniden_alerts', placements['uniden_alerts'])
    place('uniden_alerts', width - MARGIN - w, height - MARGIN - 110 - h)
  return placements
