"""Offline shared draft-authoring regression. No native SDK, model or SAP calls."""
import asyncio
import copy
import json
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path

import pytest

from sap_business_agents_platform.agent_lifecycle import AgentLifecycleService
from sap_business_agents_platform.authoring_checks import candidate_business_output_issues
from sap_business_agents_platform.authoring_harness import (
    AuthoringHarnessError, CheckOutcome, RepairLoop, check_candidate, modification_preflight, public_checkpoints, public_issues,
)
from sap_business_agents_platform.authoring_workspace import AuthoringWorkspace
from sap_business_agents_platform.runtime_agent_authoring import feedback_prompt, repair_feedback, run_feedback
from sap_business_agents_platform.runtime_execution import FULL_ACCESS_POLICY


ROOT = Path(__file__).resolve().parents[1]


def package():
    value = AgentLifecycleService._blank_package('offline-draft', 'FI', {'zh': '离线草稿', 'en': 'Offline draft'})
    execution = value['manifest']['execution']
    execution['steps'].insert(0, {'id': 'read', 'executor': 'sap_read', 'operation': 'execute_plan',
        'readOnly': True, 'request': {'plan': {'service_name': 'OFFLINE_SERVICE', 'odata_version': '2.0', 'entity_set': 'Items',
            'http_method': 'GET', 'filters': []}}})
    value['manifest']['workflow'][0]['executionStepIds'].insert(0, 'read')
    return value


def field_change(value):
    result = copy.deepcopy(value)
    schema = result['manifest']['execution']['inputSchema']
    schema['properties']['cutoff'] = {'type': 'string', 'format': 'date',
        'title': {'zh': '过账截止日期', 'en': 'Posting Date Cutoff'},
        'x-sapba-server-default': 'business_date'}
    return result


def test_preflight_warns_without_loosening_metadata_or_save_gates():
    original = package()
    before = copy.deepcopy(original)
    preflight = modification_preflight(original)
    assert preflight['status'] == 'existing_blockers'
    assert preflight['existing_issues'] == [{'code': 'agent_business_output_contract_missing',
        'path': '/manifest/execution/outputSchema'}]
    metadata = copy.deepcopy(original)
    metadata['manifest']['title']['zh'] = '仅更改名称'
    assert check_candidate(metadata, original).status == 'passed'
    assert candidate_business_output_issues(metadata['manifest'])
    assert original == before


def test_unchanged_inherited_blocker_stops_after_one_candidate(tmp_path):
    original = package()
    workspace = AuthoringWorkspace(tmp_path, tmp_path / 'owned')
    workspace.source.mkdir(parents=True)
    calls, checks = [], []
    async def native(candidate, issues, remaining):
        calls.append(issues)
        return {'action': 'revise_agent', 'package': field_change(candidate)}
    with pytest.raises(AuthoringHarnessError, match='agent_business_output_contract_missing') as error:
        asyncio.run(repair_feedback(original, workspace, native, checks=checks))
    assert len(calls) == len(checks) == 1
    assert error.value.detail['authoring_convergence']['stop_reason'] == 'preexisting_blocker_unchanged'
    assert error.value.detail['validation_issues'][0]['path'] == '/manifest/execution/outputSchema'


def test_real_progress_on_business_obligations_can_still_be_repaired(tmp_path):
    original = package()
    workspace = AuthoringWorkspace(tmp_path, tmp_path / 'owned')
    workspace.source.mkdir(parents=True)
    calls, checks = [], []
    async def native(candidate, issues, remaining):
        calls.append(issues)
        execution = candidate['manifest']['execution']
        execution['outputSchema']['properties']['records'] = {'type': 'array', 'items': {'type': 'object'},
            'title': {'zh': '业务记录', 'en': 'Business records'}}
        execution['outputMapping']['records'] = []
        candidate['manifest']['outputs'] = {'zh': ['业务状态', '查询源完整性', '业务证据完整性', '业务记录'],
            'en': ['Business status', 'Query-source completeness', 'Business-evidence completeness', 'Business records']}
        if len(calls) == 2:
            execution['steps'][-1]['operation'] = 'evaluate_business_agent'
        return {'action': 'revise_agent', 'package': candidate}
    result = asyncio.run(repair_feedback(original, workspace, native, checks=checks))
    assert len(calls) == 2 and [item['status'] for item in checks] == ['repairable', 'passed']
    assert result['package']['manifest']['validation'] == original['manifest']['validation']


