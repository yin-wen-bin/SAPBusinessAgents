"""Offline structural replay. No SDK, model, SAP, auth CLI or live state writes."""
import asyncio
import copy
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.runtime_query_contract import plans
from sap_business_agents_platform.runtime_contract import RuntimeContractError
from sap_business_agents_platform.runtime_authentication import error
from sap_business_agents_platform.runtime_terminal_contract import TerminalChecks
from sap_business_agents_platform.models import RunPresentation
from tests.test_workbuddy_post_use_auth_sync import fixture, drain
from tests.test_workbuddy_harness_nullable import schema, envelope


@pytest.mark.parametrize('field,value,allowed', [
    ('schema_version', 'v1', ['1.0']), ('schema_version', 1, ['1.0']),
    ('odata_version', 'V2', ['2.0', '4.0']), ('odata_version', 'v4', ['2.0', '4.0'])])
def test_minimax_invalid_protocol_spelling_gets_safe_exact_feedback(field, value, allowed):
    candidate = {'service_name': 'S', 'odata_version': '2.0', 'entity_set': 'Items', field: value}
    before = copy.deepcopy(candidate)
    with pytest.raises(RuntimeContractError) as caught:
        plans(candidate)
    issue = caught.value.detail['validation_issues'][0]
    assert issue['path'] == '/' + field and issue['allowed_values'] == allowed
    assert 'external reads' in issue['repair_action']
    assert candidate == before  # No platform repair or inferred value.


def test_nested_plan_feedback_resolves_the_same_contract_and_never_echoes_unknown_keys():
    candidate = {'plan_kind': 'multi_step', 'service_name': 'S', 'odata_version': '2.0',
                 'steps': [{'entity_set': 'Items', 'schema_version': 'secret-invalid-value'}]}
    with pytest.raises(RuntimeContractError) as caught: plans(candidate)
    assert caught.value.detail['validation_issues'][0]['path'] == '/steps/0/schema_version'
    assert caught.value.detail['validation_issues'][0]['allowed_values'] == ['1.0']
    assert 'secret-invalid-value' not in json.dumps(caught.value.detail)
    with pytest.raises(RuntimeContractError) as caught:
        plans({'private-unknown-property': 'private-value'})
    assert 'private-' not in json.dumps(caught.value.detail)


@pytest.mark.parametrize('tool', ['sap_query_validate', 'sap_query_execute'])
def test_real_broker_and_mcp_preserve_shared_feedback_without_external_reads(tmp_path, tool):
    from tests.test_runtime_report_contract import make_broker
    from sap_business_agents_platform.mcp_server import _result
    broker, store, run, token = make_broker(tmp_path)
    candidate = {'plan': {'service_name': 'S', 'entity_set': 'Items', 'odata_version': 'V2'}}
    before = copy.deepcopy(candidate)
    result = asyncio.run(broker.handle(run, token, tool, candidate))
    assert result['ok'] is False
    assert result['validation_issues'][0]['allowed_values'] == ['2.0', '4.0']
    wire = _result(result)
    assert wire['isError'] is True and wire['structuredContent']['validation_issues'] == result['validation_issues']
    assert store.list_harness_tool_calls(run)[-1]['output']['validation_issues'] == result['validation_issues']
    assert candidate == before


@pytest.mark.parametrize('code', ['workbuddy_upstream_error', 'workbuddy_terminal_correction_stalled'])
@pytest.mark.parametrize('ownership', ['drained', 'active', 'pending', 'unreadable', 'absent'])
def test_failed_sdk_return_only_reconciles_after_owned_cleanup_proof(tmp_path, code, ownership):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        revision = service._account('workbuddy')['revision']
        def pending():
            if ownership == 'unreadable': raise OSError('private-job-path')
            return [{}] if ownership == 'pending' else []
        if ownership != 'absent':
            adapter.workbuddy = SimpleNamespace(supervisor=SimpleNamespace(
                active={'owned': {}} if ownership == 'active' else {}, pending=pending))
        original = error(code)
        with pytest.raises(Exception) as caught:
            async with service.use('workbuddy', service.bind('workbuddy', {})):
                adapter.stamp['cache_digest'] = 'sdk-updated-cache'
                raise original
        assert caught.value is original  # Background auth cannot hide business failure.
        await drain(service)
        assert adapter.calls == (['check', 'check'] if ownership == 'drained' else ['check'])
        assert service.view('workbuddy')['authenticated'] is (True if ownership == 'drained' else None)
        assert service._account('workbuddy')['revision'] == revision
        assert 'private-job-path' not in json.dumps(service._post_use_status)
        await service.close()
    asyncio.run(run())


