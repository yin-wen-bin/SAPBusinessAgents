"""One proven transport constraint may not weaken the platform contract."""
import copy
import json
import pytest
from sap_business_agents_platform.codex_driver import native_output_schema, role_schema_error
from sap_business_agents_platform.runtime_role_consolidation import SCHEMA
from sap_business_agents_platform.runtime_diagnostics import checked_output, stage_failure
from sap_business_agents_platform.runtime_contract import RuntimeContractError


def test_native_encoding_preserves_authoritative_schema_and_other_requests():
    before = copy.deepcopy(SCHEMA)
    encoded = native_output_schema(SCHEMA)
    expected = copy.deepcopy(SCHEMA)
    for name in ('workflow_suggestions', 'agent_gaps'):
        expected['properties'][name]['items']['properties']['operation_ids'].pop('uniqueItems')
    assert encoded == expected and SCHEMA == before
    ordinary = {'type': 'array', 'uniqueItems': True, 'items': {'type': 'string'}}
    assert native_output_schema(ordinary) is ordinary


def test_duplicate_operation_ids_remain_a_platform_failure():
    text = {'zh': '缺口', 'en': 'Gap'}
    gap = {'gap_id': 'gap', 'operation_ids': ['op', 'op'], 'required_capability': text,
        'required_inputs': [], 'required_outputs': [], 'safety_boundary': text,
        'business_impact': text, 'partial_agent_ids': [], 'reason': text,
        'evidence_refs': [{'document_id': 'doc', 'chunk_id': 'chunk'}]}
    value = {'summary_zh': '总结', 'summary_en': 'Summary', 'workflow_suggestions': [], 'agent_gaps': [gap]}
    # Valid native representation is not platform acceptance.
    checked_output(json.dumps(value), native_output_schema(SCHEMA), operation='analyze_role_matching')
    with pytest.raises(RuntimeContractError) as caught:
        checked_output(json.dumps(value), SCHEMA, operation='analyze_role_matching')
    diagnostic = stage_failure(caught.value, schema=SCHEMA)
    assert diagnostic['validation_issues'] == [{'code': 'runtime_report_schema_invalid',
        'path': '/agent_gaps/0/operation_ids', 'constraint': 'uniqueItems'}]


@pytest.mark.parametrize('keyword', ["'uniqueItems'", '"uniqueItems"', 'uniqueItems'])
def test_safe_native_rejection_is_specific_without_error_text(keyword):
    error = ValueError(f"Invalid schema: {keyword} is not permitted; private-token=value")
    mapped = role_schema_error(error, SCHEMA)
    diagnostic = stage_failure(mapped, schema=SCHEMA)
    assert diagnostic['failure_code'] == 'runtime_native_schema_rejected'
    assert diagnostic['failure_category'] == 'contract'
    assert diagnostic['validation_issues'][0]['constraint'] == 'uniqueItems'
    assert 'private' not in json.dumps(diagnostic)
    assert role_schema_error(error, {'type': 'object'}) is None
    assert role_schema_error(ValueError('unknown private error'), SCHEMA) is None
