"""Failure shapes from the five-model batch; no SDK/SAP/network execution."""
import asyncio
import copy
import json

import pytest

from sap_business_agents_platform import runtime_evidence_bindings as bindings
from sap_business_agents_platform.runtime_query_contract import ReadScope
from sap_business_agents_platform.runtime_contract import RuntimeContractError
from sap_business_agents_platform.runtime_diagnostics import safe_upstream_diagnostic, project_upstream_diagnostic
from sap_business_agents_platform.runtime_terminal_contract import TerminalChecks
from sap_business_agents_platform.workbuddy_diagnostics import native_output_diagnostic, safe_native_failure
from tests.test_shared_query_money_chain import make_broker, seed, row, metric
from tests.test_runtime_report_contract import report, make_broker as generic_broker
from tests.test_workbuddy_harness_nullable import schema, envelope


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    import threading
    internal = threading.local()
    pair, connect = socket.socketpair, socket.socket.connect
    def local_pair(*a, **kw):
        internal.socketpair = True
        try: return pair(*a, **kw)
        finally: internal.socketpair = False
    def guarded_connect(sock, address):
        if getattr(internal, 'socketpair', False) and isinstance(address, tuple) and address[0] in {'127.0.0.1','::1'}:
            return connect(sock, address)
        pytest.fail('offline replay attempted a network connection')
    def denied(*a, **kw): pytest.fail('offline replay attempted a network connection')
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(socket, 'socketpair', local_pair)
    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)


@pytest.mark.parametrize('provider', ['codex', 'workbuddy'])
@pytest.mark.parametrize('mutation,reason', [('formatted','canonical_value_required'),
    ('count','count_currency_must_be_null'), ('evidence','source_reference_missing')])
def test_exact_money_feedback_then_valid_handoff(tmp_path, provider, mutation, reason):
    async def replay():
        broker, store, run, token = generic_broker(tmp_path, query='Synthetic amount report', runtime={
            'provider_id':provider, 'sdk_id':'synthetic-'+provider, 'configuration_digest':'synthetic-frozen'})
        ref = seed(broker, store, run, [row()])
        calculated = await broker.handle(run, token, 'sap_money_aggregate', {'evidence_ref': ref})
        m = metric(calculated, field='count' if mutation == 'count' else 'amount')
        if mutation == 'formatted': m['value'] = {'zh':'0.10 USD', 'en':'0.10 USD'}
        if mutation == 'count': m['currency'] = 'USD'
        if mutation == 'evidence': m['evidence_refs'] = []
        original = copy.deepcopy(m)
        bad = await broker.handle(run, token, 'sap_final_report_validate', {'report':report(blocks=[{'type':'metrics','metrics':[m]}])})
        issue = next(i for i in bad['validation_issues'] if i['code']=='money_metric_binding_invalid')
        assert reason in issue['binding_issues'] and bad['validation_ref'] is None and m == original
        assert 'expected_value' in issue
        fixed = metric(calculated)
        good = await broker.handle(run, token, 'sap_final_report_validate', {'report':report(blocks=[{'type':'metrics','metrics':[fixed]}])})
        assert good['ok'] and good['validated_report']['validation_ref']
        assert good['financial_verification_details']['business_facts_verified'] is False
    asyncio.run(replay())


def test_step_minimax_mimo_status_error_explains_nonmetric_blocks(tmp_path):
    async def replay():
        broker, store, run, token = make_broker(tmp_path)
        ref = seed(broker, store, run, [row()])
        calculated = await broker.handle(run, token, 'sap_money_aggregate', {'evidence_ref': ref})
        candidate = report(blocks=[{'type':'metrics','metrics':[metric(calculated)]},
            {'type':'text','claim_scope':'customer_business_fact','evidence_refs':[ref],
             'text':{'zh':'合成说明','en':'Synthetic explanation'}}])
        candidate['financial_verification'] = 'verified_metrics_only'
        bad = await broker.handle(run, token, 'sap_final_report_validate', {'report': candidate})
        issue = next(i for i in bad['validation_issues'] if i['code']=='money_verification_claim_invalid')
        assert issue['expected']=='unverified' and issue['unverified_business_blocks']==['/blocks/1']
        assert not any(i['code']=='money_metric_binding_invalid' for i in bad['validation_issues'])
        candidate.pop('financial_verification')
        good = await broker.handle(run, token, 'sap_final_report_validate', {'report': candidate})
        assert good['ok'] and good['validated_report']['financial_verification']=='unverified'
    asyncio.run(replay())


