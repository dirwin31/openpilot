"""Car screens using the shared main layout's initialization and lifecycle."""

from openpilot.selfdrive.ui.layouts.main import MainLayout, MainState
from openpilot.selfdrive.ui.layouts.settings.types import PanelType
from openpilot.starpilot.system.android_auto.ui.developer_sidebar import CarDeveloperSidebar
from openpilot.starpilot.system.android_auto.ui.home import CarHomeLayout
from openpilot.starpilot.system.android_auto.ui.onroad import CarOnroadView
from openpilot.starpilot.system.android_auto.ui.settings import CarSettingsLayout
from openpilot.starpilot.system.android_auto.ui.sidebar import CarSidebar


class CarMainLayout(MainLayout):
  def _create_sidebar(self):
    return CarSidebar()

  def _create_developer_sidebar(self):
    return CarDeveloperSidebar()

  def _create_layouts(self):
    return {MainState.HOME: CarHomeLayout(), MainState.SETTINGS: CarSettingsLayout(), MainState.ONROAD: CarOnroadView()}

  def open_starpilot_panel(self, panel_key: str):
    self.open_settings(PanelType.STARPILOT)
    self._layouts[MainState.SETTINGS].open_panel(panel_key)
