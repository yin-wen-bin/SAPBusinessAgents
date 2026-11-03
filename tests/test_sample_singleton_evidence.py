"""Offline Broker regressions; live SAP acceptance is recorded by the native suite."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_agent_sample_discovery import manifest, setup_broker
from sap_business_agents_platform.runtime_sample import _discover, _singleton_candidate_plan
from sap_business_agents_platform.sample_discovery import SAMPLE_MODEL, SampleDiscoveryContext


def package():
    value = manifest()
    value['execution']['inputSchema']['properties']['customer']['enum'] = ['C1']
    value['execution']['steps'][0]['inputMapping']['plan']['select_fields'].remove('PayerName')
    return value


def schema():
    source = {'service_name': 'API_TEST', 'odata_version': '2.0', 'entity_set': 'Items'}
    return {'ok': True, 'data': {'schema_authority': True, 'entities': [
        {**source, 'key_fields': ['CompanyCode', 'Item']}], 'fields': [
        {**source, 'field_name': name, 'selectable': True, 'sortable': True}
        for name in ['CompanyCode', 'Customer', 'Item', 'FinancialAccountType']]}}


def execute(tmp_path, *, rows=None, metadata=None, change=None):
    settings, store, broker, sap = setup_broker(tmp_path)
    data = package()
    if change:
        change(data)
    sap.schema = AsyncMock(return_value=metadata if metadata is not None else schema())
    if rows is not None:
        sap.execute_plan.return_value = {'ok': True, 'results': rows}
    driver = SimpleNamespace(provider_id='codex', sample_turn=AsyncMock(return_value='{"suggestions":[]}'),
                             interrupt_sample=AsyncMock())
    service = SimpleNamespace(store=store, broker=broker, _turns={}, quiesce=lambda _: None)
    result = asyncio.run(_discover(service, 'run_sample', data, {'company_code': '1710'},
        revision=2, model=SAMPLE_MODEL, started=None, selected_fields=None, driver=driver, binding=None))
    assert not broker._tokens and not broker._sample_contexts
    return result, driver, sap, store


def test_unique_enum_requires_actual_broker_read_and_cell_proof(tmp_path):
    result, driver, sap, store = execute(tmp_path)
    assert result['status'] == 'ready' and result['selection_method'] == 'deterministic'
    assert result['input'] == {'company_code': '1710', 'customer': 'C1'}
    assert result['field_sources']['customer']['row_hashes']
    assert result['source_complete'] is None and result['bounded_discovery'] is True
    assert result['query_count'] == 1
    driver.sample_turn.assert_not_awaited()
    actual = sap.execute_plan.await_args.args[0]
    assert actual['top'] == 1 and actual['order_by'] == ['CompanyCode', 'Item']
    assert actual['filters'] == [
        {'field': 'CompanyCode', 'operator': 'eq', 'value': '1710'},
        {'field': 'Customer', 'operator': 'eq', 'value': 'C1'},
        {'field': 'FinancialAccountType', 'operator': 'eq', 'value': 'D'}]
    assert {c['tool_name'] for c in store.list_harness_tool_calls('run_sample')} >= {
        'sap_schema_get', 'sap_query_validate', 'sap_query_execute'}


@pytest.mark.parametrize('rows', [[], [{'CompanyCode':'1710','Customer':'OTHER','Item':'1','FinancialAccountType':'D'}],
    [{'CompanyCode':'9999','Customer':'C1','Item':'1','FinancialAccountType':'D'}]])
def test_missing_or_out_of_scope_rows_never_turn_enum_into_evidence(tmp_path, rows):
    result, driver, sap, _ = execute(tmp_path, rows=rows)
    assert result['status'] != 'ready' and not result['field_sources']
    driver.sample_turn.assert_awaited_once()
    sap.execute_plan.assert_awaited_once()


def test_non_authoritative_schema_cannot_authorize_deterministic_read(tmp_path):
    result, driver, sap, _ = execute(tmp_path, metadata={'ok':True,'data':{'schema_authority':False}})
    assert result['status'] != 'ready'
    driver.sample_turn.assert_awaited_once()
    sap.execute_plan.assert_not_awaited()


def test_multiple_enum_candidates_keep_runtime_selection(tmp_path):
    result, driver, sap, _ = execute(tmp_path, change=lambda m: m['execution']['inputSchema']['properties']['customer'].update(enum=['C1','C2']))
    assert result['status'] != 'ready'
    driver.sample_turn.assert_awaited_once()
    sap.schema.assert_not_awaited()
    sap.execute_plan.assert_not_awaited()


def test_probe_does_not_mutate_manifest_or_supplied_scope():
    data=package();original=copy.deepcopy(data)
    ctx=SampleDiscoveryContext(data,{'company_code':'1710'},2)
    result=_singleton_candidate_plan(ctx)
    assert result['top']==1 and data==original and ctx.manifest==original
    assert ctx.supplied_inputs=={'company_code':'1710'}


def test_organizational_scope_still_requires_manual_input(tmp_path):
    settings, store, broker, sap=setup_broker(tmp_path)
    data=package();data['execution']['inputSchema']['properties']['company_code']['enum']=['1710']
    ctx=SampleDiscoveryContext(data,{},2)
    assert 'company_code' in ctx.readiness()['blocking_fields']
    assert _singleton_candidate_plan(ctx) is None