def invoice_scope():
    scope = ReadScope('synthetic', {'supplier':'S1', 'posting_date_to':'2020-12-31'})
    scope.record_schema({'ok':True,'data':{'schema_authority':True,'fields_truncated':False,
        'service':{'service_name':'S','odata_version':'4.0'},'fields':[
            {'entity_set':'Header','field_name':'Issuer','data_type':'Edm.String'},
            {'entity_set':'Header','field_name':'PostingDate','data_type':'Edm.Date'}]}})
    plan = {'service_name':'S','odata_version':'4.0','entity_set':'Header','filters':[
        {'field':'Issuer','value':'S1'}, {'field':'PostingDate','operator':'le','value':'2020-12-31'}]}
    return scope, plan


def test_minimax_service_mapping_is_reviewed_entity_scoped_not_global_alias():
    scope, plan = invoice_scope()
    with pytest.raises(RuntimeContractError) as rejected: scope.check(plan)
    assert rejected.value.detail['validation_issues'][0]['scope_issue']=='service_scope_mapping_or_verified_relation_required'
    scope.evidence_bindings['scope_mappings'] = [{'service_name':'S','odata_version':'4.0',
        'entity_set':'Header','canonical_fields':['Supplier'],'entity_fields':['Issuer'],
        'review_reference':'synthetic-reviewed-equivalence'}]
    before = copy.deepcopy(plan)
    scope.check(plan)
    assert plan==before and ('Supplier',) == scope.constraints[0][0]
    for changes in ({'entity_set':'Other'}, {'filters':[]}, {'filters':[{'field':'Issuer','value':'OTHER'}]}):
        with pytest.raises(RuntimeContractError): scope.check({**plan, **changes})


def test_configured_mapping_requires_current_complete_metadata():
    scope, plan = invoice_scope()
    scope.evidence_bindings['scope_mappings'] = [{'service_name':'S','odata_version':'4.0','entity_set':'Header',
        'canonical_fields':['Supplier'],'entity_fields':['Unknown'],'review_reference':'synthetic'}]
    with pytest.raises(RuntimeContractError) as rejected: scope.check(plan)
    assert rejected.value.detail['validation_issues'][0]['scope_issue']=='reviewed_mapping_metadata_mismatch'


def test_authoritative_replacement_revokes_old_mapping_field():
    scope, plan = invoice_scope()
    scope.evidence_bindings['scope_mappings'] = [{'service_name':'S','odata_version':'4.0','entity_set':'Header',
        'canonical_fields':['Supplier'],'entity_fields':['Issuer'],'review_reference':'synthetic'}]
    scope.check(plan)
    scope.record_schema({'ok':True,'data':{'schema_authority':True,'fields_truncated':False,
        'service':{'service_name':'S','odata_version':'4.0'},'fields':[
            {'entity_set':'Header','field_name':'PostingDate','data_type':'Edm.Date'}]}})
    with pytest.raises(RuntimeContractError): scope.check(plan)


def configuration_registry(monkeypatch):
    current = bindings.registry()
    current['configuration_sources'] = [{'service_name':'CONFIG','odata_version':'4.0','entity_set':'History',
        'review_reference':'synthetic-only', 'fields':{k:k for k in
            ('account_type','code','noted_item','valid_from','valid_to','company_code')}}]
    monkeypatch.setattr(bindings, 'registry', lambda:copy.deepcopy(current))


