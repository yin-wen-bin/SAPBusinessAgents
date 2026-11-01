"""Shared Broker -> live-schema parser -> Provider -> projection -> comparison.

All HTTP is an in-memory transport. Both SDK drivers are replaced only at the
native execution seam; no vendor SDK, SAP request or business PASS is created.
"""
import asyncio
import copy
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.harness import HarnessToolBroker
from sap_business_agents_platform.models import RunCreate, RunMode
from sap_business_agents_platform.sap_read import EmbeddedODataProvider
from sap_business_agents_platform.source_semantics import catalog
from tests.test_harness import FakeSkills
from tests.test_source_semantics import definition, gl_plan, row

ROOT = Path(__file__).resolve().parents[1]


def provider_with_memory_http(tmp_path, row_changes=None):
    plan = gl_plan()
    fields = sorted(set(plan['select_fields']) | {'ID', 'PostingDate', 'ClearingDate', 'ClearingAccountingDocument', 'ClearingDocFiscalYear'})
    properties = ''.join('<Property Name="' + f + '" Type="Edm.String" Nullable="false" />' for f in fields)
    metadata = '''<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx" Version="1.0"><edmx:DataServices>
      <Schema xmlns="http://schemas.microsoft.com/ado/2008/09/edm" Namespace="TEST"><EntityType Name="LineType">
      <Key><PropertyRef Name="ID" /></Key>''' + properties + '''</EntityType><EntityContainer Name="Container">
      <EntitySet Name="GLAccountLineItem" EntityType="TEST.LineType" /></EntityContainer></Schema></edmx:DataServices></edmx:Edmx>'''
    trace = []
    def handler(request):
        assert request.method == 'GET'
        trace.append('schema' if request.url.path.endswith('/$metadata') else 'data')
        if request.url.path.endswith('/$metadata'):
            return httpx.Response(200, text=metadata)
        assert "CompanyCode eq '1710'" in request.url.params['$filter']
        return httpx.Response(200, json={'d': {'results': [{**row(**(row_changes or {})), 'ID':'one', 'AmountInTransactionCurrency':'-12.30', 'TransactionCurrency':'USD'}]}})
    registry = tmp_path / 'odata-services.json'
    registry.write_text(json.dumps({'schema_version':'2.0','services':[{
        'service_name':'API_GLACCOUNTLINEITEM','odata_version':'2.0',
        'service_root_path':'/sap/opu/odata/sap/API_GLACCOUNTLINEITEM',
        'metadata_path':'/sap/opu/odata/sap/API_GLACCOUNTLINEITEM/$metadata',
        'artifact_id':'OFFLINE','artifact_version':'1','openapi_version':'3.0.1',
        'catalog_source':'manual','source_hash':'sha256:'+'a'*64,'status':'seed'}]}), encoding='utf-8')
    value = EmbeddedODataProvider(base_url='https://sap.invalid', username='offline', password='offline',
        service_registry_path=registry, transport=httpx.MockTransport(handler))
    return value, trace


