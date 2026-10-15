"""Follow the unmodified host's loaded engine without turning observations into overrides."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from jetlink.client import EngineMissing
from jetlink.openpilot import link
from jetlink.openpilot.models import Models
from jetlink.openpilot.parts import Parts
from jetlink.openpilot.provision import ProvisioningRun
from jetlink.openpilot.settings import FileParams, Settings
from jetlink.transport.base import LinkError
from openpilot.starpilot.models.jetlink_adapter import keys


@pytest.fixture
def context():
  values = {}
  op = SimpleNamespace(keys=keys(), catalog_selector=19, log=Mock(), get=values.get,
                       params_dir=lambda: Path('/unused'),
                       put=lambda key, value, **kw: values.__setitem__(key, value))
  parts = Parts(op)
  parts.progress = Mock()
  parts.warps = Mock()
  parts._settings = Mock()
  parts._settings.params.directory = Path('/unused')
  parts._settings.mode.return_value = 'ios'
  client = Mock()

  def ready(sha256, nbytes, **kw):
    spec = SimpleNamespace(sha256=sha256, nbytes=123, checkpoint='App checkpoint')
    spec.to_dict = lambda: {'sha256': sha256, 'nbytes': 123, 'checkpoint': 'App checkpoint'}
    return spec

  client.ensure_engine.side_effect = ready
  return parts, client, values


def test_fresh_app_hash_wins_over_old_spec_and_catalog_without_io(context):
  parts, client, values = context
  values['JetlinkSpec'] = {'sha256': 'a' * 64, 'ready': True}  # previous manual engine
  parts.models.resolve_pointer = Mock(side_effect=AssertionError('must not look up a model'))
  parts.models.shipped_model_path = Mock(side_effect=AssertionError('must not read or upload ONNX'))
  cable = Mock()
  for digest in ('b' * 64, 'c' * 64):
    client.hello.return_value = {'loaded': digest, 'cached_models': ['a' * 64]}
    with patch.object(link, 'connect_patiently', return_value=client):
      _, spec = link.open_link(parts, cable)
    assert spec.sha256 == digest
    assert values['JetlinkSpec']['source'] == 'app'
    assert 'JetlinkBigModel' not in values
    assert parts.models.selected_model()['oid'] == digest
    assert parts.models.selected_model_name() == 'App checkpoint'
    assert client.ensure_engine.call_args.args == (digest, 0)
    assert client.ensure_engine.call_args.kwargs['onnx_path'] is None


@pytest.mark.parametrize('loaded', [None, '', 'bad', 'a' * 63, 'A' * 64, ['b' * 64]])
def test_no_loaded_model_never_chooses_cached_or_recorded_engine(context, loaded):
  parts, client, values = context
  values['JetlinkSpec'] = {'sha256': 'a' * 64, 'ready': True, 'source': 'app'}
  with pytest.raises(LinkError, match='waiting for a loaded model'):
    link.follow_app(parts, client, {'loaded': loaded, 'cached_models': ['a' * 64]})
  client.ensure_engine.assert_not_called()
  assert values['JetlinkSpec']['ready'] is False
  assert parts.models.selected_model() is None
  assert parts.progress.report.call_args.args[0] == 'waiting'


def test_engine_lost_does_not_trigger_download_or_upload(context):
  parts, client, values = context
  values['JetlinkSpec'] = {'sha256': 'a' * 64, 'ready': True, 'source': 'app'}
  client.ensure_engine.side_effect = EngineMissing('app unloaded its engine')
  client.hello.return_value = {'loaded': 'b' * 64}
  cable = Mock()
  with patch.object(link, 'connect_patiently', return_value=client), pytest.raises(link.ModelMissing):
    link.open_link(parts, cable)
  cable.close.assert_called_once()
  assert client.ensure_engine.call_count == 1
  assert values['JetlinkSpec']['ready'] is False


def test_manual_override_is_not_replaced_by_app_observation(context):
  parts, client, values = context
  ref, oid = 'a' * 40, 'a' * 64
  values['JetlinkBigModel'] = {'ref': ref, 'displayName': 'Manual choice'}
  values['JetlinkPointers'] = {ref: {'oid': oid, 'size': 123}}
  values['JetlinkSpec'] = {'sha256': 'b' * 64, 'source': 'app', 'ready': True}
  assert parts.models.follow_app() is False
  assert parts.models.selected_model()['oid'] == oid
  assert parts.models.selected_model_name() == 'Manual choice'
  client.hello.return_value = {'loaded': 'b' * 64, 'cached_models': [oid]}
  with patch.object(link, 'connect_patiently', return_value=client), \
       patch.object(parts.models, 'shipped_model_path', return_value=None), \
       patch.object(parts.spec, 'ready_spec', return_value=None):
    _, spec = link.open_link(parts, Mock())
  assert spec.sha256 == oid
  assert client.ensure_engine.call_args.args == (oid, 123)
  assert 'source' not in values['JetlinkSpec']
  assert values['JetlinkBigModel']['ref'] == ref


def test_parked_worker_discovers_app_before_any_download(context):
  parts, client, values = context
  values['JetlinkSpec'] = {'sha256': 'a' * 64, 'ready': True, 'source': 'app'}
  run = ProvisioningRun(parts)
  run.client = client
  run.open_link = Mock(return_value=True)
  run.wait_for_jetson = Mock(return_value=True)
  run.needs_download = Mock(side_effect=AssertionError('must not download a default'))
  run.fetch_model = Mock(side_effect=AssertionError('must not download a default'))
  client.hello.return_value = {'loaded': 'b' * 64}
  with patch('jetlink.comma.gadget.pending_shutdown', return_value=None):
    assert run.run() is True
  assert values['JetlinkSpec']['sha256'] == 'b' * 64
  assert values['JetlinkSpec']['source'] == 'app'
  client.close.assert_called_once()


def test_missing_app_model_keeps_worker_waiting(context):
  parts, client, values = context
  run = ProvisioningRun(parts)
  run.client = client
  run.open_link = Mock(return_value=True)
  run.wait_for_jetson = Mock(return_value=True)
  client.hello.return_value = {'loaded': None}
  with patch('jetlink.comma.gadget.pending_shutdown', return_value=None):
    assert run.run() is False
  assert parts.progress.report.call_args.args[0] == 'waiting'
  client.ensure_engine.assert_not_called()


def test_owner_file_reader_agrees_with_models_on_following(context, tmp_path):
  parts, client, values = context
  settings = Settings(FileParams(tmp_path), keys())
  for slot, follows in ((None, True), ({}, True), ({'ref': ''}, True), ({'ref': 1}, True),
                        ({'ref': 'a' * 40}, False)):
    values['JetlinkBigModel'] = slot
    (tmp_path / 'JetlinkBigModel').write_text(json.dumps(slot))
    assert settings.follow_app() is follows
    assert Models(parts.op).follow_app() is follows
  (tmp_path / 'JetlinkBigModel').write_bytes(b'corrupt')
  assert settings.follow_app() is True
  assert Settings(FileParams(tmp_path), replace(keys(), follow_app=False)).follow_app() is False


def test_legacy_fork_keeps_its_default(context):
  parts, client, values = context
  parts.op.keys = replace(keys(), follow_app=False, catalog=None)
  assert parts.models.follow_app() is False
  assert parts.models.selected_model()['ref']


def test_parked_owner_checks_app_again_without_param_change(context):
  from openpilot.starpilot.system.jetlink.daemon import APP_MODEL_POLL, owner_class
  from jetlink.comma.owner import Owner
  parts, client, values = context
  settings = Mock()
  settings.follow_app.return_value = True
  owner = owner_class()([], settings=settings, chestnut_ids=frozenset())
  owner.attached = True
  owner.next_worker = 100.0
  with patch.object(Owner, 'wanted', return_value=None), patch('time.monotonic', return_value=99.0):
    assert owner.wanted() is None
  with patch.object(Owner, 'wanted', return_value=None), patch('time.monotonic', return_value=100.0):
    assert owner.wanted() == 'checking the app model'
    settings.follow_app.return_value = False
    assert owner.wanted() is None
  settings.follow_app.return_value = True
  with patch.object(Owner, 'spawn_worker'), patch('time.monotonic', return_value=100.0):
    owner.spawn_worker('app changed')
  assert owner.next_worker == 100.0 + APP_MODEL_POLL