def test_prompt_reports_inherited_blockers_without_inventing_authorization():
    original = package()
    prompt = feedback_prompt(ROOT, feedback='Change the input labels only', locale='en', package=original)
    assert 'Controller-observed draft modification preflight:' in prompt
    assert 'agent_business_output_contract_missing' in prompt
    assert 'not permission to broaden' in prompt
    assert 'return\nclarify or reply' in prompt
    explanation = feedback_prompt(ROOT, feedback='Explain only', locale='en', package=original, intent='explain')
    assert 'Controller-observed draft modification preflight:' not in explanation


@pytest.mark.parametrize('provider', ['codex', 'workbuddy'])
@pytest.mark.parametrize('mode', ['blocked', 'metadata', 'clarify'])
def test_both_actual_driver_options_use_one_shared_failure_controller(tmp_path, monkeypatch, provider, mode):
    from sap_business_agents_platform.codex_planner import CodexPlanner
    from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner
    planner = (CodexPlanner if provider == 'codex' else WorkBuddyPlanner)(ROOT, 'offline-model')
    planner.data_root = tmp_path
    original = package()
    calls = []
    def prepare(self, value, **kwargs):
        self.source.mkdir(parents=True)
        self.base_commit = 'offline-source'
        self.base_digest = 'offline-digest'
        self.write_package(value)
    monkeypatch.setattr(AuthoringWorkspace, 'prepare', prepare)
    @asynccontextmanager
    async def session(**kwargs):
        yield {'preflight': {'status': 'synthetic_only'}}
    async def native(session, prompt, *, workspace, **kwargs):
        calls.append(prompt)
        if mode == 'blocked':
            workspace.write_package(field_change(original))
        elif mode == 'metadata':
            candidate = copy.deepcopy(original)
            candidate['manifest']['title']['zh'] = '仅名称修订'
            workspace.write_package(candidate)
        return {'action': 'clarify' if mode == 'clarify' else 'revise_agent',
            'summary': {'zh': '请核对业务缺口', 'en': 'Review the business blocker'}}, 'offline-thread'
    monkeypatch.setattr(planner._driver, 'feedback_session', session)
    monkeypatch.setattr(planner._driver, 'feedback_turn', native)
    def execute():
        return asyncio.run(run_feedback(planner, feedback='Change the cutoff field', locale='en', package=original,
            tool_policy=FULL_ACCESS_POLICY))
    if mode == 'blocked':
        with pytest.raises(AuthoringHarnessError) as error:
            execute()
        assert error.value.code == 'agent_business_output_contract_missing'
        assert error.value.detail['harness']['checks'][0]['status'] == 'blocked'
        assert error.value.detail['harness']['platform_apply'] == 'not_performed'
    else:
        decision = execute()
        assert decision['action'] == ('clarify' if mode == 'clarify' else 'revise_agent')
        assert len(decision['harness']['checks']) == (0 if mode == 'clarify' else 1)
    assert len(calls) == 1
    assert 'Controller-observed draft modification preflight:' in calls[0]


