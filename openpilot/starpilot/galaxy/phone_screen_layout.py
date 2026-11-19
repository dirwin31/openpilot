"""Galaxy owner for the comma 3/3X Big layout used beside a phone screen."""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable

from openpilot.starpilot.galaxy.onroad_layout import LayoutChanged
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.system.phone_projection import comma_layout
from openpilot.starpilot.system.phone_projection import settings as projection_settings
from openpilot.starpilot.ui.onroad_customization import read_customization

SHARED_FIELDS = ('version', 'palette', 'speedSources', 'speedSourcesCard', 'speedAlign', 'clock24Hour', 'reduceMotion',
                 'showMaxSpeed', 'speedDisplayOffset')


def _revision(raw: bytes | None) -> str:
  return hashlib.sha256(b'StarPilot phone screen layout v1\0' + (b'\0' if raw is None else b'\1' + raw)).hexdigest()


def _document(base: dict, layout: dict, order: list | None) -> dict:
  """The editor's document: the shared theme with only the Big layout, arranged for the comma half."""
  document = {key: copy.deepcopy(base[key]) for key in SHARED_FIELDS if key in base}
  document.update(layouts={'large': copy.deepcopy(layout)}, widgetColors={'large': copy.deepcopy(base['widgetColors']['large'])},
                  roadColors={'large': copy.deepcopy(base['roadColors']['large'])})
  if order:
    document['widgetOrder'] = {'large': list(order)}
  return document


class PhoneScreenLayoutOwner:
  def __init__(self, params, parked: Callable[[], bool], large_screen: Callable[[], bool], vehicle_params=lambda: None,
               source: comma_layout.LayoutSource | None = None, settings_root=projection_settings.ROOT):
    self.params, self.parked, self.large_screen, self.vehicle_params = params, parked, large_screen, vehicle_params
    self.source = source or comma_layout.LayoutSource(settings_root)
    self.settings_root = settings_root

  def snapshot(self) -> dict:
    raw, readable = read_saved(self.source, comma_layout.DOCUMENT_KEY, comma_layout.MAX_BYTES)
    base = read_customization(self.params)
    (layout, order), valid = comma_layout.checked_defaults(base), readable
    if readable and raw is not None:
      try:
        layout, order = comma_layout.decode(raw, base)
      except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        valid = False
    metadata = comma_layout.metadata()
    from openpilot.starpilot.car.ford.rear_fog import main_bus
    metadata['profiles']['large']['widgets']['rear_fog']['hidden'] = main_bus(self.vehicle_params()) is None
    large = self.large_screen()
    current = projection_settings.load(self.settings_root)
    return {'document': _document(base, layout, order), 'defaults': _document(base, *comma_layout.checked_defaults(base)),
            'revision': _revision(raw), 'metadata': metadata, 'editable': bool(readable and large and self.parked()),
            'valid': valid, 'split': True, 'activeProfile': 'large', 'supportedProfiles': ['large'] if large else [],
            'reason': None if large else 'The phone screen shares the comma screen only on a comma 3 or 3X.',
            'display': {'rows': [], 'values': {}, 'defaults': {}}, 'failed': [],
            'phoneScreen': {'enabled': current.enabled, 'side': current.side,
                            'width': 2160 - comma_layout.WIDTH, 'height': comma_layout.HEIGHT}}

  def save(self, payload: dict, *, session_valid: Callable[[], bool]) -> dict:
    if (type(payload) is not dict or set(payload) != {'revision', 'document'} or type(payload['revision']) is not str or
        type(payload['document']) is not dict or type(payload['document'].get('layouts')) is not dict):
      raise ValueError('Invalid phone screen layout request')
    if not self.large_screen():
      raise ValueError('The phone screen layout is only for a comma 3 or 3X')
    order = payload['document'].get('widgetOrder', {})
    if type(order) is not dict:
      raise ValueError('Invalid widget layer order')
    layout, order = comma_layout.validate(payload['document']['layouts'].get('large'), order.get('large'), read_customization(self.params))
    encoded = comma_layout.encode(layout, order)
    if len(encoded) > comma_layout.MAX_BYTES:
      raise ValueError('Phone screen layout is too large')
    raw, readable = read_saved(self.source, comma_layout.DOCUMENT_KEY, comma_layout.MAX_BYTES)
    if not readable:
      raise OSError('Saved phone screen layout is unavailable')
    if payload['revision'] != _revision(raw):
      raise LayoutChanged('Saved layout changed; reload before saving')
    self.source.prepare()
    result = commit_exact(self.source, key=comma_layout.DOCUMENT_KEY, max_bytes=comma_layout.MAX_BYTES, raw=encoded, expected=raw,
                          authorized=lambda: session_valid() and self.parked(), temp_prefix='.phone-layout-')
    if not result.committed or not result.verified:
      raise LayoutChanged('Layout could not be saved; check that the device is parked and reload')
    return self.snapshot()

  def disable_map(self, session_valid: Callable[[], bool]) -> bool:
    """Turn this layout's map off when the car screen has taken it; True when it was on."""
    raw, readable = read_saved(self.source, comma_layout.DOCUMENT_KEY, comma_layout.MAX_BYTES)
    if not readable or raw is None:
      return False
    try:
      layout, order = comma_layout.decode(raw, read_customization(self.params))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
      return False
    if layout.get('nav_map', {}).get('enabled') is not True:
      return False
    layout['nav_map']['enabled'] = False
    result = commit_exact(self.source, key=comma_layout.DOCUMENT_KEY, max_bytes=comma_layout.MAX_BYTES,
                          raw=comma_layout.encode(layout, order), expected=raw,
                          authorized=lambda: session_valid() and self.parked(), temp_prefix='.phone-layout-')
    return bool(result.committed and result.verified)
