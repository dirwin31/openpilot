#!/usr/bin/env python3
"""
Zero-dependency Uniden R4 / R8 / R8w BLE protocol parser.
Parses active radar/laser alert packets and telemetry packets received over BLE notifications.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

DIRECTIONS = {"F": "front", "S": "side", "R": "rear"}

MUTE_STATUS = {
    "1": "not muted",
    "2": "muted",
    "3": "mute memory",
    "4": "auto mute memory",
    "5": "blocked mute",
    "6": "quiet ride mute",
}

LASER_GUNS = {
    "0": "Generic Laser", "1": "LTI 20/20", "2": "Stalker",
    "3": "RIEGL", "4": "Laser Ally", "5": "Kustom",
    "6": "Atlanta", "7": "Laveg", "8": "SL700",
    "9": "SCS-102", "10": "TraffiPat", "11": "Truspeed S",
    "12": "Stealth", "13": "TruCam", "14": "XLR",
    "15": "DragonEye Compact", "16": "DragonEye Full-Size",
    "17": "PoliScan", "18": "Traffistar s350", "19": "Vitronic Poliscan",
}

POI_KINDS = {1: "speed camera", 2: "red light camera", 3: "user mark"}
POI_RECORD_LEN = {1: 13, 2: 12, 3: 10}

def _text(payload: str | bytes | bytearray) -> str:
    if isinstance(payload, str):
        return payload
    return bytes(payload).decode("latin-1", errors="replace")

def _at(parts: list[str], i: int) -> str | None:
    if i < len(parts):
        value = parts[i].strip()
        return value or None
    return None

def _int(value: str | None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _float(value: str | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

@dataclass
class PoiAlert:
    kind: str | None = None
    distance: int | None = None
    speed_limit: int | None = None

@dataclass
class Telemetry:
    voltage: float | None = None
    poi: PoiAlert | None = None
    heading: str | None = None
    speed: int | None = None
    altitude: int | None = None
    gps_status: str | None = None
    warning: str | None = None
    scan_count: int | None = None
    wifi: str | None = None
    brightness: str | None = None
    raw: str = ""

    @property
    def gps_locked(self) -> bool:
        return self.gps_status == "C"

def parse_telemetry(payload: str | bytes | bytearray) -> Telemetry:
    text = _text(payload)
    fields = text.split("&")
    t = Telemetry(raw=text)

    t.voltage = _float(_at(fields, 0))

    poi = _at(fields, 1)
    if poi and poi != "0":
        p = poi.split(",")
        t.poi = PoiAlert(
            kind=_at(p, 0),
            distance=_int(_at(p, 1)),
            speed_limit=_int(_at(p, 2)),
        )

    gps = _at(fields, 2)
    if gps and gps != "0":
        g = gps.split(",")
        t.heading = _at(g, 0)
        t.speed = _int(_at(g, 1))
        t.altitude = _int(_at(g, 2))
        t.gps_status = _at(g, 3)

    warning = _at(fields, 3)
    t.warning = warning if warning != "0" else None
    t.scan_count = _int(_at(fields, 4))
    t.wifi = _at(fields, 5)
    t.brightness = _at(fields, 6)
    return t

@dataclass
class Alert:
    band: str = ""
    strength: int | None = None
    raw_signal: int | None = None
    frequency_ghz: float | None = None
    laser_gun: str | None = None
    direction: str | None = None
    mute_code: str | None = None
    receive_mode: str | None = None
    alert_id: str | None = None
    raw: str = ""

    @property
    def direction_name(self) -> str:
        return DIRECTIONS.get(self.direction or "", self.direction or "unknown")

    @property
    def mute_status(self) -> str:
        return MUTE_STATUS.get(self.mute_code or "", "unknown")

    @property
    def is_muted(self) -> bool:
        return self.mute_code in ("2", "3", "4", "5", "6")

    @property
    def description(self) -> str:
        if self.laser_gun:
            return self.laser_gun
        if self.band in ("RT3", "RT4"):
            return "Gatso"
        if self.frequency_ghz is not None:
            return f"{self.frequency_ghz:g} GHz"
        return self.band or "unknown"

    def __str__(self) -> str:
        return f"{self.band} {self.description} {self.strength}/8 {self.direction_name}"

def parse_alerts(payload: str | bytes | bytearray) -> list[Alert]:
    text = _text(payload)
    alerts: list[Alert] = []

    for segment in text.split("&"):
        segment = segment.strip()
        if not segment or segment == "0":
            continue
        f = segment.split(",")
        if _at(f, 0) == "0":
            continue

        band = (_at(f, 2) or "").upper()
        freq_field = _at(f, 5)

        alerts.append(Alert(
            band=band,
            strength=_int(_at(f, 3)),
            raw_signal=_int(_at(f, 4)),
            frequency_ghz=None if band == "LASER" else _float(freq_field),
            laser_gun=(
                LASER_GUNS.get(freq_field or "", f"unknown laser ({freq_field})")
                if band == "LASER" else None
            ),
            direction=_at(f, 6),
            mute_code=_at(f, 7),
            receive_mode=_at(f, 8),
            alert_id=_at(f, 1),
            raw=segment,
        ))

    return alerts
