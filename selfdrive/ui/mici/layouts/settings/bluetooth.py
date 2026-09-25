import pyray as rl

from openpilot.selfdrive.ui.mici.layouts.settings.network.wifi_ui import ForgetButton, LoadingAnimation
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, LABEL_COLOR
from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationDialog, BigDialog, BigInputDialog, BigMultiOptionDialog
from openpilot.starpilot.system.android_auto.sdp import AA_WIRELESS_UUID
from openpilot.system.ui.lib.android_auto_manager import AndroidAutoManager
from openpilot.system.ui.lib.application import FontWeight, MousePos, gui_app
from openpilot.system.ui.lib.bluetooth_manager import BluetoothManager
from openpilot.system.ui.widgets.scroller import NavScroller


PAIR_CAR_HELP = "On the car, open Bluetooth / phone settings and add a new device. Then tap the car in this list and confirm the code on the car."


class BluetoothDeviceButton(BigButton):
  LABEL_PADDING = 98
  LABEL_WIDTH = 402 - 98 - 28
  SUB_LABEL_WIDTH = 402 - BigButton.LABEL_HORIZONTAL_PADDING * 2

  def __init__(self, device, manager: BluetoothManager, icon: rl.Texture, selected_audio: str, offroad: bool):
    super().__init__(device.name, "", scroll=True)
    self.device = device
    self._manager = manager
    self._icon = icon
    self._offroad = offroad
    self._selected_audio = selected_audio
    self._check_txt = gui_app.texture("icons_mici/setup/driver_monitoring/dm_check.png", 32, 32)
    self._forget_btn = ForgetButton(self._forget_device)
    self.update_device(device, selected_audio, offroad)

  def _forget_device(self):
    self._manager.forget(self.device.address)

  def _get_label_font_size(self):
    return 48

  @property
  def _show_forget_btn(self):
    return self.device.paired and self._offroad and self._manager.operation_for(self.device.address) != "forgetting"

  def update_device(self, device, selected_audio: str, offroad: bool):
    self.device = device
    self._selected_audio = selected_audio
    self._offroad = offroad

  def _update_state(self):
    super()._update_state()
    operation = self._manager.operation_for(self.device.address)
    pairing = self._manager.status.pairing_address.upper() == self.device.address.upper()
    audio_selected = self._selected_audio.upper() == self.device.address.upper()

    if operation or pairing:
      self.set_value(operation or "pairing")
      self.set_enabled(False)
    elif self.device.connected:
      capabilities = []
      if audio_selected:
        capabilities.append("audio output")
      elif self.device.audio:
        capabilities.append("audio")
      if self.device.controller:
        capabilities.append("controller")
      self.set_value("connected" + (f" / {' / '.join(capabilities)}" if capabilities else ""))
      self.set_enabled(True)
    elif self.device.paired:
      self.set_value("connect")
      self.set_enabled(True)
    else:
      capabilities = []
      if self.device.audio:
        capabilities.append("audio")
      if self.device.controller:
        capabilities.append("controller")
      self.set_value("pair" + (f" / {' / '.join(capabilities)}" if capabilities else ""))
      self.set_enabled(self._offroad)

  def _handle_mouse_release(self, mouse_pos: MousePos):
    if self._show_forget_btn and rl.check_collision_point_rec(mouse_pos, self._forget_btn.rect):
      return
    super()._handle_mouse_release(mouse_pos)

  def set_touch_valid_callback(self, touch_callback):
    super().set_touch_valid_callback(lambda: touch_callback() and not self._forget_btn.is_pressed)
    self._forget_btn.set_touch_valid_callback(touch_callback)

  def set_touch_event_valid_callback(self, touch_callback):
    super().set_touch_event_valid_callback(touch_callback)
    self._forget_btn.set_touch_event_valid_callback(touch_callback)

  def _draw_content(self, btn_y: float):
    self._label.set_color(LABEL_COLOR)
    label_rect = rl.Rectangle(self._rect.x + self.LABEL_PADDING, btn_y + self.LABEL_VERTICAL_PADDING,
                              self.LABEL_WIDTH, self._rect.height - self.LABEL_VERTICAL_PADDING * 2)
    self._label.render(label_rect)

    sub_label_x = self._rect.x + self.LABEL_HORIZONTAL_PADDING
    label_y = btn_y + self._rect.height - self.LABEL_VERTICAL_PADDING
    sub_label_w = self.SUB_LABEL_WIDTH - (self._forget_btn.rect.width if self._show_forget_btn else 0)
    sub_label_height = self._sub_label.get_content_height(sub_label_w)
    if self.device.connected:
      check_y = int(label_y - sub_label_height + (sub_label_height - self._check_txt.height) / 2)
      rl.draw_texture_ex(self._check_txt, rl.Vector2(sub_label_x, check_y), 0.0, 1.0,
                         rl.Color(255, 255, 255, int(255 * 0.585)))
      sub_label_x += self._check_txt.width + 14
    self._sub_label.set_color(rl.Color(255, 255, 255, int(255 * 0.9)))
    self._sub_label.set_font_weight(FontWeight.SEMI_BOLD)
    self._sub_label.render(rl.Rectangle(sub_label_x, label_y - sub_label_height, sub_label_w, sub_label_height))

    rl.draw_texture_ex(self._icon, (self._rect.x + 30, btn_y + 30), 0.0, 1.0, rl.WHITE)
    if self._show_forget_btn:
      self._forget_btn.render(rl.Rectangle(
        self._rect.x + self._rect.width - self._forget_btn.rect.width,
        btn_y + self._rect.height - self._forget_btn.rect.height,
        self._forget_btn.rect.width,
        self._forget_btn.rect.height,
      ))


