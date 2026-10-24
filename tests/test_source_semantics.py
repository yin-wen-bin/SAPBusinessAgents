"""Pure offline source-grain/classification tests. Never contacts SAP/SDKs."""
import copy
from decimal import Decimal

import pytest

from sap_business_agents_platform.source_semantics import (
    assess, catalog, classify, comparison_issues, definition_issues, historical_state,
    plan_issues, profile, projection_issues, public_assessment, verify_catalog,
)


def gl_plan():
    frozen = catalog()
    p = frozen["sources"][0]
    return {"service_name": p["service_name"], "odata_version": "2.0", "entity_set": p["entity_set"],
        "http_method": "GET", "select_fields": p["detail_fields"] + list(p["amount_pairs"]) + list(p["amount_pairs"].values()),
        "filters": [{"field": "CompanyCode", "operator": "eq", "value": "1710"}]}


def row(**changes):
    value = dict(SourceLedger="MAIN", CompanyCode="1710", FiscalYear="2022", AccountingDocument="DOC1",
        AccountingDocumentItem="1", LedgerGLLineItem="001", Ledger="L1", GLAccount="AP1", FinancialAccountType="K",
        Supplier="VENDOR1", SpecialGLCode="", BusinessTransactionType="RFBU", FiscalPeriod="12",
        AccountingDocumentCategory="", PostingDate="2022-12-01", ClearingDate=None,
        ClearingAccountingDocument="", ClearingDocFiscalYear="0", AmountInCompanyCodeCurrency="-12.30", CompanyCodeCurrency="USD")
    return {**value, **changes}


def definition(**changes):
    return {"version": "1.0", "view": "ledger_lines", "ledgerScope": None,
            "categories": ["posted_normal"], "configurationTime": "posting_date", **changes}


def test_exact_projection_grain_and_currency_pairs_are_hard_checks():
    plan, frozen = gl_plan(), catalog()
    assert not plan_issues(plan, frozen)
    legacy = copy.deepcopy(plan)
    legacy["select_fields"] = ["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem", "AmountInCompanyCodeCurrency"]
    before = copy.deepcopy(legacy)
    codes = {i["code"] for i in plan_issues(legacy, frozen)}
    assert codes == {"source_detail_grain_incomplete", "source_amount_currency_pair_required"}
    assert legacy == before


def test_summary_requires_confirmed_scope_and_does_not_autoselect_ledger():
    plan = gl_plan()
    plan["select_fields"] = ["SourceLedger", "Ledger", "GLAccount", "AmountInCompanyCodeCurrency", "CompanyCodeCurrency"]
    plan["output_contract"] = {"mode": "explicit", "display_grain": "ledger_account_summary"}
    assert plan_issues(plan, catalog())[0]["code"] == "source_summary_scope_unverified"
    assert not plan_issues(plan, catalog(), definition(ledgerScope=["L1"]))
    plan["filters"].append({"field": "Ledger", "operator": "eq", "value": "L2"})
    assert "source_ledger_scope_changed" in {i["code"] for i in plan_issues(plan, catalog(), definition(ledgerScope=["L1"]))}


@pytest.mark.parametrize("doc,tx,period,category,kind", [
    ("ordinary", "RFBC", "000", "C", "balance_carryforward"),
    ("B000000001", "RFBU", "12", "", "posted_normal"),
    ("B000000001", "RFBC", "12", "C", "unknown"),
    ("ordinary", "RFBU", "000", "C", "unknown"),
])
def test_semantic_markers_not_document_prefix(doc, tx, period, category, kind):
    p = profile(gl_plan(), catalog())
    value = row(AccountingDocument=doc, BusinessTransactionType=tx, FiscalPeriod=period, AccountingDocumentCategory=category)
    assert classify(value, p)[0] == kind


@pytest.mark.parametrize("code", ["F", "Q", "Z"])
def test_noted_classification_is_config_based_and_historical_applicability_is_required(code):
    p, value = profile(gl_plan(), catalog()), row(SpecialGLCode=code)
    proof = {"account_type": "K", "code": code, "noted_item": True, "verified": True, "evidence_ref": "verified-config"}
    assert classify(value, p, [proof])[0] == "unknown"
    assert classify(value, p, [proof], configuration_time="current")[0] == "noted_item"
    proof.update(valid_from="2022-01-01", valid_to="2022-12-31")
    assert classify(value, p, [proof])[0] == "noted_item"
    proof["noted_item"] = False
    assert classify(value, p, [proof])[0] == "posted_special"
    proof["verified"] = False
    assert classify(value, p, [proof])[0] == "unknown"


@pytest.mark.parametrize("clearing,doc,year,expected", [
    ("2022-12-30", "CLEAR", "2022", "cleared_at_cutoff"),
    ("2022-12-31", "CLEAR", "2022", "cleared_at_cutoff"),
    ("2023-01-01", "CLEAR", "2023", "open_at_cutoff"),
    (None, "", "0", "open_at_cutoff"),
    (None, "CLEAR", "2022", "unknown"),
    ("bad-date", "", "0", "unknown"),
    ("2022-11-01", "CLEAR", "2022", "unknown"),
])
def test_clearing_boundary_and_conflicts(clearing, doc, year, expected):
    value = row(ClearingDate=clearing, ClearingAccountingDocument=doc, ClearingDocFiscalYear=year)
    assert historical_state(value, profile(gl_plan(), catalog()), "2022-12-31") == expected


