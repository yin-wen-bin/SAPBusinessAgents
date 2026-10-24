"""Pure display tests: no installed SDK, official auth, model or SAP calls."""
import copy
import json
import threading
import time
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.runtime_display_summary import WorkBuddyDisplaySummary, binding
from sap_business_agents_platform.status_projection import StatusProjection
from sap_business_agents_platform.workbuddy_site import make_configuration


def until(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.005)


def forbidden(*args, **kwargs):
    raise AssertionError('full_gate_on_metadata_path')


@pytest.fixture
def fixture(tmp_path):
    state = {'revision': 10, 'enabled': True, 'environment_digest': 'a' * 64,
        'default_model_id': 'sdk-model', 'configuration_revision': 3,
        **make_configuration('china', 1), 'models': {'historical-custom': {'compatible': True}},
        'validation_ledger': {'bad-historical': 'display-must-not-audit-this'},
        'model_discovery': {'source': 'locked_cli_help', 'models': ['sdk-model'],
            'environment_digest': 'a' * 64, **make_configuration('china', 1)}}
    path = tmp_path / 'state.json'
    def write(): path.write_text(json.dumps(state), encoding='utf-8')
    write()
    release = tmp_path / 'releases' / ('a' * 64)
    release.mkdir(parents=True)
    (release / 'environment.json').write_text(json.dumps({'sdk_version': '0.3.247', 'cli_version': '2.141.0'}))
    environment = SimpleNamespace(state_path=path, state=forbidden, release=forbidden)
    manager = SimpleNamespace(default_provider_id='codex', authentication_service=None,
        workbuddy=SimpleNamespace(environment=environment, models=forbidden, snapshot=forbidden))
    template = lambda: {'provider_id': 'workbuddy', 'can_enable': False, 'selectable': False, 'blockers': []}
    gate = threading.Event(); gate.set()
    checks = []
    def full():
        checks.append(1); gate.wait(2)
        return {**template(), 'can_enable': True, 'selectable': True, 'installed': True,
            'authentication': {'revision': 1, 'status': 'authenticated', 'authenticated': True},
            '_settings_binding': binding(state), '_settings_catalog': {'provider_id': 'workbuddy',
                'items': [{'model_id': 'sdk-model', 'selectable': True}], 'catalog_digest': 'verified'}}
    complete = StatusProjection({'workbuddy': full}, {'workbuddy': lambda: 1}, {'workbuddy': template})
    summary = WorkBuddyDisplaySummary(manager, complete, template)
    try:
        yield SimpleNamespace(state=state, path=path, write=write, manager=manager,
                              summary=summary, complete=complete, gate=gate, checks=checks)
    finally:
        gate.set(); summary.close(); complete.close()


def ready(f):
    f.summary.get()
    until(lambda: not f.summary.get()['display_status']['stale'])


def test_cold_and_slow_full_check_do_not_block_summary_or_catalog(fixture):
    f = fixture; f.gate.clear()
    start = time.monotonic()
    cold = f.summary.get()
    assert time.monotonic() - start < .1
    assert cold['can_enable'] is False
    ready(f)
    result = f.summary.models()
    assert [m['model_id'] for m in result['items']] == ['sdk-model']
    assert all(m['selectable'] is False for m in result['items'])
    assert result['status_refresh']['stale'] is True
    for _ in range(20): f.summary.get(); f.summary.models()
    until(lambda: len(f.checks) == 1)
    assert len(f.checks) == 1


def test_verified_catalog_reuses_same_full_inspection_without_models_get(fixture):
    f = fixture
    ready(f)
    until(lambda: not f.summary.models()['status_refresh']['stale'])
    assert f.summary.models()['catalog_digest'] == 'verified'
    assert len(f.checks) == 1
    assert f.summary.get()['recorded_sdk_version'] == '0.3.247'
    assert '_settings_catalog' not in f.summary.get()


def test_display_failure_retains_last_content_but_never_eligibility(fixture):
    f = fixture; ready(f)
    f.path.write_text('{broken')
    f.summary.invalidate(); f.summary.get()
    until(lambda: not f.summary.cache._slots['workbuddy']['running'])
    result = f.summary.get()
    assert result['display_status']['stale']
    assert result['display_status']['error_code'] == 'status_projection_failed'
    assert result['enabled'] is None
    assert result['default_model_id'] == 'sdk-model'
    assert result['can_enable'] is False and result['selectable'] is False
    assert f.summary.models()['items'][0]['selectable'] is False


def test_history_only_change_does_not_change_display_binding_or_form_identity(fixture):
    f = fixture; ready(f)
    old = f.summary.get()['display_status']['binding']
    f.state['revision'] += 1
    f.state['validation_ledger'] = {'another': 'not-a-business-proof'}
    f.state['business_evaluation_projections'] = {'quality': 'diagnostic'}
    f.write(); f.summary.invalidate(); ready(f)
    assert f.summary.get()['display_status']['binding'] == old
    assert len(f.checks) == 1


