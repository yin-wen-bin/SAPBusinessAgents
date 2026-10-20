"""Current live-fixture preparation only; mock output never grants qualification."""
import importlib.util
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('wb_operation_validation_test', ROOT / 'scripts/verify-workbuddy-operations.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_current_live_case_imports_are_tool_free_and_exclude_retired_operations(runner, tmp_path):
    before = set(tmp_path.iterdir())
    result = runner.check_cases(tmp_path / 'batch')
    assert result['phase'] == 'passed' and result['model_calls'] == result['sap_calls'] == 0
    assert result['operation_count'] == len(runner.OPERATIONS)
    assert set.union(*(value[2] for value in runner.FAMILIES.values())).isdisjoint({'plan', 'ground_plan', 'summarize'})
    assert set(tmp_path.iterdir()) == before


def test_case_modes_and_flags_use_current_authoring_paths(runner, tmp_path):
    explain = runner.load_case('agent_explain', tmp_path)
    revise = runner.load_case('agent_modify', tmp_path)
    assert explain.EXPLAIN_ONLY and not explain.FILE_MODE
    assert revise.FILE_MODE and revise.EXACT_SUFFIX and revise.NATIVE_FEEDBACK
    assert runner.FAMILIES['agent_explain'][3] == {'bounded'}
    assert runner.FAMILIES['agent_modify'][3] == {'trusted_local'}
    assert runner.FAMILIES['workflow_v2'][3] == {'trusted_local'}


def test_batch_cannot_target_repository_or_historical_reports(runner):
    for value in (str(ROOT), str(ROOT / '.local-data/workbuddy-validation'), str(ROOT / '.local-data/workbuddy-baseline')):
        with pytest.raises(ValueError, match='batch_directory_invalid'):
            runner.batch_path(value)


def test_author_fixture_passes_current_creation_gate_without_sap_or_model(runner, tmp_path):
    import asyncio
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.database import RunStore
    from sap_business_agents_platform.factory import AgentDraftService
    author = runner.load_case('author', tmp_path)
    settings = Settings(repository_root=ROOT, data_root=tmp_path / 'data', draft_root=tmp_path / 'drafts')
    store = RunStore(settings.database_path)
    source = author.create_source(store, None, 'synthetic-source')

    class Runtime:
        calls = 0
        def supports(self, operation): return operation == 'author_draft'
        async def author_draft(self, **kwargs):
            self.calls += 1
            assert kwargs['evidence'][0]['source'] == 'complete_synthetic_fixture_not_sap'
            return {'content_zh': '完整离线合成样例，未访问SAP。',
                    'content_en': 'Complete offline synthetic fixture; SAP not read.', 'rule_notes': []}

    runtime = Runtime()
    factory = AgentDraftService(settings, store, runtime)
    assert factory.source_eligibility('synthetic-source') is None
    draft = asyncio.run(factory.create_from_run('synthetic-source', module='MM'))
    retry = asyncio.run(factory.create_from_run('synthetic-source', module='MM'))
    assert runtime.calls == 1 and draft.draft_id == retry.draft_id
    assert store.get_run('synthetic-source').result == source
    assert 'offline' in (Path(draft.path) / 'content.en.md').read_text(encoding='utf-8')


@pytest.fixture
def frozen_batch(runner, tmp_path, monkeypatch):
    from sap_business_agents_platform.workbuddy_environment import atomic_json, WorkBuddyError
    from sap_business_agents_platform.workbuddy_site import make_configuration
    route = make_configuration('china', 1)
    state = dict(default_model_id='hy4-preview', authentication_revision=4, authenticated=True, **route)
    raw = dict(provider_id='workbuddy', sdk_id='codebuddy-agent-sdk', version='0.3.247', cli_version='2.141.0',
        model='hy4-preview', actual_model='hy4-preview', model_identity_known=True, model_identity_source='assistant_message',
        reasoning_effort=None, configuration_digest='c'*64, model_check_digest='m'*64, environment_digest='e'*64,
        authentication_revision=4, operation_capabilities={}, runtime_configuration_revision=10, selected_at='initial', **route)
    account_path = tmp_path / '.local-data/sdk-runtimes/authentication.json'
    account = dict(status='authenticated', authenticated=True, revision=4, **route)
    atomic_json(account_path, {'version': 1, 'accounts': {'workbuddy': account}, 'operations': {}})
    class Environment:
        root = tmp_path / '.local-data/runtimes/workbuddy'
        def state(self): return state
        def assert_verification_binding(self, snapshot, **_):
            if state.get('model_failed'):
                raise WorkBuddyError('runtime_model_check_required')
        def maintenance(self):
            from contextlib import nullcontext
            return nullcontext()
    class Manager:
        environment = Environment()
        calls = 0
        registrations = 0
        def site_configuration(self, **_): return {key: state[key] for key in route}
        def snapshot(self): return {'enabled': False}
        def runtime_snapshot(self, *_, **__): return copy.deepcopy(raw)
        async def check(self): raise AssertionError('must not reauthenticate')
        async def check_model(self, *_): raise AssertionError('must not reprobe')
        def record_operation_validation(self, *_args, **_kwargs): self.registrations += 1
    manager = Manager()
    async def native(**kwargs):
        manager.calls += 1
        if kwargs['payload'].get('late_change'): state['authentication_revision'] = 5
        return {'actual_model': 'hy4-preview', 'model_identity_source': 'assistant_message'}
    manager.supervisor = SimpleNamespace(run=native, pending=lambda: False, close=lambda: asyncio.sleep(0))
    monkeypatch.setattr(runner, 'require_idle', lambda *_: None)
    monkeypatch.setattr(runner, 'controls', lambda *_: {'codex': 'unchanged'})
    monkeypatch.setattr(runner, 'fixture_digests', lambda: {})
    monkeypatch.setattr(runner, 'orchestration_digest', lambda op: 'digest:' + op)
    monkeypatch.setattr(runner.live_guard, 'orchestration_digest', lambda op: 'digest:' + op)
    catalog = {'digest': 'catalog', 'items': [], 'runtime_catalog': {'pages': []}}
    monkeypatch.setattr(runner.live_guard, 'catalog_snapshot', lambda *_: copy.deepcopy(catalog))
    batch = tmp_path / 'batch'
    atomic_json(batch / 'before.json', {'controls': {'codex': 'unchanged'}, 'fixture_digests': {},
        'operation_digests': {op: 'digest:' + op for op in runner.OPERATIONS}})
    return SimpleNamespace(manager=manager, state=state, raw=raw, account=account,
        account_path=account_path, batch=batch, catalog=catalog, save=atomic_json)


def test_freeze_current_reuses_checks_without_model_or_sap_calls(runner, frozen_batch):
    f = frozen_batch
    result = runner.freeze_current(f.manager, f.batch)
    saved = json.loads((f.batch / 'runtime-snapshot.json').read_text(encoding='utf-8'))
    assert result['model_calls'] == result['sap_calls'] == f.manager.calls == 0
    assert result['target_operations'] == 11 and saved['authentication_binding_revision'] == 4
    freeze = json.loads((f.batch / 'freeze.json').read_text(encoding='utf-8'))
    assert set(freeze['allowed_operations']) == runner.NON_SAP
    assert freeze['case_parameters']['roles']['description']
    with pytest.raises(AssertionError, match='already_frozen'):
        runner.freeze_current(f.manager, f.batch)


def test_explicit_checked_model_is_frozen_without_changing_default(runner, frozen_batch):
    f = frozen_batch
    f.raw.update(model='deepseek-v4.1-flash', actual_model='deepseek-v4.1-flash')
    calls = []
    original = f.manager.runtime_snapshot
    def snapshot(model, **kwargs):
        calls.append(model)
        return original(model, **kwargs)
    f.manager.runtime_snapshot = snapshot
    result = runner.freeze_current(f.manager, f.batch, 'deepseek-v4.1-flash')
    saved = json.loads((f.batch / 'freeze.json').read_text(encoding='utf-8'))
    assert saved['validation_model_id'] == result['binding']['model'] == 'deepseek-v4.1-flash'
    assert f.state['default_model_id'] == 'hy4-preview' and f.manager.calls == 0
    assert runner.check_frozen(f.manager, f.batch)['scope'] == 'china_non_sap'
    assert all(model == 'deepseek-v4.1-flash' for model in calls)
    saved['validation_model_id'] = 'hy4-preview'
    f.save(f.batch / 'freeze.json', saved)
    with pytest.raises(AssertionError, match='frozen_model_changed'):
        runner.check_frozen(f.manager, f.batch)


def test_explicit_model_does_not_accept_an_alias_identity(runner, frozen_batch):
    f = frozen_batch
    with pytest.raises(AssertionError, match='model_identity_changed'):
        runner.freeze_current(f.manager, f.batch, 'deepseek-v4.1-flash')
    assert not (f.batch / 'freeze.json').exists() and f.manager.calls == 0


@pytest.mark.parametrize('change', ['expired', 'cross_site', 'revision', 'model'])
def test_invalid_existing_checks_never_dispatch_or_freeze(runner, frozen_batch, change):
    f = frozen_batch
    if change == 'expired': f.account['status'] = 'expired'
    if change == 'cross_site': f.account['service_site'] = 'international'
    if change == 'revision': f.account['revision'] = 5
    if change == 'model': f.state['model_failed'] = True
    f.save(f.account_path, {'accounts': {'workbuddy': f.account}, 'operations': {}})
    with pytest.raises(Exception): runner.freeze_current(f.manager, f.batch)
    assert f.manager.calls == 0 and not (f.batch / 'freeze.json').exists()


def test_monitored_dispatch_checks_binding_before_and_after_sdk(runner, frozen_batch):
    f = frozen_batch
    runner.freeze_current(f.manager, f.batch)
    snapshot = json.loads((f.batch / 'runtime-snapshot.json').read_text(encoding='utf-8'))
    runner.live_guard.configure(f.batch, {'author_draft'}, {'bounded'})
    runner.live_guard.attach_validation_monitor(f.manager, snapshot, f.batch / 'events.jsonl')
    async def run():
        with pytest.raises(AssertionError, match='sap_access_forbidden'):
            await f.manager.supervisor.run(snapshot=snapshot, operation='author_draft', mode='bounded', payload={'tools': ['sap_read']})
        assert f.manager.calls == 0
        with pytest.raises(AssertionError, match='authentication_revision_changed'):
            await f.manager.supervisor.run(snapshot=snapshot, operation='author_draft', mode='bounded', payload={'late_change': True})
        with pytest.raises(AssertionError, match='authentication_revision_changed'):
            f.manager.record_operation_validation(snapshot, 'author_draft', evidence={'sap_calls': 0})
    asyncio.run(run())
    assert f.manager.calls == 1 and f.manager.registrations == 0


def test_frozen_catalog_and_runtime_changes_are_rejected(runner, frozen_batch):
    f = frozen_batch
    runner.freeze_current(f.manager, f.batch)
    f.catalog['items'].append({'agent_id': 'changed'})
    with pytest.raises(AssertionError, match='catalog_changed'): runner.check_frozen(f.manager, f.batch)
    f.catalog['items'].clear()
    f.raw['model_check_digest'] = 'changed'
    with pytest.raises(AssertionError, match='runtime_identity_changed'): runner.check_frozen(f.manager, f.batch)


def test_family_attempt_is_once_only_even_after_failure(runner, frozen_batch):
    f = frozen_batch
    f.save(f.batch / 'author-summary.json', {'phase': 'failed'})
    with pytest.raises(AssertionError, match='already_attempted'):
        asyncio.run(runner.dispatch(f.manager, 'author', f.batch))
    assert f.manager.calls == 0


def test_role_wrapper_delegates_with_same_batch_not_current_defaults(runner, tmp_path, monkeypatch):
    roles = runner.load_case('roles', tmp_path / 'batch')
    calls = []
    async def verify(arguments):
        calls.append(arguments)
        return 0
    monkeypatch.setattr(roles.role_case, 'verify', verify)
    asyncio.run(roles.main())
    assert calls[0].provider == 'workbuddy' and calls[0].batch == roles.HERE
    assert Path(calls[0].data_root).is_relative_to(roles.HERE)