class BluetoothScanningButton(BigButton):
  def __init__(self):
    super().__init__("", "searching for devices")
    self.set_enabled(False)
    self._loading_animation = LoadingAnimation()

  def _draw_content(self, btn_y: float):
    super()._draw_content(btn_y)
    animation = self._loading_animation
    animation.set_position(self._rect.x + self._rect.width - animation.rect.width - 40,
                           btn_y + self._rect.height - animation.rect.height - 30)
    animation.render()


class BluetoothAudioTestDialog(BigDialog):
  def __init__(self, manager: BluetoothManager, icon: rl.Texture):
    super().__init__("starting", "The test sound is sent at NOW", icon)
    self._manager = manager

  def _render(self, rect):
    self._card.set_text(self._manager.audio_test_phase())
    super()._render(rect)


class BluetoothLayoutMici(NavScroller):
  def __init__(self):
    super().__init__()
    self._manager = BluetoothManager()
    self._last_signature = None
    self._last_prompt_id = ""
    self._bluetooth_icon = gui_app.texture("icons_mici/settings/bluetooth.png", 56, 56)
    self._dialog_icon = gui_app.texture("icons_mici/settings/bluetooth.png", 64, 64)
    self._power_btn = BigButton("bluetooth", "off", self._dialog_icon, scroll=True)
    self._power_btn.set_click_callback(self._toggle_power)
    self._scan_btn = BigButton("scan for devices", "scan", self._dialog_icon, scroll=True)
    self._scan_btn.set_click_callback(lambda: self._manager.set_scanning(True))
    self._scanning_btn = BluetoothScanningButton()
    self._android_auto = None
    self._android_auto_btn = BigButton("android auto", "off", self._dialog_icon, scroll=True)
    self._android_auto_btn.set_click_callback(self._android_auto_actions)
    self._device_buttons = {}
    self._scan_on_ready = False
    self._scroller.add_widgets([self._power_btn, self._android_auto_btn, self._scan_btn, self._scanning_btn])
    self._rebuild()

  def show_event(self):
    super().show_event()
    self._manager.set_active(True)
    self._sync_android_auto()
    self._scan_on_ready = True
    gui_app.add_nav_stack_tick(self._tick)

  def hide_event(self):
    if self._manager.status.discovering and self._manager.status.offroad:
      self._manager.set_scanning(False)
    self._manager.set_active(False)
    if self._android_auto is not None:
      self._android_auto.stop()
      self._android_auto = None
    gui_app.remove_nav_stack_tick(self._tick)
    super().hide_event()

  def _toggle_power(self):
    enabled = not self._manager.status.enabled
    self._scan_on_ready = enabled
    self._manager.set_power(enabled)

  def _sync_android_auto(self):
    enabled = gui_app.android_auto_enabled and self._manager.status.enabled
    if enabled and self._android_auto is None:
      self._android_auto = AndroidAutoManager()
      self._android_auto.set_active(True)
    elif not enabled and self._android_auto is not None:
      self._android_auto.stop()
      self._android_auto = None

  def _rebuild(self):
    status = self._manager.status
    self._power_btn.set_value("on" if status.enabled else "off")
    self._power_btn.set_enabled(status.available and status.offroad)
    self._scan_btn.set_enabled(status.enabled and status.offroad)
    items = [self._power_btn]
    if status.enabled and gui_app.android_auto_enabled:
      items.append(self._android_auto_btn)
    for device in status.devices:
      button = self._device_buttons.get(device.address)
      if button is None:
        button = BluetoothDeviceButton(device, self._manager, self._bluetooth_icon, status.selected_audio, status.offroad)
        button.set_click_callback(lambda address=device.address: self._device_selected(address))
        self._device_buttons[device.address] = button
        self._scroller.add_widget(button)
      else:
        button.update_device(device, status.selected_audio, status.offroad)
      items.append(button)
    if status.enabled:
      items.append(self._scanning_btn if status.discovering else self._scan_btn)
    self._device_buttons = {device.address: self._device_buttons[device.address] for device in status.devices}
    self._scroller.items[:] = items

  def _device_selected(self, address: str):
    device = next((device for device in self._manager.status.devices if device.address == address), None)
    if device is None:
      return
    if not device.paired:
      self._manager.pair(device.address)
    elif not device.connected:
      self._manager.connect(device.address)
    else:
      self._device_actions(device)

  def _device_actions(self, device):
    options = ["disconnect"]
    if device.audio:
      selected = self._manager.status.selected_audio.upper() == device.address.upper()
      options.append("stop using for audio" if selected else "use for audio")
      if device.connected and self._manager.status.offroad:
        options.append("test audio")
    dialog_holder = {}

    def apply():
      if self._android_auto is None or not gui_app.android_auto_enabled:
        return
      action = dialog_holder["dialog"].get_selected_option()
      if action == "disconnect":
        self._manager.disconnect(device.address)
      elif action == "use for audio":
        self._manager.select_audio(device.address)
      elif action == "stop using for audio":
        self._manager.select_audio("")
      elif action == "test audio":
        self._manager.test_audio(device.address)
        gui_app.push_widget(BluetoothAudioTestDialog(self._manager, self._dialog_icon))

    dialog = BigMultiOptionDialog(options=options, default=options[0], right_btn_callback=apply)
    dialog_holder["dialog"] = dialog
    gui_app.push_widget(dialog)

  # ------------------------------------------------------------ android auto

  def _android_auto_value(self) -> str:
    status = self._android_auto.status
    if not status:
      return "starting service"
    state = status.get("state", "idle")
    if state == "streaming":
      view = "car layout" if status.get("view") == "car" else "mirror"
      return f"projecting / {view} / {status.get('stats', {}).get('fps', 0)} fps"
    if state == "idle":
      if status.get("error"):
        return "stopped / error"
      if status.get("connection") == "wired":
        return "off / wired (usb)"
      return f"off / {status.get('receiver_name')}" if status.get("receiver_name") else "choose your car"
    if state == "backoff":
      return f"retrying in {status.get('retry_in', 0):.0f}s"
    return str(status.get("label", state))

  def _android_auto_actions(self):
    if self._android_auto is None or not gui_app.android_auto_enabled:
      return
    status = self._android_auto.status
    bt = self._manager.status
    options = []
    wired = status.get("connection") == "wired"
    if status and (wired or status.get("receiver_address")):
      options.append("stop" if status.get("running") else "start")
    if not wired:
      options.append("choose car")
    options.append("mirror comma screen" if status.get("configured_view", "car") == "car" else "use car layout")
    if not status.get("running"):
      options.append("use wireless" if wired else "use wired (usb)")
    if bt.offroad and not wired:
      options.append("pair a new car")
    if status and status.get("error"):
      options.append("show last error")
    dialog_holder = {}

    def apply():
      action = dialog_holder["dialog"].get_selected_option()
      if action == "start":
        self._android_auto.start()
      elif action == "stop":
        self._android_auto.stop_projection()
      elif action == "choose car":
        self._android_auto_choose_car()
      elif action == "mirror comma screen":
        self._android_auto.set_view("mirror")
      elif action == "use car layout":
        self._android_auto.set_view("car")
      elif action == "use wired (usb)":
        self._android_auto.set_connection("wired")
      elif action == "use wireless":
        self._android_auto.set_connection("wireless")
      elif action == "pair a new car":
        self._android_auto.prepare_pairing()
        self._manager.set_scanning(True)
        gui_app.push_widget(BigDialog("pair your car", PAIR_CAR_HELP))
      elif action == "show last error":
        gui_app.push_widget(BigDialog("android auto", str(status.get("error", ""))))

    dialog = BigMultiOptionDialog(options=options, default=options[0], right_btn_callback=apply)
    dialog_holder["dialog"] = dialog
    gui_app.push_widget(dialog)

  def _android_auto_choose_car(self):
    cars = [device for device in self._manager.status.devices if device.paired]
    if not cars:
      gui_app.push_widget(BigDialog("android auto", "Pair your car first: choose \"pair a new car\"."))
      return
    cars.sort(key=lambda device: str(AA_WIRELESS_UUID) not in device.uuids)
    labels = {}
    for device in cars:
      label = device.name + (" (android auto)" if str(AA_WIRELESS_UUID) in device.uuids else "")
      if label in labels:
        label += f" {device.address[-5:]}"
      labels[label] = device
    dialog_holder = {}

    def apply():
      device = labels.get(dialog_holder["dialog"].get_selected_option())
      if device is not None and self._android_auto is not None and gui_app.android_auto_enabled:
        self._android_auto.select_receiver(device.address, device.name)

    options = list(labels)
    dialog = BigMultiOptionDialog(options=options, default=options[0], right_btn_callback=apply)
    dialog_holder["dialog"] = dialog
    gui_app.push_widget(dialog)

  def _handle_prompt(self):
    prompt = self._manager.status.prompt
    if prompt is None or prompt.get("id") == self._last_prompt_id:
      return
    self._last_prompt_id = prompt["id"]
    name = prompt.get("name") or "Bluetooth device"
    value = str(prompt.get("value") or "")
    if prompt.get("display_only"):
      gui_app.push_widget(BigDialog(name, value))
    elif prompt.get("kind") in ("pin", "passkey"):
      gui_app.push_widget(BigInputDialog(
        f"enter {prompt['kind']} for {name}",
        minimum_length=1,
        confirm_callback=lambda response: self._manager.respond(prompt["id"], True, response),
      ))
    else:
      title = f"slide to pair\n{name}"
      if value:
        title += f"\n{value}"
      gui_app.push_widget(BigConfirmationDialog(
        title,
        self._dialog_icon,
        lambda: self._manager.respond(prompt["id"], True),
      ))

  def _tick(self):
    self._sync_android_auto()
    status = self._manager.status
    signature = (
      gui_app.android_auto_enabled,
      status.available,
      status.enabled,
      status.powered,
      status.discovering,
      status.offroad,
      status.selected_audio,
      status.pairing_address,
      tuple((device.address, device.name, device.paired, device.connected, device.audio, device.controller) for device in status.devices),
    )
    if signature != self._last_signature:
      self._last_signature = signature
      self._rebuild()
    if self._scan_on_ready and status.available and status.enabled:
      self._scan_on_ready = False
      if status.offroad and not status.discovering:
        self._manager.set_scanning(True)
    error = self._manager.consume_error()
    if error:
      self._scan_on_ready = False
      gui_app.push_widget(BigDialog("Bluetooth", error))
    if self._android_auto is not None:
      android_auto_value = self._android_auto_value()
      if android_auto_value != self._android_auto_btn.get_value():
        self._android_auto_btn.set_value(android_auto_value)
      self._android_auto_btn.set_enabled(not self._android_auto.busy)
      android_auto_error = self._android_auto.consume_error()
      if android_auto_error:
        gui_app.push_widget(BigDialog("android auto", android_auto_error))
    self._handle_prompt()
