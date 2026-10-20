"""Offline scheduler -> auth -> pinned events -> Harness -> native-boundary replay.

The native boundary is a test double, not an SDK call or business qualification.
The real authentication assertions, reservation, projection and result checks run.
"""
import asyncio
import copy
import json
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from sap_business_agents_platform.acceptance_projection import AcceptanceProjectionSpec
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.engine import RunCoordinator
from sap_business_agents_platform.harness import HarnessToolBroker
from sap_business_agents_platform.models import RunCreate, RunMode, RunStatus, RuntimeSnapshot
from sap_business_agents_platform.runtime import RuntimeRouter
from sap_business_agents_platform.runtime_authentication import RuntimeAuthenticationService
from sap_business_agents_platform.runtime_policy import ORCHESTRATION_VERSION
from sap_business_agents_platform.sdk_manager import SDKManagerError
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
from sap_business_agents_platform.workbuddy_harness import WorkBuddyHarnessController
from sap_business_agents_platform.workbuddy_site import make_configuration
from sap_business_agents_platform.workbuddy_supervisor import WorkBuddySupervisor
from sap_business_agents_platform.runtime_validation_store import atomic
from tests.test_harness import FakeSapRead, FakeSkills

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('direct_baseline', [True, False])
@pytest.mark.parametrize('failure', [None, 'auth', 'admission', 'flow', 'terminal'])
def test_full_workbuddy_startup_never_reads_the_default_provider(tmp_path, monkeypatch, direct_baseline, failure):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    from sap_business_agents_platform.agent_acceptance_campaigns import _contract, _canonical_case
    from sap_business_agents_platform.manifests import AgentRepository
    from scripts.run_three_stage_acceptance import _acceptance_prompt, _projection_spec
    settings = Settings(repository_root=ROOT, data_root=tmp_path / 'data')
    store = RunStore(settings.database_path)
    site = make_configuration('china', 1)
    saved = RuntimeSnapshot.model_validate({'provider_id': 'workbuddy', 'sdk_id': 'codebuddy-agent-sdk',
        'model': 'deepseek-v4.1-flash', 'actual_model': 'deepseek-v4.1-flash',
        'configuration_digest': 'offline-frozen', 'reasoning_effort': None,
        'authentication_revision': 4, 'authentication_binding_revision': 4,
        'execution_flow': 'harness', 'orchestration_version': ORCHESTRATION_VERSION, **site}).model_dump(mode='json')
    if failure == 'flow':
        saved.pop('execution_flow')
        saved.pop('orchestration_version')
    atomic(settings.data_root / 'sdk-runtimes/authentication.json', {'version': 1, 'accounts': {
        'workbuddy': {'revision': 4, 'status': 'authenticated', 'authenticated': True, **site}}, 'operations': {}})
    auth = RuntimeAuthenticationService(settings.data_root / 'sdk-runtimes',
        {'workbuddy': SimpleNamespace(context=lambda: site)})
    trace, calls = [], []
    class Environment:
        root = tmp_path / 'worker-metadata'
        def assert_verification_binding(self, snapshot, *, formal):
            assert formal and snapshot == saved
            auth.assert_binding('workbuddy', snapshot)
            return {'environment_digest': 'offline-only'}
        def assert_operation(self, *_, **__):
            raise WorkBuddyError('runtime_disabled')  # Production admission remains closed.
    supervisor = WorkBuddySupervisor(Environment())
    def bound(snapshot, **_):
        auth.assert_binding('workbuddy', snapshot)
        return copy.deepcopy(snapshot)
    manager = SimpleNamespace(environment=supervisor.environment, supervisor=supervisor, bound_snapshot=bound)
    class GlobalManager:
        default_provider_id = 'codex'
        authentication_service = auth
        workbuddy = manager
        def runtime_snapshot(self, provider_id):
            raise SDKManagerError('Unconfigured isolated default', code='runtime_not_selectable',
                detail={'provider_id': provider_id, 'blockers': ['runtime_model_not_configured']})
    @contextmanager
    def events(sink):
        assert router.current_provider_id == 'workbuddy' and router.current_model_id == saved['model']
        trace.append('events_entered_after_runtime')
        sink('offline_bound_event', {'provider_id': 'workbuddy'})
        try:
            yield
        finally:
            trace.append('events_exited')
    router = RuntimeRouter(GlobalManager(), {'workbuddy': SimpleNamespace(bind_events=events)})
    class NoSap(FakeSapRead):
        async def validate_plan(self, *_): pytest.fail('No SAP reads in startup tests')
        async def execute_plan(self, *_): pytest.fail('No SAP reads in startup tests')
    broker = HarnessToolBroker(settings, store, NoSap(), FakeSkills())
    owner = WorkBuddyHarnessController(settings, store, broker, manager)
    owner.authentication_service = auth
    coordinator = RunCoordinator(settings, store, SimpleNamespace(), NoSap(), FakeSkills(), router)
    coordinator.workbuddy_harness = owner
    async def native(**kwargs):
        supervisor.check_operation(kwargs['snapshot'], kwargs['operation'])
        assert router.current_provider_id == 'workbuddy'
        assert kwargs['snapshot'] == saved and auth.leases['workbuddy'] == 1
        assert supervisor.reserved and kwargs['operation'] == ('acceptance_baseline' if direct_baseline else 'acceptance_free_query')
        assert 0 < kwargs['seconds'] <= 600 and callable(kwargs['tool_handler'])
        assert kwargs['payload']['timeout_ms'] <= 600000
        assert Path(kwargs['payload']['cwd']).is_relative_to(settings.data_root)
        assert not (Path(kwargs['payload']['cwd']) / 'agent-package').exists()
        calls.append(kwargs['operation'])
        trace.append('native_boundary')
        payload = {'status': 'inconclusive', 'intent': 'read', 'clarification_question': '',
            'input_kind': None, 'input_field': None, 'summary': {'zh': '仅离线启动链测试', 'en': 'Offline startup only'},
            'source_complete': False, 'business_complete': False, 'missing_evidence': ['offline_probe'],
            'evidence_refs': [], 'executed_plans': [], 'presentation': None, 'acceptance_projection': None}
        if failure != 'terminal':
            Draft202012Validator(kwargs['payload']['output_schema']).validate(payload)
        else:
            payload['input_kind'] = 'illegal-value'
        return {'text': json.dumps(payload), 'output_format': 'native_json_schema',
            'actual_model': saved['model'], 'session_id': 'offline-native'}
    supervisor.run = native
    manifest = AgentRepository(ROOT / 'agents').get('mm-po-gr-status')
    contract = _contract(manifest)
    case = _canonical_case(manifest, {'purchase_order': '4500001466',
        'schedule_line_delivery_date': '2022-11-15', 'purchasing_organization': '1010'}, 'offline-case', contract)
    query = _acceptance_prompt(case, contract)
    async def scenario():
        verification = (supervisor.verification(saved, {'acceptance_baseline', 'acceptance_free_query'})
            if failure != 'admission' else nullcontext())
        with verification:
            await coordinator.start()  # Real scheduler workers inherit the frozen context.
            if failure == 'auth':
                auth.state['accounts']['workbuddy']['revision'] = 5
            try:
                run_id = await coordinator.submit_acceptance_query(RunCreate(mode=RunMode.free_query,
                    query=query, acceptanceSpec=AcceptanceProjectionSpec.model_validate(_projection_spec(contract))),
                    runtime=saved, direct_baseline=direct_baseline, hard_limit_seconds=600)
                for _ in range(500):
                    record = store.get_run(run_id)
                    if record.status in {RunStatus.inconclusive, RunStatus.failed, RunStatus.completed}:
                        break
                    await asyncio.sleep(.01)
                else:
                    pytest.fail('Offline startup did not finish')
                assert record.runtime.authentication_binding_revision == 4
                assert router.current_provider_id == 'codex'
                assert store.list_harness_tool_calls(run_id) == []
                if failure in {'auth', 'admission', 'flow'}:
                    assert record.status == RunStatus.failed and not calls
                    assert record.error['code'] == ('runtime_authentication_binding_changed' if failure == 'auth' else 'runtime_disabled')
                elif failure == 'terminal':
                    assert record.status == RunStatus.failed and calls
                    assert record.error['code'] == 'acceptance_report_validation_failed'
                else:
                    assert record.status == RunStatus.inconclusive and len(calls) == 1
                    assert trace == ['events_entered_after_runtime', 'native_boundary', 'events_exited']
                assert auth.leases.get('workbuddy', 0) == 0
                assert supervisor._reserved.get() is False and supervisor.reconcile() == []
            finally:
                await coordinator.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize('fails', [False, True])