@pytest.mark.parametrize('provider', ['codex', 'workbuddy'])
@pytest.mark.parametrize('cleanup_failed', [False, True])
def test_lifecycle_persists_causes_checkpoints_and_preflight_without_saving(tmp_path, provider, cleanup_failed):
    from tests.test_agent_authoring import _service, create, feedback_payload, send_and_wait, Runtime
    from sap_business_agents_platform.models import AgentDraftUpdate
    service, store, _ = _service(tmp_path)
    class OfflineRuntime(Runtime):
        def snapshot(self):return {'provider_id': provider, 'model': 'offline-model'}
        def snapshot_for_binding(self, snapshot):return copy.deepcopy(snapshot)
        def pin_snapshot(self, *_):return nullcontext()
        def workbuddy_reserved(self):return True
        async def review_agent_feedback(self, **kwargs):
            self.calls.append(kwargs)
            raise AuthoringHarnessError('agent_business_output_contract_missing', detail={
                **({'cleanup_failure_code': 'runtime_cleanup_incomplete'} if cleanup_failed else {}),
                'validation_issues': [{'code': 'agent_business_output_contract_missing',
                    'path': '/manifest/execution/outputSchema', 'message': 'PRIVATE candidate body'}],
                'authoring_convergence': {'stop_reason': 'preexisting_blocker_unchanged', 'attempts': 1},
                'harness': {'checks': [{'round': 1, 'status': 'blocked', 'candidate_digest': 'a'*64,
                    'issues': [{'code': 'agent_business_output_contract_missing', 'path': '/manifest/execution/outputSchema'}],
                    'raw': 'PRIVATE SDK output'}]}})
    service.runtime = OfflineRuntime()
    draft = create(service)
    # A synthetic saved incomplete draft, never a real draft or business result.
    saved = copy.deepcopy(draft['package']['manifest'])
    saved['execution']['steps'] = package()['manifest']['execution']['steps']
    saved['workflow'][0]['executionStepIds'].insert(0, 'read')
    draft = service.update(draft['draft_id'], AgentDraftUpdate(expectedRevision=draft['revision'], manifest=saved))
    history = copy.deepcopy(draft['conversation'])
    payload = feedback_payload(draft, text='Change the input cutoff', request='offline-only')
    if provider == 'workbuddy':
        payload.execution_mode = 'trusted_local'; payload.trusted_local_confirmed = True
    _, result = asyncio.run(send_and_wait(service, draft, payload))
    turn = result['conversation'][-1]
    assert result['revision'] == draft['revision'] and result['package'] == draft['package']
    assert result['conversation'][:-1] == history
    assert turn['decision']['error_code'] == ('runtime_cleanup_incomplete' if cleanup_failed else 'agent_business_output_contract_missing')
    assert turn['decision']['authoring_preflight']['status'] == 'existing_blockers'
    assert turn['decision']['validation_issues'][0]['path'] == '/manifest/execution/outputSchema'
    assert turn['decision']['harness']['checks'][0]['status'] == 'blocked'
    assert turn['decision']['authoring_convergence']['attempts'] == 1
    assert turn['result_revision'] is None and not turn['diff']
    assert 'PRIVATE' not in json.dumps(turn)
    assert (store.get_agent_operation(draft['draft_id']) is not None) is cleanup_failed


def test_public_diagnostics_do_not_export_untrusted_payloads():
    raw = [{'code': 'PRIVATE payload', 'path': '/../../credentials', 'message': 'PRIVATE'}, None]
    assert public_issues(raw) == [{'code': 'definition_invalid', 'path': '/manifest'}]
    assert public_checkpoints([{'round': 1, 'status': 'blocked', 'candidate_digest': 'PRIVATE',
        'issues': raw, 'raw': 'PRIVATE'}]) == [{'round': 1, 'status': 'blocked',
            'issues': [{'code': 'definition_invalid', 'path': '/manifest'}]}]
    assert public_checkpoints([None, {'round': 1, 'status': {}}, {'round': True, 'status': 'passed'}]) == []


def test_preflight_does_not_turn_an_incomplete_manually_saved_schema_into_a_new_failure():
    original = package()
    original['manifest']['execution']['outputSchema']['properties'] = ['incomplete manual edit']
    assert modification_preflight(original)['status'] == 'not_evaluated'
    assert modification_preflight(original)['existing_issues'] == []


def test_new_repeated_errors_keep_their_real_causes_and_original_two_attempt_limit():
    calls = []
    async def revise(candidate, issues, remaining):
        calls.append(issues)
        return {'action': 'revise_agent', 'package': {'round': len(calls)}}
    async def check(*_):
        return CheckOutcome('repairable', ({'code': 'input_title_en_invalid',
            'path': '/manifest/execution/inputSchema/properties/cutoff/title/en', 'message': 'PRIVATE'},))
    with pytest.raises(AuthoringHarnessError, match='agent_harness_no_progress') as error:
        asyncio.run(RepairLoop().run({}, revise=revise, check=check, checkpoint=lambda *_: None, assert_current=lambda: None))
    assert len(calls) == 2
    assert error.value.detail['validation_issues'][0]['code'] == 'input_title_en_invalid'
    assert error.value.detail['authoring_convergence']['attempts'] == 2
    assert 'PRIVATE' not in json.dumps(error.value.detail)


@pytest.mark.parametrize('operation', ['', 'evidence_summary', 'evaluate_business_agent'])
@pytest.mark.parametrize('field', ['none', 'visible', 'hidden'])
@pytest.mark.parametrize('sap_read', [False, True])
def test_business_gate_truth_table_is_unchanged(operation, field, sap_read):
    value = package()['manifest']
    if not sap_read:
        value['execution']['steps'].pop(0)
    value['execution']['steps'][-1]['operation'] = operation
    if field != 'none':
        value['execution']['outputSchema']['properties']['records'] = {
            'type': 'array', 'x-sapba-display': {'visible': field == 'visible'}}
    expected_blocked = sap_read and not (operation not in {'', 'evidence_summary'} and field == 'visible')
    assert bool(candidate_business_output_issues(value)) is expected_blocked