@pytest.mark.parametrize('field,value', [('default_model_id', 'other'), ('environment_digest', 'b'*64),
    ('authenticated', False), ('sdk_integration_records', {'changed': {'evidence': 'different'}})])
def test_relevant_change_invalidates_full_display_not_execution_records(fixture, field, value):
    f = fixture; ready(f)
    old = f.summary.get()['display_status']['binding']
    f.state[field] = value; f.state['revision'] += 1; f.write()
    f.summary.invalidate(); ready(f)
    assert f.summary.get()['display_status']['binding'] != old
    assert f.state[field] == value


def test_authentication_lock_is_not_waited_or_used_to_grant_capabilities(fixture):
    f = fixture; lock = threading.Lock(); lock.acquire()
    f.manager.authentication_service = SimpleNamespace(lock=lock, invalid_state=False,
        state={'accounts': {'workbuddy': {'authenticated': True}}, 'operations': {}})
    try:
        ready(f)
        result = f.summary.get()
        assert result['authentication']['authenticated'] is None
        assert not any(result['authentication']['capabilities'].values())
        assert not result['can_enable']
    finally: lock.release()


def test_credentials_and_unrelated_values_never_escape_projection(fixture):
    f = fixture
    f.state.update(secret='do-not-export', credential={'token': 'do-not-export'},
                   sap_rows=[{'secret': 'do-not-export'}])
    f.write(); ready(f)
    assert 'do-not-export' not in json.dumps(f.summary.get())
    assert 'do-not-export' not in json.dumps(f.summary.models())


def test_runtime_execution_components_remain_outside_new_display_module():
    import inspect
    from sap_business_agents_platform import runtime_display_summary as module
    source = inspect.getsource(module)
    assert 'codebuddy_agent_sdk' not in source
    assert '.capabilities()' not in source and '.observation()' not in source
    assert '.environment.release(' not in source and '.workbuddy.snapshot(' not in source


def test_snapshot_revisions_regress_fail_closed_and_source_is_read_only(fixture):
    f = fixture; ready(f)
    f.state['revision'] = 1; f.write()
    original = f.path.read_bytes()
    f.summary.invalidate(); f.summary.get()
    until(lambda: not f.summary.cache._slots['workbuddy']['running'])
    assert f.summary.get()['display_status']['stale']
    assert f.path.read_bytes() == original


def test_http_summary_is_opt_in_and_legacy_models_are_unchanged(tmp_path, monkeypatch):
    from pathlib import Path
    from fastapi.testclient import TestClient
    from sap_business_agents_platform.app import create_app
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform import status_projection
    calls = []
    class Display:
        def get(self, provider): calls.append(('legacy-list', provider)); return {'provider_id': provider}
        def get_summary(self, provider):
            if provider != 'workbuddy': return self.get(provider)
            calls.append(('summary-list', provider)); return {'provider_id': provider, 'display_status': {'stale': True}}
        def models_summary(self): calls.append(('summary-models',)); return {'items': [], 'display_status': {'stale': True}}
        def close(self): pass
    monkeypatch.setattr(status_projection, 'sdk_display_projection', lambda manager: Display())
    manager = SimpleNamespace(default_provider_id='codex', definitions=[SimpleNamespace(provider_id=p, integration_runtime={}) for p in ('codex', 'workbuddy')],
        list=lambda: [], models=lambda provider: calls.append(('legacy-models', provider)) or {'items': []})
    root = Path(__file__).resolve().parents[1]
    settings = Settings(repository_root=root, data_root=tmp_path/'data', draft_root=tmp_path/'drafts', skillhub_root=tmp_path/'skills')
    app = create_app(settings, planner=object(), sdk_manager=manager, authentication_adapters={})
    # This test covers routing, not application lifecycle. Starting unrelated
    # catalog warmers would leak background disk reads into global I/O spies.
    from contextlib import closing
    with closing(TestClient(app)) as client:
        calls.clear()
        response = client.get('/api/system/sdk-runtimes?view=summary')
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        assert calls == [('legacy-list', 'codex'), ('summary-list', 'workbuddy')]
        calls.clear(); client.get('/api/system/sdk-runtimes')
        assert calls == [('legacy-list', 'codex'), ('legacy-list', 'workbuddy')]
        calls.clear(); client.get('/api/system/sdk-runtimes/workbuddy/models?view=summary')
        assert calls == [('summary-models',)]
        calls.clear(); client.get('/api/system/sdk-runtimes/workbuddy/models')
        assert calls == [('legacy-models', 'workbuddy')]
        calls.clear(); client.get('/api/system/sdk-runtimes/codex/models?view=summary')
        assert calls == [('legacy-models', 'codex')]


