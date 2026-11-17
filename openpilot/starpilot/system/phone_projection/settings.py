"""Phone projection settings: one JSON file plus an enable flag the manager can check with a single stat."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

ROOT = Path("/data/starpilot/phone_projection")
SIDES = ("left", "right")
AUDIO_ROUTES = ("phone", "music")  # phone: the phone keeps its own Bluetooth audio; music: the comma's music output
ADDRESS_CHARS = set("0123456789ABCDEF:")


@dataclass(frozen=True)
class Settings:
  enabled: bool = False
  side: str = "right"
  audio: str = "phone"
  iphone: str = ""    # preferred iPhone Bluetooth address; empty picks any paired iPhone
  android: str = ""   # preferred Android phone; empty accepts any paired phone


def _address(value) -> str:
  text = str(value or "").strip().upper()
  return text if len(text) == 17 and set(text) <= ADDRESS_CHARS else ""


def validate(values: dict) -> Settings:
  side = values.get("side", "right")
  audio = values.get("audio", "phone")
  return Settings(enabled=bool(values.get("enabled", False)), side=side if side in SIDES else "right",
                  audio=audio if audio in AUDIO_ROUTES else "phone", iphone=_address(values.get("iphone")),
                  android=_address(values.get("android")))


def load(root: Path = ROOT) -> Settings:
  try:
    values = json.loads((root / "settings.json").read_text())
  except (OSError, ValueError):
    values = {}
  settings = validate(values if isinstance(values, dict) else {})
  return replace(settings, enabled=enabled(root))


def save(settings: Settings, root: Path = ROOT) -> Settings:
  settings = validate(asdict(settings))
  root.mkdir(mode=0o755, parents=True, exist_ok=True)
  tmp = root / "settings.json.tmp"
  tmp.write_text(json.dumps(asdict(settings), indent=2))
  os.replace(tmp, root / "settings.json")
  flag = root / "enabled"
  if settings.enabled:
    flag.touch()
  elif flag.exists():
    flag.unlink()
  return settings


def update(root: Path = ROOT, **changes) -> Settings:
  return save(replace(load(root), **changes), root)


def enabled(root: Path = ROOT) -> bool:
  return (root / "enabled").exists()
