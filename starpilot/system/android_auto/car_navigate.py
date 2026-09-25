"""Starting a route on the car view.

CarNavigateCard sits on the car's home screen: pick Home or Work, press Start, and the
drive opens in the chosen car screen layout. "Other destination" opens
CarNavigateScreen, which fills the car screen: search, favorites and recent
destinations on the left, the route preview on the right. Starting a route (or the
Back button) returns to where the driver was.

Onroad a destination can only be set below NAV_UNLOCK_MPH, measured by the car's own
wheel speed; the Navigate screen closes by itself if the car goes faster. A Desktop
Head Unit session (car_screen.DHU_ENV, set by tools/android_auto/dhu_device.py) pins the speed
below the limit, since a comma on a desk has no wheel speed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pyray as rl

from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

NAV_UNLOCK_MPH = 10.0
MPH_TO_MS = 0.44704

PAD = 28.0
TOP_BAR = 124.0
ROUND_BUTTON = 84.0
SEARCH_HEIGHT = 96.0
SECTION_HEIGHT = 62.0
ROW_HEIGHT = 108.0
ROW_GAP = 10.0
ICON = 56.0
CHIP_HEIGHT = 60.0
DRAG_SLOP = 18.0

SCREEN_BG = rl.Color(6, 6, 15, 255)
TEXT = rl.Color(240, 244, 250, 255)
SUBTEXT = rl.Color(160, 170, 186, 255)
ACCENT = rl.Color(64, 150, 255, 255)
START = rl.Color(52, 199, 120, 255)
DANGER = rl.Color(236, 96, 110, 255)
WARNING = rl.Color(240, 180, 80, 255)
CARD_BG = rl.Color(16, 20, 32, 255)
CARD_BORDER = rl.Color(255, 255, 255, 26)
TILE_BG = rl.Color(24, 30, 46, 255)
TILE_PRESSED = rl.Color(34, 44, 66, 255)
ROUND_BG = rl.Color(12, 15, 24, 170)  # the quick menu's round button


def fit_text(font, text: str, size: int, width: float) -> str:
  """Shorten text with an ellipsis to fit width."""
  if width <= 0:
    return ""
  if measure_text_cached(font, text, size).x <= width:
    return text
  while text and measure_text_cached(font, text + "…", size).x > width:
    text = text[:-1]
  return text.rstrip(" ,") + "…"


def draw_chevron(x: float, y: float, color, *, left: bool = False, size: float = 16.0, thickness: float = 5.0) -> None:
  tip = x - size * 0.75 if left else x + size * 0.75
  rl.draw_line_ex(rl.Vector2(x, y - size), rl.Vector2(tip, y), thickness, color)
  rl.draw_line_ex(rl.Vector2(tip, y), rl.Vector2(x, y + size), thickness, color)


def speed_allows_navigation(started: bool, speed_ms: float | None) -> bool:
  """Destinations can be set offroad, or onroad below NAV_UNLOCK_MPH of wheel speed.
  An unknown speed onroad (no recent carState) counts as moving."""
  if not started:
    return True
  return speed_ms is not None and abs(speed_ms) < NAV_UNLOCK_MPH * MPH_TO_MS


def locked_text() -> str:
  return tr("Slow below {} mph to set a destination").format(int(NAV_UNLOCK_MPH))


@dataclass
class ListRow:
  target: str  # the Navigation page's target id: "result:0", "favorite:<id>", "recent:2"
  kind: str  # "result", "favorite" or "recent"
  title: str
  subtitle: str = ""
  badge: str = ""
  selected: bool = False


def _place_detail(place: dict, title: str) -> str:
  detail = str(place.get("place_name") or place.get("address") or "").strip()
  return "" if detail.casefold() == title.casefold() else detail


class CarNavigateScreen(Widget):
  """Full-screen destination picker for the car.

  The settings Navigation page (StarPilotNavigationLayout) does the work: search,
  route previews, favorites and starting the route. This screen only draws it for a
  car screen and turns taps into the page's target ids.
  """

  def __init__(self, on_started: Callable[[], None], on_close: Callable[[], None], on_offline_maps: Callable[[], None]):
    super().__init__()
    from openpilot.selfdrive.ui.layouts.settings.starpilot.navigation import StarPilotNavigationLayout
    self._on_close = on_close
    self._on_offline_maps = on_offline_maps
    self.page = StarPilotNavigationLayout(on_started=on_started)
    self.scroll = 0.0
    self._content_height = 0.0
    self._list_rect = rl.Rectangle(0, 0, 0, 0)
    self._targets: list[tuple[str, rl.Rectangle, bool]] = []  # (target, rect, in the scrolling list)
    self._pressed: str | None = None
    self._press_y = 0.0
    self._last_y = 0.0
    self._dragging = False
    self._last_query = ""
    self._fonts: dict = {}

  def show_event(self):
    super().show_event()
    self.page.show_event()
    self.scroll = 0.0

  def hide_event(self):
    super().hide_event()
    self.page.hide_event()

  # ── the page's state, shaped for the car ──────────────────────────────────

  def list_rows(self) -> list[tuple[str, list[ListRow]]]:
    """(section title, rows) for the scrolling list."""
    from openpilot.starpilot.navigation.destination_store import ordered_favorite_destinations
    page = self.page
    draft = page._draft_destination
    sections = []
    if page._search_results:
      rows = [ListRow(f"result:{i}", "result", r.name, r.subtitle,
                      selected=r.has_coordinates and page._same_destination(draft, r.to_destination()))
              for i, r in enumerate(page._search_results)]
      sections.append((tr("Results"), rows))
    favorites = ordered_favorite_destinations(page._favorites)
    if favorites:
      rows = []
      for favorite in favorites:
        title = str(favorite.get("name") or tr("Saved place"))
        badge = tr("Home") if favorite.get("is_home") else tr("Work") if favorite.get("is_work") else ""
        rows.append(ListRow(f"favorite:{favorite.get('id')}", "favorite", title, _place_detail(favorite, title), badge,
                            page._same_destination(draft, favorite)))
      sections.append((tr("Favorites"), rows))
    if page._recent_destinations:
      rows = []
      for index, recent in enumerate(page._recent_destinations):
        title = str(recent.get("name") or recent.get("place_name") or tr("Recent place"))
        rows.append(ListRow(f"recent:{index}", "recent", title, _place_detail(recent, title),
                            selected=page._same_destination(draft, recent)))
      sections.append((tr("Recent"), rows))
    return sections

  def notice(self) -> tuple[str, str, rl.Color] | None:
    """(title, body, colour) of a status card above the list, if any."""
    page = self.page
    if page._search_loading:
      return tr("Searching…"), page._query, SUBTEXT
    if page._search_error:
      return tr("Search unavailable"), page._search_error, WARNING
    if not self.list_rows():
      if page._query:
        return tr("No matches"), tr("Try a different place or address."), SUBTEXT
      return tr("No places yet"), tr("Search for a place or address to begin."), SUBTEXT
    return None

  def route_chips(self) -> list[tuple[str, str, bool]]:
    """(target, label, selected) per previewed route, when there's a choice."""
    page = self.page
    if len(page._preview_routes) < 2:
      return []
    chips = []
    for index, route in enumerate(page._preview_routes):
      name = tr("Fastest") if index == 0 else tr("Route {}").format(index + 1)
      chips.append((f"route:{index}", f"{name} · {page._duration_text(route.total_duration)}", index == page._preview_route_index))
    return chips

  def favorite_chips(self) -> list[tuple[str, str, bool]]:
    page = self.page
    favorite = page._selected_favorite or page._favorite_for_destination(page._draft_destination)
    return [
      ("action:favorite", tr("Saved") if favorite else tr("Save"), favorite is not None),
      ("action:home", tr("Home"), bool(favorite and favorite.get("is_home"))),
      ("action:work", tr("Work"), bool(favorite and favorite.get("is_work"))),
    ]

  def route_summary(self) -> tuple[str, rl.Color]:
    from openpilot.selfdrive.ui.onroad.starpilot.navigation_card import _format_distance
    from openpilot.selfdrive.ui.ui_state import ui_state
    page = self.page
    if page._routes_loading:
      return tr("Finding routes…"), SUBTEXT
    if page._routes_error:
      return page._routes_error, WARNING
    if not page._routing_available():
      return tr("Add a Mapbox secret key in The Galaxy to navigate"), WARNING
    if page._preview_routes:
      route = page._preview_routes[page._preview_route_index]
      return f"{page._duration_text(route.total_duration)} · {_format_distance(route.total_distance, ui_state.is_metric)}", TEXT
    return tr("Route preview needs a GPS fix"), SUBTEXT

  def activate(self, target: str) -> None:
    if target == "back":
      self._on_close()
    elif target == "offline_maps":
      self._on_offline_maps()
    else:
      self.page._activate_navigation_target(target)

  # ── input ─────────────────────────────────────────────────────────────────

  def _target_at(self, x: float, y: float) -> str | None:
    point = rl.Vector2(x, y)
    for target, area, scrolled in reversed(self._targets):
      if scrolled and not rl.check_collision_point_rec(point, self._list_rect):
        continue
      if rl.check_collision_point_rec(point, area):
        return target
    return None

  def _max_scroll(self) -> float:
    return max(0.0, self._content_height - self._list_rect.height)

  def _handle_mouse_press(self, mouse_pos) -> None:
    self._pressed = self._target_at(mouse_pos.x, mouse_pos.y)
    self._press_y = self._last_y = mouse_pos.y
    self._dragging = False

  def _handle_mouse_event(self, mouse_event) -> None:
    if not mouse_event.left_down or mouse_event.left_pressed:
      return
    y = mouse_event.pos.y
    in_list = rl.check_collision_point_rec(rl.Vector2(mouse_event.pos.x, self._press_y), self._list_rect)
    if in_list and (self._dragging or abs(y - self._press_y) > DRAG_SLOP):
      self._dragging = True
      self._pressed = None
      self.scroll = min(self._max_scroll(), max(0.0, self.scroll - (y - self._last_y)))
    self._last_y = y

  def _handle_mouse_release(self, mouse_pos) -> None:
    pressed, self._pressed = self._pressed, None
    if not self._dragging and pressed is not None and self._target_at(mouse_pos.x, mouse_pos.y) == pressed:
      self.activate(pressed)
    self._dragging = False

  # ── drawing ───────────────────────────────────────────────────────────────

  def _font(self, weight: FontWeight):
    if weight not in self._fonts:
      self._fonts[weight] = gui_app.font(weight)
    return self._fonts[weight]

  def _target(self, target: str, area: rl.Rectangle, scrolled: bool = False) -> bool:
    """Register a tap target; returns whether it's being pressed."""
    self._targets.append((target, area, scrolled))
    return self._pressed == target

  def _render(self, rect: rl.Rectangle) -> None:
    self.page._update_state()  # consume search and route results; the page itself isn't rendered
    if self.page._query != self._last_query:
      self._last_query, self.scroll = self.page._query, 0.0
    self._targets = []
    rl.draw_rectangle_rec(rect, SCREEN_BG)
    list_w = min(860.0, max(640.0, rect.width * 0.44))
    self._draw_top_bar(rect)
    body_y = rect.y + TOP_BAR
    body_h = rect.y + rect.height - PAD - body_y
    self._draw_list_column(rl.Rectangle(rect.x + PAD, body_y, list_w, body_h))
    map_x = rect.x + PAD + list_w + PAD
    self._draw_map_panel(rl.Rectangle(map_x, body_y, rect.x + rect.width - PAD - map_x, body_h))

  def _draw_top_bar(self, rect: rl.Rectangle) -> None:
    bold, medium = self._font(FontWeight.BOLD), self._font(FontWeight.MEDIUM)
    back = rl.Rectangle(rect.x + PAD, rect.y + (TOP_BAR - ROUND_BUTTON) / 2, ROUND_BUTTON, ROUND_BUTTON)
    pressed = self._target("back", back)
    center = rl.Vector2(back.x + ROUND_BUTTON / 2, back.y + ROUND_BUTTON / 2)
    rl.draw_circle_v(center, ROUND_BUTTON / 2, TILE_PRESSED if pressed else ROUND_BG)
    rl.draw_circle_lines_v(center, ROUND_BUTTON / 2, CARD_BORDER)
    draw_chevron(center.x + 5, center.y, TEXT, left=True)
    title_x = back.x + ROUND_BUTTON + 26
    rl.draw_text_ex(bold, tr("Navigate"), rl.Vector2(title_x, rect.y + (TOP_BAR - 52) / 2), 52, 0, TEXT)

    # Offline maps live in Settings; this is the car's way there.
    label = tr("Offline maps")
    width = measure_text_cached(medium, label, 28).x + 100
    button = rl.Rectangle(rect.x + rect.width - PAD - width, rect.y + (TOP_BAR - 72) / 2, width, 72)
    pressed = self._target("offline_maps", button)
    rl.draw_rectangle_rounded(button, 0.5, 10, TILE_PRESSED if pressed else TILE_BG)
    rl.draw_rectangle_rounded_lines_ex(button, 0.5, 10, 2, CARD_BORDER)
    arrow = rl.Vector2(button.x + 38, button.y + 36)  # download glyph: an arrow into a tray
    rl.draw_line_ex(rl.Vector2(arrow.x, arrow.y - 16), rl.Vector2(arrow.x, arrow.y + 6), 4, SUBTEXT)
    rl.draw_line_ex(rl.Vector2(arrow.x - 9, arrow.y - 3), rl.Vector2(arrow.x, arrow.y + 7), 4, SUBTEXT)
    rl.draw_line_ex(rl.Vector2(arrow.x + 9, arrow.y - 3), rl.Vector2(arrow.x, arrow.y + 7), 4, SUBTEXT)
    rl.draw_line_ex(rl.Vector2(arrow.x - 14, arrow.y + 16), rl.Vector2(arrow.x + 14, arrow.y + 16), 4, SUBTEXT)
    rl.draw_text_ex(medium, label, rl.Vector2(button.x + 66, button.y + (72 - 28) / 2), 28, 0, TEXT)

  def _draw_list_column(self, column: rl.Rectangle) -> None:
    bold, medium = self._font(FontWeight.BOLD), self._font(FontWeight.MEDIUM)
    search = rl.Rectangle(column.x, column.y, column.width, SEARCH_HEIGHT)
    pressed = self._target("action:search", search)
    rl.draw_rectangle_rounded(search, 0.5, 12, TILE_PRESSED if pressed else TILE_BG)
    rl.draw_rectangle_rounded_lines_ex(search, 0.5, 12, 2, CARD_BORDER)
    glass = rl.Vector2(search.x + 50, search.y + SEARCH_HEIGHT / 2 - 4)
    rl.draw_ring(glass, 11, 16, 0, 360, 24, SUBTEXT)
    rl.draw_line_ex(rl.Vector2(glass.x + 11, glass.y + 11), rl.Vector2(glass.x + 22, glass.y + 22), 5, SUBTEXT)
    query = self.page._query
    text = fit_text(medium, query or tr("Search for a place or address"), 34, search.width - 120)
    rl.draw_text_ex(medium, text, rl.Vector2(search.x + 92, search.y + (SEARCH_HEIGHT - 34) / 2), 34, 0, TEXT if query else SUBTEXT)

    self._list_rect = rl.Rectangle(column.x, search.y + SEARCH_HEIGHT + 14, column.width, column.y + column.height - search.y - SEARCH_HEIGHT - 14)
    view = self._list_rect
    rl.begin_scissor_mode(int(view.x), int(view.y), int(view.width), int(view.height))
    y = view.y - self.scroll
    notice = self.notice()
    if notice is not None:
      title, body, color = notice
      card = rl.Rectangle(view.x, y + 8, view.width, 120)
      rl.draw_rectangle_rounded(card, 0.2, 10, CARD_BG)
      rl.draw_rectangle_rounded_lines_ex(card, 0.2, 10, 2, rl.Color(color.r, color.g, color.b, 90))
      rl.draw_text_ex(bold, fit_text(bold, title, 32, card.width - 56), rl.Vector2(card.x + 28, card.y + 22), 32, 0, color)
      rl.draw_text_ex(medium, fit_text(medium, body, 26, card.width - 56), rl.Vector2(card.x + 28, card.y + 70), 26, 0, SUBTEXT)
      y += 140
    for title, rows in self.list_rows():
      rl.draw_text_ex(bold, title.upper(), rl.Vector2(view.x + 8, y + (SECTION_HEIGHT - 26) / 2 + 6), 26, 1.5, SUBTEXT)
      y += SECTION_HEIGHT
      for row in rows:
        self._draw_row(row, rl.Rectangle(view.x, y, view.width, ROW_HEIGHT))
        y += ROW_HEIGHT + ROW_GAP
    rl.end_scissor_mode()
    self._content_height = y + self.scroll - view.y + 12
    self.scroll = min(self.scroll, self._max_scroll())

    if self._max_scroll() > 0:
      # A thin bar shows there's more below.
      track = view.height - 16
      bar_h = max(60.0, track * view.height / self._content_height)
      bar_y = view.y + 8 + (track - bar_h) * self.scroll / self._max_scroll()
      rl.draw_rectangle_rounded(rl.Rectangle(view.x + view.width - 6, bar_y, 4, bar_h), 1.0, 4, rl.Color(255, 255, 255, 60))

  def _draw_row(self, row: ListRow, area: rl.Rectangle) -> None:
    bold, medium = self._font(FontWeight.BOLD), self._font(FontWeight.MEDIUM)
    pressed = self._target(row.target, area, scrolled=True)
    rl.draw_rectangle_rounded(area, 0.22, 10, TILE_PRESSED if pressed else TILE_BG)
    rl.draw_rectangle_rounded_lines_ex(area, 0.22, 10, 4 if row.selected else 2, ACCENT if row.selected else CARD_BORDER)
    self._draw_icon(row.kind, rl.Vector2(area.x + 24 + ICON / 2, area.y + area.height / 2), ACCENT if row.selected else SUBTEXT)

    text_x = area.x + 24 + ICON + 22
    right = area.x + area.width - 24
    if row.badge:
      badge_w = measure_text_cached(medium, row.badge, 24).x + 32
      badge = rl.Rectangle(right - badge_w, area.y + (area.height - 44) / 2, badge_w, 44)
      rl.draw_rectangle_rounded(badge, 0.5, 10, rl.Color(ACCENT.r, ACCENT.g, ACCENT.b, 40))
      rl.draw_text_ex(medium, row.badge, rl.Vector2(badge.x + 16, badge.y + 10), 24, 0, ACCENT)
      right = badge.x - 16
    width = right - text_x
    if row.subtitle:
      rl.draw_text_ex(bold, fit_text(bold, row.title, 34, width), rl.Vector2(text_x, area.y + 18), 34, 0, TEXT)
      rl.draw_text_ex(medium, fit_text(medium, row.subtitle, 26, width), rl.Vector2(text_x, area.y + 62), 26, 0, SUBTEXT)
    else:
      rl.draw_text_ex(bold, fit_text(bold, row.title, 34, width), rl.Vector2(text_x, area.y + (area.height - 34) / 2), 34, 0, TEXT)

  @staticmethod
  def _draw_icon(kind: str, center: rl.Vector2, color) -> None:
    rl.draw_circle_v(center, ICON / 2, rl.Color(255, 255, 255, 14))
    if kind == "recent":  # clock
      rl.draw_ring(center, 13, 17, 0, 360, 24, color)
      rl.draw_line_ex(center, rl.Vector2(center.x, center.y - 9), 4, color)
      rl.draw_line_ex(center, rl.Vector2(center.x + 7, center.y + 3), 4, color)
    elif kind == "favorite":  # bookmark
      rl.draw_rectangle_rec(rl.Rectangle(center.x - 11, center.y - 15, 22, 22), color)
      rl.draw_triangle(rl.Vector2(center.x - 11, center.y + 7), rl.Vector2(center.x - 11, center.y + 16), rl.Vector2(center.x, center.y + 7), color)
      rl.draw_triangle(rl.Vector2(center.x + 11, center.y + 7), rl.Vector2(center.x, center.y + 7), rl.Vector2(center.x + 11, center.y + 16), color)
    else:  # map pin
      rl.draw_circle_v(rl.Vector2(center.x, center.y - 5), 12, color)
      rl.draw_triangle(rl.Vector2(center.x - 10, center.y + 1), rl.Vector2(center.x, center.y + 17), rl.Vector2(center.x + 10, center.y + 1), color)
      rl.draw_circle_v(rl.Vector2(center.x, center.y - 5), 5, CARD_BG)

  def _chip(self, target: str, label: str, selected: bool, x: float, y: float) -> float:
    medium = self._font(FontWeight.MEDIUM)
    width = measure_text_cached(medium, label, 26).x + 44
    area = rl.Rectangle(x, y, width, CHIP_HEIGHT)
    pressed = self._target(target, area)
    fill = rl.Color(ACCENT.r, ACCENT.g, ACCENT.b, 60) if selected else TILE_PRESSED if pressed else TILE_BG
    rl.draw_rectangle_rounded(area, 0.5, 10, fill)
    rl.draw_rectangle_rounded_lines_ex(area, 0.5, 10, 2, ACCENT if selected else CARD_BORDER)
    rl.draw_text_ex(medium, label, rl.Vector2(x + 22, y + (CHIP_HEIGHT - 26) / 2), 26, 0, TEXT if selected else SUBTEXT)
    return width

  def _big_button(self, target: str, label: str, color, area: rl.Rectangle, enabled: bool = True) -> None:
    bold = self._font(FontWeight.BOLD)
    pressed = enabled and self._target(target, area)
    alpha = 60 if not enabled else 200 if pressed else 255
    rl.draw_rectangle_rounded(area, 0.35, 10, rl.Color(color.r, color.g, color.b, alpha))
    width = measure_text_cached(bold, label, 40).x
    rl.draw_text_ex(bold, label, rl.Vector2(area.x + (area.width - width) / 2, area.y + (area.height - 40) / 2), 40, 0,
                    rl.Color(8, 20, 14, 255) if enabled else SUBTEXT)

  def _draw_map_panel(self, panel: rl.Rectangle) -> None:
    bold, medium = self._font(FontWeight.BOLD), self._font(FontWeight.MEDIUM)
    page = self.page
    draft = page._draft_destination
    route_chips = self.route_chips() if draft is not None else []
    sheet_h = 150.0 if draft is None else 250.0 + (CHIP_HEIGHT + 16 if route_chips else 0)
    map_rect = rl.Rectangle(panel.x, panel.y, panel.width, panel.height - sheet_h - 16)
    page._map.render(map_rect)
    rl.draw_rectangle_rounded_lines_ex(map_rect, 0.03, 10, 2, CARD_BORDER)

    sheet = rl.Rectangle(panel.x, panel.y + panel.height - sheet_h, panel.width, sheet_h)
    rl.draw_rectangle_rounded(sheet, 0.12, 10, CARD_BG)
    rl.draw_rectangle_rounded_lines_ex(sheet, 0.12, 10, 2, CARD_BORDER)
    inner_x, inner_w = sheet.x + 30, sheet.width - 60

    if draft is None:
      active = page._active_destination
      if active is not None:
        button = rl.Rectangle(sheet.x + sheet.width - 30 - 240, sheet.y + (sheet_h - 96) / 2, 240, 96)
        self._big_button("action:cancel", tr("End route"), DANGER, button)
        name = str(active.get("name") or active.get("place_name") or "")
        rl.draw_text_ex(medium, tr("Navigating to"), rl.Vector2(inner_x, sheet.y + 30), 26, 0, SUBTEXT)
        rl.draw_text_ex(bold, fit_text(bold, name, 38, button.x - inner_x - 24), rl.Vector2(inner_x, sheet.y + 68), 38, 0, TEXT)
      else:
        rl.draw_text_ex(bold, tr("Where to?"), rl.Vector2(inner_x, sheet.y + 32), 38, 0, TEXT)
        rl.draw_text_ex(medium, fit_text(medium, tr("Pick a place on the left to preview the route"), 28, inner_w),
                        rl.Vector2(inner_x, sheet.y + 84), 28, 0, SUBTEXT)
      return

    start = rl.Rectangle(sheet.x + sheet.width - 30 - 240, sheet.y + 30, 240, 110)
    can_start = page._routing_available()
    self._big_button("action:start", tr("Start"), START, start, enabled=can_start)
    text_w = start.x - inner_x - 24
    name = str(draft.get("name") or draft.get("place_name") or tr("Destination"))
    rl.draw_text_ex(bold, fit_text(bold, name, 40, text_w), rl.Vector2(inner_x, sheet.y + 32), 40, 0, TEXT)
    summary, color = self.route_summary()
    rl.draw_text_ex(medium, fit_text(medium, summary, 30, text_w), rl.Vector2(inner_x, sheet.y + 88), 30, 0, color)

    y = sheet.y + 164
    for chips in (route_chips, self.favorite_chips()):
      if not chips:
        continue
      x = inner_x
      for target, label, selected in chips:
        x += self._chip(target, label, selected, x, y) + 14
      y += CHIP_HEIGHT + 16


