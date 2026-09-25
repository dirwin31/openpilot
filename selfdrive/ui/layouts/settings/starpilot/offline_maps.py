"""Offline Maps: both kinds of offline data in one place.

  * Map display: what the navigation map has saved. Save new areas, update or
    delete them. navtilesd does the downloading; this page only writes area
    records through OfflineMaps and reads the progress navtilesd reports.
  * Speed limit data: mapd's road data by state or country (the Map Data page,
    embedded as-is).
"""

from __future__ import annotations

import json
import math
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pyray as rl

from openpilot.selfdrive.ui.layouts.settings.starpilot.aethergrid import (
  AETHER_LIST_METRICS,
  AetherListColors,
  AetherSegmentedControl,
  DEFAULT_PANEL_STYLE,
  PanelManagerView,
  draw_action_pill,
  draw_empty_state_card,
  draw_list_group_shell,
  draw_section_header,
  draw_selection_list_row,
  with_alpha,
)
from openpilot.selfdrive.ui.layouts.settings.starpilot.navigation import MapboxSearchClient, SearchResult
from openpilot.selfdrive.ui.layouts.settings.starpilot.panel import FrameCachedParams, _SettingsPage
from openpilot.starpilot.navigation.destination_store import NavigationDestinationStore
from openpilot.starpilot.navigation.offline_maps import AREA_PRESETS, OFFLINE_MAX_BYTES, OfflineMaps, estimate_area, format_bytes
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import DialogResult
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
from openpilot.system.ui.widgets.keyboard import Keyboard

PANEL_STYLE = DEFAULT_PANEL_STYLE
METRICS = replace(AETHER_LIST_METRICS, header_height=0)

INSET = 18.0
GAP = 14.0
STORAGE_HEIGHT = 190.0
SECTION_HEIGHT = 72.0
ROW_HEIGHT = 118.0
BUTTON_HEIGHT = 84.0
EMPTY_HEIGHT = 140.0
REFRESH_SECONDS = 2.0
AREA_DETAIL = {16: "street detail", 15: "city detail", 14: "road detail", 13: "regional"}

SEGMENT_DISPLAY = 0
SEGMENT_ROAD_DATA = 1
SEGMENT_HEIGHT = 68.0
CAPTION_HEIGHT = 44.0
SEGMENT_CAPTIONS = (
  "The map on the comma and car screen. Saved areas keep it working without signal.",
  "Road data for speed limits and curve control, by state or country. Doesn't draw the map.",
)


def _road_data_layout():
  from openpilot.selfdrive.ui.layouts.settings.starpilot.maps import StarPilotMapsLayout
  return StarPilotMapsLayout()


class OfflineMapsManagerView(PanelManagerView):
  METRICS = METRICS
  PANEL_STYLE = PANEL_STYLE

  def __init__(self, controller: StarPilotOfflineMapsLayout):
    super().__init__()
    self._controller = controller

  def _draw_header(self, rect: rl.Rectangle):
    del rect

  def _measure_content_height(self, content_width: float) -> float:
    del content_width
    return INSET * 2 + sum(height for _, height, _ in self._controller.layout_rows())

  def _draw_scroll_content(self, scroll_rect: rl.Rectangle, content_width: float):
    self._controller.draw_rows(scroll_rect, content_width, self._scroll_offset, self)

  def _activate_target(self, target_id: str | None):
    if target_id:
      self._controller.activate(target_id)


