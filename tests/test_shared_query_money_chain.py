"""Synthetic full Broker/contract replay. No real SDK, models or SAP."""
import asyncio
import copy
import json
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sap_business_agents_platform.runtime_report_contract import freeze, tools_for_contract, verify, digest
from sap_business_agents_platform.runtime_query_contract import PLAN_SCHEMA, frozen_contract
from sap_business_agents_platform.runtime_contract import RuntimeContractError
from sap_business_agents_platform.runtime_money import calculate
from sap_business_agents_platform.source_semantics import catalog
from sap_business_agents_platform.mcp_server import _SAP_TOOLS, _result
from tests.test_runtime_report_contract import make_broker as generic_broker, report


def make_broker(tmp_path):
    return generic_broker(tmp_path, query='Synthetic amount report')


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def forbidden(*args, **kwargs): pytest.fail('offline replay must not access network')
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def plan():
    p = catalog()['sources'][1]
    return {k: p[k] for k in ('service_name', 'odata_version', 'entity_set')} | {
        'select_fields': list(dict.fromkeys([*p['detail_fields'], *p['amount_pairs'],
            *p['amount_pairs'].values(), 'PostingDate', 'ClearingDate', 'ClearingAccountingDocument', 'ClearingDocFiscalYear'])),
        'filters': [{'field': 'PostingDate', 'operator': 'le', 'value': '2020-12-31'}]}


def row(**changes):
    return {'CompanyCode': 'C1', 'FiscalYear': '2020', 'AccountingDocument': 'D1',
        'AccountingDocumentItem': '1', 'FinancialAccountType': 'K', 'SpecialGLCode': '',
        'PostingDate': '2020-01-01', 'ClearingDate': '', 'ClearingAccountingDocument': '',
        'ClearingDocFiscalYear': '0000', 'AmountInTransactionCurrency': '0.10',
        'TransactionCurrency': 'USD', 'AmountInCompanyCodeCurrency': '0.10',
        'CompanyCodeCurrency': 'USD', **changes}


def seed(broker, store, run, rows, *, complete=True, raw_patch=None):
    raw = {'rows': rows, 'source_complete': complete, **(raw_patch or {})}
    ref = broker._save_evidence(run, 'sap_live', raw)
    call = 'seed_' + ref
    store.begin_harness_tool_call(call_id=call, run_id=run, tool_name='sap_query_execute',
        request_hash=ref, safe_input={'plan': plan()})
    store.complete_harness_tool_call(call, status='completed', output={'ok': True,
        'evidence_ref': ref, 'source_type': 'sap_live', 'source_complete': complete, 'row_count': len(rows)}, evidence_ref=ref)
    return ref


def metric(record, *, field='amount'):
    item = record['metrics'][0]
    return {'id': 'amount', 'label': {'zh': '合成指标', 'en': 'Synthetic metric'},
        'value': {'zh': item[field], 'en': item[field]},
        'evidence_refs': [record['evidence_ref']], 'calculation_ref': record['calculation_ref'],
        'calculation_item': item['id'], 'calculation_field': field,
        'currency': item['currency'] if field == 'amount' else None}


def test_shared_query_schema_and_frozen_file_contract_are_identical():
    frozen = freeze('run_synthetic', 'request', {'provider_id': 'codex'})
    tools = tools_for_contract(_SAP_TOOLS, frozen)
    query_tools = [t for t in tools if t['name'] in {'sap_query_validate', 'sap_query_execute'}]
    assert all(t['inputSchema']['properties']['plan'] == PLAN_SCHEMA for t in query_tools)
    assert frozen['query_contract'] == frozen_contract()
    assert all(t['inputSchema'] == next(v for v in _SAP_TOOLS if v['name'] == t['name'])['inputSchema'] for t in query_tools)
    changed = copy.deepcopy(frozen)
    changed['query_contract']['schema']['properties']['odata_version']['enum'] = ['guessed']
    changed['digest'] = digest({k: v for k, v in changed.items() if k != 'digest'})
    with pytest.raises(Exception, match='query_contract_changed'): verify(changed)


