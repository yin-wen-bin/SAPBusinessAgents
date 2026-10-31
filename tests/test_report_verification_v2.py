"""Real Broker and finalizer, synthetic owned rows only; both Runtime providers."""
import asyncio
import copy
import time

import pytest

from sap_business_agents_platform.models import RunCreate, RunMode, RunPresentation
from sap_business_agents_platform.runtime_harness_result import finalize
from tests.test_five_model_unified_repair import offline
from tests.test_runtime_report_contract import make_broker, report
from tests.test_shared_query_money_chain import row, plan, seed, metric


def source_binding(ref, record, field, currency_field=None):
    return {'kind': 'source', 'evidence_ref': ref,
            'source_key': {k: record[k] for k in ('CompanyCode','FiscalYear','AccountingDocument','AccountingDocumentItem')},
            'source_field': field, 'currency_field': currency_field}


def table(ref, records):
    return {'type': 'table', 'claim_scope': 'customer_business_fact', 'evidence_refs': [ref],
        'columns': [{'key': k, 'label': {'zh': k, 'en': k}, 'format': f} for k,f in
                    [('document','text'),('amount','currency'),('currency','text')]],
        'rows': [{'values': [{'zh': str(r[f]), 'en': str(r[f])} for f in
                 ('AccountingDocument','AmountInTransactionCurrency','TransactionCurrency')],
                 'evidence_refs': [ref], 'value_bindings': {
                     'document': source_binding(ref,r,'AccountingDocument'),
                     'amount': source_binding(ref,r,'AmountInTransactionCurrency','TransactionCurrency'),
                     'currency': source_binding(ref,r,'TransactionCurrency')}} for r in records]}


def setup(tmp_path, provider='codex'):
    return make_broker(tmp_path, query='Synthetic amount report as of 2020-12-31', runtime={
        'provider_id':provider,'sdk_id':'synthetic-'+provider,'configuration_digest':'synthetic'})


def finish(broker, store, run, validated):
    return finalize(broker.settings,store,broker,run, {'status':'completed','business_complete':False,
        'source_complete':True,'presentation':validated['validated_report'],'missing_evidence':[],
        'executed_plans':[],'evidence_refs':[]},thread_id=None,turn_count=1,run_started=time.monotonic())


@pytest.mark.parametrize('provider',['codex','workbuddy'])
def test_exact_summary_rows_and_final_receipt(tmp_path,provider):
    async def scenario():
        broker,store,run,token=setup(tmp_path,provider)
        rows=[row(AmountInTransactionCurrency='100.00'),row(AccountingDocument='D2',AmountInTransactionCurrency='200.00')]
        ref=seed(broker,store,run,rows)
        calc=await broker.handle(run,token,'sap_money_aggregate',{'evidence_ref':ref})
        candidate=report(schema_version='2.0',blocks=[{'type':'metrics','metrics':[metric(calc)]},table(ref,rows)])
        result=await broker.handle(run,token,'sap_final_report_validate',{'report':candidate})
        assert result['ok'],result.get('validation_issues')
        proof=result['verification']
        assert all(proof[k]['status']=='verified' for k in ('summary_amounts','detail_amounts','business_facts','coverage'))
        assert result['financial_verification']=='verified_metrics_only'
        outcome=finish(broker,store,run,result)
        assert outcome.business_complete and outcome.status=='completed',outcome.missing_evidence
        from sap_business_agents_platform.harness import _validated_presentation_snapshot
        restored=_validated_presentation_snapshot(result['validation_ref'],store.list_harness_tool_calls(run))
        assert restored.model_dump(mode='json')==result['validated_report']
    asyncio.run(scenario())


@pytest.mark.parametrize('mutation', ['value','sign','currency_field','key','duplicate','foreign_ref','status','row_key_conflict'])
def test_tampered_cell_or_proof_is_rejected(tmp_path,mutation):
    async def scenario():
        broker,store,run,token=setup(tmp_path);rows=[row(),row(AccountingDocument='D2')]
        ref=seed(broker,store,run,rows);block=table(ref,rows)
        if mutation=='value':block['rows'][0]['values'][1]={'zh':'301.00','en':'301.00'}
        if mutation=='sign':block['rows'][0]['values'][1]={'zh':'-0.10','en':'-0.10'}
        if mutation=='currency_field':block['rows'][0]['value_bindings']['amount']['currency_field']='CompanyCode'
        if mutation=='key':block['rows'][0]['value_bindings']['amount']['source_key'].pop('FiscalYear')
        if mutation=='duplicate':block['rows'].append(copy.deepcopy(block['rows'][0]))
        if mutation=='foreign_ref':block['rows'][0]['value_bindings']['amount']['evidence_ref']='ev_'+'0'*24
        if mutation=='row_key_conflict':block['rows'][0]['value_bindings']['amount']['source_key']['AccountingDocument']='D2'
        candidate=report(schema_version='2.0',blocks=[block])
        if mutation=='status':candidate['verification']={'summary_amounts':{'status':'verified'}}
        result=await broker.handle(run,token,'sap_final_report_validate',{'report':candidate})
        assert not result['ok'] and result['validation_ref'] is None
    asyncio.run(scenario())


