"""Places shared from a phone, looked up with Places API (New) Text Search on the user's own Google key."""

from __future__ import annotations

import json
import math
from pathlib import Path

import requests

from openpilot.starpilot.navigation.google_tiles import FAILURE_BUDGET, FAILURE_NETWORK, FAILURE_SERVER, GoogleBudget, _body, classify

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = "places.displayName,places.formattedAddress,places.location"
FREE_SEARCHES = 5_000  # Text Search Pro: the SKU for name, address and location
BIAS_RADIUS_M = 50_000.0
TIMEOUT = (3.0, 8.0)
MAX_RESPONSE = 256 * 1024

MESSAGES = {
  "key": "Google rejected the key for place lookups. Check that its restrictions allow Places API (New).",
  "billing": "Billing is not turned on for this Google Cloud project. Add a billing account, then test again.",
  "disabled": "Places API (New) is not enabled for this key's project. Enable it in Google Cloud, then test again.",
  FAILURE_SERVER: "Google could not look up this place just now.",
  FAILURE_NETWORK: "The comma could not reach Google.",
  FAILURE_BUDGET: "This month's free Google place lookups are used up.",
}


class GooglePlacesFailed(Exception):
  def __init__(self, failure: str):
    super().__init__(failure)
    self.failure = failure


def places_budget(root: Path | None = None) -> GoogleBudget:
  return GoogleBudget(root, file="places-usage.json", month_free=FREE_SEARCHES, day_free=None)


def text_search(http, key: str, query: str, budget: GoogleBudget, proximity: tuple[float, float] | None = None, timeout=TIMEOUT) -> dict | None:
  """Google's top place for the text as {name, address, latitude, longitude}, None when it finds nothing."""
  if not budget.spend():
    raise GooglePlacesFailed(FAILURE_BUDGET)
  body: dict = {"textQuery": query, "pageSize": 1}
  if proximity is not None:
    body["locationBias"] = {"circle": {"center": {"latitude": proximity[1], "longitude": proximity[0]}, "radius": BIAS_RADIUS_M}}
  try:
    with http.post(SEARCH_URL, json=body, headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK},
                   timeout=timeout, stream=True) as response:
      status, raw = response.status_code, _body(response, MAX_RESPONSE)
  except requests.RequestException:
    raise GooglePlacesFailed(FAILURE_NETWORK) from None
  except ValueError:
    raise GooglePlacesFailed(FAILURE_SERVER) from None
  failure = classify(status, raw)
  if failure is not None:
    raise GooglePlacesFailed(failure)
  try:
    rows = json.loads(raw).get("places", [])
    if not rows:
      return None
    row = rows[0]
    latitude, longitude = float(row["location"]["latitude"]), float(row["location"]["longitude"])
    if not math.isfinite(latitude) or not math.isfinite(longitude):
      raise ValueError
    return {"name": str(row.get("displayName", {}).get("text") or row.get("formattedAddress") or query),
            "address": row.get("formattedAddress"), "latitude": latitude, "longitude": longitude}
  except (ValueError, TypeError, KeyError, IndexError, AttributeError):
    raise GooglePlacesFailed(FAILURE_SERVER) from None


def test_places(key: str, http=requests, budget: GoogleBudget | None = None) -> dict:
  """One place lookup, the same request a shared place uses."""
  try:
    text_search(http, key, "Googleplex, 1600 Amphitheatre Pkwy, Mountain View, CA", budget or places_budget())
  except GooglePlacesFailed as error:
    return {"ok": False, "message": MESSAGES.get(error.failure, MESSAGES[FAILURE_SERVER])}
  return {"ok": True, "message": "Google place lookups work with this key."}
