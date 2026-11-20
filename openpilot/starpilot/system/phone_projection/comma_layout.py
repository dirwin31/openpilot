"""The comma 3/3X Big layout used while a phone screen takes the other half of the screen.

Saved beside the phone screen settings, in screen pixels of the 1080 x 1080 comma half. At runtime it is
moved into the Big layout's anchored positions so the normal onroad view draws it unchanged.
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path

from openpilot.starpilot.system.phone_projection import settings as projection_settings
from openpilot.starpilot.ui.narrow_layout import arrange_narrow
from openpilot.starpilot.ui.onroad_customization import (
  PROFILES, ROAD_BASE_WIDTH, customization_metadata, default_document, validate_document, widget_size,
)

WIDTH = HEIGHT = 1080
MAX_BYTES = 12288
DOCUMENT_KEY = 'comma_layout'
FILE_NAME = 'layout.json'
SOURCE_KEYS = ('speedSources', 'speedSourcesCard')


class LayoutSource:
  """Path adapter for read_saved/commit_exact."""
  def __init__(self, root: Path = projection_settings.ROOT):
    self.path = Path(root) / FILE_NAME

  def get_param_path(self, key):
    if key != DOCUMENT_KEY:
      raise ValueError('Unknown phone screen layout')
    return str(self.path)

  def prepare(self):
    self.path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)


def _shift(key: str) -> float:
  return PROFILES['large']['widgets'][key].get('anchorShift', 0) * (WIDTH - ROAD_BASE_WIDTH)


def _size(key: str, placed: dict) -> tuple[float, float]:
  return widget_size({'layouts': {'large': {key: placed}}}, 'large', key)


def anchored(layout: dict) -> dict:
  """Screen pixels of the comma half to the Big layout's anchored positions."""
  return {key: {**placed, 'x': placed['x'] - _shift(key)} for key, placed in layout.items()}


def defaults() -> dict:
  layout = copy.deepcopy(default_document()['layouts']['large'])
  for key, placed in layout.items():
    placed['x'] += _shift(key)

  def boxed(key):
    box = PROFILES['large']['widgets'][key].get('box')
    return box and {**box, 'maxWidth': min(box['maxWidth'], WIDTH - 60), 'maxHeight': min(box['maxHeight'], HEIGHT - 60), 'aspect': 4 / 3}
  arrange_narrow(layout, _size, WIDTH, HEIGHT, boxed)
  torque = layout['torque_bar']
  torque['x'] = round((WIDTH - _size('torque_bar', torque)[0]) / 2)
  return layout


def metadata() -> dict:
  """Editor metadata: the Big profile cut down to the comma half, with no anchoring."""
  result = customization_metadata()
  profile = result['profiles']['large']
  placements = defaults()
  profile.update(label='Big UI beside a phone', width=WIDTH, height=HEIGHT,
                 bounds={'x': 30, 'y': 30, 'width': WIDTH - 60, 'height': HEIGHT - 60})
  profile.pop('quickSelectShelves', None)
  for key, widget in profile['widgets'].items():
    for field in ('bounds', 'anchorShift', 'expandable'):
      widget.pop(field, None)
    if widget.get('box'):
      widget['box'] = {**widget['box'], 'maxWidth': min(widget['box']['maxWidth'], WIDTH - 60),
                       'maxHeight': min(widget['box']['maxHeight'], HEIGHT - 60)}
    widget['default'] = {**widget['default'], **{axis: placements[key][axis] for axis in ('x', 'y', 'width', 'height') if axis in placements[key]}}
  # The torque arc is drawn for the whole screen width; give it the comma half's full width.
  profile['widgets']['torque_bar']['bounds'] = {'x': 0, 'y': 30, 'width': WIDTH, 'height': HEIGHT - 60}
  result['profiles'] = {'large': profile}
  result['split'] = True
  return result


def validate(layout, order=None, base: dict | None = None) -> tuple[dict, list | None]:
  """(layout, layer order) checked like the Big layout with base's speed-limit sources, and inside the comma half."""
  document = default_document()
  document.update({key: base[key] for key in SOURCE_KEYS if base and key in base})
  document['layouts']['large'] = layout
  if order is not None:
    document['widgetOrder'] = {'large': order}
  result = validate_document(document)
  checked = result['layouts']['large']
  for key, placed in checked.items():
    w, h = widget_size(result, 'large', key)  # the cruise card's size already includes the sources
    if not (0 <= placed['x'] and placed['x'] + w <= WIDTH and 0 <= placed['y'] and placed['y'] + h <= HEIGHT):
      raise ValueError('Widget outside the comma half')
  return checked, result.get('widgetOrder', {}).get('large')


def encode(layout: dict, order: list | None) -> bytes:
  return json.dumps({'version': 1, 'layout': layout, **({'widgetOrder': order} if order else {})},
                    separators=(',', ':'), allow_nan=False).encode()


def decode(raw, base: dict | None = None) -> tuple[dict, list | None]:
  value = json.loads(raw)
  if type(value) is not dict or value.get('version') != 1 or not set(value) <= {'version', 'layout', 'widgetOrder'}:
    raise ValueError('Invalid phone screen layout')
  return validate(value.get('layout'), value.get('widgetOrder'), base)


def checked_defaults(base: dict | None = None) -> tuple[dict, list | None]:
  try:
    return validate(defaults(), None, base)
  except ValueError:
    return defaults(), None


def customization(document: dict, layout: dict, order: list | None) -> dict:
  """The full Big document with the split arrangement, ready for the normal onroad view."""
  result = {**document, 'layouts': {**document['layouts'], 'large': anchored(layout)}}
  if order:
    result['widgetOrder'] = {**document.get('widgetOrder', {}), 'large': order}
  return result


class LayoutCache:
  """Rereads the saved split layout at most once a second, only when the file or the sources setting changed."""
  CHECK_SECONDS = 1.0

  def __init__(self, root: Path = projection_settings.ROOT):
    self.path = Path(root) / FILE_NAME
    self._checked = 0.0
    self._stamp = None
    self._value: tuple[dict, list | None] | None = None
    self._sources: tuple | None = None
    self._base: dict | None = None

  def get(self, base: dict | None = None) -> tuple[dict, list | None]:
    now = time.monotonic()
    sources = tuple(base.get(key) for key in SOURCE_KEYS) if base else (None, None)
    if sources != self._sources:
      self._sources, self._base, self._value = sources, base, None
    if self._value is None or now - self._checked >= self.CHECK_SECONDS:
      self._checked = now
      try:
        info = os.stat(self.path)
        stamp = info.st_ino, info.st_size, info.st_mtime_ns
      except OSError:
        stamp = None
      if stamp != self._stamp or self._value is None:
        self._stamp = stamp
        self._value = self._read() if stamp is not None else checked_defaults(self._base)
    return self._value

  def _read(self) -> tuple[dict, list | None]:
    try:
      if self.path.stat().st_size > MAX_BYTES:
        raise ValueError('Phone screen layout too large')
      return decode(self.path.read_bytes(), self._base)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
      return checked_defaults(self._base)
