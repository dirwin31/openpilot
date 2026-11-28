"""The "Birds-eye view instead of camera" setting.

When on, the rendered road view takes the front camera's place, with the layout's widgets and alerts still on top.
Only one screen draws it: the car screen while StarPort is streaming, otherwise the comma 3 / 3X's own screen. It is
the ``driveRender`` field of the OnroadCustomizations document; an absent field is off.
"""

FIELD = "driveRender"
KEY = "DriveRender"
LABEL = "Birds-eye view instead of camera"
DESCRIPTION = ("Replaces the front camera with a rendered birds-eye view of the road, lanes and traffic; the widgets " +
               "and alerts stay on top. While StarPort streams it shows on the car screen, otherwise on the comma 3 " +
               "or 3X screen. StarPort uses the choice from its next connection.")


def enabled(document) -> bool:
  """The saved choice: on only for a document that says so."""
  return isinstance(document, dict) and document.get(FIELD) is True


def read(params) -> bool:
  """The saved choice; needs only the read interface (ProjectionParams works)."""
  from openpilot.starpilot.ui.onroad_customization import read_customization
  return enabled(read_customization(params))
