import pyray as rl
import time
from dataclasses import dataclass
from collections.abc import Callable
from cereal import log
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos, FONT_SCALE
from openpilot.system.ui.lib.multilang import tr, tr_noop
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

SIDEBAR_WIDTH = 300
METRIC_HEIGHT = 126
METRIC_WIDTH = 240
METRIC_MARGIN = 30
FONT_SIZE = 35

SETTINGS_BTN = rl.Rectangle(50, 35, 200, 117)
HOME_BTN = rl.Rectangle(60, 860, 180, 180)

ThermalStatus = log.DeviceState.ThermalStatus
NetworkType = log.DeviceState.NetworkType


# Color scheme - Galaxy palette
class Colors:
  WHITE = rl.Color(240, 240, 248, 255)
  WHITE_DIM = rl.Color(160, 160, 185, 200)
  GRAY = rl.Color(38, 38, 58, 255)

  # Status colors (Galaxy palette)
  GOOD = rl.Color(94, 200, 200, 255)      # Stellar Teal
  WARNING = rl.Color(212, 160, 96, 255)   # Solar Amber
  DANGER = rl.Color(224, 85, 119, 255)    # Nebula Rose

  # UI elements & Galaxy theme
  SIDEBAR_BG = rl.Color(10, 10, 22, 255)        # Deep cosmic void (#0a0a16)
  SIDEBAR_BORDER = rl.Color(30, 30, 62, 255)    # Subtle boundary line (#1e1e3e)
  SIDEBAR_ACCENT = rl.Color(139, 92, 246, 50)   # Cosmic purple accent glow
  CARD_BG = rl.Color(18, 18, 36, 255)           # Galaxy card plate (#121224)
  CARD_BORDER = rl.Color(35, 35, 68, 255)       # Card border (#232344)
  BUTTON_NORMAL = rl.WHITE
  BUTTON_PRESSED = rl.Color(200, 200, 220, 255)
  BUTTON_BG = rl.Color(18, 18, 36, 220)
  BUTTON_BORDER = rl.Color(35, 35, 68, 255)
  BUTTON_ACTIVE_BG = rl.Color(139, 108, 197, 60)
  BUTTON_ACTIVE_BORDER = rl.Color(139, 92, 246, 180)
  TEXT_LABEL = rl.Color(128, 128, 168, 255)     # Muted lavender
  TEXT_VALUE = rl.Color(240, 240, 248, 255)     # Bright cosmic white
  METRIC_BORDER = rl.Color(35, 35, 68, 255)


NETWORK_TYPES = {
  NetworkType.none: tr_noop("--"),
  NetworkType.wifi: tr_noop("Wi-Fi"),
  NetworkType.ethernet: tr_noop("ETH"),
  NetworkType.cell2G: tr_noop("2G"),
  NetworkType.cell3G: tr_noop("3G"),
  NetworkType.cell4G: tr_noop("LTE"),
  NetworkType.cell5G: tr_noop("5G"),
}


@dataclass(slots=True)
class MetricData:
  label: str
  value: str
  color: rl.Color

  def update(self, label: str, value: str, color: rl.Color):
    self.label = label
    self.value = value
    self.color = color


