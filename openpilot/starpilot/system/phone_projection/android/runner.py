"""Wireless Android Auto for projectiond: the comma is the head unit, a paired Android phone projects into it."""
from __future__ import annotations

import socket
import threading
import time

from openpilot.starpilot.system.phone_projection.android.session import (
  HeadUnitConfig, HeadUnitSession, POINTER_DOWN, POINTER_UP, TOUCH_CANCEL, TOUCH_DOWN, TOUCH_MOVE, TOUCH_UP)
from openpilot.starpilot.system.phone_projection.android.wireless import WirelessSetup
from openpilot.starpilot.system.phone_projection.bluez import android_candidates
from openpilot.starpilot.system.phone_projection.ipc import Touch
from openpilot.starpilot.system.phone_projection.layout import android_crop, android_density, android_margins, android_mode
from openpilot.starpilot.system.phone_projection.runtime import HOTSPOT_ADDRESS, Context, TouchTracker, hotspot
from openpilot.starpilot.system.phone_projection.video import ANDROID_STREAM, VideoPipe

AAP_PORT = 5288
RETRY_SECONDS = 10.0
WAKE_SECONDS = 30.0
ACCEPT_SECONDS = 30.0


class AndroidAutoRunner:
  kind = "android"

  def __init__(self, ctx: Context, *, hotspot_source=hotspot, listen_address: str = HOTSPOT_ADDRESS, port: int = AAP_PORT,
               session_factory=HeadUnitSession):
    self.ctx, self.hotspot_source, self.listen_address, self.port = ctx, hotspot_source, listen_address, port
    self.session_factory = session_factory
    self.state, self.message, self.phone = "starting", "", ""
    self.session: HeadUnitSession | None = None
    self.pipe: VideoPipe | None = None
    self.setup: WirelessSetup | None = None
    self.listener: socket.socket | None = None
    self.touches = TouchTracker()
    self.lock = threading.Lock()
    self.woken: dict[str, float] = {}
    self.registered = False
    self.thread = threading.Thread(target=self._run, name="projection-android", daemon=True)

  def start(self) -> AndroidAutoRunner:
    self.thread.start()
    return self

  @property
  def active(self) -> bool:
    return self.state == "active"

  def status(self) -> dict:
    return {"state": self.state, "message": self.message, "phone": self.phone}

  def _set(self, state: str, message: str = "") -> None:
    if (state, message) != (self.state, self.message):
      self.ctx.log(f"android: {state} {message}".rstrip())
    self.state, self.message = state, message

  # ---- connection loop -------------------------------------------------------------------------------------
  def _run(self) -> None:
    while not self.ctx.stop.is_set():
      try:
        wait = self._attempt()
      except Exception as error:  # one failed attempt never ends Android Auto
        self._set("retrying", f"{type(error).__name__}: {error}")
        wait = RETRY_SECONDS
      self.ctx.stop.wait(wait)
    self.close()

  def _attempt(self) -> float:
    if self.session is not None and not self.session.closed.is_set():
      return 1.0
    if self.hotspot_source() is None:
      self._set("waiting_hotspot", "Turn on the Galaxy hotspot so the phone can connect.")
      return RETRY_SECONDS
    if not self.registered:
      self.ctx.bluez.register_android_auto(self._on_bluetooth)
      self.registered = True
    phones = android_candidates(self.ctx.bluez.devices(), self.ctx.settings().android)
    if not phones:
      self._set("waiting_phone", "Pair your Android phone with the comma over Bluetooth.")
      return RETRY_SECONDS
    if self.state not in ("connecting",):
      self._set("waiting_phone", f"Waiting for Android Auto on {phones[0]['name']}…")
    for phone in phones:
      if not phone["connected"] and time.monotonic() - self.woken.get(phone["address"], 0) > WAKE_SECONDS:
        self.woken[phone["address"]] = time.monotonic()
        try:
          self.ctx.bluez.connect(phone["path"])
        except Exception as error:
          self.ctx.log(f"android: could not reach {phone['name']}: {error}")
    return 3.0

  def _on_bluetooth(self, address: str, sock: socket.socket) -> None:
    """The phone opened our Android Auto service: hand it the hotspot and wait for it on TCP."""
    spot = self.hotspot_source()
    if spot is None or (self.session is not None and not self.session.closed.is_set()):
      sock.close()
      return
    names = {d["address"]: d["name"] for d in self.ctx.bluez.devices()}
    self.phone = names.get(address, address)
    self._set("connecting", f"Connecting to {self.phone}…")
    listener = self._listen()
    setup = WirelessSetup(sock, HOTSPOT_ADDRESS, self.port, spot.ssid, spot.passphrase, spot.bssid_text, log=self.ctx.log)
    self.setup = setup
    accepted: list[socket.socket] = []

    def accept():
      listener.settimeout(ACCEPT_SECONDS)
      try:
        conn, _ = listener.accept()
        accepted.append(conn)
      except OSError:
        pass

    acceptor = threading.Thread(target=accept, daemon=True)
    acceptor.start()
    try:
      setup.run()
    except Exception as error:
      self.ctx.log(f"android: setup channel: {error}")
    acceptor.join(ACCEPT_SECONDS)
    if not accepted:
      setup.close()
      self._set("retrying", f"{self.phone} did not connect over Wi-Fi.")
      return
    conn = accepted[0]
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    threading.Thread(target=setup.hold, args=(lambda: self.session is None or self.session.closed.is_set(),),
                     daemon=True).start()
    self._serve(conn)

  def _listen(self) -> socket.socket:
    if self.listener is None:
      listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      listener.bind((self.listen_address, self.port))
      listener.listen(1)
      self.listener = listener
    return self.listener

  def _serve(self, conn: socket.socket) -> None:
    margin_w, margin_h = android_margins(self.ctx.panel)
    index, (width, height) = android_mode(self.ctx.panel)
    cfg = HeadUnitConfig(resolution=index, width=width, height=height, margin_width=margin_w,
                         margin_height=margin_h, density=android_density(self.ctx.panel), audio=self.ctx.settings().audio == "music",
                         bluetooth_address=self.ctx.bluez.adapter_address())
    session = self.session_factory(conn, cfg, self, log=self.ctx.log)
    with self.lock:
      self.session = session
    try:
      session.handshake()
    except Exception as error:
      self._set("retrying", f"Android Auto handshake failed: {error}")
      session.close()
      return
    session.run()

  # ---- head-unit sink ----------------------------------------------------------------------------------------
  def on_video_start(self, session) -> None:
    with self.lock:
      if self.pipe is None:
        width, height = android_mode(self.ctx.panel)[1]
        self.pipe = VideoPipe(self.ctx.publisher, ANDROID_STREAM, width, height, request_keyframe=self._keyframe, log=self.ctx.log)
    self.ctx.audio.request(self.ctx.settings().audio == "music")
    self._set("active", f"{self.phone or 'Android phone'} connected")

  def on_video_config(self, data: bytes) -> None:
    pipe = self.pipe
    if pipe is not None:
      pipe.config(data)

  def on_video_frame(self, data: bytes) -> None:
    pipe = self.pipe
    if pipe is not None:
      pipe.submit(data)

  def on_audio(self, name: str, rate: int, channels: int, pcm: bytes) -> None:
    self.ctx.audio.write(f"android-{name}", rate, channels, pcm)

  def on_audio_stopped(self, name: str) -> None:
    self.ctx.audio.stop(f"android-{name}")

  def on_session_closed(self, session) -> None:
    with self.lock:
      if self.session is not session:
        return
      pipe, self.pipe, self.session = self.pipe, None, None
    if pipe is not None:
      threading.Thread(target=pipe.close, daemon=True).start()
    self.ctx.audio.stop_all()
    self.ctx.audio.request(False)
    self.touches.reset()
    setup, self.setup = self.setup, None
    if setup is not None:
      setup.close()
    if self.state in ("active", "connecting"):
      self._set("retrying", "Android Auto disconnected.")

  def _keyframe(self) -> None:
    session = self.session
    if session is not None:
      session.request_keyframe()

  # ---- input -------------------------------------------------------------------------------------------------
  def touch(self, touches: list[Touch]) -> None:
    session = self.session
    if session is None:
      return
    x0, y0, cw, ch = android_crop(self.ctx.panel)

    def px(x: float, y: float) -> tuple[int, int]:
      return int(round(x0 + x * (cw - 1))), int(round(y0 + y * (ch - 1)))

    for touch in touches:
      if touch.kind == "cancel":
        contacts = [(*px(x, y), slot) for slot, (x, y) in self.touches.active().items()]
        self.touches.reset()
        if contacts:
          session.send_touch(TOUCH_CANCEL, contacts)
        continue
      before = dict(self.touches.active())
      result = self.touches.apply(touch)
      if result is None:
        continue
      slot, down = result
      after = self.touches.active()
      if touch.kind == "move":
        session.send_touch(TOUCH_MOVE, [(*px(x, y), s) for s, (x, y) in after.items()])
      elif down:
        contacts = [(*px(x, y), s) for s, (x, y) in after.items()]
        index = next(i for i, c in enumerate(contacts) if c[2] == slot)
        session.send_touch(TOUCH_DOWN if len(after) == 1 else POINTER_DOWN, contacts, index)
      else:
        contacts = [(*px(x, y), s) for s, (x, y) in before.items() if s != slot] + [(*px(touch.x, touch.y), slot)]
        session.send_touch(TOUCH_UP if not after else POINTER_UP, contacts, len(contacts) - 1)

  def frames(self) -> int:
    pipe = self.pipe
    return pipe.published if pipe is not None else 0

  def problem(self) -> str:
    pipe = self.pipe
    return pipe.error if pipe is not None else ""

  def close(self) -> None:
    session = self.session
    if session is not None:
      session.close()
    if self.listener is not None:
      try:
        self.listener.close()
      except OSError:
        pass
      self.listener = None
    if self.registered:
      self.ctx.bluez.unregister_android_auto()
      self.registered = False
    pipe, self.pipe = self.pipe, None
    if pipe is not None:
      pipe.close()
