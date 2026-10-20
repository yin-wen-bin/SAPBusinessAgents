"""Real authentication assertions over saved metadata; no SDK/model/SAP I/O."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import runtime_validation_freeze as freeze
from sap_business_agents_platform.models import RuntimeSnapshot
from sap_business_agents_platform.runtime_validation import Registry
from sap_business_agents_platform.runtime_validation_store import atomic
from sap_business_agents_platform.workbuddy_environment import WorkBuddyEnvironment
from sap_business_agents_platform.workbuddy_site import make_configuration

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def context(tmp_path, monkeypatch):
    from sap_business_agents_platform.config import Settings
    monkeypatch.setattr(Settings, 'from_env', lambda *_: SimpleNamespace(data_root=tmp_path / '.local-data'))
    site = make_configuration('china', 1)
    env = WorkBuddyEnvironment(tmp_path)
    env.mutate(lambda state: state.update(authentication_revision=4, **site))
    auth_path = tmp_path / '.local-data/sdk-runtimes/authentication.json'
    account = {'status': 'authenticated', 'authenticated': True, 'revision': 4, **site}
    saved = {'version': 1, 'accounts': {'workbuddy': account,
        'codex': {'status': 'authenticated', 'authenticated': True, 'revision': 7}}, 'operations': {}}
    atomic(auth_path, saved)
    raw = {'provider_id': 'workbuddy', 'sdk_id': 'codebuddy-agent-sdk',
        'model': 'deepseek-v4.1-flash', 'configuration_digest': 'unchanged',
        'authentication_revision': 4, **site}
    return SimpleNamespace(root=tmp_path, env=env, auth_path=auth_path, account=account,
        saved=saved, snapshot=RuntimeSnapshot.model_validate(raw).model_dump(mode='json'))


def test_binding_is_explicit_before_fingerprinting_and_never_writes_accounts(context):
    c = context
    source = copy.deepcopy(c.snapshot)
    before = c.auth_path.read_bytes()
    bound = freeze.bind_verified_snapshot(c.root, c.snapshot)
    assert bound['authentication_revision'] == bound['authentication_binding_revision'] == 4
    assert bound['model'] == 'deepseek-v4.1-flash'
    assert c.snapshot == source and 'authentication_binding_revision' not in c.snapshot
    freeze.require_typed_snapshot(bound)
    freeze.require_authentication_binding(c.root, bound)
    assert c.auth_path.read_bytes() == before
    assert json.loads(before)['accounts']['codex']['revision'] == 7


@pytest.mark.parametrize('binding', [None, 3, 5, True, '4'])
def test_frozen_missing_wrong_or_invalid_binding_is_not_repaired(context, binding):
    c = context
    snapshot = {**c.snapshot, 'authentication_binding_revision': binding}
    before = copy.deepcopy(snapshot)
    with pytest.raises(Exception) as raised:
        freeze.require_authentication_binding(c.root, snapshot)
    assert raised.value.code in {'runtime_authentication_binding_changed', 'runtime_authentication_binding_invalid'}
    assert snapshot == before


def test_new_binding_cannot_overwrite_an_existing_stale_binding(context):
    with pytest.raises(Exception) as raised:
        freeze.bind_verified_snapshot(context.root, {**context.snapshot, 'authentication_binding_revision': 3})
    assert raised.value.code == 'runtime_authentication_binding_changed'


@pytest.mark.parametrize('change', [
    {'authentication_revision': 5}, {'authentication_revision': True},
    make_configuration('international', 1),
])
def test_binding_refuses_changed_account_or_site(context, change):
    with pytest.raises(Exception):
        freeze.bind_verified_snapshot(context.root, {**context.snapshot, **change})


@pytest.mark.parametrize('change', [
    {'authenticated': False}, {'status': 'check_failed'}, {'invalidation_pending': True}, {'revision': 5},
])
def test_changed_authentication_after_freeze_blocks_preflight(context, change):
    c = context
    bound = freeze.bind_verified_snapshot(c.root, c.snapshot)
    saved = copy.deepcopy(c.saved)
    saved['accounts']['workbuddy'].update(change)
    atomic(c.auth_path, saved)
    before = c.auth_path.read_bytes()
    with pytest.raises(Exception):
        freeze.require_authentication_binding(c.root, bound)
    assert c.auth_path.read_bytes() == before


@pytest.mark.parametrize('provider', ['workbuddy', 'codex'])
def test_inspection_does_not_reconcile_any_active_authentication_operation(context, provider):
    c = context
    saved = copy.deepcopy(c.saved)
    saved['operations']['login'] = {'status': 'running', 'provider_id': provider,
        'operation_id': 'login', 'created_at': 'now', 'pid': 12345}
    atomic(c.auth_path, saved)
    before = c.auth_path.read_bytes()
    with pytest.raises(Exception, match='validation_authentication_operation_active'):
        freeze.bind_verified_snapshot(c.root, c.snapshot)
    assert c.auth_path.read_bytes() == before


def test_freeze_rejects_missing_binding_before_creating_archive_or_manifest(context, monkeypatch):
    c = context
    monkeypatch.setattr(freeze, 'capture', lambda *_: pytest.fail('must not archive an unbound request'))
    batch = c.root / 'batch'
    with pytest.raises(Exception) as raised:
        freeze.prepare(c.root, batch, {'runtime': c.snapshot, 'selected': ['acceptance.campaign']})
    assert raised.value.code == 'runtime_authentication_binding_changed'
    assert not (batch / 'manifest.json').exists()


def test_case_preflight_rejects_missing_binding_before_claim_or_campaign(context, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    from runtime_validation_cases import WorkBuddyCases
    adapter = WorkBuddyCases.__new__(WorkBuddyCases)
    adapter.root = context.root
    adapter.manifest = {'runtime': context.snapshot}
    with pytest.raises(Exception) as raised:
        adapter.check_case(Registry().entries['acceptance.campaign'])
    assert raised.value.code == 'runtime_authentication_binding_changed'


def test_actual_isolated_authentication_check_precedes_plugin_and_campaign(context, monkeypatch):
    """Replay the omitted-field failure through the real fixture entry point."""
    c = context
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    from sap_business_agents_platform import workbuddy_manager, workbuddy_validation
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.runtime_authentication import RuntimeAuthenticationService
    service = RuntimeAuthenticationService(c.auth_path.parent,
        {'workbuddy': SimpleNamespace(context=lambda: make_configuration('china', 1))})
    monkeypatch.setattr(workbuddy_manager, 'WorkBuddyManager', lambda *_: SimpleNamespace())
    monkeypatch.setattr(workbuddy_validation, 'legacy_fixture_gate', lambda *_: True)
    monkeypatch.setattr(Settings, 'from_env', lambda *_: Settings(repository_root=c.root))
    spec = importlib.util.spec_from_file_location('auth_preflight_acceptance', ROOT / 'scripts/workbuddy_live_cases/acceptance.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HERE = c.root / 'fixture'
    atomic(module.HERE / 'runtime-snapshot.json', c.snapshot)
    async def forbidden_start():
        pytest.fail('plugin/model/SAP/Campaign path must not start')
    module.create_app = lambda _: SimpleNamespace(state=SimpleNamespace(runtime_authentication=service,
        plugin_manager=SimpleNamespace(start=forbidden_start)))
    with pytest.raises(Exception) as raised:
        asyncio.run(module.main())
    assert raised.value.code == 'runtime_authentication_binding_changed'


def test_private_binding_helper_does_not_change_codex_requests(context):
    value = {**context.snapshot, 'provider_id': 'codex'}
    before = copy.deepcopy(value)
    with pytest.raises(Exception, match='validation_provider_live_adapter_unavailable'):
        freeze.bind_verified_snapshot(context.root, value)
    assert value == before


def test_harness_annotations_are_frozen_before_authorization_not_added_during_dispatch(context):
    from sap_business_agents_platform.runtime_policy import ORCHESTRATION_VERSION
    bound = freeze.bind_verified_snapshot(context.root, context.snapshot, harness=True)
    freeze.require_harness_binding(bound)
    assert bound['execution_flow'] == 'harness' and bound['orchestration_version'] == ORCHESTRATION_VERSION
    for change in ({'execution_flow': None}, {'orchestration_version': 'old'}):
        with pytest.raises(Exception, match='validation_runtime_flow_binding_required'):
            freeze.require_harness_binding({**bound, **change})
    with pytest.raises(Exception, match='validation_runtime_flow_binding_changed'):
        freeze.bind_verified_snapshot(context.root, {**context.snapshot, 'orchestration_version': 'old'}, harness=True)
