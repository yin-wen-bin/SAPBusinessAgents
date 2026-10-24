"""Trusted synthetic offline regression against actual candidate rule source."""
import copy
import importlib.util
from pathlib import Path
import pytest
from sap_business_agents_platform.managed_rules import execute_managed_rule,source_digest,validate_managed_rule
from sap_business_agents_platform.source_semantics import catalog,profile,classify,historical_state
SOURCE=Path(__file__).parent/'fixtures/supplier_historical_scope_rule.py'
spec=importlib.util.spec_from_file_location('supplier_scope_rule',SOURCE);rule=importlib.util.module_from_spec(spec);spec.loader.exec_module(rule)

def row(**changes):
    return {'CompanyCode':'TEST','Supplier':'VENDOR','FinancialAccountType':'K','FiscalYear':'2022','AccountingDocument':'DOC',
        'AccountingDocumentItem':'001','SpecialGLCode':'','PostingDate':'2022-12-01','ClearingDate':None,
        'ClearingAccountingDocument':'','ClearingDocFiscalYear':'0','AccountingDocumentType':'RE',
        'AmountInTransactionCurrency':'-12.30','TransactionCurrency':'USD',**changes}

def inputs(rows):
    return {'run_input':{'company_code':'TEST','supplier':'VENDOR','posting_date_to':'2022-12-31'},
            'evidence':{'ok':True,'data':{'results':rows,'source_complete':True,'source_truncated':False}}}

@pytest.mark.parametrize('clearing,expected',[('2022-12-30','cleared_at_cutoff'),('2022-12-31','cleared_at_cutoff'),('2023-01-01','open_at_cutoff')])
def test_cutoff_and_shared_semantics(clearing,expected):
    value=row(ClearingDate=clearing,ClearingAccountingDocument='CLEAR',ClearingDocFiscalYear=clearing[:4])
    result=rule.evaluate(inputs([value]))
    assert result['records'][0]['classification']==expected and result['business_complete']
    p=catalog()['sources'][1]
    assert historical_state(value,p,'2022-12-31')==expected

@pytest.mark.parametrize('code',['F','OTHER'])
def test_unknown_configuration_retains_row_and_no_balance_guess(code):
    value=row(SpecialGLCode=code);source=inputs([value]);before=copy.deepcopy(source)
    result=rule.evaluate(source)
    assert result['records'][0]['record_kind']=='unknown' and not result['business_complete']
    assert result['amounts_by_currency'][0]['posted_open_amount'] is None
    assert len(result['records'])==1 and source==before
    assert classify(value,catalog()['sources'][1])[0]=='unknown'

def test_posted_noted_re_subset_and_currencies_remain_separate():
    values=[row(),row(AccountingDocument='NOTE',SpecialGLCode='ANY',IsNotedItmWithoutBalUpdt='X'),
            row(AccountingDocument='EUR',TransactionCurrency='EUR',AmountInTransactionCurrency='5.25')]
    result=rule.evaluate(inputs(values))
    assert result['business_complete'] and result['open_item_count']==3
    assert result['posted_open_item_count']==2 and result['noted_open_item_count']==1
    assert result['invoice_verification_count']==2
    usd=next(v for v in result['amounts_by_currency'] if v['currency']=='USD')
    assert usd['posted_open_amount']==usd['noted_open_amount']=='-12.30' and usd['open_amount']=='-24.60'
    assert len(result['amounts_by_currency'])==2

@pytest.mark.parametrize('mutation',[lambda r:r.pop('ClearingDate'),lambda r:r.update(IsReversed=True),
    lambda r:r.update(ClearingDate='2022-12-20',ClearingAccountingDocument='CLEAR',ClearingDocFiscalYear='2021'),lambda r:r.update(AmountInTransactionCurrency='NaN')])
def test_evidence_gaps_never_become_zero(mutation):
    value=row();mutation(value);result=rule.evaluate(inputs([value]))
    assert not result['business_complete'] and result['open_item_count'] is None
    assert len(result['records'])==1

def test_duplicate_conflict_and_missing_keys_never_drop_rows():
    values=[row(),row(),row(AmountInTransactionCurrency='-99'),row(AccountingDocumentItem='')]
    result=rule.evaluate(inputs(values))
    assert len(result['records'])==4 and result['duplicate_count']==2
    assert not result['business_complete'] and 'business_key_conflict' in result['limitations']

def test_carryforward_and_ledger_copies_never_enter_invoice_count_or_cross_addition():
    values=[row(AccountingDocument='ordinary-prefix',BusinessTransactionType='RFBC',FiscalPeriod='000',AccountingDocumentCategory='C',Ledger='A'),
            row(AccountingDocument='second',BusinessTransactionType='RFBC',FiscalPeriod='0',AccountingDocumentCategory='C',Ledger='B')]
    result=rule.evaluate(inputs(values))
    assert result['carryforward_count']==2 and result['invoice_verification_count']==0
    assert len(result['records'])==2 and len(result['amounts_by_currency'])==2
    assert all(v['carryforward_amount']=='-12.30' for v in result['amounts_by_currency'])

def test_complete_zero_differs_from_incomplete_and_actual_sandbox_runs():
    source=SOURCE.read_text(encoding='utf-8');validate_managed_rule(source)
    result=execute_managed_rule(source,inputs([]),expected_digest=source_digest(source))
    assert result['open_item_count']==0 and result['business_complete']
    incomplete=inputs([]);incomplete['evidence']['data']['source_complete']=False
    assert rule.evaluate(incomplete)['open_item_count'] is None
