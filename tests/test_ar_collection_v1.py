from __future__ import annotations

from sap_business_agents_platform.agent_rules import evaluate_business_agent


def _complete(
    *,
    results: list[dict] | None = None,
    step_results: dict | None = None,
    **values: object,
) -> dict:
    payload = {"source_complete": True, "source_truncated": False, **values}
    if results is not None:
        payload["results"] = results
    if step_results is not None:
        payload["step_results"] = step_results
    return payload


def _inputs(as_of: str) -> dict:
    customer_item = {
        "CompanyCode": "1710",
        "Ledger": "0L",
        "FiscalYear": "2026",
        "AccountingDocument": "AR-FIXTURE",
        "AccountingDocumentItem": "1",
        "Customer": "CUSTOMER-FIXTURE",
        "FinancialAccountType": "D",
        "IsOpenItemManaged": True,
        "PostingDate": "2026-01-01",
        "NetDueDate": "2026-01-31",
        "ClearingDate": "2026-02-01",
        "ClearingIsReversed": True,
        "ClearingAccountingDocument": "CLEAR-FIXTURE",
        "ClearingDocFiscalYear": "2026",
        "AmountInTransactionCurrency": "100.00",
        "TransactionCurrency": "USD",
        "DebitCreditCode": "S",
        "SpecialGLCode": "",
    }
    clearing_document = {
        "CompanyCode": "1710",
        "Ledger": "0L",
        "FiscalYear": "2026",
        "AccountingDocument": "CLEAR-FIXTURE",
        "AccountingDocumentItem": "1",
        "IsReversed": True,
        "ReverseDocument": "REVERSE-FIXTURE",
        "ReverseDocumentFiscalYear": "2026",
    }
    reversal_document = {
        "CompanyCode": "1710",
        "Ledger": "0L",
        "FiscalYear": "2026",
        "AccountingDocument": "REVERSE-FIXTURE",
        "AccountingDocumentItem": "1",
        "PostingDate": "2026-06-01",
        "IsReversal": True,
    }
    return {
        "agent_id": "ar-collection",
        "run_input": {
            "company_code": "1710",
            "customers": ["CUSTOMER-FIXTURE"],
            "as_of": as_of,
            "business_date": "2026-09-04",
        },
        "evidence": {
            "leading_ledger": _complete(),
            "ledger_scope": _complete(ledger="0L", evidence_gaps=[]),
            "collect_ar_evidence": _complete(
                step_results={
                    "customer_items": _complete(results=[customer_item]),
                    "clearing_document_evidence": _complete(
                        results=[clearing_document]
                    ),
                    "clearing_reversal_documents": _complete(
                        results=[reversal_document]
                    ),
                    "customer_dunning": _complete(results=[]),
                }
            ),
        },
        "known_gaps": [],
    }


def test_ar_collection_item_cleared_at_cutoff_before_later_reversal() -> None:
    result = evaluate_business_agent(_inputs("2026-05-31"))

    customer = result["customer_results"][0]
    assert result["business_status"] == "normal"
    assert customer["open_item_count"] == 0
    assert customer["evidence_complete"] is True


def test_ar_collection_item_reopens_when_reversal_is_before_cutoff() -> None:
    result = evaluate_business_agent(_inputs("2026-06-30"))

    customer = result["customer_results"][0]
    # The AR item is deterministically reopened, while historical dunning
    # remains explicitly incomplete because only current master data exists.
    assert result["business_status"] == "inconclusive"
    assert customer["open_item_count"] == 1
    assert customer["items"][0]["historical_open_status"] == (
        "reopened_by_reversal"
    )
    assert customer["items"][0]["clearing_reversal_date"] == "2026-06-01"
    assert result["business_report"]["action_tables"][0]["artifact_name"] == (
        "ar-collection-worklist.csv"
    )


def test_ar_collection_reversed_clearing_without_date_is_inconclusive() -> None:
    inputs = _inputs("2026-06-30")
    inputs["evidence"]["collect_ar_evidence"]["step_results"][
        "clearing_reversal_documents"
    ]["results"] = []

    result = evaluate_business_agent(inputs)

    assert result["business_status"] == "inconclusive"
    assert result["customer_results"][0]["evidence_gaps"] == [
        "historical_clearing_reversal_date_missing"
    ]


def test_ar_collection_blank_dunning_area_is_not_a_wildcard() -> None:
    inputs = _inputs("2026-09-04")
    steps = inputs["evidence"]["collect_ar_evidence"]["step_results"]
    steps["customer_items"]["results"][0]["ClearingDate"] = None
    steps["customer_items"]["results"][0]["ClearingIsReversed"] = False
    steps["customer_dunning"]["results"] = [
        {
            "Customer": "CUSTOMER-FIXTURE",
            "CompanyCode": "1710",
            "DunningArea": "",
        },
        {
            "Customer": "CUSTOMER-FIXTURE",
            "CompanyCode": "1710",
            "DunningArea": "A1",
        },
    ]

    result = evaluate_business_agent(inputs)

    assert result["business_status"] == "inconclusive"
    assert "dunning_area_ambiguous" in result["customer_results"][0][
        "evidence_gaps"
    ]