def test_plan_stagnation_retransmit_concurrency_and_report_exit(tmp_path):
    async def run_test():
        broker, store, run, token = make_broker(tmp_path)
        bad = {'plan': {'service_name': 'S', 'odata_version': 'V2', 'entity_set': 'Items'}}
        first = await broker.handle(run, token, 'sap_query_validate', {**bad, 'tool_call_id': 'first'})
        replay = await broker.handle(run, token, 'sap_query_validate', {**bad, 'tool_call_id': 'first'})
        assert first['plan_convergence'] == replay['plan_convergence'] == {'without_progress': 1, 'terminal': False}
        conflict = await broker.handle(run, token, 'sap_query_validate', {'plan': {}, 'tool_call_id': 'first'})
        assert conflict['code'] == 'harness_tool_call_conflict'
        results = await asyncio.gather(*[broker.handle(run, token, 'sap_query_validate', {**bad, 'tool_call_id': str(i)}) for i in range(2)])
        assert [r['plan_convergence']['without_progress'] for r in results] == [2, 3]
        assert results[-1]['code'] == 'query_plan_correction_stalled'
        for tool in ('sap_query_execute', 'sap_schema_get', 'sap_skill_execute', 'external_tool_execute'):
            denied = await broker.handle(run, token, tool, {})
            assert denied['code'] == 'query_plan_correction_stalled'
        honest = await broker.handle(run, token, 'sap_final_report_validate', {'report': report()})
        assert honest['ok'] and store.get_harness_state(run)['plan_convergence']['terminal']
        assert not store.get_harness_state(run).get('report_convergence')
        assert len(store.list_harness_tool_calls(run)) == 4
    asyncio.run(run_test())


def test_alternating_errors_cannot_infinite_reset_and_actual_repair_is_progress(tmp_path):
    async def run_test():
        broker, store, run, token = make_broker(tmp_path)
        for i in range(7):
            p = {'service_name': 'S', 'odata_version': '2.0', 'entity_set': 'Items'}
            p.update({'schema_version': 'bad'} if i % 2 else {'odata_version': 'V2'})
            result = await broker.handle(run, token, 'sap_query_validate', {'plan': p, 'tool_call_id': str(i)})
            if result.get('code') == 'query_plan_correction_stalled': break
        else: pytest.fail('alternating failures never converged')
        assert i < 6
    asyncio.run(run_test())


def test_money_broker_to_report_exact_binding_and_local_only(tmp_path):
    async def run_test():
        broker, store, run, token = make_broker(tmp_path)
        rows = [row(), row(AccountingDocument='D2', AmountInTransactionCurrency='0.20', AmountInCompanyCodeCurrency='0.20')]
        ref = seed(broker, store, run, rows)
        before = copy.deepcopy(rows)
        result = await broker.handle(run, token, 'sap_money_aggregate', {'evidence_ref': ref})
        assert result['ok'] and result['metrics'][0]['amount'] == '0.30'
        assert result['metrics'][0]['count'] == '2' and rows == before
        block = {'type': 'metrics', 'claim_scope': 'customer_business_fact', 'metrics': [metric(result)]}
        validated = await broker.handle(run, token, 'sap_final_report_validate', {'report': report(blocks=[block])})
        assert validated['ok'], validated['validation_issues']
        assert validated['financial_verification'] == 'verified_metrics_only'
        assert validated['validated_report']['financial_verification'] == 'verified_metrics_only'
        block['metrics'][0]['value']['en'] = '0.31'
        invalid = await broker.handle(run, token, 'sap_final_report_validate', {'report': report(blocks=[block])})
        assert not invalid['ok'] and invalid['validation_ref'] is None
        assert any(i['code'] == 'money_metric_binding_invalid' for i in invalid['validation_issues'])
        assert _result(invalid)['isError'] is True
    asyncio.run(run_test())


@pytest.mark.parametrize('mutation,code', [
    ('duplicate', 'money_key_duplicate'), ('conflict', 'money_key_conflict'), ('missing_key', 'money_key_missing'),
    ('float', 'money_amount_invalid'), ('nan', 'money_amount_invalid'),
    ('currency', 'money_currency_missing'), ('partial', 'money_source_incomplete'),
    ('restricted', 'money_evidence_restricted'), ('unknown_ref', 'money_evidence_not_owned')])
