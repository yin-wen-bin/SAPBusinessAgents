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
    plan = agent["execution"]["steps"][0]["request"]["plan"]

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
