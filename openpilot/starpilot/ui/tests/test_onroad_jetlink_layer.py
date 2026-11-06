from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.models.jetlink_status import JetlinkStatus
from openpilot.starpilot.ui.onroad_customization import default_document
from openpilot.starpilot.ui.onroad_jetlink import ACCENT, GRAY, GREEN, ORANGE, big_model_card, gpu_inputs
from openpilot.starpilot.ui.onroad_jetlink_layer import NativeJetlinkLayer


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
    assert (widget.gpu, widget.chestnut) == (None, False)  # nothing tracks the GPU on this fake UIState
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


@pytest.mark.parametrize('jetlink,gpu,chestnut,expected', [
  (JetlinkStatus(), None, False, ('Small model', 'Disconnected', GRAY, None)),
  (JetlinkStatus(), 'disconnected', False, ('Small model', 'Disconnected', GRAY, None)),
  (JetlinkStatus('Computer', 'active', 24.6), 'active', False, ('Big model', 'Jetlink', GREEN, 24.6)),
  (JetlinkStatus('Computer', 'ready', offer=True), 'loading', False, ('Small model', 'Re-engage to switch', ACCENT, None)),
  (JetlinkStatus('Computer', 'ready', offer=True), 'failed', False, ('Small model', 'Re-engage to switch', ORANGE, None)),
  (JetlinkStatus('Computer', 'connecting'), 'failed', False, ('Small model', 'Fell back', ORANGE, None)),
  (JetlinkStatus('Computer', 'ready'), None, False, ('Small model', 'Jetlink ready', ACCENT, None)),
  (JetlinkStatus('Computer', 'connected', building=True), 'loading', False, ('Small model', 'Jetlink building', ACCENT, None)),
  (JetlinkStatus('Computer', 'disconnected'), 'failed', False, ('Small model', 'Jetlink offline', GRAY, None)),
  (JetlinkStatus(state='unavailable'), 'failed', False, ('Small model', 'Jetlink error', ORANGE, None)),
  (JetlinkStatus(), 'active', True, ('Big model', 'Chestnut', GREEN, None)),
  (JetlinkStatus(), 'failed', True, ('Small model', 'Fell back', ORANGE, None)),
  (JetlinkStatus(), 'loading', True, ('Small model', 'Chestnut loading', ACCENT, None)),
])
def test_big_model_card_says_which_model_drives_in_a_few_words(jetlink, gpu, chestnut, expected):
  assert tuple(big_model_card(jetlink, gpu, chestnut)) == expected


def test_gpu_inputs_read_uistate_and_never_raise():
  ui = NS(chestnut_state=NS(value='active'), sm={'deviceState': NS(chestnutPresent=True)})
  assert gpu_inputs(ui) == ('active', True)
  assert gpu_inputs(NS()) == (None, False)