def test_no_cross_ledger_currency_sign_or_category_addition_and_all_rows_retained():
    values = [row(), row(Ledger="L2", CompanyCodeCurrency="EUR"),
        row(LedgerGLLineItem="002", SpecialGLCode="ANY", IsNotedItmWithoutBalUpdt="X"),
        row(LedgerGLLineItem="003", BusinessTransactionType="RFBC", FiscalPeriod="000", AccountingDocumentCategory="C")]
    before = copy.deepcopy(values)
    value = assess(gl_plan(), values, catalog(), source_complete=True, cutoff="2022-12-31")
    assert values == before and len(value["records"]) == 4
    assert value["derived_balance_total"] is None and value["retention"] == "all_original_rows_preserved"
    assert len(value["grouped_amounts"]) == 4
    assert all(Decimal(g["amount"]) == Decimal("-12.30") for g in value["grouped_amounts"])
    assert {g["record_kind"] for g in value["grouped_amounts"]} == {"posted_normal", "noted_item", "balance_carryforward"}


@pytest.mark.parametrize("mutate,code", [
    (lambda rows: rows[0].pop("LedgerGLLineItem"), "source_technical_key_missing"),
    (lambda rows: rows.append(copy.deepcopy(rows[0])), "source_technical_key_duplicate"),
    (lambda rows: rows[0].update(AmountInCompanyCodeCurrency="NaN"), "source_amount_or_currency_invalid"),
    (lambda rows: rows[0].pop("GLAccount"), "source_detail_grain_incomplete"),
])
def test_bad_evidence_is_not_repaired_or_discarded(mutate, code):
    values = [row()]
    mutate(values)
    before = copy.deepcopy(values)
    result = assess(gl_plan(), values, catalog(), source_complete=True)
    assert code in {i["code"] for i in result["issues"]}
    assert result["business_complete"] is False and values == before
    assert result["row_count"] == len(values) == len(result["records"])


def test_empty_complete_and_incomplete_are_distinct():
    assert assess(gl_plan(), [], catalog(), source_complete=True)["business_complete"]
    assert not assess(gl_plan(), [], catalog(), source_complete=False)["business_complete"]


def test_current_configuration_cannot_make_a_historical_result_complete():
    p = gl_plan()
    p['filters'].append({'field':'PostingDate','operator':'le','value':'2022-12-31T00:00:00'})
    proof = {'account_type':'K','code':'ANY','verified':True,'evidence_ref':'configuration-now','noted_item':True}
    result = assess(p,[row(SpecialGLCode='ANY')],catalog(),source_complete=True,
                    configurations=[proof],definition=definition(configurationTime='current'))
    assert result['records'][0]['record_kind'] == 'unknown' and not result['business_complete']


@pytest.mark.parametrize("definition_value", [{}, {"version": "1"}, [], "bad", definition(categories=[{}]), definition(ledgerScope=[{}])])
def test_invalid_definitions_are_closed_not_runtime_exceptions(definition_value):
    assert definition_issues(definition_value)


def test_config_and_implementation_are_frozen_and_tamper_is_rejected():
    value = catalog()
    verify_catalog(value)
    value["sources"][0]["coverage"].append("noted_item")
    with pytest.raises(ValueError, match="binding_invalid"):
        verify_catalog(value)


def test_source_complete_is_not_comparable_coverage_or_grain():
    value = public_assessment(assess(gl_plan(), [row()], catalog(), source_complete=True))
    assert not comparison_issues([value], [value], definition())
    assert comparison_issues([value], [value], None)[0]["code"] == "source_scope_definition_required"
    coverage = definition(categories=["posted_normal", "noted_item"])
    assert "source_category_coverage_gap" in {i["code"] for i in comparison_issues([value], [value], coverage)}
    other = {**value, "view": "account_items"}
    assert "source_view_not_comparable" in {i["code"] for i in comparison_issues([value], [other], definition())}
    assert comparison_issues([], [], definition())


def test_projection_cannot_drop_rows_or_relabel_category():
    value = public_assessment(assess(gl_plan(), [row()], catalog(), source_complete=True))
    assert not projection_issues({"records": [{"record_kind": "posted_normal"}]}, [value], definition())
    assert projection_issues({"records": []}, [value], definition())[0]["code"] == "source_projection_grain_mismatch"
    assert projection_issues({"records": [{"record_kind": "noted_item"}]}, [value], definition())[0]["code"] == "source_projection_category_mismatch"


def test_semantic_comparison_blocks_before_numeric_match_but_unrelated_domains_unchanged():
    from sap_business_agents_platform.acceptance import compare_semantic_results
    value = public_assessment(assess(gl_plan(), [row()], catalog(), source_complete=True))
    base = {"records": [{"id": "one", "amount": "-12.30"}], "metrics": {}, "limitations": [], "source_complete": True}
    contract = {"business_keys": ["id"], "facts": ["amount"], "decimal_fields": ["amount"]}
    assert compare_semantic_results(base, base, contract).verdict == "MATCH"
    financial = {**base, "source_semantics": [value]}
    assert compare_semantic_results(financial, financial, contract).verdict == "BLOCKED"
    assert compare_semantic_results(financial, financial, {**contract, "source_semantics": definition()}).verdict == "MATCH"
    multiple = copy.deepcopy(financial)
    multiple["source_semantics"][0]["observed_ledgers"] = ["L1", "L2"]
    comparison = compare_semantic_results(multiple, multiple, {**contract, "source_semantics": definition(), "decimal_metrics": ["total"]})
    assert comparison.verdict == "BLOCKED" and comparison.differences[-1]["code"] == "source_non_additive_metric_rejected"