class CarNavigateCard(Widget):
  """The car home screen's Navigate card: Home / Work, Start, and Other destination.

  OnroadControls feeds it state (favorites, lock, active route) and handles the actions.
  """

  PAD = 26.0
  HEADER = 112.0
  TILE_HEIGHT = 230.0
  START_HEIGHT = 110.0
  OTHER_HEIGHT = 104.0
  GAP = 22.0

  def __init__(self, *, start: Callable[[dict], None], open_other: Callable[[], None], end_route: Callable[[], None]):
    super().__init__()
    self._start, self._open_other, self._end_route = start, open_other, end_route
    self.home: dict | None = None
    self.work: dict | None = None
    self.selected: str | None = None  # "home" or "work"
    self.locked_text = ""
    self.routing_ok = True
    self.destination_name = ""  # of the active route, if any
    self._pressed: str | None = None
    self._fonts: dict = {}

  def show_event(self):
    super().show_event()
    self.selected = None

  # ── state ─────────────────────────────────────────────────────────────────

  def set_favorites(self, favorites: list[dict]) -> None:
    self.home = next((f for f in favorites if f.get("is_home")), None)
    self.work = next((f for f in favorites if f.get("is_work")), None)
    if self.selected and self.favorite(self.selected) is None:
      self.selected = None

  def favorite(self, key: str) -> dict | None:
    return self.home if key == "home" else self.work if key == "work" else None

  @property
  def can_set(self) -> bool:
    return self.routing_ok and not self.locked_text

  def allowed(self, key: str) -> bool:
    if key == "end":
      return bool(self.destination_name)
    if key == "start":
      return self.can_set and self.favorite(self.selected or "") is not None
    if key == "other":
      return self.can_set
    return self.can_set and self.favorite(key) is not None

  def status_text(self) -> str:
    if not self.routing_ok:
      return tr("Add a Mapbox secret key in The Galaxy to navigate")
    if self.locked_text:
      return self.locked_text
    if self.destination_name:
      return tr("Navigating to {}").format(self.destination_name)
    return tr("Pick Home or Work, then Start")

  # ── geometry and input ────────────────────────────────────────────────────

  def layout(self, rect: rl.Rectangle) -> dict[str, rl.Rectangle]:
    pad, gap = self.PAD, self.GAP
    inner_w = rect.width - 2 * pad
    y = rect.y + self.HEADER
    tile_w = (inner_w - gap) / 2
    rects = {
      "end": rl.Rectangle(rect.x + rect.width - pad - 200, rect.y + 24, 200, 64),
      "home": rl.Rectangle(rect.x + pad, y, tile_w, self.TILE_HEIGHT),
      "work": rl.Rectangle(rect.x + pad + tile_w + gap, y, tile_w, self.TILE_HEIGHT),
    }
    y += self.TILE_HEIGHT + gap
    rects["start"] = rl.Rectangle(rect.x + pad, y, inner_w, self.START_HEIGHT)
    y += self.START_HEIGHT + gap
    rects["other"] = rl.Rectangle(rect.x + pad, y, inner_w, self.OTHER_HEIGHT)
    return rects

  def key_at(self, x: float, y: float) -> str | None:
    for key, area in self.layout(self._rect).items():
      if (key != "end" or self.destination_name) and rl.check_collision_point_rec(rl.Vector2(x, y), area):
        return key
    return None

  def _handle_mouse_press(self, mouse_pos) -> None:
    self._pressed = self.key_at(mouse_pos.x, mouse_pos.y)

  def _handle_mouse_release(self, mouse_pos) -> None:
    pressed, self._pressed = self._pressed, None
    key = self.key_at(mouse_pos.x, mouse_pos.y)
    if key is not None and key == pressed:
      self.activate(key)

  def activate(self, key: str) -> None:
    if not self.allowed(key):
      return
    if key in ("home", "work"):
      self.selected = None if self.selected == key else key
    elif key == "start":
      favorite = self.favorite(self.selected or "")
      self.selected = None
      if favorite is not None:
        self._start(favorite)
    elif key == "other":
      self._open_other()
    elif key == "end":
      self._end_route()

  # ── drawing ───────────────────────────────────────────────────────────────

  def _font(self, weight: FontWeight):
    if weight not in self._fonts:
      self._fonts[weight] = gui_app.font(weight)
    return self._fonts[weight]

  def _centered(self, font, text: str, size: int, area: rl.Rectangle, y: float, color) -> None:
    text = fit_text(font, text, size, area.width - 32)
    width = measure_text_cached(font, text, size).x
    rl.draw_text_ex(font, text, rl.Vector2(area.x + (area.width - width) / 2, y), size, 0, color)

  def _render(self, rect: rl.Rectangle) -> None:
    bold, medium = self._font(FontWeight.BOLD), self._font(FontWeight.MEDIUM)
    rl.draw_rectangle_rounded(rect, 0.04, 12, CARD_BG)
    rl.draw_rectangle_rounded_lines_ex(rect, 0.04, 12, 2, CARD_BORDER)
    rects = self.layout(rect)

    rl.draw_text_ex(bold, tr("NAVIGATE"), rl.Vector2(rect.x + self.PAD, rect.y + 22), 36, 0, TEXT)
    status_width = rect.width - 2 * self.PAD - (220 if self.destination_name else 0)
    status_color = ACCENT if self.destination_name and self.can_set else SUBTEXT
    rl.draw_text_ex(medium, fit_text(medium, self.status_text(), 26, status_width),
                    rl.Vector2(rect.x + self.PAD, rect.y + 66), 26, 0, status_color)
    if self.destination_name:
      end = rects["end"]
      rl.draw_rectangle_rounded(end, 0.5, 10, rl.Color(DANGER.r, DANGER.g, DANGER.b, 70 if self._pressed == "end" else 40))
      rl.draw_rectangle_rounded_lines_ex(end, 0.5, 10, 2, DANGER)
      self._centered(bold, tr("End route"), 28, end, end.y + 17, DANGER)

    for key, label in (("home", tr("Home")), ("work", tr("Work"))):
      area, favorite = rects[key], self.favorite(key)
      enabled, selected = self.allowed(key), self.selected == key
      rl.draw_rectangle_rounded(area, 0.12, 10, TILE_PRESSED if self._pressed == key and enabled else TILE_BG)
      rl.draw_rectangle_rounded_lines_ex(area, 0.12, 10, 5 if selected else 2, ACCENT if selected else CARD_BORDER)
      self._centered(bold, label, 54, area, area.y + 52, TEXT if enabled else SUBTEXT)
      if favorite is None:
        detail = tr("Not set: mark a favorite as {} in Other").format(label)
      else:
        detail = str(favorite.get("name") or favorite.get("place_name") or label)
      self._centered(medium, detail, 28, area, area.y + 128, ACCENT if selected else SUBTEXT)
      if selected:
        self._centered(medium, tr("Selected"), 24, area, area.y + 172, ACCENT)

    start, enabled = rects["start"], self.allowed("start")
    alpha = 255 if enabled else 60
    rl.draw_rectangle_rounded(start, 0.3, 10, rl.Color(START.r, START.g, START.b, 210 if enabled and self._pressed == "start" else alpha))
    target = self.favorite(self.selected or "")
    label = tr("Start to {}").format(target.get("name") or tr("destination")) if enabled and target else tr("Start")
    self._centered(bold, label, 44, start, start.y + (start.height - 44) / 2, rl.Color(8, 24, 14, 255) if enabled else SUBTEXT)

    other, enabled = rects["other"], self.allowed("other")
    rl.draw_rectangle_rounded(other, 0.25, 10, TILE_PRESSED if enabled and self._pressed == "other" else TILE_BG)
    rl.draw_text_ex(bold, tr("Other destination"), rl.Vector2(other.x + 30, other.y + 16), 38, 0, TEXT if enabled else SUBTEXT)
    rl.draw_text_ex(medium, tr("Search, all favorites and recent places"), rl.Vector2(other.x + 30, other.y + 62), 26, 0, SUBTEXT)
    draw_chevron(other.x + other.width - 52, other.y + other.height / 2, SUBTEXT)