def confirmed_account(f):
    f.manager.authentication_service = SimpleNamespace(lock=threading.RLock(), invalid_state=False,
        state={'accounts': {'workbuddy': {'revision': 1, 'status': 'authenticated',
            'authenticated': True, 'identity': 'masked-account'}}, 'operations': {}})
    original = f.complete.loaders['workbuddy']
    def full():
        value = original()
        value['authentication'].update(identity='masked-account', capabilities={'check': True})
        value['_settings_catalog']['items'][0].update(check_status='compatible', actual_model='sdk-model')
        return value
    f.complete.loaders['workbuddy'] = full
    ready(f)
    until(lambda: not f.summary.models()['status_refresh']['stale'])


def test_refresh_retains_confirmed_identity_and_model_without_granting_actions(fixture):
    f = fixture; confirmed_account(f)
    f.gate.clear(); f.complete.invalidate(['workbuddy'])
    result = f.summary.get(); catalog = f.summary.models()
    assert result['status_refresh']['stale']
    assert result['authentication']['identity'] == 'masked-account'
    assert result['authentication']['status'] == 'authenticated'
    assert result['authentication']['display_only'] is True
    assert not any(result['authentication']['capabilities'].values())
    assert result['authenticated'] is None and not result['can_enable'] and not result['selectable']
    item = catalog['items'][0]
    assert item['actual_model'] == 'sdk-model' and item['check_status'] == 'compatible'
    assert item['last_confirmed'] and item['display_pending'] and not item['selectable']
    f.gate.set()
    until(lambda: not f.summary.get()['status_refresh']['stale'])
    assert f.summary.get()['authentication']['capabilities']['check']


@pytest.mark.parametrize('field,value', [('default_model_id', 'other'), ('environment_digest', 'b'*64),
    ('service_site_revision', 99)])
def test_changed_binding_cannot_retain_prior_model_confirmation(fixture, field, value):
    f = fixture; confirmed_account(f); f.gate.clear()
    f.state[field] = value; f.state['revision'] += 1; f.write()
    if field == 'service_site_revision':
        f.state.update(make_configuration('international', value)); f.write()
    f.summary.invalidate(); ready(f)
    result = f.summary.models()
    assert not any(item.get('last_confirmed') for item in result['items'])
    assert not result.get('can_enable')


def test_changed_authentication_or_failed_metadata_cannot_restore_capabilities(fixture):
    f = fixture; confirmed_account(f); f.gate.clear()
    f.manager.authentication_service.state['accounts']['workbuddy'].update(revision=2, status='signed_out', authenticated=False)
    f.summary.invalidate(); ready(f)
    result = f.summary.get()
    assert result['authentication']['status'] == 'signed_out'
    assert not any(result['authentication']['capabilities'].values())
    assert not any(item.get('last_confirmed') for item in f.summary.models()['items'])


def test_completed_negative_context_check_is_not_infinite_pending_or_authorization(fixture):
    f = fixture; confirmed_account(f)
    original = f.complete.loaders['workbuddy']
    def negative():
        result = original()
        result['authentication'].update(status='not_checked', authenticated=None,
            authentication_context_status='check_required', failure_code='runtime_authentication_check_required')
        # The raw manager's saved authentication may still be positive: display
        # must explicitly deny it, not use pending as a permanent safety gate.
        return result
    f.complete.loaders['workbuddy'] = negative
    f.complete.invalidate(['workbuddy'])
    until(lambda: not f.summary.get()['status_refresh']['stale'])
    value = f.summary.get(); catalog = f.summary.models()
    assert value['authentication']['status'] == 'not_checked'
    assert value['authentication']['capabilities']['check'] is True
    assert not value['can_enable'] and not value['selectable'] and not value['model_check_supported']
    assert 'runtime_authentication_check_required' in value['blockers']
    assert not catalog['status_refresh']['stale']
    assert catalog['items'][0]['check_status'] == 'compatible'
    assert not catalog['items'][0]['selectable']
    assert catalog['items'][0]['compatibility_applicability']['status'] == 'authentication_required'


def test_negative_context_different_revision_cannot_finish_an_old_binding(fixture):
    f = fixture; confirmed_account(f)
    original = f.complete.loaders['workbuddy']
    def old():
        result = original()
        result['authentication'].update(revision=0, status='not_checked', authenticated=None,
                                       authentication_context_status='check_required')
        return result
    f.complete.loaders['workbuddy'] = old
    f.complete.invalidate(['workbuddy']); f.summary.get()
    until(lambda: not f.complete._slots['workbuddy']['running'])
    assert f.summary.get()['status_refresh']['stale']
    assert not f.summary.get()['can_enable']


def test_cold_definition_authentication_description_is_not_an_account(fixture):
    f = fixture
    f.manager.authentication_service = SimpleNamespace(lock=threading.RLock(), invalid_state=False,
        state={'accounts': {'workbuddy': {'revision': 1, 'status': 'authenticated', 'authenticated': True}}, 'operations': {}})
    f.complete.templates['workbuddy'] = lambda: {'authentication': 'Official CLI account description', 'blockers': []}
    f.gate.clear()
    assert not f.summary.get()['can_enable']
    ready(f)
    assert f.summary.get()['status_refresh']['stale']
    assert not f.summary.get()['can_enable']
    assert f.summary.models()['status_refresh']['stale']