def test_codex_saved_binding_and_event_cleanup_do_not_depend_on_other_default(tmp_path, fails):
    settings = Settings(repository_root=ROOT, data_root=tmp_path / 'data')
    store = RunStore(settings.database_path)
    snapshot = {'provider_id': 'codex', 'sdk_id': 'codex-python-sdk', 'model': 'codex-frozen',
        'configuration_digest': 'codex-unchanged', 'reasoning_effort': 'max',
        'authentication_binding_revision': 2, 'execution_flow': 'harness'}
    atomic(settings.data_root / 'sdk-runtimes/authentication.json', {'version': 1, 'accounts': {
        'codex': {'revision': 2, 'status': 'authenticated', 'authenticated': True}}, 'operations': {}})
    auth = RuntimeAuthenticationService(settings.data_root / 'sdk-runtimes', {'codex': SimpleNamespace(context=lambda: {})})
    class Manager:
        default_provider_id = 'workbuddy'
        authentication_service = auth
        def runtime_snapshot(self, provider):
            pytest.fail('Saved Codex task must not read the disabled WorkBuddy default')
    trace = []
    @contextmanager
    def bind(sink):
        assert router.current_provider_id == 'codex' and router.current_model_id == 'codex-frozen'
        assert router.current_reasoning_effort == 'max'
        trace.append('events_entered')
        sink('codex_event_fixture', {'provider_id': 'codex'})
        try:
            yield
        finally:
            trace.append('events_exited')
    router = RuntimeRouter(Manager(), {'codex': SimpleNamespace(bind_events=bind)})
    coordinator = RunCoordinator(settings, store, SimpleNamespace(), FakeSapRead(), FakeSkills(), router,
        harness=SimpleNamespace())
    store.create_run('codex-offline', RunCreate(mode=RunMode.free_query, query='Offline'), runtime=snapshot)
    async def harness(run_id):
        assert router.current_provider_id == 'codex' and router.current_model_id == snapshot['model']
        assert router.current_reasoning_effort == snapshot['reasoning_effort']
        trace.append('codex_harness')
        if fails:
            raise RuntimeError('offline_sentinel')
    coordinator._execute_free_query_harness = harness
    if fails:
        with pytest.raises(RuntimeError, match='offline_sentinel'):
            asyncio.run(coordinator._execute_free_query('codex-offline'))
    else:
        asyncio.run(coordinator._execute_free_query('codex-offline'))
    assert trace == ['events_entered', 'codex_harness', 'events_exited']
    assert router.current_provider_id == 'workbuddy'
    assert router._authentication_snapshot.get() is None
    assert store.get_run('codex-offline').runtime.model == 'codex-frozen'
    assert store.get_run('codex-offline').runtime.reasoning_effort == 'max'
