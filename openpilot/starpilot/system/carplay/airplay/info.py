"""The /info plist and HID descriptors the receiver advertises, ported from DiPlay's AirPlayInfoPlist."""
from __future__ import annotations

from dataclasses import dataclass, field

MAIN_UUID = "b7e6c5a0-1111-4000-8000-000000000001"
STREAM_MAIN_SCREEN = 110
CARPLAY_FEATURES = 0x615653AEE2
CARPLAY_AUDIO_FEATURES = 0x10004540A00
TOUCH_HID_UID = 0x2A2A2A2A
KNOB_HID_UID = 0x2A2A2A2B
MEDIA_HID_UID = 0x2A2A2A2C
TELEPHONY_HID_UID = 0x2A2A2A2D
TOUCH_CONTACTS = 2


@dataclass
class ReceiverConfig:
  name: str
  device_id: str
  bluetooth_mac: str
  width: int
  height: int
  width_mm: int = 200
  fps: int = 60
  source_version: str = "950.7.1"
  model: str = "comma"
  manufacturer: str = "StarPilot"
  # The iPhone keeps sending audio over its own Bluetooth to the car when the receiver takes no audio.
  disable_audio: bool = True
  view_areas: list[tuple[int, int, int, int]] = field(default_factory=list)  # (x, y, w, h)

  @property
  def features(self) -> int:
    return CARPLAY_FEATURES & ~CARPLAY_AUDIO_FEATURES if self.disable_audio else CARPLAY_FEATURES


def _finger(x_max: int, y_max: int) -> list[int]:
  return [0x05, 0x0D, 0x09, 0x22, 0xA1, 0x02,
          0x09, 0x38, 0x75, 0x08, 0x95, 0x01, 0x81, 0x02,
          0x15, 0x00, 0x25, 0x01, 0x09, 0x33, 0x75, 0x01, 0x95, 0x01, 0x81, 0x02,
          0x95, 0x07, 0x81, 0x03,
          0x05, 0x01, 0x26, x_max & 0xFF, x_max >> 8 & 0xFF, 0x09, 0x30, 0x75, 0x10, 0x95, 0x01, 0x81, 0x02,
          0x26, y_max & 0xFF, y_max >> 8 & 0xFF, 0x09, 0x31, 0x81, 0x02, 0xC0]


def touch_descriptor(x_max: int, y_max: int) -> bytes:
  return bytes([0x05, 0x0D, 0x09, 0x04, 0xA1, 0x01] + _finger(x_max, y_max) * TOUCH_CONTACTS + [0xC0])


KNOB = bytes([0x05, 0x01, 0x09, 0x08, 0xA1, 0x01,
              0x05, 0x09, 0x09, 0x01, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x01, 0x81, 0x02,
              0x05, 0x0C, 0x0A, 0x23, 0x02, 0x0A, 0x24, 0x02, 0x95, 0x02, 0x81, 0x02,
              0x95, 0x05, 0x81, 0x01,
              0x05, 0x01, 0x09, 0x01, 0xA1, 0x00,
              0x09, 0x30, 0x09, 0x31, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x02, 0x81, 0x02, 0xC0,
              0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06, 0xC0])
MEDIA = bytes([0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01, 0x15, 0x00, 0x25, 0x06, 0x05, 0x0C,
               0x0A, 0x00, 0x00, 0x0A, 0xB0, 0x00, 0x0A, 0xB1, 0x00, 0x0A, 0xCD, 0x00,
               0x0A, 0xB5, 0x00, 0x0A, 0xB6, 0x00, 0x0A, 0x9E, 0x02, 0x75, 0x08, 0x95, 0x01, 0x81, 0x00, 0xC0])
TELEPHONY = bytes([0x05, 0x0B, 0x09, 0x07, 0xA1, 0x01, 0x15, 0x00, 0x25, 0x11, 0x05, 0x0B,
                   0x09, 0x00, 0x09, 0x20, 0x09, 0x21, 0x09, 0x26, 0x09, 0x2F,
                   0x09, 0xB0, 0x09, 0xB1, 0x09, 0xB2, 0x09, 0xB3, 0x09, 0xB4, 0x09, 0xB5,
                   0x09, 0xB6, 0x09, 0xB7, 0x09, 0xB8, 0x09, 0xB9, 0x09, 0xBA, 0x09, 0xBB,
                   0x05, 0x07, 0x09, 0x2A, 0x75, 0x08, 0x95, 0x01, 0x81, 0x00, 0xC0])


