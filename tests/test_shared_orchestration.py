"""Offline behavior traces captured before refactoring, never regenerated in tests."""
import asyncio
import ast
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.codex_planner import CodexPlanner
from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner
from sap_business_agents_platform.runtime_contract import RuntimeRequest, RuntimeResult, RuntimeSession, RuntimeContractError
from sap_business_agents_platform.runtime_policy import OPERATIONS, ORCHESTRATION_VERSION, orchestration_digest, operation_seconds
from tests.runtime_trace_fixture import TraceClient, cases, normalize

ROOT = Path(__file__).resolve().parents[1]
BEFORE = json.loads((ROOT / 'tests/fixtures/codex-orchestration-before.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('operation', [op for op in cases() if op in OPERATIONS])
@pytest.mark.parametrize('provider', ['codex', 'workbuddy'])
def test_shared_business_trace_matches_original_codex(monkeypatch, tmp_path, provider, operation):
    trace = []
    if provider == 'codex':
        import openai_codex
        monkeypatch.setattr(openai_codex, 'AsyncCodex', lambda **options: TraceClient(trace, operation, **options))
        planner = CodexPlanner(tmp_path, model='fixture-model', reasoning_effort='max')
    else:
        planner = WorkBuddyPlanner(tmp_path, model='fixture-model')
        # The neutral seam is simulated. Real worker transport has separate
        # owned-process tests; matching SDK wire encoding is not required.
        planner.reasoning_effort = 'max'
        planner._driver = SimpleNamespace(provider_id=provider, client=lambda **options: TraceClient(trace, operation, **options))
    result = asyncio.run(getattr(planner, operation)(**copy.deepcopy(cases()[operation])))
    if operation in {'analyze_role_matching', 'review_role_matching_feedback'}:
        from sap_business_agents_platform.runtime_role_consolidation import VERSION, SCHEMA
        from tests.runtime_trace_fixture import sha
        # Preserve the immutable legacy comparison for understanding and paging.
        # Only the explicitly planned isolated final turn and Schema may change.
        assert trace[:-3] == BEFORE[operation]['trace'][:-2]
        assert trace[-3]['call'] == 'start'
        assert trace[-3]['options'] == {
            'cwd': '<repository>', 'sandbox': 'read_only', 'approval_mode': 'deny_all', 'model': 'fixture-model',
            'service_name': 'sap_business_agents_role_consolidation',
            'developer_instructions': 'Summarize only supplied frozen role understanding and catalog matches. Never call tools, read files, execute SAP, or modify files.'}
        from sap_business_agents_platform.codex_driver import native_output_schema
        expected_schema = native_output_schema(SCHEMA) if provider == 'codex' else SCHEMA
        assert trace[-2]['call'] == 'run' and trace[-2]['schema_sha256'] == sha(expected_schema)
        assert trace[-2]['effort'] == BEFORE[operation]['trace'][-2]['effort']
        assert trace[-2]['options'] == BEFORE[operation]['trace'][-2]['options']
        assert trace[-1] == BEFORE[operation]['trace'][-1]
        assert result['analysis'].pop('consolidation_contract') == VERSION
    else:
        assert trace == BEFORE[operation]['trace']
    expected = copy.deepcopy(BEFORE[operation]['result'])
    if operation == 'review_free_query_feedback':
        expected['reason'] = 'Offline'  # old fixture violated the declared string Schema
    assert normalize(result) == expected


def test_contract_snapshots_and_cross_provider_handles():
    binding, schema = {'provider_id': 'codex', 'model': 'frozen'}, {'type': 'object'}
    request = RuntimeRequest('codex', 'plan', 'planning', binding, 'question', schema,
                             session=RuntimeSession('codex', 'thread'), deadline=time.monotonic() + 1)
    binding['model'], schema['type'] = 'changed', 'string'
    assert request.binding['model'] == 'frozen' and request.output_schema['type'] == 'object'
    with pytest.raises(RuntimeContractError, match='provider_mismatch'):
        RuntimeRequest('workbuddy', 'plan', 'planning', {}, 'q', {}, session=request.session)
    with pytest.raises(RuntimeContractError, match='deadline_exceeded'):
        RuntimeRequest('codex', 'plan', 'planning', {}, 'q', {}, deadline=0).remaining()
    with pytest.raises(RuntimeContractError, match='not_complete'):
        RuntimeResult({}, request.session, cleanup_complete=False).final_response


@pytest.mark.parametrize('operation', OPERATIONS)
def test_operation_qualification_is_content_bound(operation):
    assert len(orchestration_digest(operation)) == 64
    assert orchestration_digest(operation) != orchestration_digest(next(op for op in OPERATIONS if op != operation))


@pytest.mark.parametrize('name', ['codex_planner.py', 'workbuddy_planner.py', 'workbuddy_harness.py',
    'workbuddy_authoring.py', 'workbuddy_sample.py', 'workbuddy_diagnostics.py'])
def test_native_compatibility_changes_invalidate_operation_qualification(name, monkeypatch):
    original = Path.read_bytes
    previous = orchestration_digest('author_draft')
    def changed(path):
        contents = original(path)
        return contents + b'\n# changed native compatibility behavior\n' if path.name == name else contents
    monkeypatch.setattr(Path, 'read_bytes', changed)
    assert orchestration_digest('author_draft') != previous


def test_budget_policy_is_shared_and_preserves_shorter_deadlines():
    for operation in OPERATIONS:
        assert operation_seconds('codex', operation, existing=123) == 123
    assert operation_seconds('workbuddy', 'compose_workflow') == 3600
    assert operation_seconds('codex', 'review_workflow_feedback') == 3600
    assert operation_seconds('workbuddy', 'analyze_role_matching') == 300


def test_old_workbuddy_qualification_is_not_reused_or_rewritten(tmp_path, monkeypatch):
    from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
    from sap_business_agents_platform.workbuddy_environment import atomic_json
    manager = WorkBuddyManager(tmp_path)
    previous = {'status': 'validated', 'permission_modes': ['bounded'], 'contract_version': '1.0'}
    atomic_json(manager.environment.root / 'state.json', {'capabilities': {'old:plan:1.0': previous}})
    # Use the registry accessor, without authentication/probe/model processes.
    monkeypatch.setattr(manager.environment, 'state', lambda: {'capabilities': {'old:plan:1.0': previous}})
    assert 'plan' not in manager.capabilities('old')
    assert manager.capabilities('old')['author_draft']['status'] == 'unverified'
    assert previous['status'] == 'validated'


def test_sdk_free_authorities_have_no_sdk_imports():
    names = ['shared_planner.py', 'runtime_prompts.py', 'runtime_contract.py', 'runtime_policy.py',
        'runtime_agent_authoring.py', 'runtime_workflow_authoring.py', 'runtime_harness_contract.py',
        'runtime_harness_result.py', 'runtime_query_contract.py', 'runtime_workflow_contract.py', 'runtime_role_contract.py',
        'runtime_harness.py', 'runtime_sample.py', 'runtime_diagnostics.py', 'runtime_role_consolidation.py']
    for name in names:
        tree = ast.parse((ROOT / 'src/sap_business_agents_platform' / name).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            imports = [node.module or ''] if isinstance(node, ast.ImportFrom) else [item.name for item in node.names] if isinstance(node, ast.Import) else []
            assert not any('openai_codex' in item or 'codebuddy_agent_sdk' in item or 'codex_planner' in item for item in imports), name


def test_inherited_deadline_never_renews():
    from sap_business_agents_platform.runtime_contract import deadline_scope, remaining_budget
    deadline = time.monotonic() + 3
    with deadline_scope(deadline):
        with deadline_scope(time.monotonic() + 3600):
            request = RuntimeRequest('workbuddy', 'compose_workflow', 'compose', {}, 'question', {})
            assert request.deadline == deadline
            assert 0 < remaining_budget(3600) <= 3


def test_optional_absent_grounding_field_is_not_a_confirmed_field():
    from sap_business_agents_platform.runtime_query_contract import preserve_grounding
    before = {'service_name': 'S', 'odata_version': '2.0', 'entity_set': 'E',
        'select_fields': ['Key', 'Unavailable'], 'filters': [{'field': 'Key', 'value': 'fixed'}], 'top': 1}
    after = {**before, 'select_fields': ['Key']}
    metadata = [{'ok': True, 'data': {'schema_authority': True, 'fields_truncated': False,
        'service': {'service_name': 'S', 'odata_version': '2.0'},
        'fields': [{'entity_set': 'E', 'field_name': 'Key'}]}}]
    preserve_grounding(before, after, schemas=metadata)
    with pytest.raises(RuntimeContractError):
        preserve_grounding(before, {**after, 'select_fields': []}, schemas=metadata)
    with pytest.raises(RuntimeContractError):
        preserve_grounding(before, {**after, 'filters': []}, schemas=metadata)
    metadata[0]['data']['fields_truncated'] = True
    with pytest.raises(RuntimeContractError):
        preserve_grounding(before, after, schemas=metadata)


def test_read_scope_allows_only_platform_declared_case_normalization():
    from sap_business_agents_platform.runtime_query_contract import ReadScope
    from sap_business_agents_platform.normalization import SapValueNormalizer
    scope = ReadScope('material abc100')
    scope.normalizer = SapValueNormalizer(ROOT / 'config/sap-value-normalization.json')
    plan = {'service_name': 'API_MATERIAL_STOCK_SRV', 'odata_version': '2.0', 'entity_set': 'A_MatlStkInAcctMod',
        'select_fields': ['Material'], 'filters': [{'field': 'Material', 'value': 'ABC100'}], 'top': 1}
    scope.check(plan)
    with pytest.raises(RuntimeContractError):
        scope.check({**plan, 'filters': [{'field': 'Material', 'value': 'OTHER100'}]})


def test_workbuddy_platform_handles_do_not_merge_equal_native_ids(tmp_path, monkeypatch):
    planner = WorkBuddyPlanner(tmp_path, 'frozen', runtime_snapshot={'provider_id': 'workbuddy', 'model': 'frozen'})
    async def native(*args, **kwargs):
        return {'status': 'ok'}, 'same-native-id'
    monkeypatch.setattr(planner, '_structured_turn', native)
    async def run():
        from sap_business_agents_platform.runtime_contract import deadline_scope
        with deadline_scope(time.monotonic() + 5):
            await bound_run()
    async def bound_run():
        async with planner._driver.client() as client:
            first, second = await client.thread_start(), await client.thread_start()
            await first.run('one', output_schema={})
            await second.run('two', output_schema={})
            assert first.id != second.id
            assert len(planner._driver.sessions[first.id]['history']) == 1
            planner.model = 'changed'
            with pytest.raises(RuntimeContractError, match='binding_changed'):
                await client.thread_resume(first.id)
    asyncio.run(run())


def test_workbuddy_legacy_handles_require_explicit_platform_context(tmp_path):
    planner = WorkBuddyPlanner(tmp_path, 'frozen', runtime_snapshot={'provider_id': 'workbuddy', 'model': 'frozen'})
    async def run():
        async with planner._driver.client() as client:
            with pytest.raises(RuntimeContractError, match='platform_context_missing'):
                await client.thread_resume('same-legacy-native-id')
        async with planner._driver.client(platform_context_restored=True) as client:
            first = await client.thread_resume('same-legacy-native-id')
            second = await client.thread_resume('same-legacy-native-id')
            assert first.id.startswith('workbuddy:') and second.id.startswith('workbuddy:')
            assert first.id != second.id
            assert 'same-legacy-native-id' not in planner._driver.sessions
            assert planner._driver.sessions[first.id]['binding'] == planner.runtime_snapshot
            with pytest.raises(ValueError, match='provider_mismatch'):
                await client.thread_resume('thr_codex')
    asyncio.run(run())


def test_shared_candidate_checker_is_not_replaced_by_native_completion(tmp_path):
    from sap_business_agents_platform.authoring_workspace import AuthoringWorkspace
    from sap_business_agents_platform.runtime_agent_authoring import repair_feedback
    from sap_business_agents_platform.authoring_harness import AuthoringHarnessError
    package = {'manifest': {'slug': 'invalid', 'validation': {}}, 'readme': 'text', 'rules': None, 'files': {}}
    workspace = AuthoringWorkspace(tmp_path, tmp_path / 'copy')
    workspace.source.mkdir(parents=True)
    checks = []
    async def native(candidate, issues, remaining):
        return {'action': 'revise_agent', 'package': candidate}
    with pytest.raises(AuthoringHarnessError, match='no_progress'):
        asyncio.run(repair_feedback(package, workspace, native, checks=checks))
    assert len(checks) == 2 and all(item['status'] == 'repairable' for item in checks)


@pytest.mark.parametrize('text', [None, ''])
def test_missing_terminal_diagnostics_are_not_a_verified_empty_output(text):
    from sap_business_agents_platform.runtime_diagnostics import output_diagnostic
    value = output_diagnostic(text, operation='free_query')
    if text is None:
        assert value['output_length'] is None and value['output_sha256'] is None
    else:
        import hashlib
        assert value['output_length'] == 0
        assert value['output_sha256'] == hashlib.sha256(b'').hexdigest()