class Sidebar(Widget):
  def __init__(self):
    super().__init__()
    self._net_type = NETWORK_TYPES.get(NetworkType.none)
    self._net_strength = 0

    self._temp_status = MetricData(tr_noop("TEMP"), "--°C", Colors.GOOD)
    self._panda_status = MetricData(tr_noop("VEHICLE"), tr_noop("ONLINE"), Colors.GOOD)
    self._connect_status = MetricData(tr_noop("CONNECT"), tr_noop("OFFLINE"), Colors.WARNING)
    self._recording_audio = False

    self._settings_btn_rect = rl.Rectangle(SETTINGS_BTN.x, SETTINGS_BTN.y, SETTINGS_BTN.width, SETTINGS_BTN.height)
    self._home_btn_rect = rl.Rectangle(HOME_BTN.x, HOME_BTN.y, HOME_BTN.width, HOME_BTN.height)

    self._home_img = gui_app.texture("images/button_home.png", HOME_BTN.width, HOME_BTN.height)
    self._flag_img = gui_app.texture("images/button_flag.png", HOME_BTN.width, HOME_BTN.height)
    self._settings_img = gui_app.texture("images/button_settings.png", SETTINGS_BTN.width, SETTINGS_BTN.height)
    self._mic_img = gui_app.texture("icons/microphone.png", 30, 30)
    self._mic_indicator_rect = rl.Rectangle(0, 0, 0, 0)
    self._font_regular = gui_app.font(FontWeight.NORMAL)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)
    self._font_bold = gui_app.font(FontWeight.BOLD)

    # Callbacks
    self._on_settings_click: Callable | None = None
    self._on_flag_click: Callable | None = None
    self._open_settings_callback: Callable | None = None

  def set_callbacks(self, on_settings: Callable | None = None, on_flag: Callable | None = None,
                    open_settings: Callable | None = None):
    self._on_settings_click = on_settings
    self._on_flag_click = on_flag
    self._open_settings_callback = open_settings

  def _render(self, rect: rl.Rectangle):
    # Deep cosmic background
    rl.draw_rectangle_rec(rect, Colors.SIDEBAR_BG)
    # Right border divider with purple accent glow
    rl.draw_line_ex(rl.Vector2(rect.x + rect.width - 1, rect.y),
                    rl.Vector2(rect.x + rect.width - 1, rect.y + rect.height),
                    2.0, Colors.SIDEBAR_BORDER)
    rl.draw_line_ex(rl.Vector2(rect.x + rect.width - 1, rect.y),
                    rl.Vector2(rect.x + rect.width - 1, rect.y + rect.height),
                    1.0, Colors.SIDEBAR_ACCENT)

    self._draw_buttons(rect)
    self._draw_network_indicator(rect)
    self._draw_metrics(rect)

  def _update_state(self):
    sm = ui_state.sm
    if not sm.updated['deviceState']:
      return

    device_state = sm['deviceState']

    self._recording_audio = ui_state.recording_audio
    self._update_network_status(device_state)
    self._update_temperature_status(device_state)
    self._update_connection_status(device_state)
    self._update_panda_status()

  def _update_network_status(self, device_state):
    self._net_type = NETWORK_TYPES.get(device_state.networkType.raw, tr_noop("Unknown"))
    strength = device_state.networkStrength
    self._net_strength = max(0, min(5, strength.raw + 1)) if strength.raw > 0 else 0

  def _update_temperature_status(self, device_state):
    thermal_status = device_state.thermalStatus
    temperature = f"{int(device_state.maxTempC)}°C"

    if thermal_status == ThermalStatus.ok:
      self._temp_status.update(tr_noop("TEMP"), temperature, Colors.GOOD)
    elif thermal_status == ThermalStatus.warmDEPRECATED:
      self._temp_status.update(tr_noop("TEMP"), temperature, Colors.WARNING)
    else:
      self._temp_status.update(tr_noop("TEMP"), temperature, Colors.DANGER)

  def _update_connection_status(self, device_state):
    last_ping = device_state.lastAthenaPingTime
    if last_ping == 0:
      self._connect_status.update(tr_noop("CONNECT"), tr_noop("OFFLINE"), Colors.WARNING)
    elif time.monotonic_ns() - last_ping < 80_000_000_000:  # 80 seconds in nanoseconds
      self._connect_status.update(tr_noop("CONNECT"), tr_noop("ONLINE"), Colors.GOOD)
    else:
      self._connect_status.update(tr_noop("CONNECT"), tr_noop("ERROR"), Colors.DANGER)

  def _update_panda_status(self):
    if ui_state.panda_type == log.PandaState.PandaType.unknown:
      self._panda_status.update(tr_noop("NO"), tr_noop("PANDA"), Colors.DANGER)
    else:
      self._panda_status.update(tr_noop("VEHICLE"), tr_noop("ONLINE"), Colors.GOOD)

  def _handle_mouse_release(self, mouse_pos: MousePos):
    settings_hit = rl.check_collision_point_rec(mouse_pos, self._settings_btn_rect) or rl.check_collision_point_rec(mouse_pos, SETTINGS_BTN)
    home_hit = rl.check_collision_point_rec(mouse_pos, self._home_btn_rect) or rl.check_collision_point_rec(mouse_pos, HOME_BTN)
    if settings_hit:
      if self._on_settings_click:
        self._on_settings_click()
    elif home_hit and ui_state.started:
      if self._on_flag_click:
        self._on_flag_click()
    elif self._recording_audio and rl.check_collision_point_rec(mouse_pos, self._mic_indicator_rect):
      if self._open_settings_callback:
        self._open_settings_callback()

  def _draw_buttons(self, rect: rl.Rectangle):
    mouse_pos = gui_app.last_mouse_event.pos
    mouse_down = self.is_pressed and gui_app.last_mouse_event.left_down

    # Settings button (dynamically positioned at top, centered horizontally)
    btn_w = min(200.0, rect.width - 40.0)
    btn_h = 117.0
    btn_x = rect.x + (rect.width - btn_w) / 2.0
    btn_y = rect.y + 35.0
    self._settings_btn_rect = rl.Rectangle(btn_x, btn_y, btn_w, btn_h)

    settings_down = mouse_down and rl.check_collision_point_rec(mouse_pos, self._settings_btn_rect)
    card_bg = Colors.BUTTON_ACTIVE_BG if settings_down else Colors.BUTTON_BG
    card_border = Colors.BUTTON_ACTIVE_BORDER if settings_down else Colors.BUTTON_BORDER
    rl.draw_rectangle_rounded(self._settings_btn_rect, 0.25, 12, card_bg)
    rl.draw_rectangle_rounded_lines_ex(self._settings_btn_rect, 0.25, 12, 1.5, card_border)
    tint = Colors.BUTTON_PRESSED if settings_down else Colors.BUTTON_NORMAL
    img_x = int(self._settings_btn_rect.x + (self._settings_btn_rect.width - self._settings_img.width) / 2)
    img_y = int(self._settings_btn_rect.y + (self._settings_btn_rect.height - self._settings_img.height) / 2)
    rl.draw_texture(self._settings_img, img_x, img_y, tint)

    # Home/Flag button (dynamically anchored near bottom)
    home_size = min(180.0, rect.width - 60.0)
    home_x = rect.x + (rect.width - home_size) / 2.0
    home_y = rect.y + rect.height - home_size - 40.0
    self._home_btn_rect = rl.Rectangle(home_x, home_y, home_size, home_size)

    flag_pressed = mouse_down and rl.check_collision_point_rec(mouse_pos, self._home_btn_rect)
    home_card_bg = Colors.BUTTON_ACTIVE_BG if flag_pressed else Colors.BUTTON_BG
    home_card_border = Colors.BUTTON_ACTIVE_BORDER if flag_pressed else Colors.BUTTON_BORDER
    rl.draw_rectangle_rounded(self._home_btn_rect, 0.28, 14, home_card_bg)
    rl.draw_rectangle_rounded_lines_ex(self._home_btn_rect, 0.28, 14, 1.5, home_card_border)
    button_img = self._flag_img if ui_state.started else self._home_img
    tint = Colors.BUTTON_PRESSED if (ui_state.started and flag_pressed) else Colors.BUTTON_NORMAL
    h_img_x = int(self._home_btn_rect.x + (self._home_btn_rect.width - button_img.width) / 2)
    h_img_y = int(self._home_btn_rect.y + (self._home_btn_rect.height - button_img.height) / 2)
    rl.draw_texture(button_img, h_img_x, h_img_y, tint)

    # Microphone button
    if self._recording_audio:
      self._mic_indicator_rect = rl.Rectangle(rect.x + rect.width - 130, rect.y + 245, 75, 40)

      mic_pressed = mouse_down and rl.check_collision_point_rec(mouse_pos, self._mic_indicator_rect)
      bg_color = rl.Color(Colors.DANGER.r, Colors.DANGER.g, Colors.DANGER.b, int(255 * 0.65)) if mic_pressed else Colors.DANGER

      rl.draw_rectangle_rounded(self._mic_indicator_rect, 1, 10, bg_color)
      rl.draw_texture(self._mic_img, int(self._mic_indicator_rect.x + (self._mic_indicator_rect.width - self._mic_img.width) / 2),
                      int(self._mic_indicator_rect.y + (self._mic_indicator_rect.height - self._mic_img.height) / 2), Colors.WHITE)

  def _draw_network_indicator(self, rect: rl.Rectangle):
    # Signal strength dots
    dot_size = 20
    dot_spacing = 30
    total_dots_w = 4 * dot_spacing + dot_size
    x_start = rect.x + (rect.width - total_dots_w) / 2
    y_pos = self._settings_btn_rect.y + self._settings_btn_rect.height + 26

    for i in range(5):
      is_active = i < self._net_strength
      color = Colors.GOOD if is_active else Colors.GRAY
      x = int(x_start + i * dot_spacing + dot_size / 2)
      y = int(y_pos + dot_size / 2)
      if is_active:
        rl.draw_circle(x, y, dot_size / 2 + 1, rl.Color(Colors.GOOD.r, Colors.GOOD.g, Colors.GOOD.b, 50))
      rl.draw_circle(x, y, dot_size / 2, color)

    # Network type text
    text_y = y_pos + dot_size + 14
    text_str = tr(self._net_type)
    text_size = measure_text_cached(self._font_medium, text_str, 28)
    text_x = rect.x + (rect.width - text_size.x) / 2
    rl.draw_text_ex(self._font_medium, text_str, rl.Vector2(round(text_x), round(text_y)), 28, 0, Colors.TEXT_LABEL)

  def _draw_metrics(self, rect: rl.Rectangle):
    net_bottom = self._settings_btn_rect.y + self._settings_btn_rect.height + 26 + 20 + 14 + 32
    home_top = self._home_btn_rect.y
    available_h = home_top - net_bottom - 20
    num_metrics = 3
    metric_h = min(float(METRIC_HEIGHT), max(96.0, (available_h - (num_metrics - 1) * 14.0) / num_metrics))
    total_cards_h = num_metrics * metric_h
    remaining_space = available_h - total_cards_h
    gap = max(12.0, min(24.0, remaining_space / max(1, num_metrics - 1)))
    start_y = net_bottom + 10.0 + (available_h - (total_cards_h + (num_metrics - 1) * gap)) / 2.0

    metrics = [self._temp_status, self._panda_status, self._connect_status]
    for i, metric in enumerate(metrics):
      card_y = start_y + i * (metric_h + gap)
      self._draw_metric(rect, metric, card_y, metric_h)

  def _draw_metric(self, rect: rl.Rectangle, metric: MetricData, y: float, metric_h: float = METRIC_HEIGHT):
    metric_w = min(float(METRIC_WIDTH), rect.width - 40.0)
    metric_rect = rl.Rectangle(rect.x + (rect.width - metric_w) / 2, y, metric_w, metric_h)

    # Galaxy card plate
    rl.draw_rectangle_rounded(metric_rect, 0.18, 12, Colors.CARD_BG)
    rl.draw_rectangle_rounded_lines_ex(metric_rect, 0.18, 12, 1.5, Colors.CARD_BORDER)

    # Sleek glowing left indicator pill
    pill_x = metric_rect.x + 10
    pill_y = metric_rect.y + 14
    pill_h = metric_rect.height - 28
    glow_rect = rl.Rectangle(pill_x - 2, pill_y - 2, 8, pill_h + 4)
    rl.draw_rectangle_rounded(glow_rect, 0.8, 6, rl.Color(metric.color.r, metric.color.g, metric.color.b, 40))
    rl.draw_rectangle_rounded(rl.Rectangle(pill_x, pill_y, 4, pill_h), 1.0, 4, metric.color)

    # Label & Value text
    label_text = tr(metric.label)
    value_text = tr(metric.value)

    label_size = measure_text_cached(self._font_medium, label_text, 22)
    value_size = measure_text_cached(self._font_bold, value_text, 36)

    content_x = metric_rect.x + 22
    content_w = metric_rect.width - 26

    # Center text block vertically inside card
    total_text_h = label_size.y + value_size.y + 4
    start_text_y = metric_rect.y + (metric_rect.height - total_text_h) / 2

    label_pos = rl.Vector2(content_x + (content_w - label_size.x) / 2, start_text_y)
    rl.draw_text_ex(self._font_medium, label_text, label_pos, 22, 0, Colors.TEXT_LABEL)

    value_pos = rl.Vector2(content_x + (content_w - value_size.x) / 2, start_text_y + label_size.y + 4)
    val_color = metric.color if metric.color != Colors.GOOD else Colors.TEXT_VALUE
    rl.draw_text_ex(self._font_bold, value_text, value_pos, 36, 0, val_color)