def test_money_rejects_missing_conflicting_or_unauthorized_evidence(tmp_path, mutation, code):
    async def run_test():
        broker, store, run, token = make_broker(tmp_path)
        rows = [row()]
        if mutation == 'duplicate': rows *= 2
        if mutation == 'conflict': rows += [row(AmountInTransactionCurrency='9.99')]
        if mutation == 'missing_key': rows[0]['AccountingDocument'] = ''
        if mutation == 'float': rows[0]['AmountInTransactionCurrency'] = 0.1
        if mutation == 'nan': rows[0]['AmountInTransactionCurrency'] = 'NaN'
        if mutation == 'currency': rows[0]['TransactionCurrency'] = ''
        ref = seed(broker, store, run, rows, complete=mutation != 'partial',
            raw_patch={'rows_redacted': True} if mutation == 'restricted' else {})
        if mutation == 'unknown_ref': ref = 'ev_' + '0' * 24
        result = await broker.handle(run, token, 'sap_money_aggregate', {'evidence_ref': ref})
        assert not result['ok'] and result['code'] == code
        assert not store.get_harness_state(run).get('money_calculations')
    asyncio.run(run_test())


def test_money_currencies_signed_values_dates_and_unknowns_are_separate():
    rows = [row(AmountInTransactionCurrency='-1.10'),
        row(AccountingDocument='D2', TransactionCurrency='EUR', AmountInTransactionCurrency='2.20', ClearingDate='2020-12-31'),
        row(AccountingDocument='D3', ClearingDate='2021-01-01', AmountInTransactionCurrency='3.30'),
        row(AccountingDocument='D4', ClearingDate='invalid', SpecialGLCode='Z')]
    result = calculate(run_id='r', binding='frozen', evidence_ref='ev_'+'1'*24, rows=rows,
        plan=plan(), frozen=catalog(), source_complete=True)
    values = {(m['currency'], m['historical_status'], m['record_kind'], m['amount_field']): m['amount'] for m in result['metrics']}
    assert values['USD', 'open_at_cutoff', 'posted_normal', 'AmountInTransactionCurrency'] == '2.20'
    assert values['EUR', 'cleared_at_cutoff', 'posted_normal', 'AmountInTransactionCurrency'] == '2.20'
    assert any(m['record_kind'] == 'unknown' and m['historical_status'] == 'unknown' for m in result['metrics'])
    assert result['business_complete'] is False


def test_unbound_money_is_visible_but_never_business_complete(tmp_path):
    async def run_test():
        from sap_business_agents_platform.runtime_harness_result import finalize
        broker, store, run, token = make_broker(tmp_path)
        seed(broker, store, run, [row()])
        result = await broker.handle(run, token, 'sap_final_report_validate', {'report': report()})
        assert result['ok'] and result['financial_verification'] == 'unverified'
        assert result['financial_warning']
        payload = {'status': 'completed', 'business_complete': True, 'source_complete': True,
                   'presentation': result['validated_report'], 'evidence_refs': [], 'executed_plans': []}
        outcome = finalize(broker.settings, store, broker, run, payload, thread_id=None,
                           turn_count=1, run_started=time.monotonic())
        assert not outcome.business_complete and 'monetary_claims_unverified' in outcome.missing_evidence
    asyncio.run(run_test())


def test_calculation_record_tampering_is_recomputed_and_rejected(tmp_path):
    async def run_test():
        broker, store, run, token = make_broker(tmp_path)
        ref = seed(broker, store, run, [row()])
        result = await broker.handle(run, token, 'sap_money_aggregate', {'evidence_ref': ref})
        state = store.get_harness_state(run)
        records = state['money_calculations']
        records[result['calculation_ref']]['metrics'][0]['amount'] = '999'
        store.update_harness_state(run, {'money_calculations': records})
        bad = await broker.handle(run, token, 'sap_final_report_validate', {'report': report(blocks=[
            {'type': 'metrics', 'metrics': [metric(result)]}])})
        assert bad['ok'] is False and bad['code'] == 'money_calculation_changed'
    asyncio.run(run_test())


def test_complete_zero_proves_count_but_never_invents_currency_or_amount(tmp_path):
    async def run_test():
        broker, store, run, token = make_broker(tmp_path)
        ref = seed(broker, store, run, [])
        result = await broker.handle(run, token, 'sap_money_aggregate', {'evidence_ref': ref})
        assert result['ok'] and result['metrics'][0]['amount'] is None
        assert result['metrics'][0]['count'] == '0' and result['metrics'][0]['currency'] is None
        block = {'type': 'metrics', 'metrics': [metric(result, field='count')]}
        validated = await broker.handle(run, token, 'sap_final_report_validate', {'report': report(blocks=[block])})
        assert validated['ok'] and validated['financial_verification'] == 'verified_metrics_only'
    asyncio.run(run_test())


