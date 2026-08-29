from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform.agent_rules import evaluate_business_agent


def _row(document: str, clearing_date: str | None, *, account_type: str = "K") -> dict:
    return {
        "CompanyCode": "1710",
        "FiscalYear": "2018",
        "Ledger": "",
        "AccountingDocument": document,
        "AccountingDocumentItem": "1",
        "FinancialAccountType": account_type,
        "IsOpenItemManaged": "X",
        "IsCleared": clearing_date is not None,
        "PostingDate": "2018-03-15T00:00:00Z",
        "ClearingDate": clearing_date,
        "ClearingAccountingDocument": "2000000010" if clearing_date else "",
        "DebitCreditCode": "H",
        "AmountInTransactionCurrency": "-1000.00",
        "TransactionCurrency": "USD",
    }


def test_ap_payment_reconstructs_open_items_at_cutoff() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "ap-payment",
            "run_input": {
                "company_code": "1710",
                "supplier": "17300001",
                "as_of": "2018-10-01",
            },
            "evidence": {
                "collect_ap_evidence": {
                    "source_complete": True,
                    "data": {
                        "results": [
                            _row("open", None),
                            _row("later", "2018-10-16T00:00:00Z"),
                            _row("cleared", "2018-03-13T00:00:00Z"),
                            _row("wrong-account-type", None, account_type="S"),
                        ]
                    },
                }
            },
            "known_gaps": ["payment_run_and_bank_master_evidence"],
        }
    )

    report = result["business_report"]
    assert result["workflow_output"]["source_complete"] is True
    assert next(item for item in report["metrics"] if item["id"] == "open_items")["value"] == 2
    assert [item["accounting_document"] for item in report["records"]] == ["open", "later"]
    assert report["records"][0]["as_of_status"] == "open"
    assert report["records"][1]["as_of_status"] == "open_subsequently_cleared"
    assert all(item["payment_evidence_status"] == "bank_settlement_not_proven" for item in report["records"])


def test_ap_manifest_filters_account_type_and_posting_cutoff() -> None:
    root = Path(__file__).resolve().parents[1]
    agent = json.loads((root / "agents" / "FI" / "ap-payment" / "agent.json").read_text(encoding="utf-8"))
    plan = next(
        step["request"]["plan"]
        for step in agent["execution"]["steps"]
        if step["id"] == "collect_ap_evidence"
    )

    filters = {(item["field"], item["operator"]): item for item in plan["filters"]}
    assert filters[("FinancialAccountType", "eq")]["value"] == "K"
    assert filters[("PostingDate", "le")]["value"] == "{{input.as_of}}"
    assert "ClearingDate" in plan["select_fields"]
    assert "Ledger" in plan["order_by"]


def test_ap_payment_parses_sap_v2_dates_for_historical_cutoff() -> None:
    row = _row("100001011", "/Date(1539820800000)/")
    row["AccountingDocumentItem"] = "2"
    row["PostingDate"] = "/Date(1517270400000)/"
    result = evaluate_business_agent(
        {
            "agent_id": "ap-payment",
            "run_input": {
                "company_code": "1710",
                "supplier": "17300001",
                "as_of": "2018-10-01",
            },
            "evidence": {
                "collect_ap_evidence": {
                    "source_complete": True,
                    "data": {"results": [row]},
                }
            },
        }
    )

    record = result["business_report"]["records"][0]
    assert record["posting_date"] == "2018-01-30"
    assert record["clearing_date"] == "2018-10-18"
    assert record["as_of_status"] == "open_subsequently_cleared"


def test_ap_payment_consumes_p2p_scopes_without_claiming_bank_settlement() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "ap-payment",
            "run_input": {
                "query_mode": "p2p_evidence",
                "as_of": "2026-08-29",
                "ap_payment_scopes": [
                    {
                        "scope_id": "1710:1000001",
                        "company_code": "1710",
                        "supplier": "1000001",
                        "purchase_orders": ["4500000041"],
                        "source_complete": True,
                        "evidence_complete": True,
                        "evidence_refs": ["case-fixture"],
                        "fi_supplier_items": [
                            {
                                "purchase_order": "4500000041",
                                "purchase_order_item": "10",
                                "company_code": "1710",
                                "supplier": "1000001",
                                "fiscal_year": "2026",
                                "accounting_document": "5100000010",
                                "accounting_document_item": "1",
                                "posting_date": "2026-08-01",
                                "net_due_date": "2026-08-20",
                                "payment_blocking_reason": "A",
                                "amount": "100.00",
                                "currency": "CNY",
                                "is_cleared": False,
                                "clearing_document": "",
                                "clearing_fiscal_year": "",
                                "clearing_date": "",
                                "payment_method": "T",
                            }
                        ],
                    }
                ],
            },
            "evidence": {"collect_ap_evidence": {"source_complete": True, "status": "skipped"}},
        }
    )

    output = result["workflow_output"]
    assert output["business_status"] == "blocked"
    assert output["scope_results"][0]["payment_blocked_count"] == 1
    assert output["bank_settlement_status"] == "not_assessed"
    assert result["business_report"]["records"][0]["payment_readiness"] == "overdue_blocked"