def hid_device(uid: int, name: str, descriptor: bytes) -> dict:
  return {"hidProductID": 1, "hidVendorID": 2, "hidCountryCode": 0, "uuid": format(uid, "x"), "name": name,
          "displayUUID": MAIN_UUID, "hidDescriptor": descriptor}


def touch_report(contacts: list[tuple[int, float, float, bool]]) -> bytes:
  report = bytearray(6 * TOUCH_CONTACTS)
  for slot in range(TOUCH_CONTACTS):
    offset = slot * 6
    report[offset] = slot
    contact = next((c for c in contacts if c[0] == slot), None)
    if contact is None:
      continue
    _, x, y, down = contact
    report[offset + 1] = 1 if down else 0
    report[offset + 2:offset + 4] = int(round(max(0.0, x))).to_bytes(2, "little")
    report[offset + 4:offset + 6] = int(round(max(0.0, y))).to_bytes(2, "little")
  return bytes(report)


def _area(cfg: ReceiverConfig, area: tuple[int, int, int, int] | None) -> dict:
  x, y, w, h = area or (0, 0, cfg.width, cfg.height)
  return {"widthPixels": w, "heightPixels": h, "originXPixels": x, "originYPixels": y,
          "safeArea": {"widthPixels": w, "heightPixels": h, "originXPixels": x, "originYPixels": y,
                       "drawUIOutsideSafeArea": True}}


def display_entry(cfg: ReceiverConfig) -> dict:
  height_mm = max(1, round(cfg.width_mm * cfg.height / cfg.width))
  entry = {"uuid": MAIN_UUID, "type": STREAM_MAIN_SCREEN, "maxFPS": cfg.fps, "widthPixels": cfg.width,
           "heightPixels": cfg.height, "widthPhysical": cfg.width_mm, "heightPhysical": height_mm,
           "features": 0x08 | 0x02, "primaryInputDevice": 1, "uiAppearanceMode": 0, "uiAppearanceSetting": 0,
           "mapAppearanceMode": 0, "mapAppearanceSetting": 0}
  areas = cfg.view_areas or [None]
  entry["viewAreas"] = [_area(cfg, a) for a in areas]
  entry["initialViewArea"] = 0
  if len(areas) > 1:
    entry["viewAreaTransitionControl"] = True
  return entry


def _audio_formats() -> list[dict]:
  pcm = 0x3FC | 0xC000
  pcm_mono = 0x154 | 0x4000
  opus = 0x70000000
  fmt = lambda t, a, out: {"type": t, "audioType": a, "audioOutputFormats": out}  # noqa: E731
  return [fmt(100, "compatibility", pcm), fmt(101, "compatibility", pcm), fmt(100, "default", pcm | opus),
          fmt(100, "alert", pcm | opus), fmt(100, "media", pcm), fmt(100, "telephony", pcm_mono | opus),
          fmt(100, "speechRecognition", pcm_mono | opus), fmt(101, "default", pcm | opus), fmt(102, "media", 0x800000)]


def info_plist(cfg: ReceiverConfig) -> dict:
  resource = lambda rid: {"resourceID": rid, "transferType": 1, "transferPriority": 100, "takeConstraint": 100,  # noqa: E731
                          "borrowConstraint": 100, "unborrowConstraint": 100}
  info = {
    "sourceVersion": cfg.source_version,
    "features": cfg.features,
    "statusFlags": 4,
    "model": cfg.model,
    "manufacturer": cfg.manufacturer,
    "deviceID": cfg.device_id,
    "bluetoothIDs": [cfg.bluetooth_mac],
    "name": cfg.name,
    "rightHandDrive": False,
    "keepAliveLowPower": False,
    "keepAliveSendStatsAsBody": False,
    "modes": {"resources": [resource(1), resource(2)],
              "appStates": [{"appStateID": 2, "state": False}, {"appStateID": 1, "speechMode": -1},
                            {"appStateID": 3, "state": False}]},
  }
  if not cfg.disable_audio:
    info["audioFormats"] = _audio_formats()
  info["extendedFeatures"] = ["vocoderInfo", "enhancedRequestCarUI"]
  info["displays"] = [display_entry(cfg)]
  info["hidDevices"] = [hid_device(TOUCH_HID_UID, "StarPilot Touchscreen", touch_descriptor(cfg.width, cfg.height)),
                        hid_device(KNOB_HID_UID, "StarPilot Knob", KNOB),
                        hid_device(MEDIA_HID_UID, "StarPilot Media", MEDIA),
                        hid_device(TELEPHONY_HID_UID, "StarPilot Telephony", TELEPHONY)]
  return info
