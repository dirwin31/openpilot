"""Per-car tuning, picked by fingerprint so nobody has to choose their car from a list.

A car is recognised from what its head unit reported on a previous connection. Cars without a rule
keep the standard behaviour exactly. Add a rule to ship a variation for one car; ``car_profile_override``
in the config pins a profile (or "default") for a single comma while testing with someone.
"""
import re

# connect_delay_s: wait before the Bluetooth connect, for head units that need time to wake
# max_window: most unacknowledged video messages in flight; phone_services: open the car-data and Bluetooth channels
# like a phone does; phone_frames: send picture NAL units only, without AUD or repeated SPS/PPS
DEFAULTS = {"connect_delay_s": 0.0, "max_window": 2, "phone_services": False, "phone_frames": False}

FINGERPRINT_KEYS = ("head_unit_make", "head_unit_model", "car_make", "car_model", "car_year")

# {"id": "...", "label": "...", "match": {fingerprint key: regex, ...}, "tuning": {key from DEFAULTS: value}}
RULES: list[dict] = [
  {"id": "aawireless", "label": "AAWireless v1", "match": {},
   "tuning": {"max_window": 30, "phone_services": True, "phone_frames": True}},
]


def profile_ids() -> list[str]:
  return [rule["id"] for rule in RULES]


def valid_override(value: str) -> bool:
  return value in ("", "default", *profile_ids())


def fingerprint_from(head_unit: dict, address: str) -> dict:
  found = {key: str(head_unit[key])[:80] for key in FINGERPRINT_KEYS if head_unit.get(key)}
  return {"address": address.upper(), **found} if found else {}


def _matches(rule: dict, fingerprint: dict) -> bool:
  match = rule.get("match") or {}
  return bool(match) and all(re.search(pattern, str(fingerprint.get(key, "")), re.IGNORECASE) for key, pattern in match.items())


def resolve(config: dict) -> tuple[str, str]:
  """(profile id, source) where source is "override", "detected" or "standard"."""
  override = config.get("car_profile_override", "")
  if override == "default":
    return "default", "override"
  if override in profile_ids():
    return override, "override"
  fingerprint = config.get("car_fingerprint") or {}
  if fingerprint.get("address") == str(config.get("receiver_address", "")).upper():
    for rule in RULES:
      if _matches(rule, fingerprint):
        return rule["id"], "detected"
  return "default", "standard"


def tuning(config: dict) -> dict:
  profile, _ = resolve(config)
  rule = next((item for item in RULES if item["id"] == profile), {})
  return {**DEFAULTS, **{key: value for key, value in (rule.get("tuning") or {}).items() if key in DEFAULTS}}


def summary(config: dict) -> dict:
  """What the UI may show: nothing at all while the standard behaviour applies."""
  profile, source = resolve(config)
  rule = next((item for item in RULES if item["id"] == profile), None)
  return {"car_profile": profile, "car_profile_source": source, "car_profile_label": rule["label"] if rule else "",
          "car_profile_ids": profile_ids()}
