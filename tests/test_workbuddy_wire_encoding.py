"""Native wire encoding without business repair, live SDK or SAP calls."""
import asyncio
import copy
import json
import time

import pytest

from sap_business_agents_platform.runtime_contract import RuntimeContractError, deadline_scope
from sap_business_agents_platform.runtime_prompts import PLANNER_OUTPUT_SCHEMA, ROLE_MATCHING_OUTPUT_SCHEMA
from sap_business_agents_platform.runtime_role_contract import (
    COLLECTIONS, native_output_schema, canonical_output, decode,
)
from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner, WorkBuddyRuntimeError


def analysis():
    result = {name: [] for name in COLLECTIONS}
    result.update(document_issues=[], catalog_evaluation={}, non_sap_operation_count=0)
    result['operations'] = [{'operation_id': 'op_1', 'name': {'zh': '引号"与换行\n', 'en': 'Quote " and newline\n'},
        'evidence_refs': [{'document_id': 'd1', 'chunk_id': 'c1'}]}]
    return result


def test_role_native_wire_is_typed_and_lossless_without_mutating_shared_schema():
    before = copy.deepcopy(ROLE_MATCHING_OUTPUT_SCHEMA)
    wire = native_output_schema(before)
    assert wire['properties']['analysis_json']['type'] == 'object'
    assert before == ROLE_MATCHING_OUTPUT_SCHEMA
    raw = {'analysis_json': analysis(), 'summary_zh': '有引用的离线材料', 'summary_en': 'Cited offline material'}
    original = copy.deepcopy(raw)
    canonical = canonical_output(raw, before, operation='analyze_role_matching')
    assert raw == original
    assert json.loads(canonical['analysis_json']) == raw['analysis_json']
    assert canonical['summary_zh'] == raw['summary_zh']
    assert decode(canonical['analysis_json'], {'documents': [{'document_id': 'd1', 'chunks': [{'chunk_id': 'c1'}]}]},
                  'analyze_role_matching') == raw['analysis_json']
    assert native_output_schema(PLANNER_OUTPUT_SCHEMA) == PLANNER_OUTPUT_SCHEMA


@pytest.mark.parametrize('value', ['{"invalid":"sensitive-original"', [], None])
def test_role_native_wire_rejects_non_object_without_text_repair(value):
    with pytest.raises(RuntimeContractError) as error:
        canonical_output({'analysis_json': value, 'summary_zh': '', 'summary_en': ''},
                         ROLE_MATCHING_OUTPUT_SCHEMA, operation='analyze_role_matching')
    assert any(issue['path'] == '/analysis_json' and issue['constraint'] == 'type'
               for issue in error.value.detail['validation_issues'])
    assert 'sensitive-original' not in json.dumps(error.value.detail)


@pytest.mark.parametrize('patch', [{'operations': 'not-an-array'}, {'non_sap_operation_count': -1},
    {'operations': [{'evidence_refs': []}]}])
def test_role_native_wire_retains_types_and_evidence_requirements(patch):
    with pytest.raises(RuntimeContractError):
        canonical_output({'analysis_json': {**analysis(), **patch}, 'summary_zh': '', 'summary_en': ''},
                         ROLE_MATCHING_OUTPUT_SCHEMA, operation='analyze_role_matching')


def test_native_role_encoding_does_not_fabricate_or_validate_unknown_citations():
    raw = {'analysis_json': analysis(), 'summary_zh': '', 'summary_en': ''}
    raw['analysis_json']['operations'][0]['evidence_refs'][0]['chunk_id'] = 'unrelated'
    canonical = canonical_output(raw, ROLE_MATCHING_OUTPUT_SCHEMA, operation='analyze_role_matching')
    assert json.loads(canonical['analysis_json']) == raw['analysis_json']
    with pytest.raises(RuntimeContractError):
        decode(canonical['analysis_json'], {'documents': [{'document_id': 'd1', 'chunks': [{'chunk_id': 'c1'}]}]},
               'analyze_role_matching')