def test_company_and_ledger_groups_never_silently_merge():
    p = catalog()['sources'][0]
    query = {k: p[k] for k in ('service_name', 'odata_version', 'entity_set')} | {
        'select_fields': list(dict.fromkeys([*p['detail_fields'], *p['amount_pairs'], *p['amount_pairs'].values()])),
        'filters': [{'field': 'PostingDate', 'operator': 'le', 'value': '2020-12-31'}]}
    base = row(SourceLedger='0L', Ledger='0L', LedgerGLLineItem='1', GLAccount='G',
               BusinessTransactionType='RFBU', FiscalPeriod='001', AccountingDocumentCategory='')
    rows = [base, {**base, 'SourceLedger': '2L', 'Ledger': '2L'}, {**base, 'CompanyCode': 'C2'}]
    result = calculate(run_id='r', binding='frozen', evidence_ref='ev_'+'2'*24, rows=rows,
        plan=query, frozen=catalog(), source_complete=True)
    assert len(result['metrics']) == 12  # Two amount pairs x three scopes x detail/explicit rollup.
    assert all(m['amount'] == '0.10' and m['count'] == '1' for m in result['metrics'])
    assert {m['company_code'] for m in result['metrics']} == {'C1', 'C2'}
    assert {tuple(m['ledger']) for m in result['metrics']} == {('0L', '0L'), ('2L', '2L')}


def test_real_plan_repair_resets_only_plan_counter(tmp_path):
    async def run_test():
        broker, store, run, token = make_broker(tmp_path)
        await broker.handle(run, token, 'sap_query_validate', {'plan': {'odata_version': 'bad'}, 'tool_call_id': 'bad'})
        store.update_harness_state(run, {'report_convergence': {'without_progress': 1}})
        class OfflineRead:
            async def validate_plan(self, p, q): return {'ok': True, 'normalized_plan': p}
        broker.sap_read = OfflineRead()
        fixed = await broker.handle(run, token, 'sap_query_validate', {'plan': {
            'service_name': 'S', 'odata_version': '2.0', 'entity_set': 'Items'}, 'tool_call_id': 'fixed'})
        assert fixed['ok'] and store.get_harness_state(run)['plan_convergence'] is None
        assert store.get_harness_state(run)['report_convergence']['without_progress'] == 1
    asyncio.run(run_test())


def test_money_rejects_cross_run_and_unsupported_source(tmp_path):
    async def run_test():
        broker, store, run, token = make_broker(tmp_path)
        from sap_business_agents_platform.models import RunCreate, RunMode
        store.create_run('different_owned_run', RunCreate(mode=RunMode.free_query, query='Other synthetic task'))
        ref = broker._save_evidence('different_owned_run', 'sap_live', {'rows': [row()], 'source_complete': True})
        missing = await broker.handle(run, token, 'sap_money_aggregate', {'evidence_ref': ref})
        assert missing['code'] == 'money_evidence_not_owned'
        ref = seed(broker, store, run, [row()])
        # Claiming a valid currency without an owned calculation still fails.
        forged = {'id': 'a', 'label': {'zh': '金额', 'en': 'Amount'}, 'value': {'zh': '0.10', 'en': '0.10'},
                  'currency': 'USD', 'evidence_refs': [ref], 'calculation_ref': 'calc_foreign',
                  'calculation_item': 'm_fake', 'calculation_field': 'amount'}
        result = await broker.handle(run, token, 'sap_final_report_validate', {'report': report(blocks=[
            {'type': 'metrics', 'metrics': [forged]}])})
        assert not result['ok'] and result['validation_ref'] is None
    asyncio.run(run_test())


def test_reducing_required_field_set_is_real_plan_progress(tmp_path):
    async def run_test():
        broker, store, run, token = make_broker(tmp_path)
        class OfflineRead:
            remaining = 4
            async def validate_plan(self, p, q):
                n = self.remaining
                self.remaining -= 1
                return {'ok': False, 'validation_issues': [{'code': 'source_detail_grain_incomplete',
                    'path': '/select_fields', 'required_fields': ['F' + str(i) for i in range(n)]}]}
        broker.sap_read = OfflineRead()
        p = {'service_name': 'S', 'odata_version': '2.0', 'entity_set': 'Items'}
        for i in range(4):
            output = await broker.handle(run, token, 'sap_query_validate', {'plan': p, 'tool_call_id': str(i)})
            assert output['plan_convergence'] == {'without_progress': 1, 'terminal': False}
    asyncio.run(run_test())