@pytest.mark.parametrize('provider', ['codex', 'workbuddy'])
@pytest.mark.parametrize('broken', [False, True])
@pytest.mark.parametrize('unclassified', [False, True])
def test_shared_native_harness_reaches_actual_metadata_and_query_guard(tmp_path, provider, broken, unclassified):
    from sap_business_agents_platform.runtime_harness import run
    from sap_business_agents_platform.runtime_contract import RuntimeResult, RuntimeSession
    from scripts.run_three_stage_acceptance import _normalize_run, _compare
    from sap_business_agents_platform.acceptance import CanonicalTestCase
    sap, trace = provider_with_memory_http(tmp_path, {'SpecialGLCode':'UNPROVEN'} if unclassified else None)
    settings = Settings(repository_root=ROOT, data_root=tmp_path/'state')
    store = RunStore(settings.database_path)
    query = 'Inspect company code 1710; do not infer another source.'
    store.create_run('isolated', RunCreate(mode=RunMode.free_query, query=query))
    store.update_run('isolated', status='planning')
    broker = HarnessToolBroker(settings, store, sap, FakeSkills())
    native_calls = []
    class Driver:
        provider_id, engineering_copy = provider, False
        def binding(self, *a): return {'provider_id':provider, 'model':'offline-frozen'}
        async def cleanup(self, *a): pass
        async def harness_turn(self, request, context):
            native_calls.append(request.operation)
            assert request.operation == 'free_query' and request.remaining() <= settings.free_query_run_seconds
            cap, rid = context['capability'], context['run_id']
            schema = await broker.handle(rid, cap, 'sap_schema_get', {
                'service_name':'API_GLACCOUNTLINEITEM','odata_version':'2.0','entity_sets':['GLAccountLineItem']})
            assert schema['ok']
            candidate = gl_plan()
            if broken:
                candidate['select_fields'] = ['CompanyCode','AccountingDocument','AmountInCompanyCodeCurrency']
            validated = await broker.handle(rid, cap, 'sap_query_validate', {'plan':candidate})
            if broken:
                assert not validated['ok']
                assert 'source_detail_grain_incomplete' in {i['code'] for i in validated['validation_issues']}
                assert 'data' not in trace
                body = {'status':'inconclusive','intent':'read',
                    'summary':{'zh':'缺少明细粒度','en':'Detail grain missing'},'source_complete':False,'business_complete':False,
                    'missing_evidence':['source_detail_grain_incomplete'],'evidence_refs':[], 'executed_plans':[], 'presentation':None}
            else:
                assert validated['ok'], validated
                read = await broker.handle(rid, cap, 'sap_query_execute', {'plan':candidate})
                assert read['ok'] and read['source_complete']
                semantics = read['source_semantics'][0]
                assert semantics['record_kind_counts']['unknown' if unclassified else 'posted_normal'] == 1
                assert semantics['grouped_amounts'][0]['amount'] == '-12.30'
                cap_record = store.get_harness_state(rid)['source_semantics_catalog']
                assert cap_record == catalog()
                refs = [read['evidence_ref']]
                from sap_business_agents_platform.source_semantics import profile
                fields = profile(candidate, catalog())['technical_key']
                record = row(**({'SpecialGLCode':'UNPROVEN'} if unclassified else {}))
                # This fixture tests the real current report contract. Source-bound
                # key cells replace the obsolete unverified narrative fixture;
                # every existing business assertion and failure guard is retained.
                report = {'schema_version':'2.0','title':{'zh':'本地回归','en':'Offline regression'}, 'blocks':[
                    {'type':'table','claim_scope':'customer_business_fact','evidence_refs':refs,
                     'columns':[{'key':name,'label':{'zh':name,'en':name},'format':'text'} for name in fields],
                     'rows':[{'values':[{'zh':str(record[name]),'en':str(record[name])} for name in fields],
                              'evidence_refs':refs,'value_bindings':{name:{'kind':'source','evidence_ref':refs[0],
                                'source_key':{key:record[key] for key in fields},'source_field':name,'currency_field':None}
                                for name in fields}}]}]}
                validated_report = await broker.handle(rid, cap, 'sap_final_report_validate', {'report':report})
                assert validated_report['ok'], validated_report
                body = {'status':'completed','intent':'read',
                    'summary':{'zh':'离线完成','en':'Offline complete'},'source_complete':True,'business_complete':True,
                    'missing_evidence':[], 'evidence_refs':refs, 'executed_plans':[], 'presentation':validated_report['validated_report']}
            context['thread_id'] = 'offline-thread'
            return RuntimeResult(body, RuntimeSession(provider, 'offline-thread'), cleanup_complete=True)
    async def scenario():
        with patch('socket.socket.connect', side_effect=AssertionError('live network forbidden')), patch('subprocess.Popen', side_effect=AssertionError('SDK process forbidden')):
            return await run(SimpleNamespace(settings=settings, store=store, broker=broker), Driver(), 'isolated', query, None, 'offline-frozen', None)
    outcome = asyncio.run(scenario())
    assert native_calls == ['free_query']
    assert outcome.status == ('inconclusive' if broken or unclassified else 'completed')
    if unclassified and not broken:
        assert not outcome.business_complete and outcome.source_complete
        assert 'source_business_completeness_unproven' in outcome.missing_evidence
    calls, _ = broker.snapshot('isolated')
    if not broken:
        context = next(c['source_semantics'] for c in calls if c.get('source_semantics'))
        canonical = {'records':[{'id':'one'}], 'metrics':{}, 'limitations':[], 'source_complete':True, 'source_semantics':context}
        contract = {'business_keys':['id'], 'source_semantics':definition()}
        assert _compare(canonical, canonical, contract).verdict == ('BLOCKED' if unclassified else 'MATCH')
        assert _compare(canonical, canonical, {'business_keys':['id']}).verdict == 'BLOCKED'


def test_current_financial_draft_requires_reviewed_contract_not_implicit_migration():
    from sap_business_agents_platform.acceptance_contract import readiness
    from tests.test_acceptance_contract import sample_manifest
    manifest = sample_manifest()
    manifest['execution']['steps'] = [{'executor':'sap_read','input':{'plan':gl_plan()}}]
    before = copy.deepcopy(manifest)
    assert 'source_scope_definition_required' in {i['code'] for i in readiness(manifest, 1)['issues']}
    assert manifest == before


def test_late_or_changed_semantics_do_not_authorize_tools(tmp_path):
    settings = Settings(repository_root=ROOT, data_root=tmp_path)
    store = RunStore(settings.database_path)
    store.create_run('offline', RunCreate(mode=RunMode.free_query, query='offline'))
    broker = HarnessToolBroker(settings, store, SimpleNamespace(), FakeSkills())
    token = broker.open_session('offline')
    old = store.get_harness_state('offline')['source_semantics_catalog']
    old['digest'] = 'tampered'
    store.update_harness_state('offline', {'source_semantics_catalog':old})
    response = asyncio.run(broker.handle('offline', token, 'sap_query_execute', {'plan':gl_plan()}))
    assert response['code'] == 'source_semantics_binding_changed' and not response['ok']
    assert store.list_harness_tool_calls('offline') == []