class StarPilotOfflineMapsLayout(_SettingsPage):
  def __init__(self, offline: OfflineMaps | None = None, params=None, road_data_factory: Callable[[], Any] | None = None):
    super().__init__()
    self._params = params or FrameCachedParams()
    self._store = NavigationDestinationStore(self._params)
    self._offline = offline or OfflineMaps()
    self._search_client = MapboxSearchClient()
    self._keyboard: Keyboard | None = None
    self._pending: queue.Queue[tuple[str, int, Any]] = queue.Queue()
    self._generation = 0
    self._session_token = str(uuid.uuid4())

    self.summary: dict[str, Any] = {}
    self.areas: list = []
    self.selected_area_id: str | None = None
    self.chooser: dict[str, Any] | None = None  # {"latitude", "longitude", "name", "estimates"}
    self.search_results: list[SearchResult] = []
    self.search_busy = False
    self.message = ""
    self._refreshed = -1.0
    self._manager_view = OfflineMapsManagerView(self)

    self.segment = SEGMENT_DISPLAY
    self._road_data_factory = road_data_factory or _road_data_layout
    self._road_data = None  # built on first use: it subscribes to mapd
    self._segments: AetherSegmentedControl | None = None
    self._shown = False

  # ── segments ──────────────────────────────────────────────────────────────

  @property
  def road_data(self):
    if self._road_data is None:
      self._road_data = self._road_data_factory()
    return self._road_data

  def _segment_view(self, segment: int):
    return self.road_data if segment == SEGMENT_ROAD_DATA else self._manager_view

  def open_segment(self, segment: int) -> None:
    segment = SEGMENT_ROAD_DATA if segment == SEGMENT_ROAD_DATA else SEGMENT_DISPLAY
    if segment == self.segment:
      return
    if self._shown:
      self._segment_view(self.segment).hide_event()
    self.segment = segment
    if self._shown:
      self._segment_view(segment).show_event()

  def _render(self, rect: rl.Rectangle):
    if self._segments is None:
      self._segments = AetherSegmentedControl([tr("Map display"), tr("Speed limit data")], lambda: self.segment, self.open_segment,
                                              style=PANEL_STYLE, suppress_background=True)
    self._segments.render(rl.Rectangle(rect.x + INSET, rect.y, rect.width - INSET * 2, SEGMENT_HEIGHT))
    caption_y = rect.y + SEGMENT_HEIGHT + 8
    font = gui_app.font(FontWeight.NORMAL)
    rl.draw_text_ex(font, tr(SEGMENT_CAPTIONS[self.segment]), rl.Vector2(rect.x + INSET + 4, caption_y + 8), 26, 0, AetherListColors.SUBTEXT)
    top = SEGMENT_HEIGHT + 8 + CAPTION_HEIGHT
    self._segment_view(self.segment).render(rl.Rectangle(rect.x, rect.y + top, rect.width, max(1.0, rect.height - top)))

  def hide_event(self):
    self._shown = False
    if self.segment == SEGMENT_ROAD_DATA:
      self.road_data.hide_event()
    super().hide_event()

  def show_event(self):
    # The segment survives a re-show (dialogs hide and re-show the page); deep links pick one with open_segment.
    self._shown = True
    if self.segment == SEGMENT_ROAD_DATA:
      self.road_data.show_event()
    self._generation += 1
    self._session_token = str(uuid.uuid4())
    self.chooser = None
    self.search_results = []
    self.search_busy = False
    self.message = ""
    self.selected_area_id = None
    self.refresh()
    super().show_event()

  def _update_state(self):
    self._consume_pending()
    if self._refreshed < 0 or time.monotonic() - self._refreshed >= REFRESH_SECONDS:
      self.refresh()

  def refresh(self) -> None:
    self._refreshed = time.monotonic()
    self.summary = self._offline.summary()
    self.areas = self._offline.areas(include_deleted=True)
    if self.selected_area_id is not None and not any(a.id == self.selected_area_id and not a.deleted for a in self.areas):
      self.selected_area_id = None

  # ── state helpers ─────────────────────────────────────────────────────────

  @property
  def used_bytes(self) -> int:
    return int(self.summary.get("offline_bytes") or 0)

  def _progress(self, area_id: str) -> dict[str, Any]:
    return next((item for item in self.summary.get("items") or [] if item.get("id") == area_id), {})

  def connection_text(self) -> tuple[str, rl.Color]:
    if not self.summary.get("service_running"):
      return tr("Map service not running • downloads resume when it starts"), AetherListColors.WARNING
    if self.summary.get("offline"):
      return tr("No connection • the map uses what's saved"), AetherListColors.WARNING
    if self.summary.get("unmetered"):
      return tr("On Wi-Fi • downloads run now"), AetherListColors.SUCCESS
    return tr("Not on Wi-Fi • downloads wait for Wi-Fi"), AetherListColors.MUTED

  def area_title(self, area) -> str:
    return tr("Route to {}").format(area.name) if area.kind == "route" else area.name

  def area_status(self, area) -> str:
    if area.deleted:
      return tr("Removing…")
    state = self._progress(area.id)
    kind = state.get("state") or "queued"
    total, done = int(state.get("total") or 0), int(state.get("done") or 0)
    percent = f"{100 * done // total}%" if total else "0%"
    if kind == "complete":
      return tr("Saved • {} • updated {}").format(format_bytes(state.get("bytes") or 0), self._age_text(float(state.get("completed_at") or 0.0)))
    if kind == "downloading":
      return tr("Downloading {} • {} of {} tiles").format(percent, f"{done:,}", f"{total:,}")
    if kind == "waiting_wifi":
      if state.get("metered_wifi"):
        return tr("This Wi-Fi is marked metered • tap, then Download now")
      return tr("Waiting for Wi-Fi • {} done").format(percent)
    if kind == "incomplete":
      return tr("Partly saved • retrying later")
    if kind == "storage_full":
      return tr("Offline storage is full ({})").format(format_bytes(OFFLINE_MAX_BYTES))
    if kind == "no_space":
      return tr("Not enough free space on the device")
    return tr("Queued • downloads on Wi-Fi")

  @staticmethod
  def _age_text(timestamp: float) -> str:
    days = int(max(0.0, time.time() - timestamp) // 86400)  # noqa: TID251 - compared with a saved wall-clock time
    if days == 0:
      return tr("today")
    return tr("yesterday") if days == 1 else tr("{} days ago").format(days)

  def route_text(self) -> str | None:
    """The active route's offline status, or None without a route."""
    if self._store.active_destination() is None:
      return None
    route = self.summary.get("route") or {}
    total, remaining = int(route.get("total") or 0), int(route.get("remaining") or 0)
    if not total:
      return tr("Saved automatically once the route is ready")
    if remaining <= 0:
      return tr("Saved • the map works without a connection")
    percent = 100 * (total - remaining) // total
    if self.summary.get("offline"):
      return tr("Waiting for a connection • {}% saved").format(percent)
    return tr("Saving for offline • {}%").format(percent)

  def _last_position(self) -> tuple[float, float] | None:
    """(latitude, longitude) of the last GPS fix."""
    raw = self._params.get("LastGPSPosition", encoding="utf-8") or ""
    try:
      data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
      latitude, longitude = float(data["latitude"]), float(data["longitude"])
    except (TypeError, ValueError, KeyError):
      return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)) or (abs(latitude) < 1e-6 and abs(longitude) < 1e-6):
      return None
    return latitude, longitude

  def _public_key(self) -> str:
    return str(self._params.get("MapboxPublicKey", encoding="utf-8") or "").strip()

  # ── actions ───────────────────────────────────────────────────────────────

  def activate(self, target: str) -> None:
    self.message = ""
    if target == "add:here":
      position = self._last_position()
      if position is None:
        self.message = tr("No location yet. Try again once the device has GPS.")
      else:
        self.open_chooser(position[0], position[1], "", reverse=True)
    elif target == "add:destination":
      destination = self._store.active_destination()
      if destination is not None:
        name = str(destination.get("name") or destination.get("place_name") or tr("Destination"))
        self.open_chooser(float(destination["latitude"]), float(destination["longitude"]), name)
    elif target == "add:search":
      self._open_search_keyboard()
    elif target == "add:cancel":
      self.chooser = None
      self.search_results = []
    elif target.startswith("result:"):
      index = int(target.split(":", 1)[1])
      if 0 <= index < len(self.search_results):
        self._pick_result(self.search_results[index])
    elif target.startswith("preset:"):
      self.save_preset(int(target.split(":", 1)[1]))
    elif target.startswith("area:"):
      area_id = target.split(":", 1)[1]
      self.selected_area_id = None if self.selected_area_id == area_id else area_id
    elif target == "area_action:update" and self.selected_area_id:
      self._offline.request_update(self.selected_area_id)
      self.refresh()
    elif target == "area_action:now" and self.selected_area_id:
      self._offline.allow_metered(self.selected_area_id)
      self.refresh()
    elif target == "area_action:delete" and self.selected_area_id:
      self._confirm_delete(self.selected_area_id)

  def open_chooser(self, latitude: float, longitude: float, name: str, reverse: bool = False) -> None:
    self._generation += 1
    generation = self._generation
    fallback = name or f"{latitude:.3f}, {longitude:.3f}"
    self.search_results = []
    self.chooser = {"latitude": latitude, "longitude": longitude, "name": fallback, "estimates": None}
    public_key, search_client = self._public_key(), self._search_client

    def worker():
      estimates = [(radius, zoom) + estimate_area(latitude, longitude, radius, zoom) for radius, zoom in AREA_PRESETS]
      place = fallback
      if reverse and public_key:
        try:
          place = search_client.reverse(latitude, longitude, public_key) or fallback
        except Exception:
          pass
      self._pending.put(("chooser", generation, {"estimates": estimates, "name": place}))

    threading.Thread(target=worker, daemon=True, name="offline-area-estimate").start()

  def save_preset(self, index: int) -> None:
    chooser = self.chooser
    if chooser is None or not chooser.get("estimates") or not 0 <= index < len(chooser["estimates"]):
      return
    radius, zoom, _, size = chooser["estimates"][index]
    if self.used_bytes + size > OFFLINE_MAX_BYTES:
      return
    self._offline.add_area(chooser["name"], chooser["latitude"], chooser["longitude"], radius, zoom)
    self.chooser = None
    self.refresh()

  def _open_search_keyboard(self) -> None:
    if not self._public_key():
      self.message = tr("Mapbox search isn't set up. Add a Mapbox public key in The Galaxy.")
      return
    if self._keyboard is None:
      self._keyboard = Keyboard(min_text_size=3)
    keyboard = self._keyboard
    keyboard.reset(min_text_size=3)
    keyboard.set_title(tr("Save a map around…"), tr("Enter a city, place or address"))
    keyboard.set_text("")
    keyboard.set_callback(lambda result: self._search(keyboard.text) if result == DialogResult.CONFIRM else None)
    gui_app.push_widget(keyboard)

  def _search(self, query: str) -> None:
    query = query.strip()
    if len(query) < 3:
      return
    self._generation += 1
    generation, public_key, token = self._generation, self._public_key(), self._session_token
    self.chooser = None
    self.search_results = []
    self.search_busy = True
    position = self._last_position()
    proximity = (position[1], position[0]) if position else None

    def worker():
      try:
        self._pending.put(("search", generation, self._search_client.search(query, public_key, token, proximity=proximity)))
      except Exception as error:
        self._pending.put(("search", generation, error))

    threading.Thread(target=worker, daemon=True, name="offline-area-search").start()

  def _pick_result(self, result: SearchResult) -> None:
    if result.has_coordinates:
      self.open_chooser(float(result.latitude), float(result.longitude), result.name)
      return
    self._generation += 1
    generation, public_key, token = self._generation, self._public_key(), self._session_token
    self.search_busy = True

    def worker():
      try:
        self._pending.put(("resolve", generation, self._search_client.resolve(result, public_key, token)))
      except Exception as error:
        self._pending.put(("resolve", generation, error))

    threading.Thread(target=worker, daemon=True, name="offline-area-resolve").start()

  def _consume_pending(self) -> None:
    while True:
      try:
        kind, generation, payload = self._pending.get_nowait()
      except queue.Empty:
        return
      if generation != self._generation:
        continue
      if kind == "chooser" and self.chooser is not None:
        self.chooser.update(payload)
      elif kind == "search":
        self.search_busy = False
        if isinstance(payload, Exception):
          self.message = tr("Search is unavailable. Check your connection and try again.")
        else:
          self.search_results = payload
          if not payload:
            self.message = tr("No places found. Try another search.")
      elif kind == "resolve":
        self.search_busy = False
        if isinstance(payload, SearchResult) and payload.has_coordinates:
          self.open_chooser(float(payload.latitude), float(payload.longitude), payload.name)
        else:
          self.message = tr("Couldn't find that place on the map. Try another result.")

  def _confirm_delete(self, area_id: str) -> None:
    area = next((item for item in self.areas if item.id == area_id), None)
    if area is None:
      return

    def on_result(result: DialogResult):
      if result == DialogResult.CONFIRM:
        self._offline.delete_area(area_id)
        self.selected_area_id = None
        self.refresh()

    gui_app.push_widget(ConfirmDialog(tr("Delete the offline map for {}?").format(self.area_title(area)), tr("Delete"), callback=on_result))

  # ── layout ────────────────────────────────────────────────────────────────

  def add_buttons(self) -> list[tuple[str, str]]:
    buttons = [("add:here", tr("Around me"))]
    if self._store.active_destination() is not None:
      buttons.append(("add:destination", tr("Around destination")))
    buttons.append(("add:search", tr("Search a place")))
    return buttons

  def layout_rows(self) -> list[tuple[str, float, Any]]:
    """(kind, height, data) rows, shared by drawing and measuring."""
    rows: list[tuple[str, float, Any]] = [("storage", STORAGE_HEIGHT + GAP, None), ("add_header", SECTION_HEIGHT, None)]
    if self.chooser is not None:
      if self.chooser.get("estimates") is None:
        rows.append(("estimating", ROW_HEIGHT, None))
      else:
        rows += [("preset", ROW_HEIGHT, index) for index in range(len(self.chooser["estimates"]))]
      rows.append(("cancel", BUTTON_HEIGHT + GAP, None))
    else:
      rows.append(("add_buttons", BUTTON_HEIGHT + GAP, None))
      if self.search_busy:
        rows.append(("searching", ROW_HEIGHT, None))
      rows += [("result", ROW_HEIGHT, index) for index in range(len(self.search_results))]
      if self.search_results:
        rows.append(("cancel", BUTTON_HEIGHT + GAP, None))
    if self.message:
      rows.append(("message", EMPTY_HEIGHT + GAP, None))
    route = self.route_text()
    if route is not None:
      rows += [("route_header", SECTION_HEIGHT, None), ("route", ROW_HEIGHT + GAP, route)]
    rows.append(("saved_header", SECTION_HEIGHT, None))
    for area in self.areas:
      rows.append(("area", ROW_HEIGHT, area))
      if area.id == self.selected_area_id and not area.deleted:
        rows.append(("area_actions", BUTTON_HEIGHT + GAP, area))
    if not self.areas:
      rows.append(("empty", EMPTY_HEIGHT, None))
    return rows

  # ── drawing ───────────────────────────────────────────────────────────────

  def _pill(self, manager, rect: rl.Rectangle, target: str, label: str, color=None) -> None:
    hovered, pressed = manager._interactive_state(target, rect, pad_y=4)
    color = color or AetherListColors.PRIMARY
    draw_action_pill(rect, label, with_alpha(color, 54 if hovered or pressed else 24), with_alpha(color, 110), AetherListColors.HEADER,
                     font_size=26)

  def _pills(self, manager, x: float, y: float, width: float, pills: list[tuple[str, str, Any]]) -> None:
    pill_w = (width - GAP * (len(pills) - 1)) / len(pills)
    for index, (target, label, color) in enumerate(pills):
      self._pill(manager, rl.Rectangle(x + index * (pill_w + GAP), y, pill_w, BUTTON_HEIGHT), target, label, color)

  def _row(self, manager, rect: rl.Rectangle, target: str | None, title: str, subtitle: str, action: str = "", current: bool = False):
    hovered, pressed = manager._interactive_state(target, rect) if target else (False, False)
    draw_selection_list_row(
      rect, title=title, subtitle=subtitle, action_text=action, current=current, hovered=hovered, pressed=pressed,
      action_width=170 if action else 0, action_pill=bool(action), action_pill_height=58, action_pill_width=140,
      title_size=32, subtitle_size=24, action_text_size=24, row_separator=PANEL_STYLE.divider_color,
      current_bg=AetherListColors.CURRENT_BG, current_border=AetherListColors.CURRENT_BORDER,
    )

  def _draw_storage(self, rect: rl.Rectangle) -> None:
    bold, medium = gui_app.font(FontWeight.SEMI_BOLD), gui_app.font(FontWeight.MEDIUM)
    draw_list_group_shell(rect, style=PANEL_STYLE)
    x, width = rect.x + 30, rect.width - 60
    saved = [area for area in self.areas if not area.deleted]
    used = tr("{} of {} used").format(format_bytes(self.used_bytes), format_bytes(OFFLINE_MAX_BYTES))
    count = tr("{} saved map").format(len(saved)) if len(saved) == 1 else tr("{} saved maps").format(len(saved))
    rl.draw_text_ex(bold, used, rl.Vector2(x, rect.y + 26), 38, 0, AetherListColors.HEADER)
    count_w = measure_text_cached(medium, count, 28).x
    rl.draw_text_ex(medium, count, rl.Vector2(x + width - count_w, rect.y + 32), 28, 0, AetherListColors.MUTED)

    bar = rl.Rectangle(x, rect.y + 86, width, 16)
    fraction = min(1.0, self.used_bytes / OFFLINE_MAX_BYTES)
    rl.draw_rectangle_rounded(bar, 1.0, 8, with_alpha(AetherListColors.HEADER, 22))
    if fraction > 0:
      color = AetherListColors.WARNING if fraction > 0.9 else AetherListColors.PRIMARY
      rl.draw_rectangle_rounded(rl.Rectangle(bar.x, bar.y, max(bar.height, bar.width * fraction), bar.height), 1.0, 8, color)

    text, color = self.connection_text()
    rl.draw_circle_v(rl.Vector2(x + 8, rect.y + 145), 7, color)
    rl.draw_text_ex(medium, text, rl.Vector2(x + 28, rect.y + 131), 27, 0, AetherListColors.SUBTEXT)

  def draw_rows(self, scroll_rect: rl.Rectangle, content_width: float, scroll_offset: float, manager) -> None:
    x = scroll_rect.x + INSET
    width = max(1.0, content_width - INSET * 2)
    y = scroll_rect.y + scroll_offset + INSET
    chooser = self.chooser
    for kind, height, data in self.layout_rows():
      rect = rl.Rectangle(x, y, width, height)
      if kind == "storage":
        self._draw_storage(rl.Rectangle(x, y, width, STORAGE_HEIGHT))
      elif kind == "add_header":
        title = tr("How much around {}?").format(chooser["name"]) if chooser else tr("Save a new area")
        draw_section_header(rect, title, title_size=30, style=PANEL_STYLE)
      elif kind == "add_buttons":
        self._pills(manager, x, y, width, [(target, label, None) for target, label in self.add_buttons()])
      elif kind == "estimating":
        self._row(manager, rect, None, tr("Sizing up the area…"), chooser["name"] if chooser else "")
      elif kind == "preset" and chooser is not None:
        radius, zoom, tiles, size = chooser["estimates"][data]
        fits = self.used_bytes + size <= OFFLINE_MAX_BYTES
        radius_text = f"{radius:.0f} km" if ui_state.is_metric else f"{radius * 0.621371:.0f} mi"
        subtitle = (tr("{} • about {} • {} tiles").format(tr(AREA_DETAIL.get(zoom, "")), format_bytes(size), f"{tiles:,}")
                    if fits else tr("Too large for the space left"))
        self._row(manager, rect, f"preset:{data}" if fits else None, tr("{} around").format(radius_text), subtitle,
                  tr("Save") if fits else "")
      elif kind == "cancel":
        self._pills(manager, x, y, width, [("add:cancel", tr("Cancel"), None)])
      elif kind == "searching":
        self._row(manager, rect, None, tr("Searching…"), "")
      elif kind == "result":
        result = self.search_results[data]
        self._row(manager, rect, f"result:{data}", result.name, result.subtitle, tr("Choose"))
      elif kind == "message":
        draw_empty_state_card(rl.Rectangle(x, y, width, EMPTY_HEIGHT), tr("Can't do that yet"), self.message, title_size=28, body_size=24,
                              border=with_alpha(AetherListColors.WARNING, 60), style=PANEL_STYLE)
      elif kind == "route_header":
        draw_section_header(rect, tr("Current route"), title_size=30, style=PANEL_STYLE)
      elif kind == "route":
        destination = self._store.active_destination() or {}
        self._row(manager, rl.Rectangle(x, y, width, ROW_HEIGHT), None,
                  tr("To {}").format(destination.get("name") or destination.get("place_name") or tr("destination")), data)
      elif kind == "saved_header":
        draw_section_header(rect, tr("Saved maps"), trailing_text=tr("Updated every {} days").format(self.summary.get("refresh_days") or 90),
                            title_size=30, trailing_size=24, style=PANEL_STYLE)
      elif kind == "area":
        selected = self.selected_area_id == data.id
        action = "" if data.deleted else (tr("Close") if selected else tr("Manage"))
        self._row(manager, rect, None if data.deleted else f"area:{data.id}", self.area_title(data), self.area_status(data), action, selected)
      elif kind == "area_actions":
        state = self._progress(data.id)
        waiting = state.get("state") == "waiting_wifi" and not data.allow_metered
        first = ("area_action:now", tr("Download now"), AetherListColors.SUCCESS) if waiting else ("area_action:update", tr("Update now"), None)
        self._pills(manager, x, y, width, [first, ("area_action:delete", tr("Delete"), AetherListColors.DANGER)])
      elif kind == "empty":
        draw_empty_state_card(rl.Rectangle(x, y, width, EMPTY_HEIGHT), tr("No saved maps yet"),
                              tr("Save an area on Wi-Fi so the map keeps working without signal."),
                              title_size=30, body_size=24, border=with_alpha(PANEL_STYLE.surface_border, 14), style=PANEL_STYLE)
      y += height