def test_narrative_does_not_invalidate_bound_arithmetic(tmp_path):
    async def scenario():
        broker,store,run,token=setup(tmp_path);ref=seed(broker,store,run,[row()])
        calc=await broker.handle(run,token,'sap_money_aggregate',{'evidence_ref':ref})
        result=await broker.handle(run,token,'sap_final_report_validate',{'report':report(schema_version='2.0',blocks=[
            {'type':'metrics','metrics':[metric(calc)]},{'type':'text','text':{'zh':'有未清发票','en':'Open invoices exist'},
            'claim_scope':'customer_business_fact','evidence_refs':[ref]}])})
        assert result['ok'] and result['verification']['summary_amounts']['status']=='verified'
        assert result['verification']['business_facts']['status']=='needs_evidence'
        outcome=finish(broker,store,run,result)
        assert not outcome.business_complete and 'business_claims_unverified' in outcome.missing_evidence
        assert 'monetary_claims_unverified' not in outcome.missing_evidence
    asyncio.run(scenario())


def test_missing_source_semantics_cannot_hide_unknown_classification(tmp_path):
    async def scenario():
        broker,store,run,token=setup(tmp_path);rows=[row(SpecialGLCode='X')]
        ref=seed(broker,store,run,rows)
        result=await broker.handle(run,token,'sap_final_report_validate',{'report':report(schema_version='2.0',blocks=[table(ref,rows)])})
        assert result['ok']
        assert result['verification']['coverage']['status']=='needs_evidence'
    asyncio.run(scenario())


def test_fact_count_cannot_use_an_amount_binding(tmp_path):
    async def scenario():
        from sap_business_agents_platform.report_verification import OPEN_TEMPLATE
        broker,store,run,token=setup(tmp_path);ref=seed(broker,store,run,[row()])
        calc=await broker.handle(run,token,'sap_money_aggregate',{'evidence_ref':ref})
        item=next(m for m in calc['metrics'] if m['historical_status']=='open_at_cutoff')
        binding={'kind':'calculation','calculation_ref':calc['calculation_ref'],
                 'calculation_item':item['id'],'calculation_field':'amount','currency':item['currency']}
        result=await broker.handle(run,token,'sap_final_report_validate',{'report':report(schema_version='2.0',blocks=[{
            'type':'text','fact_rule':'open_items_exist','text_template':OPEN_TEMPLATE,
            'evidence_refs':[ref],'value_bindings':{'count':binding}}])})
        assert not result['ok']
    asyncio.run(scenario())


def test_template_is_rendered_from_owned_calculation_and_copied_unchanged(tmp_path):
    async def scenario():
        broker,store,run,token=setup(tmp_path);ref=seed(broker,store,run,[row()])
        calc=await broker.handle(run,token,'sap_money_aggregate',{'evidence_ref':ref});m=metric(calc)
        binding={'kind':'calculation',**{k:m[k] for k in ('calculation_ref','calculation_item','calculation_field','currency')}}
        candidate=report(schema_version='2.0',blocks=[{'type':'text','evidence_refs':[ref],
            'text_template':{'zh':'合计 {{total}}','en':'Total {{total}}'},'value_bindings':{'total':binding}}])
        result=await broker.handle(run,token,'sap_final_report_validate',{'report':candidate})
        assert result['ok'],result.get('validation_issues')
        canonical=result['validated_report'];assert canonical['blocks'][0]['text']=={'zh':'合计 0.10','en':'Total 0.10'}
        repeated=await broker.handle(run,token,'sap_final_report_validate',{'report':canonical})
        assert repeated['ok'] and repeated['validated_report']==canonical
        canonical['blocks'][0]['text']['zh']='合计 900'
        bad=await broker.handle(run,token,'sap_final_report_validate',{'report':canonical})
        assert not bad['ok']
    asyncio.run(scenario())


