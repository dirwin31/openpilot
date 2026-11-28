"""The "3D drive render" setting: which one screen draws the rendered road view in place of the front camera.

The render is a layer: it takes the camera's place and the layout's widgets, alerts and borders still draw over it.
It is the ``driveRender`` field of the OnroadCustomizations document; an absent field is the default, off.
One choice, so the comma's screen and StarPort never both draw it. The comma's own screen only offers it on the large
profile (comma 3 / 3X); elsewhere ``device`` acts as off.
"""

FIELD = "driveRender"
OFF, STARPORT, DEVICE = "off", "starport", "device"
VALUES = (OFF, STARPORT, DEVICE)
DEFAULT = OFF

KEY = "DriveRender"
LABEL = "3D drive render"
LABELS = {OFF: "Off", STARPORT: "StarPort", DEVICE: "Comma display"}


def normalize(value) -> str:
  """A saved value, or the default when it is absent or not one of the choices."""
  return value if type(value) is str and value in VALUES else DEFAULT


def effective(value, large_profile: bool) -> str:
  """The choice this device acts on: the comma display needs the large profile."""
  value = normalize(value)
  return OFF if value == DEVICE and not large_profile else value


def on_device(value, large_profile: bool = True) -> bool:
  return effective(value, large_profile) == DEVICE


def on_starport(value) -> bool:
  return normalize(value) == STARPORT


def choices(large_profile: bool) -> tuple[str, ...]:
  return VALUES if large_profile else (OFF, STARPORT)


def description(large_profile: bool) -> str:
  screens = "StarPort shows it on the car screen" + (", Comma display on the comma 3 or 3X screen" if large_profile else "")
  return ("Draws a rendered 3D view of the road, lanes and traffic in place of the front camera; the layout's widgets " +
          "and alerts stay on top. " + screens + ". StarPort uses the choice from its next connection.")


def read(params) -> str:
  """The saved choice; needs only the read interface (ProjectionParams works)."""
  from openpilot.starpilot.ui.onroad_customization import read_customization
  return normalize(read_customization(params).get(FIELD))
