"""Synthetic-only reference behavior for the target draft. No live SAP."""
import copy
from pathlib import Path
import pytest
from tests.supplier_historical_open_reference import evaluate
from sap_business_agents_platform.managed_rules import validate_managed_rule


def row(**values):
    return dict(CompanyCode='TEST', FiscalYear='2020', AccountingDocument='EXAMPLE', AccountingDocumentItem='1',
        Supplier='SYNTHETIC', FinancialAccountType='K', PostingDate='2020-06-01', ClearingDate=None,
        ClearingAccountingDocument='', ClearingDocFiscalYear='', AccountingDocumentType='RE',
        DebitCreditCode='H', AmountInTransactionCurrency='-100.20', TransactionCurrency='USD') | values


def run(rows, **source):
    return evaluate({'run_input': {'supplier': 'SYNTHETIC', 'posting_date_to': '2020-06-30'},
        'evidence': {'ok': True, 'data': {'results': rows, 'source_complete': True, 'source_truncated': False}, **source}})


@pytest.mark.parametrize('clearing,expected', [('2020-06-29', 0), ('2020-06-30', 0), ('2020-07-01', 1)])
def test_clearing_cutoff_boundary(clearing, expected):
    result = run([row(ClearingDate=clearing, ClearingAccountingDocument='CLEAR', ClearingDocFiscalYear='2020')])
    assert result['open_item_count'] == expected
    assert result['business_complete']


@pytest.mark.parametrize('change,gap', [
    ({'PostingDate': None}, 'posting_date_or_scope_missing'),
    ({'PostingDate': '2020-02-31'}, 'posting_date_or_scope_missing'),
    ({'ClearingDate': 'broken'}, 'clearing_evidence_conflict'),
    ({'ClearingAccountingDocument': 'CLEAR'}, 'clearing_evidence_conflict'),
    ({'ClearingDate': '2020-07-01'}, 'clearing_evidence_conflict'),
    ({'Supplier': 'OTHER'}, 'source_scope_mismatch'),
    ({'CompanyCode': ''}, 'business_key_missing'),
    ({'AmountInTransactionCurrency': 'NaN'}, 'amount_or_currency_missing')])
def test_missing_or_conflicting_evidence_is_not_zero(change, gap):
    result = run([row(**change)])
    assert result['business_status'] == 'needs_evidence'
    assert result['open_item_count'] is None
    assert gap in result['limitations']


def test_decimal_sign_currency_and_invoice_subset():
    result = run([row(), row(AccountingDocumentItem='2', AmountInTransactionCurrency='20.10', AccountingDocumentType='KZ'),
                  row(AccountingDocumentItem='3', AmountInTransactionCurrency='-0.01', TransactionCurrency='EUR')])
    assert result['open_item_count'] == 3 and result['invoice_verification_count'] == 2
    assert result['amounts_by_currency'] == [
        {'currency': 'EUR', 'open_amount': '-0.01', 'invoice_verification_amount': '-0.01'},
        {'currency': 'USD', 'open_amount': '-80.10', 'invoice_verification_amount': '-100.20'}]


def test_exact_duplicates_conflicts_and_empty_completeness():
    result = run([row(), row()])
    assert result['open_item_count'] == 1 and result['duplicate_count'] == 1
    result = run([row(), row(AmountInTransactionCurrency='-999')])
    assert result['business_status'] == 'needs_evidence' and result['open_item_count'] is None
    assert result['records'][0]['amount'] is None
    assert run([])['business_status'] == 'no_open_items'
    result = run([], data={'results': [], 'source_complete': False})
    assert result['business_status'] == 'needs_evidence' and result['open_item_count'] is None


def test_absent_clearing_field_is_not_explicitly_uncleared():
    item = row()
    item.pop('ClearingDate')
    result = run([item])
    assert result['open_item_count'] is None
    assert 'clearing_evidence_missing' in result['limitations']


@pytest.mark.parametrize('year', [0, '0', '0000', ''])
def test_explicit_sap_zero_clearing_year(year):
    result = run([row(ClearingDocFiscalYear=year)])
    assert result['business_complete'] is True and result['open_item_count'] == 1


def test_reference_satisfies_existing_managed_rule_sandbox():
    source = (Path(__file__).parent / 'supplier_historical_open_reference.py').read_text(encoding='utf-8')
    assert validate_managed_rule(source)['ok']