def test_exception_reconciliation_failed_official_check_is_not_retried(tmp_path):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        adapter.workbuddy = SimpleNamespace(supervisor=SimpleNamespace(active={}, pending=lambda: []))
        adapter.negative = True
        original = error('workbuddy_upstream_error')
        with pytest.raises(Exception) as caught:
            async with service.use('workbuddy', service.bind('workbuddy', {})):
                adapter.stamp['cache_digest'] = 'renewed'; raise original
        assert caught.value is original
        await drain(service)
        for _ in range(10): assert service.view('workbuddy')['authenticated'] is not True
        assert adapter.calls == ['check', 'check']
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('code', ['workbuddy_authentication_required',
    'workbuddy_authentication_refresh_failed', 'workbuddy_authentication_confirmation_required',
    'runtime_authentication_binding_changed', 'runtime_cleanup_incomplete',
    'workbuddy_deadline_exceeded', 'runtime_deadline_exceeded', 'workbuddy_cancelled'])
def test_security_or_unconfirmed_exit_does_not_schedule_even_with_empty_supervisor(tmp_path, code):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        adapter.workbuddy = SimpleNamespace(supervisor=SimpleNamespace(active={}, pending=lambda: []))
        with pytest.raises(Exception):
            async with service.use('workbuddy', service.bind('workbuddy', {})):
                adapter.stamp['cache_digest'] = 'changed'; raise error(code)
        await drain(service)
        assert adapter.calls == ['check'] and service.view('workbuddy')['authenticated'] is None
        await service.close()
    asyncio.run(run())


def test_codex_exception_does_not_observe_or_reconcile_workbuddy(tmp_path):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        service._observation = lambda *_: pytest.fail('Codex must not observe WorkBuddy')
        with pytest.raises(Exception):
            async with service.use('codex', service.bind('codex', {})):
                raise error('runtime_native_execution_failed')
        assert not service._post_use_checks and service.adapters['codex'].calls == []
        assert adapter.calls == ['check']
        await service.close()
    asyncio.run(run())


def test_mimo_fragment_is_not_a_terminal_and_three_stagnant_candidates_stop():
    checks = TerminalChecks(schema())
    candidate = {'input_field': None}
    before = copy.deepcopy(candidate)
    for index in range(3):
        result = checks.check(candidate, submission_id=f'{index:064x}',
            origin='formatter_hook', phase='native_candidate')
        assert result['stop'] is (index == 2)
        paths = {v['path'] for v in result['repair_feedback']['validation_issues']}
        assert {'/status', '/input_kind', '/presentation'} <= paths
        assert not checks.accepts_native_result(json.dumps(envelope()))
    assert candidate == before


def test_step_amount_problem_is_not_confused_with_shape_validity():
    # Documents the current boundary; arbitrary narrative arithmetic has no
    # structured operand/evidence binding. Do not add a locale-dependent regex
    # that silently rewrites model prose or claims all financial facts checked.
    value = {'title': {'zh': '合成报告', 'en': 'Synthetic report'}, 'blocks': [
        {'type': 'notice', 'text': {'zh': '6项 -3749；2项 -275；合计 -3774',
                                   'en': 'Six -3749; two -275; total -3774'}}]}
    assert RunPresentation.model_validate(value)
    assert Decimal('-3749') + Decimal('-275') != Decimal('-3774')


def test_old_confirmed_sync_is_not_displayed_for_a_new_cache_observation(tmp_path):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        service._post_use_status['workbuddy'] = {'status': 'confirmed'}
        adapter.stamp['cache_digest'] = 'new-exception-cache'
        before = service.path.read_bytes()
        view = service.view('workbuddy')
        assert view['authentication_context_status'] == 'check_required'
        assert view['authentication_sync']['status'] == 'blocked'
        assert service.path.read_bytes() == before and adapter.calls == ['check']
        await service.close()
    asyncio.run(run())
