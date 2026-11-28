"""The 3D drive render as the comma 3 / 3X's camera layer.

With Galaxy's "3D drive render" on the comma display, the rendered road view takes the camera's place and the
layout's widgets, alerts and borders draw over it. Nothing is imported or built until the first frame that needs
it, so a device that never chooses it pays nothing.
"""


def build(fonts, ui_state):
  """The view, reading the live UIState; imported here so only a device that draws it loads it."""
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer, BitmapText
  from openpilot.starpilot.ui.birds_eye_map import new_underlay
  view = BirdsEyeRenderer(text=BitmapText(fonts), source=lambda: (ui_state.sm, ui_state.started_frame))
  view.map_underlay = new_underlay()
  return view


class DriveRenderLayer:
  def __init__(self, fonts, ui_state, *, factory=build):
    self.fonts = fonts
    self.ui_state = ui_state
    self._factory = factory
    self.view = None
    self._failed = False

  def ready(self) -> bool:
    """Build on first use. A failed build is remembered, so the caller keeps drawing the camera."""
    if self.view is None and not self._failed:
      try:
        self.view = self._factory(self.fonts, self.ui_state)
      except Exception:
        self._failed = True
        from openpilot.common.swaglog import cloudlog
        cloudlog.exception("3D drive render unavailable on the comma display; showing the camera")
    return self.view is not None

  def render(self, content, state) -> None:
    """Draw the view over ``content``; the caller owns the scissor and draws everything above it.

    A frame that raises ends the render for this drive screen; ready() is False from the next frame, so the camera
    returns.
    """
    try:
      view = self.view
      view.panel = False
      view.opacity = 1.0
      view.speed_limit_sign = False
      view.parent_clip = content
      view.metric = bool(state.metric)
      view.navigation = getattr(state, 'navigation', None)
      view.road_style = state.customization.get('roadColors', {}).get('large', {})
      view.long_control = bool(getattr(self.ui_state, 'has_longitudinal_control', True))
      view.render(content)
    except Exception:
      self._failed = True
      self.close()
      from openpilot.common.swaglog import cloudlog
      cloudlog.exception("3D drive render failed on the comma display; showing the camera")

  def close(self) -> None:
    underlay = getattr(self.view, 'map_underlay', None)
    if underlay is not None:
      underlay.close()
    self.view = None