@pytest.mark.parametrize('variant', ['valid','current_only','other_company','partial','restricted','bad_boolean','conflict'])
def test_owned_configuration_evidence_to_money_recomputation(tmp_path, monkeypatch, variant):
    configuration_registry(monkeypatch)
    async def replay():
        broker, store, run, token = make_broker(tmp_path)
        source = seed(broker, store, run, [row(SpecialGLCode='X')])
        proof = {'account_type':'K','code':'X','noted_item':True,'valid_from':'2019-01-01',
                 'valid_to':'2021-12-31','company_code':'C1'}
        if variant=='current_only': proof.update(valid_from='2026-01-01', valid_to='2026-12-31')
        if variant=='other_company': proof['company_code']='OTHER'
        if variant=='bad_boolean': proof['noted_item']='true'
        rows = [proof]
        if variant=='conflict': rows.append({**proof,'noted_item':False})
        raw = {'rows':rows,'source_complete':variant!='partial'}
        if variant=='restricted': raw['rows_redacted']=True
        ref = broker._save_evidence(run, 'sap_live', raw)
        store.begin_harness_tool_call(call_id='config', run_id=run, tool_name='sap_query_execute', request_hash=ref,
            safe_input={'plan':{'service_name':'CONFIG','odata_version':'4.0','entity_set':'History'}})
        store.complete_harness_tool_call('config',status='completed',output={'ok':True,'evidence_ref':ref,
            'source_complete':variant!='partial','row_count':len(rows)}, evidence_ref=ref)
        output = await broker.handle(run, token, 'sap_money_aggregate', {'evidence_ref':source})
        if variant in {'partial','restricted','bad_boolean'}:
            assert not output['ok']
            assert output['code'] in {'source_configuration_evidence_incomplete','source_configuration_evidence_invalid'}
        else:
            assert output['ok'] and output['business_complete'] is (variant=='valid')
            assert output['configuration_evidence_refs']==[ref]
            if variant=='valid': assert all(i['record_kind'] in {'noted_item','all_record_kinds'} for i in output['metrics'])
    asyncio.run(replay())


@pytest.mark.parametrize('shape',['hy4','mimo'])
def test_native_shapes_require_explicit_model_correction_not_auto_repair(shape):
    frozen = schema(); checks = TerminalChecks(frozen)
    bad = {**envelope(),'input_kind':'INVALID','input_field':'INVALID'} if shape=='hy4' else {'input_field':None}
    before = copy.deepcopy(bad)
    for index in range(3):
        result = checks.check(bad, submission_id=str(index)*64, origin='formatter_hook',phase='native_candidate')
    assert result['stop'] and bad==before
    assert 'INVALID' not in json.dumps(result)
    assert result['handoff_diagnostic']['root_fields']['status']['present'] is (shape=='hy4')
    assert not checks.accepts_native_result(json.dumps(envelope()))
    valid = TerminalChecks(frozen)
    valid.check(envelope(),submission_id=None,origin='native_terminal',phase='native_terminal')
    assert valid.accepts_native_result(json.dumps(envelope()))
    diagnostic = native_output_diagnostic(bad,frozen,operation='free_query',phase='native_candidate')
    assert diagnostic['schema_sha256']==result['handoff_diagnostic']['schema_sha256']


def test_kimi_error_remains_unknown_without_protocol_evidence_and_no_secrets():
    raw = {'code':'rate_limit_exceeded','status_code':429,'message':'secret https://private/?token=secret'}
    observed = safe_upstream_diagnostic(raw)
    assert observed['upstream_category']=='rate_limited' and observed['upstream_http_status']==429
    assert 'secret' not in json.dumps(observed)
    assert project_upstream_diagnostic(observed)==observed
    assert safe_native_failure({'failure_code':'workbuddy_upstream_error', **observed})['upstream_category']=='rate_limited'
    unknown = safe_upstream_diagnostic('401 quota network might be secret')
    assert unknown['upstream_category']=='unknown' and 'upstream_http_status' not in unknown
    assert safe_upstream_diagnostic({'status':True})['upstream_category']=='unknown'