@pytest.mark.parametrize('field', ['value', 'currency', 'item', 'reference'])
def test_wrong_amount_currency_or_reference_never_gets_validation_credential(tmp_path, field):
    async def run_test():
        broker, store, run, token = make_broker(tmp_path)
        ref = seed(broker, store, run, [row()])
        record = await broker.handle(run, token, 'sap_money_aggregate', {'evidence_ref': ref})
        m = metric(record)
        if field == 'value': m['value'] = {'zh': '-3774', 'en': '-3774'}
        if field == 'currency': m['currency'] = 'EUR'
        if field == 'item': m['calculation_item'] = 'invented'
        if field == 'reference': m['evidence_refs'] = []
        output = await broker.handle(run, token, 'sap_final_report_validate', {'report': report(blocks=[
            {'type': 'metrics', 'metrics': [m]}])})
        assert not output['ok'] and output['validation_ref'] is None
        assert 'validated_report' not in output
    asyncio.run(run_test())


def test_financial_request_without_a_registered_source_cannot_claim_verified_amounts(tmp_path):
    async def run_test():
        broker, store, run, token = make_broker(tmp_path)
        output = await broker.handle(run, token, 'sap_final_report_validate', {'report': report()})
        assert output['ok'] and output['financial_verification'] == 'unverified'
        assert output['financial_warning']
    asyncio.run(run_test())


def test_historical_report_receipt_is_read_without_upgrading_money_provenance():
    import hashlib
    from sap_business_agents_platform.models import RunPresentation
    from sap_business_agents_platform.harness import _validated_presentation_snapshot
    old = RunPresentation.model_validate(report()).model_dump(mode='json', exclude={'validation_ref', 'financial_verification'})
    value = 'sha256:' + hashlib.sha256(json.dumps(old, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    ref = 'validation_' + value.removeprefix('sha256:')[:24]
    call = {'tool_name': 'sap_final_report_validate', 'status': 'completed', 'output': {
        'ok': True, 'report_hash': value, 'validation_ref': ref, '_validated_report': old}}
    before = copy.deepcopy(call)
    recovered = _validated_presentation_snapshot(ref, [call])
    assert recovered is not None and recovered.validation_ref == ref
    assert recovered.financial_verification == 'not_requested' and call == before
    # New-format output cannot fall back to the old normalization.
    call['output']['presentation_contract'] = 'sapba.presentation-money/1'
    assert _validated_presentation_snapshot(ref, [call]) is None


def test_safe_compute_still_has_the_same_pure_expression_boundary():
    from sap_business_agents_platform.tool_gateway import _safe_evaluate, ToolAdmissionError
    assert _safe_evaluate('sum(values)', {'values': [1, 2]}) == 3
    with pytest.raises(ToolAdmissionError) as syntax:
        _safe_evaluate('result = 3', {})
    assert syntax.value.code == 'safe_compute_expression_invalid'
    with pytest.raises(ToolAdmissionError): _safe_evaluate("__import__('os')", {})


def test_terminal_handoff_requirements_are_schema_owned_not_a_generated_answer():
    from tests.test_workbuddy_harness_nullable import schema
    from sap_business_agents_platform.runtime_terminal_contract import handoff_requirements, repair_feedback
    frozen = schema()
    before = copy.deepcopy(frozen)
    requirements = handoff_requirements(frozen)
    assert set(requirements) == set(frozen['required'])
    feedback = repair_feedback(frozen, [{'code': 'runtime_report_schema_invalid', 'path': '/status', 'constraint': 'required'}])
    assert feedback['required_root_members'] == requirements
    assert 'validated_report' not in feedback and 'validation_ref' not in feedback
    assert frozen == before and feedback['retry_scope'] == 'terminal_only'


def test_rollup_computes_subtotals_without_guessing_unknown_classification():
    rows = [row(AmountInTransactionCurrency='1.10'),
            row(AccountingDocument='D2', SpecialGLCode='UNPROVEN', AmountInTransactionCurrency='-2.20')]
    result = calculate(run_id='r', binding='frozen', evidence_ref='ev_'+'3'*24, rows=rows,
                       plan=plan(), frozen=catalog(), source_complete=True)
    totals = [m for m in result['metrics'] if m.get('record_kind') == 'all_record_kinds'
              and m['amount_field'] == 'AmountInTransactionCurrency']
    assert len(totals) == 1 and totals[0]['amount'] == '-1.10' and totals[0]['count'] == '2'
    assert any(m.get('record_kind') == 'unknown' for m in result['metrics'])
    assert result['business_complete'] is False and result['row_count'] == len(rows)
