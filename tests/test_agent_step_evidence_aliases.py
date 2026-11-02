"""Source-record counting must respect Provider canonical collections."""
import copy

import pytest

from sap_business_agents_platform.engine import _step_execution_envelope


@pytest.mark.parametrize('canonical', ['data', 'results', 'rows'])
def test_query_diagnostics_do_not_duplicate_the_canonical_rows(canonical):
    row = {'PurchaseOrder': '4500000001', 'CompanyCode': '1710'}
    payload = {'source_complete': True, 'queries': [{'data': {'results': [copy.deepcopy(row)]}}]}
    payload[canonical] = {'results': [row]} if canonical == 'data' else [row]
    value = _step_execution_envelope({'executor': 'sap_read'}, payload, execution_status='completed')
    assert value['record_count'] == 1
    assert value['preview_rows'] == [row]


def test_identical_real_records_are_not_removed_by_value_deduplication():
    rows = [{'id': 'same'}, {'id': 'same'}]
    value = _step_execution_envelope({'executor': 'sap_read'},
        {'source_complete': True, 'data': {'results': rows}, 'queries': [{'data': {'results': rows}}]},
        execution_status='completed')
    assert value['record_count'] == 2
    assert value['preview_rows'] == rows


def test_distinct_queries_without_a_canonical_collection_remain_separate():
    payload = {'source_complete': True, 'queries': [
        {'data': {'results': [{'id': 'same'}]}}, {'data': {'results': [{'id': 'same'}]}}]}
    value = _step_execution_envelope({'executor': 'sap_read'}, payload, execution_status='completed')
    assert value['record_count'] == 2
    assert len(value['preview_rows']) == 2


def test_an_explicit_empty_canonical_collection_is_authoritative():
    payload = {'source_complete': True, 'data': {'results': []},
        'queries': [{'data': {'results': [{'id': 'old-diagnostic'}]}}]}
    value = _step_execution_envelope({'executor': 'sap_read'}, payload, execution_status='completed')
    assert value['record_count'] == 0
    assert value['preview_rows'] == []
    assert value['evidence_status'] == 'not_found'