@pytest.mark.parametrize('confirmed',[False,True])
def test_confirmed_scope_can_exclude_exploration_but_unconfirmed_filter_cannot(tmp_path,confirmed):
    async def scenario():
        broker,store,_,_=setup(tmp_path)
        run='scoped_'+str(confirmed);f={'field':'SpecialGLCode','operator':'eq','value':''}
        store.create_run(run,RunCreate(mode=RunMode.free_query,query='Amount as of 2020-12-31',
            input={'answer_scope':{'filters':[f],'categories':['posted_normal']}} if confirmed else {}))
        token=broker.open_session(run)
        seed(broker,store,run,[row(SpecialGLCode='X')])
        p=plan();p['filters'].append(f);rows=[row()]
        from sap_business_agents_platform.source_semantics import assess,public_assessment
        sem=public_assessment(assess(p,rows,store.get_harness_state(run)['source_semantics_catalog'],source_complete=True))
        ref=broker._save_evidence(run,'sap_live',{'rows':rows,'source_complete':True})
        cid=run+'-refined';store.begin_harness_tool_call(call_id=cid,run_id=run,tool_name='sap_query_execute',request_hash=cid,safe_input={'plan':p})
        store.complete_harness_tool_call(cid,status='completed',evidence_ref=ref,output={'ok':True,'evidence_ref':ref,'source_type':'sap_live','source_complete':True,'row_count':1,'source_semantics':[sem]})
        result=await broker.handle(run,token,'sap_final_report_validate',{'report':report(schema_version='2.0',blocks=[table(ref,rows)])})
        assert result['ok'],result.get('validation_issues')
        assert result['verification']['coverage']['status']==('verified' if confirmed else 'needs_evidence')
        assert len(result['verification']['scope_receipt']['roles'])==2
    asyncio.run(scenario())


def test_v1_serialization_has_no_v2_fields():
    p=RunPresentation.model_validate(report())
    assert p.schema_version=='1.0' and 'verification' not in p.model_dump()
    assert 'value_bindings' not in p.model_dump()['blocks'][0]


def test_versioned_native_schema_preserves_v1_and_reports_v2_field_errors():
    from sap_business_agents_platform.runtime_harness_contract import terminal_schema
    from sap_business_agents_platform.runtime_diagnostics import schema_issues
    from tests.test_workbuddy_harness_nullable import envelope
    old = terminal_schema()
    assert old['properties']['presentation']['anyOf'][0]['properties']['schema_version']['enum'] == ['1.0']
    value = {**envelope(), 'presentation': {'schema_version':'2.0',
        'title':{'zh':'private','en':'private'}, 'blocks':[{'type':'private-invalid'}]}}
    for field in ('input_kind','input_field','clarification_question'):value.pop(field)
    issues = schema_issues(value, terminal_schema(version=2))
    assert any(i['path']=='/presentation/blocks/0/type' and i['constraint']=='enum' for i in issues)
    assert 'private' not in str(issues)


@pytest.mark.parametrize('mutation',['missing_row','untyped_amount','diagnostic_label','wrong_locale','template_slot','cross_run_calc','changed_receipt'])
def test_no_proof_bypass_can_complete_a_report(tmp_path,mutation):
    async def scenario():
        broker,store,run,token=setup(tmp_path);rows=[row(),row(AccountingDocument='D2')]
        ref=seed(broker,store,run,rows)
        calc=await broker.handle(run,token,'sap_money_aggregate',{'evidence_ref':ref})
        block=table(ref,rows);candidate=report(schema_version='2.0',blocks=[{'type':'metrics','metrics':[metric(calc)]},block])
        if mutation=='missing_row':block['rows'].pop()
        if mutation=='untyped_amount':
            block['columns'][1]['format']='text';block['claim_scope']='diagnostic'
            del block['rows'][0]['value_bindings']['amount']
        if mutation=='diagnostic_label':
            candidate['blocks'].append({'type':'text','claim_scope':'diagnostic',
                'text':{'zh':'任意业务事实','en':'Arbitrary business claim'}})
        if mutation=='wrong_locale':block['rows'][0]['values'][1]['en']='999'
        if mutation=='template_slot':
            block={'type':'text','text_template':{'zh':'{{x}}','en':'{{y}}'},'value_bindings':{},'evidence_refs':[ref]}
            candidate['blocks']=[block]
        if mutation=='cross_run_calc':candidate['blocks'][0]['metrics'][0]['calculation_ref']='calc_other_run'
        result=await broker.handle(run,token,'sap_final_report_validate',{'report':candidate})
        if mutation in {'missing_row','untyped_amount','diagnostic_label'}:
            assert result['ok'],result.get('validation_issues')
            outcome=finish(broker,store,run,result)
            assert not outcome.business_complete
        elif mutation=='changed_receipt':
            assert result['ok'];canonical=result['validated_report']
            canonical['verification']['coverage']['status']='failed'
            bad=await broker.handle(run,token,'sap_final_report_validate',{'report':canonical})
            assert not bad['ok']
        else:assert not result['ok']
    asyncio.run(scenario())