def test_workbuddy_review_uses_native_schema_without_changing_budget_or_permissions(tmp_path):
    from sap_business_agents_platform.runtime_prompts import WORKFLOW_REVIEW_OUTPUT_SCHEMA
    observed = []
    output = {'verdict': 'pass', 'summary': {'zh': '合法', 'en': 'Valid'}, 'issues': []}
    async def native(**kwargs):
        observed.append(kwargs)
        return {'text': json.dumps(output), 'output_format': 'native_json_schema',
                'actual_model': 'hy4-preview', 'session_id': 'native-id'}
    planner = WorkBuddyPlanner(tmp_path, 'hy4-preview', runtime_snapshot={'provider_id': 'workbuddy', 'model': 'hy4-preview'})
    planner.supervisor.run = native
    result = asyncio.run(planner.review_workflow(workflow={'id': 'offline'}, agent_contracts=[],
        validation_input={}, review_contract={}))
    assert {key: value for key, value in result.items() if key != 'thread_id'} == output and len(observed) == 1
    assert result['thread_id'].startswith('workbuddy:')
    request = observed[0]
    assert request['payload']['output_schema'] == WORKFLOW_REVIEW_OUTPUT_SCHEMA
    assert request['operation'] == 'review_workflow' and 0 < request['seconds'] <= 180
    assert request['mode'] == 'bounded' and request['tool_handler'] is None
    assert not request['payload'].get('tools')


def test_workbuddy_driver_encodes_role_native_object_for_shared_decoder(tmp_path):
    observed = []
    planner = WorkBuddyPlanner(tmp_path, 'hy4-preview', runtime_snapshot={'provider_id': 'workbuddy', 'model': 'hy4-preview'})
    async def native(**kwargs):
        observed.append(kwargs)
        return {'text': json.dumps({'analysis_json': analysis(), 'summary_zh': '', 'summary_en': ''}),
                'output_format': 'native_json_schema', 'actual_model': 'hy4-preview', 'session_id': 'native-id'}
    planner.supervisor.run = native
    async def run():
        token = planner._operation.set('analyze_role_matching')
        try:
            with deadline_scope(time.monotonic() + 300):
                async with planner._driver.client() as client:
                    thread = await client.thread_start()
                    result = await thread.run('Same business requirements', output_schema=ROLE_MATCHING_OUTPUT_SCHEMA)
                    assert json.loads(json.loads(result.final_response)['analysis_json']) == analysis()
        finally:
            planner._operation.reset(token)
    asyncio.run(run())
    assert len(observed) == 1 and observed[0]['seconds'] <= 300
    assert observed[0]['payload']['output_schema']['properties']['analysis_json']['type'] == 'object'
    assert 'Same business requirements' in observed[0]['payload']['prompt']


def test_role_followup_keeps_lossless_native_objects_not_escaped_envelopes(tmp_path):
    observed = []
    original_schema = copy.deepcopy(ROLE_MATCHING_OUTPUT_SCHEMA)
    sample = {'analysis_json': analysis(), 'summary_zh': '引号 " 与换行\n', 'summary_en': 'Quoted " text\n'}
    planner = WorkBuddyPlanner(tmp_path, 'hy4-preview', runtime_snapshot={'provider_id': 'workbuddy', 'model': 'hy4-preview'})
    async def native(**kwargs):
        observed.append(kwargs)
        return {'text': json.dumps(sample, ensure_ascii=False), 'output_format': 'native_json_schema',
                'actual_model': 'hy4-preview', 'session_id': 'native-id'}
    planner.supervisor.run = native
    async def run():
        token = planner._operation.set('analyze_role_matching')
        try:
            with deadline_scope(time.monotonic() + 300):
                async with planner._driver.client() as client:
                    thread = await client.thread_start()
                    first = await thread.run('understand supplied records', output_schema=ROLE_MATCHING_OUTPUT_SCHEMA)
                    assert json.loads(json.loads(first.final_response)['analysis_json']) == sample['analysis_json']
                    assert thread.state['history'][0]['output'] == sample
                    await thread.run('finalize exactly supplied records', output_schema=ROLE_MATCHING_OUTPUT_SCHEMA)
                    text = observed[1]['payload']['prompt'].split('Previous turns of this platform-owned operation (context, not SAP evidence):\n', 1)[1]
                    replay = json.loads(text.split('\n\nNative transport encoding only:', 1)[0])
                    assert replay[0]['output'] == sample
        finally:
            planner._operation.reset(token)
    asyncio.run(run())
    assert ROLE_MATCHING_OUTPUT_SCHEMA == original_schema


def test_review_missing_native_terminal_is_not_success_or_retried(tmp_path):
    calls = []
    async def native(**kwargs):
        calls.append(kwargs)
        return {'text': '{"plan_json":"{}"}', 'output_format': 'text'}
    planner = WorkBuddyPlanner(tmp_path, 'hy4-preview', runtime_snapshot={'provider_id': 'workbuddy', 'model': 'hy4-preview'})
    planner.supervisor.run = native
    with pytest.raises(WorkBuddyRuntimeError) as error:
        asyncio.run(planner.review_workflow(workflow={'id': 'offline'}, agent_contracts=[],
            validation_input={}, review_contract={}))
    assert error.value.code == 'workbuddy_structured_output_missing'
    assert len(calls) == 1
