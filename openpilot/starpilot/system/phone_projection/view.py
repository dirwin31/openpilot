"""The phone half of the split screen: the phone's picture (or its connection status) and its touches."""
from __future__ import annotations

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.selfdrive.ui.onroad.cameraview import CameraView
from openpilot.starpilot.system.phone_projection import settings as projection_settings
from openpilot.starpilot.system.phone_projection.ipc import StatusReader, Touch, TouchSender, VIPC_NAME
from openpilot.starpilot.system.phone_projection.layout import Rect, fit, split
from openpilot.starpilot.ui.raylib import rl

BACKGROUND = rl.Color(12, 12, 14, 255)
TEXT = rl.Color(235, 235, 240, 255)
SUBTLE = rl.Color(150, 150, 160, 255)


class PhoneCamera(CameraView):
  """CameraView over projectiond's streams: no road colour grading, and only the part of the frame the phone draws."""

  def __init__(self):
    super().__init__(VIPC_NAME, VisionStreamType.VISION_STREAM_NARROW_ROAD)
    self.crop: tuple[float, float, float, float] | None = None
    self.picture: Rect | None = None

  def _update_texture_color_filtering(self):
    self._color_strength_val[0] = 0.0
    self._road_camera_val[0] = 0
    rl.set_shader_value(self.shader, self._color_strength_loc, self._color_strength_val, rl.ShaderUniformDataType.SHADER_UNIFORM_FLOAT)
    rl.set_shader_value(self.shader, self._road_camera_loc, self._road_camera_val, rl.ShaderUniformDataType.SHADER_UNIFORM_INT)

  def _render(self, rect: rl.Rectangle, *, poll_camera: bool = True):
    if (poll_camera and not self._poll_camera()) or self.frame is None:
      self.picture = None
      return
    cx, cy, cw, ch = self.crop or (0, 0, self.frame.width, self.frame.height)
    picture = fit(cw, ch, Rect(rect.x, rect.y, rect.width, rect.height))
    self.picture = picture
    src = rl.Rectangle(cx, cy, cw, ch)
    dst = rl.Rectangle(picture.x, picture.y, picture.width, picture.height)
    from openpilot.common.hardware import COMMA_HARDWARE
    if COMMA_HARDWARE:
      self._render_egl(src, dst)
    else:
      self._render_textures(src, dst)


class PhoneView:
  """Draw and route input for the phone half; the caller decides where that half is."""

  def __init__(self, font=None, status: StatusReader | None = None, sender: TouchSender | None = None):
    self.status_reader = status or StatusReader()
    self.sender = sender or TouchSender()
    self.camera: PhoneCamera | None = None
    self.font = font
    self.status: dict = {}
    self.pressed: set[int] = set()

  # ---- layout --------------------------------------------------------------------------------------------
  def refresh(self) -> dict:
    self.status = self.status_reader.read()
    return self.status

  def split(self, rect: Rect) -> tuple[Rect, Rect] | None:
    """(comma rect, phone rect) while projection is on, else None and the comma keeps the whole screen."""
    self.refresh()
    if not self.status.get("enabled") and not projection_settings.enabled():
      return None
    side = self.status.get("side") or projection_settings.load().side
    return split(rect, side)

  # ---- drawing -------------------------------------------------------------------------------------------
  def render(self, rect: Rect) -> None:
    status = self.status
    rl.draw_rectangle_rec(rl.Rectangle(rect.x, rect.y, rect.width, rect.height), BACKGROUND)
    if status.get("state") == "active":
      camera = self._camera()
      stream = VisionStreamType(int(status.get("stream", 0)))
      camera.switch_stream(stream)
      crop = status.get("crop")
      camera.crop = tuple(crop) if isinstance(crop, list) and len(crop) == 4 else None
      camera.render(rl.Rectangle(rect.x, rect.y, rect.width, rect.height))
      if camera.picture is not None:
        return
      self._message(rect, status.get("phone") or "Phone", "Starting…")
      return
    if self.camera is not None:
      self.camera.picture = None
    title = "Phone Screen"
    message = status.get("message") or ("Starting…" if status else "Waiting for the phone service…")
    self._message(rect, title, message)

  def _camera(self) -> PhoneCamera:
    if self.camera is None:
      self.camera = PhoneCamera()
    return self.camera

  def _message(self, rect: Rect, title: str, message: str) -> None:
    font = self.font or rl.get_font_default()
    size = max(28, min(56, rect.width / 14))
    for text, y, color, scale in ((title, rect.y + rect.height / 2 - size, TEXT, 1.0),
                                  (message, rect.y + rect.height / 2 + size * 0.3, SUBTLE, 0.6)):
      for i, line in enumerate(_wrap(text, max(12, int(rect.width / (size * scale * 0.55))))):
        width = rl.measure_text_ex(font, line, size * scale, 0).x
        rl.draw_text_ex(font, line, rl.Vector2(rect.x + (rect.width - width) / 2, y + i * size * scale * 1.2), size * scale, 0, color)

  # ---- input -----------------------------------------------------------------------------------------------
  def touch(self, kind: str, pointer: int, x: float, y: float) -> bool:
    """Route one touch in screen coordinates; True when it belongs to the phone."""
    picture = self.camera.picture if self.camera is not None and self.status.get("state") == "active" else None
    if kind == "cancel":
      if self.pressed:
        self.pressed.clear()
        self.sender.send(Touch("cancel", pointer, 0, 0))
      return False
    if picture is None:
      self.pressed.discard(pointer)
      return False
    inside = picture.contains(x, y)
    if kind == "down" and not inside:
      return False
    if kind != "down" and pointer not in self.pressed:
      return False
    nx = min(max((x - picture.x) / picture.width, 0.0), 1.0)
    ny = min(max((y - picture.y) / picture.height, 0.0), 1.0)
    if kind == "down":
      self.pressed.add(pointer)
    elif kind == "up":
      self.pressed.discard(pointer)
    self.sender.send(Touch(kind, pointer, nx, ny))
    return True

  def close(self) -> None:
    if self.camera is not None:
      self.camera.close()
      self.camera = None
    self.sender.close()


def _wrap(text: str, width: int) -> list[str]:
  lines, line = [], ""
  for word in text.split():
    if line and len(line) + 1 + len(word) > width:
      lines.append(line)
      line = word
    else:
      line = f"{line} {word}".strip()
  return lines + ([line] if line else [])
