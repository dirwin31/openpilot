from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.onroad_customization import default_document
from openpilot.starpilot.ui.onroad_jetlink_layer import NativeJetlinkLayer
from openpilot.starpilot.ui.onroad_jetlink import JetlinkStatusWidget


def test_native_jetlink_polls_only_while_its_widget_is_showing_onroad():
  with patch('openpilot.starpilot.ui.onroad_jetlink_layer.gui_app') as app, \
       patch('openpilot.starpilot.ui.onroad_jetlink_layer.JetlinkStatusFeed') as feed:
    widget, ui = Mock(), NS(started=True)
    layer = NativeJetlinkLayer(widget, ui)
    state = NS(customization=default_document())
    app.add_render_prepare.assert_called_once_with(layer.prepare, layer.close)
    layer.render(state)
    layer.prepare()
    widget.render.assert_not_called()  # Off by default.
    feed.return_value.set_enabled.assert_called_with(False)
    state.customization['layouts']['large']['jetlink_status']['enabled'] = True
    layer.render(state)
    assert widget.status is feed.return_value.snapshot.return_value
    widget.render.assert_called_once_with(state)
    layer.prepare()
    feed.return_value.set_enabled.assert_called_with(True)
    layer.prepare()  # No render request: the page is hidden.
    feed.return_value.set_enabled.assert_called_with(False)
    layer.render(state)
    ui.started = False
    layer.prepare()
    feed.return_value.set_enabled.assert_called_with(False)
    layer.close()
    app.remove_render_prepare.assert_called_once_with(layer.prepare)
    feed.return_value.close.assert_called_once()


def test_opted_in_widget_shows_off_and_hides_under_alerts():
  fonts = Mock()
  fonts.measure.return_value = NS(width=100)
  widget = JetlinkStatusWidget(fonts)
  state = NS(customization=default_document(), alert=NS(size='none'))
  state.customization['layouts']['large']['jetlink_status']['enabled'] = True
  with patch('openpilot.starpilot.ui.onroad_jetlink.rl.draw_rectangle_rounded'), \
       patch('openpilot.starpilot.ui.onroad_jetlink.rl.draw_circle_v'):
    widget.render(state)
    assert fonts.draw.call_args_list[0].args[0] == 'Off • not in use'
    fonts.draw.reset_mock()
    state.alert.size = 'full'
    widget.render(state)
    fonts.draw.assert_not_called()
