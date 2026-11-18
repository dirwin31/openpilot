"""projectiond: CarPlay and Android Auto phones shown beside the comma's own screen.

Runs while phone projection is turned on. Phones reach the comma over Bluetooth, then join the Galaxy
hotspot for video. Pictures are decoded in hardware and published on VisionIPC; the 3X UI and the
StarPort car renderer draw them in half the screen and send touches back.
"""
from __future__ import annotations

import os
import select
import signal
import sys
import threading
import time

from openpilot.starpilot.system.phone_projection import ipc, settings as projection_settings
from openpilot.starpilot.system.phone_projection.audio import MusicOutput, music_library_dir
from openpilot.starpilot.system.phone_projection.layout import android_crop, android_mode, carplay_size, panel_size
from openpilot.starpilot.system.phone_projection.runtime import Context
from openpilot.starpilot.system.phone_projection.video import ANDROID_STREAM, CARPLAY_STREAM, VideoPublisher

STATUS_INTERVAL = 1.0
SETTINGS_INTERVAL = 2.0
SHUTDOWN_TIMEOUT = 5.0


def log(message: str) -> None:
  print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)


def ensure_audio_libraries() -> None:
  """The music server's client library ships in the Spotify bundle; load it like the music relays do."""
  library = str(music_library_dir())
  current = os.environ.get("LD_LIBRARY_PATH", "")
  if library not in current.split(":") and os.path.isdir(library):
    os.environ["LD_LIBRARY_PATH"] = ":".join(filter(None, (library, current)))
    os.execv(sys.executable, [sys.executable, "-m", "openpilot.starpilot.system.phone_projection.daemon"])


def device_type() -> str:
  try:
    from openpilot.common.hardware import HARDWARE
    return HARDWARE.get_device_type()
  except Exception:
    return "tici"


class SettingsCache:
  def __init__(self):
    self.value = projection_settings.load()
    self.loaded = time.monotonic()

  def __call__(self) -> projection_settings.Settings:
    if time.monotonic() - self.loaded > SETTINGS_INTERVAL:
      self.value = projection_settings.load()
      self.loaded = time.monotonic()
    return self.value


def build_status(settings, panel, runners, active) -> dict:
  status = {"enabled": settings.enabled, "side": settings.side, "audio": settings.audio, "panel": list(panel),
            "kind": "", "state": "waiting", "message": "", "phone": ""}
  carplay, android = runners.get("carplay"), runners.get("android")
  for runner in (carplay, android):
    if runner is not None:
      status[f"{runner.kind}"] = runner.status()
  if active is not None:
    status.update(kind=active.kind, state="active", phone=active.status()["phone"], message=active.problem())
    if active.kind == "carplay":
      status.update(stream=CARPLAY_STREAM, frame=list(carplay_size(panel)), crop=[0, 0, *carplay_size(panel)], frames=active.frames())
    else:
      status.update(stream=ANDROID_STREAM, frame=list(android_mode(panel)[1]), crop=list(android_crop(panel)), frames=active.frames())
  else:
    waiting = [r.status() for r in (carplay, android) if r is not None]
    connecting = next((s for s in waiting if s["state"] == "connecting"), None)
    status["state"] = "connecting" if connecting else "waiting"
    status["message"] = (connecting or {}).get("message") or "Connect your iPhone or Android phone."
  return status


def main() -> int:
  ensure_audio_libraries()
  settings = SettingsCache()
  device = device_type()
  panel = ipc.read_target() or panel_size(device)
  from openpilot.starpilot.system.phone_projection.decoder import nv12_layout
  publisher = VideoPublisher({CARPLAY_STREAM: nv12_layout(*carplay_size(panel)), ANDROID_STREAM: nv12_layout(*android_mode(panel)[1])})
  from openpilot.starpilot.system.phone_projection.bluez import Bluez
  ctx = Context(publisher=publisher, panel=panel, settings=settings, audio=MusicOutput(log=log), bluez=Bluez(), log=log,
                fps=30 if device == "mici" else 60)
  signal.signal(signal.SIGTERM, lambda *_: ctx.stop.set())
  signal.signal(signal.SIGINT, lambda *_: ctx.stop.set())
  touches = ipc.TouchReceiver()
  runners = {}
  from openpilot.starpilot.system.phone_projection.carplay_runner import CarPlayRunner
  runners["carplay"] = CarPlayRunner(ctx).start()
  try:
    from openpilot.starpilot.system.phone_projection.android.runner import AndroidAutoRunner
    runners["android"] = AndroidAutoRunner(ctx).start()
  except ImportError as error:
    log(f"android auto unavailable: {error}")
  log(f"projectiond started panel={panel[0]}x{panel[1]} side={settings().side} audio={settings().audio}")
  last_status = 0.0
  active = None
  try:
    while not ctx.stop.is_set():
      active = next((r for r in runners.values() if r.active), None)
      pending = touches.drain()
      if pending and active is not None:
        active.touch(pending)
      now = time.monotonic()
      if now - last_status >= STATUS_INTERVAL:
        last_status = now
        ipc.write_status(build_status(settings(), panel, runners, active))
        if not settings().enabled:
          log("phone projection turned off")
          break
        target = ipc.read_target()
        if target is not None and target != panel:
          log(f"car screen shows the phone at {target[0]}x{target[1]}; restarting at that size")
          break
      try:
        select.select([touches], [], [], max(0.0, min(0.25, last_status + STATUS_INTERVAL - time.monotonic())))
      except (OSError, ValueError):
        ctx.stop.wait(0.01)
  finally:
    ctx.stop.set()
    for runner in runners.values():
      threading.Thread(target=runner.close, daemon=True).start()
    try:
      ipc.STATUS_PATH.unlink()
    except OSError:
      pass
    cleanup = threading.Thread(target=shutdown, args=(ctx, touches), daemon=True)
    cleanup.start()
    cleanup.join(SHUTDOWN_TIMEOUT)
  return 0


def shutdown(ctx, touches) -> None:
  ctx.audio.close()
  touches.close()
  ctx.bluez.close()


if __name__ == "__main__":
  sys.exit(main())
