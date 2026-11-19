"""The phone half of the split screen: the phone's picture (or its connection status) and its touches."""
from __future__ import annotations

import time

from msgq.visionipc import VisionIpcClient

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.common.hardware import COMMA_HARDWARE
from openpilot.selfdrive.ui.onroad import cameraview
from openpilot.selfdrive.ui.onroad.cameraview import CameraView
from openpilot.starpilot.system.phone_projection.ipc import StatusReader, Touch, TouchSender, VIPC_NAME
from openpilot.starpilot.system.phone_projection.layout import Rect, fit, snap, split
from openpilot.starpilot.ui.raylib import rl
from openpilot.system.ui.lib.text_measure import measure_text_cached

BACKGROUND = rl.Color(12, 12, 14, 255)
TEXT = rl.Color(235, 235, 240, 255)
SUBTLE = rl.Color(150, 150, 160, 255)
RECONNECT_HOLD = 6.0  # keep the phone half through a short drop instead of resizing the comma view twice
SLIDE_SECONDS = 0.25


HIGHP = "#version 330 core\n" if cameraview.platform.system() == "Darwin" else "#version 300 es\nprecision highp float;\n"
# Full precision and no colour change: thin phone text must land on exact texels.
PHONE_VERTEX_SHADER = HIGHP + """
in vec3 vertexPosition;
in vec2 vertexTexCoord;
uniform mat4 mvp;
out vec2 fragTexCoord;
void main() {
  fragTexCoord = vertexTexCoord;
  gl_Position = mvp * vec4(vertexPosition, 1.0);
}
"""
if COMMA_HARDWARE:
  PHONE_FRAGMENT_SHADER = """#version 300 es
#extension GL_OES_EGL_image_external_essl3 : enable
precision highp float;
in vec2 fragTexCoord;
uniform samplerExternalOES texture0;
out vec4 fragColor;
void main() {
  fragColor = vec4(texture(texture0, fragTexCoord).rgb, 1.0);
}
"""
else:
  PHONE_FRAGMENT_SHADER = HIGHP + """
in vec2 fragTexCoord;
uniform sampler2D texture0;
uniform sampler2D texture1;
out vec4 fragColor;
void main() {
  float y = texture(texture0, fragTexCoord).r;
  vec2 uv = texture(texture1, fragTexCoord).ra - 0.5;
  fragColor = vec4(y + 1.402*uv.y, y - 0.344*uv.x - 0.714*uv.y, y + 1.772*uv.x, 1.0);
}
"""
PHONE_STREAMS = (VisionStreamType.VISION_STREAM_NARROW_ROAD, VisionStreamType.VISION_STREAM_WIDE_ROAD)


class PhoneCamera(CameraView):
  """CameraView over projectiond's streams: its own shader, and only the part of the frame the phone draws."""
  VERTEX_SHADER = PHONE_VERTEX_SHADER
  FRAGMENT_SHADER = PHONE_FRAGMENT_SHADER

  def __init__(self):
    super().__init__(VIPC_NAME, PHONE_STREAMS[0])
    self.crop: tuple[float, float, float, float] | None = None
    self.picture: Rect | None = None

  def _init_clients(self, name: str, stream_type: VisionStreamType):
    self._clients = {stream: VisionIpcClient(name, stream, conflate=True) for stream in PHONE_STREAMS}
    self.client = self._clients[stream_type]

  def _update_texture_color_filtering(self):
    pass

  def _render(self, rect: rl.Rectangle, *, poll_camera: bool = True):
    if (poll_camera and not self._poll_camera()) or self.frame is None:
      self.picture = None
      return
    cx, cy, cw, ch = self.crop or (0, 0, self.frame.width, self.frame.height)
    picture = snap(fit(cw, ch, Rect(rect.x, rect.y, rect.width, rect.height)))
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
    self.shown_at: float | None = None  # set only by split(): the 3X slides the phone half in
    self.last_active = 0.0
    self.side = "right"

  # ---- layout --------------------------------------------------------------------------------------------
  def refresh(self) -> dict:
    self.status = self.status_reader.read()
    return self.status

  def split(self, rect: Rect) -> tuple[Rect, Rect] | None:
    """(comma rect, phone rect) while a phone is showing, else None and the comma keeps the whole screen."""
    self.refresh()
    now = time.monotonic()
    if self.status.get("state") == "active":
      if self.shown_at is None:
        self.shown_at = now
      self.last_active = now
      self.side = self.status.get("side") or "right"
    elif self.shown_at is None or now - self.last_active > RECONNECT_HOLD or not self.status:
      self.shown_at = None
      return None
    return split(rect, self.side)

  def _slide(self, rect: Rect) -> Rect:
    """rect pushed toward its screen edge while the phone half slides in; whole pixels once it has."""
    if self.shown_at is None:
      return rect
    t = min(1.0, (time.monotonic() - self.shown_at) / SLIDE_SECONDS)
    if t >= 1.0:
      return rect
    offset = round((1.0 - t) ** 3 * rect.width) * (1 if self.side == "right" else -1)
    return Rect(rect.x + offset, rect.y, rect.width, rect.height)

  # ---- drawing -------------------------------------------------------------------------------------------
  def render(self, rect: Rect) -> None:
    status = self.status
    rect = self._slide(rect)
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
      self._message(rect, status.get("phone") or "Phone", status.get("message") or "Starting…")
      return
    if self.camera is not None:
      self.camera.picture = None
    title = "Reconnecting…" if self.shown_at is not None else "Phone Screen"
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
        width = measure_text_cached(font, line, int(size * scale)).x
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
