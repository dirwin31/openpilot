"""Android Auto session supervisor: owns every stage, deadline, retry and cleanup.

One explicitly started session runs in one worker thread:

  idle -> connecting_bluetooth -> discovering -> rfcomm -> wifi_start -> wifi_info
       -> joining_wifi -> connecting_tcp -> authenticating -> negotiating -> streaming
  streaming <-> suspended (head unit showing its own screen)
  failure -> cleanup -> backoff -> retry          stop -> cleanup -> idle

Each attempt has a generation number; Stop cancels immediately (sockets are
closed from the caller's thread to unblock I/O). Between retries the projection
network is released without restoring the previous Wi-Fi, which happens once,
on Stop. Nothing here runs as root or touches vehicle control.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from openpilot.starpilot.system.android_auto import identity as identity_store
from openpilot.starpilot.system.android_auto.bootstrap import NAMES, BootstrapError, WirelessBootstrap
from openpilot.starpilot.system.android_auto.frame_source import FrameConsumer, FrameRequest, SyntheticFrames
from openpilot.starpilot.system.android_auto.session import AuthenticationRejected, PeerRequestedStop, ProjectionSession

BACKOFF_SECONDS = (2.0, 4.0, 8.0, 15.0, 30.0)
STABLE_SESSION_SECONDS = 30.0
PEER_STOP_RETRY_SECONDS = 10.0
FRAME_MAX_AGE = 0.5          # never send a UI frame older than this
UNAVAILABLE_AFTER = 3.0      # focused but no fresh UI frame for this long -> "unavailable" card
SDP_SETTLE = (1.5, 2.2, 3.0)
TCP_ATTEMPTS = 6
MAX_LOG_FILES = 20

STATE_LABELS = {
  "idle": "off", "connecting_bluetooth": "connecting to car", "discovering": "finding android auto",
  "rfcomm": "starting wireless setup", "wifi_start": "waiting for car", "wifi_info": "getting car wi-fi",
  "joining_wifi": "joining car wi-fi", "connecting_tcp": "connecting", "authenticating": "authenticating",
  "negotiating": "negotiating video", "streaming": "projecting", "suspended": "car showing its own screen",
  "backoff": "retrying", "stopping": "stopping", "error": "error",
}


class Cancelled(Exception):
  pass


class EventLog:
  """Sanitized JSONL session log under /data/android_auto/logs, plus the recent tail in memory."""

  def __init__(self, directory: Path | None = None):
    self.directory = directory or identity_store.LOG_DIR
    self.handle = None
    self.recent: deque[dict] = deque(maxlen=40)
    self.lock = threading.Lock()

  def open(self) -> None:
    try:
      self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
      logs = sorted(self.directory.glob("session-*.jsonl"))
      for old in logs[:max(0, len(logs) - MAX_LOG_FILES + 1)]:
        old.unlink(missing_ok=True)
      path = self.directory / f"session-{identity_store.timestamp()}.jsonl"
      fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
      self.handle = os.fdopen(fd, "a")
    except OSError:
      self.handle = None

  def close(self) -> None:
    with self.lock:
      if self.handle is not None:
        self.handle.close()
        self.handle = None

  def __call__(self, name: str, **values) -> None:
    record = {"t": datetime.now(UTC).isoformat(timespec="milliseconds"), "event": name, **values}
    with self.lock:
      self.recent.append(record)
      if self.handle is not None:
        try:
          self.handle.write(json.dumps(record, default=str) + "\n")
          self.handle.flush()
        except OSError:
          pass


class Supervisor:
  def __init__(self, bluez_factory=None, lease_factory=None, bluetooth_client=None, frame_path: str | None = None,
               synthetic: bool = False):
    self._synthetic = synthetic
    self._bluez_factory = bluez_factory
    self._lease_factory = lease_factory
    self._bt_client = bluetooth_client
    self._frame_path = frame_path
    self._lock = threading.RLock()
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None
    self._generation = 0
    self._sockets: set[socket.socket] = set()
    self._bluez = None
    self._pairing_until = 0.0
    self.log = EventLog()
    self.config = identity_store.load_config()
    self._status: dict = {"state": "idle", "detail": "", "error": "", "last_stage": "", "attempt": 0,
                          "retry_in": 0.0, "mode": None, "stats": {}, "head_unit": {}, "identity": "", "running": False}

  # ------------------------------------------------------------------ status

  def _set(self, **values) -> None:
    with self._lock:
      self._status.update(values)

  def _stage(self, state: str, detail: str = "") -> None:
    self._check_cancel()
    self._set(state=state, detail=detail, last_stage=state)
    self.log("stage", state=state, detail=detail)

  def _check_cancel(self) -> None:
    if self._stop.is_set():
      raise Cancelled()

  def status(self) -> dict:
    with self._lock:
      status = dict(self._status)
    status["label"] = STATE_LABELS.get(status["state"], status["state"])
    status["receiver_address"] = self.config["receiver_address"]
    status["receiver_name"] = self.config["receiver_name"]
    status["pairing_ready"] = time.monotonic() < self._pairing_until
    status["recent"] = list(self.log.recent)[-8:]
    return status

  # ---------------------------------------------------------------- commands

  def start(self) -> None:
    with self._lock:
      if self._thread is not None and self._thread.is_alive():
        if self._stop.is_set():
          raise RuntimeError("Android Auto is still stopping; try again in a moment")
        return
      if not self.config["receiver_address"]:
        raise RuntimeError("Choose your car first")
      identity = identity_store.load_identity()  # fail fast with a clear message
      self._stop.clear()
      self._generation += 1
      self._set(state="connecting_bluetooth", detail="", error="", attempt=0, retry_in=0.0, running=True,
                identity=identity_store.expiry_warning(identity))
      self._thread = threading.Thread(target=self._run, args=(self._generation,), name="android_auto_session", daemon=True)
      self._thread.start()

  def stop(self, timeout: float = 8.0, graceful: float = 3.0) -> None:
    """Cancel the session: let a streaming session say goodbye to the car, then force it."""
    thread = self._thread
    self._stop.set()
    if thread is not None:
      thread.join(timeout=graceful)
    self._close_sockets()  # unblocks any stage still waiting on Bluetooth or the network
    if thread is not None:
      thread.join(timeout=max(0.0, timeout - graceful))
    with self._lock:
      if thread is not None and thread.is_alive():
        return  # still cleaning up; start() refuses until it finishes
      self._thread = None
      if self._status["state"] != "error":
        self._set(state="idle", detail="")
      self._set(running=False, retry_in=0.0)

  def select_receiver(self, address: str, name: str = "") -> None:
    from openpilot.starpilot.system.android_auto.bt_sockets import normalize_address
    address = normalize_address(address)
    with self._lock:
      if self._thread is not None and self._thread.is_alive():
        raise RuntimeError("Stop Android Auto before changing the car")
      self.config.update(receiver_address=address, receiver_name=name or address)
      identity_store.save_config(self.config)
    self._unselect_car_audio(address)

  def prepare_pairing(self, seconds: float = 180.0) -> None:
    """Present as a phone (HFP gateway, smartphone class) while the car pairs."""
    bluez = self._phone()
    if self.config.get("phone_class", True):
      bluez.acquire()
    self._pairing_until = time.monotonic() + seconds
    self.log("pairing_window", seconds=seconds)

  def devices(self) -> list[dict]:
    try:
      devices = self._phone().devices()
    except Exception as error:
      raise RuntimeError(f"Bluetooth unavailable: {error}") from error
    return [{key: device[key] for key in ("address", "name", "paired", "connected", "android_auto")}
            for device in devices if device["paired"]]

  def maintain(self) -> None:
    """Periodic housekeeping from the daemon thread: end an unused pairing window."""
    if self._pairing_until and time.monotonic() >= self._pairing_until:
      self._pairing_until = 0.0
      if self._bluez is not None and not (self._thread is not None and self._thread.is_alive()):
        self._bluez.release()

  def close(self) -> None:
    self.stop()
    if self._bluez is not None:
      try:
        self._bluez.close()
      except Exception:
        pass
      self._bluez = None

  # ----------------------------------------------------------------- helpers

  def _phone(self):
    if self._bluez is None:
      if self._bluez_factory is None:
        from openpilot.starpilot.system.android_auto.bluez_phone import BluezPhone
        self._bluez = BluezPhone(self.log)
      else:
        self._bluez = self._bluez_factory(self.log)
    return self._bluez

  def _lease(self):
    if self._lease_factory is None:
      from openpilot.starpilot.system.android_auto.network import NetworkLease
      return NetworkLease(self.log, self.config["wifi_interface"])
    return self._lease_factory(self.log, self.config["wifi_interface"])

  def _unselect_car_audio(self, address: str) -> None:
    try:
      client = self._bt_client
      if client is None:
        from openpilot.starpilot.system.bluetooth import BluetoothClient
        client = BluetoothClient(timeout=5.0)
      if client.status().selected_audio.upper() == address.upper():
        client.select_audio("")
        self.log("car_audio_unselected")
    except Exception:
      pass

  def _track(self, sock: socket.socket) -> socket.socket:
    with self._lock:
      self._sockets.add(sock)
    if self._stop.is_set():
      self._close_sockets()
      raise Cancelled()
    return sock

  def _close_sockets(self) -> None:
    with self._lock:
      sockets, self._sockets = self._sockets, set()
    for sock in sockets:
      try:
        sock.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      try:
        sock.close()
      except OSError:
        pass

  def _wait(self, seconds: float) -> None:
    if self._stop.wait(seconds):
      raise Cancelled()

  # ---------------------------------------------------------------- session

  def _run(self, generation: int) -> None:
    self.log.open()
    self.log("session_start", receiver=self.config["receiver_name"], generation=generation)
    lease = self._lease()
    attempt = 0
    try:
      while not self._stop.is_set():
        started = time.monotonic()
        peer_stopped = False
        try:
          self._attempt(lease)
          self.log("session_ended")
        except Cancelled:
          break
        except PeerRequestedStop as error:
          self._set(error="", detail=str(error))
          self.log("session_ended", reason=str(error))
          peer_stopped = True
        except Exception as error:
          if self._stop.is_set():
            break  # I/O torn down by Stop; not a failure to report
          stage = error.stage if isinstance(error, BootstrapError) else self._status["last_stage"]
          message = self._describe_error(stage, error)
          self._set(error=message)
          self.log("attempt_failed", stage=stage, error=message, kind=type(error).__name__)
        finally:
          self._close_sockets()
          try:
            lease.release(restore=False)
          except Exception as error:
            self.log("wifi_release_failed", error=str(error))
        if self._stop.is_set():
          break
        attempt = 0 if time.monotonic() - started > STABLE_SESSION_SECONDS else attempt + 1
        delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
        if peer_stopped:
          delay = max(delay, PEER_STOP_RETRY_SECONDS)  # the car ended projection itself; do not bounce straight back
        self._set(state="backoff", attempt=attempt, retry_in=delay, mode=None)
        try:
          self._wait(delay)
        except Cancelled:
          break
    finally:
      self._set(state="stopping", detail="")
      try:
        lease.release(restore=True)
      except Exception as error:
        self.log("wifi_release_failed", error=str(error))
      if self._bluez is not None:
        try:
          self._bluez.release()
        except Exception as error:
          self.log("bluetooth_release_failed", error=str(error))
      self.log("session_stop")
      self.log.close()
      self._set(state="idle", detail="", running=False, retry_in=0.0, mode=None)

  @staticmethod
  def _describe_error(stage: str, error: Exception) -> str:
    if isinstance(error, AuthenticationRejected):
      return "The car rejected the Android Auto identity; it may have expired"
    text = str(error) or type(error).__name__
    if stage == "authenticating" and "certificate" in text.lower():
      text += " (set verify_head_unit false in config.json to test without verifying the car)"
    return f"{STATE_LABELS.get(stage, stage)}: {text}"[:300]

  def _attempt(self, lease) -> None:
    config = self.config
    address = config["receiver_address"]
    ident = identity_store.load_identity()

    self._stage("connecting_bluetooth", config["receiver_name"])
    bluez = self._phone()
    if config.get("phone_class", True):
      bluez.acquire()
    device = bluez.device(address)
    if device is None or not device["paired"]:
      raise RuntimeError("The car is not paired with this comma; pair it in Bluetooth settings")
    bluez.connect_device(address)
    self._wait(1.0)

    from openpilot.starpilot.system.android_auto import bt_sockets, sdp
    channel = int(config.get("rfcomm_channel") or 0)
    self._stage("discovering")
    if not channel:
      last_error: Exception | None = None
      for settle in SDP_SETTLE:
        self._check_cancel()
        try:
          with self._track(bt_sockets.connect_l2cap(address, sdp.SDP_PSM)) as sdp_sock:
            self._wait(settle)
            channel = sdp.query_channel(sdp_sock)
          break
        except (OSError, sdp.SdpError) as error:
          last_error = error
          self.log("sdp_retry", error=str(error), settle=settle)
          self._wait(0.35)
      if not channel:
        raise RuntimeError(f"Could not find the car's Android Auto service: {last_error}")
    self.log("rfcomm_channel", channel=channel, source="config" if config.get("rfcomm_channel") else "sdp")

    self._stage("rfcomm", f"channel {channel}")
    rfcomm = self._track(bt_sockets.connect_rfcomm(address, channel))
    boot = WirelessBootstrap(rfcomm, self._bootstrap_log, device_serial=config["device_name"],
                             version_status=int(config.get("version_status", 0)))
    self._set(state="wifi_start")
    result = boot.run(lambda credentials: lease.acquire(credentials, cancelled=self._stop.is_set), cancelled=self._stop.is_set)
    self._set(head_unit=result.head_unit)
    keepalive_stop = threading.Event()
    threading.Thread(target=boot.keepalive, args=(keepalive_stop,), name="aa_rfcomm_keepalive", daemon=True).start()
    try:
      self._project(result, lease, ident)
    finally:
      keepalive_stop.set()

  def _bootstrap_log(self, name: str, **values) -> None:
    self.log(name, **values)
    if name == "bootstrap_tx" and values.get("message") == NAMES[2]:
      self._set(state="wifi_info", last_stage="wifi_info")
    elif name == "wifi_joining":
      self._set(state="joining_wifi", last_stage="joining_wifi", detail=str(values.get("ssid", "")))

  def _connect_tcp(self, result, lease) -> socket.socket:
    self._stage("connecting_tcp", f"{result.endpoint.ip}:{result.endpoint.port}")
    last_error: Exception | None = None
    for attempt in range(TCP_ATTEMPTS):
      self._check_cancel()
      try:
        sock = socket.create_connection((result.endpoint.ip, result.endpoint.port), timeout=5.0,
                                        source_address=(lease.local_ip, 0) if lease.local_ip else None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return self._track(sock)
      except OSError as error:
        last_error = error
        self.log("tcp_retry", attempt=attempt + 1, error=str(error))
        self._wait(1.0 + attempt * 0.5)
    raise RuntimeError(f"Car did not accept the projection connection: {last_error}")

  def _project(self, result, lease, ident) -> None:
    config = self.config
    sock = self._connect_tcp(result, lease)
    ca = ident.root if config.get("verify_head_unit", True) else None
    session = ProjectionSession(sock, ident.cert, ident.key, self.log, ca)
    self._stage("authenticating")
    session.authenticate()
    self._stage("negotiating")
    mode = session.start(config["device_name"], "comma.ai")
    self._set(mode=mode.as_dict(), error="")
    self.log("projection_ready", mode=mode.as_dict(), head_unit_subject=session.head_unit_subject)

    from openpilot.starpilot.system.android_auto.encoder import H264Encoder
    encoder = H264Encoder(mode.width, mode.height, fps=mode.fps, bitrate_kbps=config["bitrate_kbps"])
    if self._synthetic:
      consumer = SyntheticFrames()
    else:
      consumer = FrameConsumer(self._frame_path) if self._frame_path else FrameConsumer()
    interval = 1.0 / config["fps"]
    consumer.configure(FrameRequest(mode.width, mode.height, mode.margin_width, mode.margin_height, int(interval * 1e6)))
    try:
      self._stream(session, encoder, consumer, lease, interval)
    finally:
      consumer.close()
      encoder.close()
      if self._stop.is_set():
        try:
          session.shutdown()
        except Exception:
          pass

  def _stream(self, session: ProjectionSession, encoder, consumer, lease, interval: float) -> None:
    started = time.monotonic()
    last_fresh = time.monotonic()
    last_unavailable = 0.0
    next_check = 0.0
    ages: deque[float] = deque(maxlen=120)
    sent_times: deque[float] = deque(maxlen=60)
    unavailable = None
    self._stage("streaming")
    while not self._stop.is_set():
      now = time.monotonic()
      consumer.demand(1.0)
      session.pump(0.01 if session.can_send() else 0.05)
      session.check_progress()
      state = "streaming" if session.focused else "suspended"
      if self._status["state"] != state:
        self._set(state=state)
      if session.can_send():
        frame = consumer.latest()
        if frame is not None:
          age = now - frame.captured_ns / 1e9
          if age <= FRAME_MAX_AGE:
            data, keyframe = encoder.encode_rgba(frame.data, keyframe=session.needs_keyframe)
            session.send_frame(data, frame.captured_ns // 1000, keyframe=keyframe)
            ages.append(age + encoder.last_encode_ms / 1000)
            sent_times.append(now)
            last_fresh = now
        elif now - last_fresh > UNAVAILABLE_AFTER and now - last_unavailable > 1.0:
          # The UI stopped producing frames (e.g. the offroad render budget ran
          # out). Say so on the car instead of freezing on an old image.
          if unavailable is None:
            unavailable = self._unavailable_frame(consumer.request)
          data, keyframe = encoder.encode_rgba(unavailable, keyframe=True)
          session.send_frame(data, time.monotonic_ns() // 1000, keyframe=keyframe)
          last_unavailable = now
      if now >= next_check:
        next_check = now + 1.0
        if not lease.still_connected():
          raise RuntimeError("Lost the car's Wi-Fi network")
        window = [t for t in sent_times if now - t <= 5.0]
        ordered = sorted(ages)
        stats = {**session.stats(), "fps": round(len(window) / 5.0, 1), "encode_ms": round(encoder.last_encode_ms, 1),
                 "frame_age_p95_ms": round(ordered[int(len(ordered) * 0.95) - 1] * 1000) if ordered else None,
                 "uptime_s": round(now - started)}
        self._set(stats=stats)
        if int(now - started) % 30 == 0:
          self.log("stats", **stats)
      if not session.can_send():
        time.sleep(0.005)
      else:
        time.sleep(max(0.0, min(interval / 4, 0.01)))

  @staticmethod
  def _unavailable_frame(request: FrameRequest | None) -> bytes:
    assert request is not None
    try:
      import cv2
      import numpy as np
      image = np.zeros((request.height, request.width, 4), np.uint8)
      image[..., 3] = 255
      scale = request.height / 480
      text = "StarPilot display unavailable"
      size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, max(1, int(2 * scale)))[0]
      cv2.putText(image, text, ((request.width - size[0]) // 2, (request.height + size[1]) // 2), cv2.FONT_HERSHEY_SIMPLEX,
                  scale, (255, 255, 255, 255), max(1, int(2 * scale)), cv2.LINE_AA)
      return image.tobytes()
    except ImportError:
      return bytes(request.width * request.height * 4)
