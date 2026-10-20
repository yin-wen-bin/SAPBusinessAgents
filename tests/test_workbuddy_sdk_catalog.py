"""SDK directory membership is independent from custom check history."""
import asyncio
import copy
import pytest
from sap_business_agents_platform.workbuddy_site import make_configuration
from tests.test_workbuddy_service_site import managed


def fixture(tmp_path, monkeypatch):
    manager, release = managed(tmp_path, monkeypatch)
    site = make_configuration('china', 1)
    checks = {name: {'compatible': passed, 'status': 'compatible' if passed else 'failed',
              'environment_digest': release['environment_digest'], 'authentication_revision': 0,
              'actual_model': name, 'model_identity_source': 'assistant_message', **site}
              for name, passed in [('historical-gpt', False), ('historical-custom-pass', True), ('sdk-checked', True)]}
    manager.environment.mutate(lambda state: state.update(**site,
        models=checks, model_check_history=[copy.deepcopy(c) for c in checks.values()],
        default_model_id='historical-custom-pass', authenticated=True, authentication_revision=0,
        model_discovery={'models':['sdk-new','sdk-checked','sdk-new'], 'source':'locked_cli_help',
                         'environment_digest':release['environment_digest'], **site}))
    monkeypatch.setattr(manager, 'integration', lambda *_: {'status':'certified'})
    monkeypatch.setattr(manager, '_model_check', lambda state, _release, model: state['models'].get(model, {}))
    return manager, release


def test_only_sdk_members_are_listed_in_sdk_order_without_history_mutation(tmp_path, monkeypatch):
    manager, release = fixture(tmp_path, monkeypatch)
    before = manager.environment.state()
    def forbidden(*_, **__):pytest.fail('catalog read must not dispatch a worker or model check')
    monkeypatch.setattr(manager.supervisor, 'run', forbidden)
    monkeypatch.setattr(manager, 'check_model', forbidden)
    catalog = manager.models()
    assert [m['model_id'] for m in catalog['items']] == ['sdk-new','sdk-checked']
    assert catalog['default_model_id'] == 'historical-custom-pass'
    assert not catalog['items'][0]['selectable']
    assert catalog['items'][1]['selectable']
    assert manager.environment.state() == before
    assert manager.configuration_digest(before, release, before['default_model_id'], before['models'][before['default_model_id']]) == manager.configuration_digest(manager.environment.state(), release, before['default_model_id'], before['models'][before['default_model_id']])


def test_explicit_check_reply_does_not_insert_history_into_sdk_directory(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch)
    before = manager.environment.state()
    reply = manager.models(_checked_model='historical-custom-pass')
    assert any(m['model_id']=='historical-custom-pass' and m['selectable'] for m in reply['items'])
    assert [m['model_id'] for m in manager.models()['items']] == ['sdk-new','sdk-checked']
    assert manager.environment.state() == before


@pytest.mark.parametrize('change', ['missing','empty','failed','foreign_environment','foreign_site','unknown_source'])
def test_absent_or_inapplicable_sdk_directory_never_falls_back_to_history(tmp_path, monkeypatch, change):
    manager, _ = fixture(tmp_path, monkeypatch)
    def update(state):
        discovery = state['model_discovery']
        if change == 'missing':state.pop('model_discovery')
        elif change == 'empty':discovery['models'] = []
        elif change == 'failed':discovery.update(models=[],error={'code':'workbuddy_model_discovery_failed'})
        elif change == 'foreign_environment':discovery['environment_digest'] = 'b'*64
        elif change == 'foreign_site':discovery.update(make_configuration('international',2))
        elif change == 'unknown_source':discovery['source'] = 'user_specified'
    manager.environment.mutate(update)
    before = manager.environment.state()
    assert manager.models()['items'] == []
    assert manager.environment.state() == before


def test_sdk_refresh_replaces_membership_but_retains_all_checks_and_selected_config(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch)
    before = manager.environment.state()
    calls = []
    async def discover(**kwargs):
        calls.append(kwargs)
        assert kwargs['operation'] == 'models' and kwargs['probe'] is True
        assert kwargs['payload'] == {}  # No prompt, SAP tools or model task.
        return {'models':['fresh-sdk-only','sdk-checked','fresh-sdk-only']}
    monkeypatch.setattr(manager.supervisor, 'run', discover)
    catalog = asyncio.run(manager.refresh_models())
    assert len(calls) == 1
    assert [m['model_id'] for m in catalog['items']] == ['fresh-sdk-only','sdk-checked']
    after = manager.environment.state()
    assert after['models'] == before['models']
    assert after['model_check_history'] == before['model_check_history']
    assert after['default_model_id'] == before['default_model_id']
    assert after['authentication_revision'] == before['authentication_revision']
