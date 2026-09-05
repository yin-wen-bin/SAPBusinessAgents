from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform.agent_rules import evaluate_business_agent


def _row(document: str, level: str, last_dunning: str | None) -> dict:
    return {
        "CompanyCode": "1710",
        "FiscalYear": "2018",
        "AccountingDocument": document,
        "AccountingDocumentItem": "1",
        "Customer": "17100001",
        "FinancialAccountType": "D",
        "IsOpenItemManaged": "X",
        "PostingDate": "2018-03-15T00:00:00Z",
        "NetDueDate": "2018-04-15T00:00:00Z",
        "ClearingDate": None,
        "AmountInTransactionCurrency": "100.00",
        "TransactionCurrency": "USD",
        "DunningLevel": level,
        "LastDunningDate": last_dunning,
    }


def test_ar_collection_separates_cutoff_dunning_from_current_master() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "ar-collection",
            "run_input": {
                "company_code": "1710",
                "customer": "17100001",
                "as_of": "2018-10-01",
            },
            "evidence": {
                "collect_ar_evidence": {
                    "source_complete": True,
                    "data": {
                        "step_results": {
                            "customer_items": {
                                "source_complete": True,
                                "results": [
                                    _row("confirmed", "1", "2018-03-13T00:00:00Z"),
                                    _row("later", "1", "2020-11-11T00:00:00Z"),
                                    _row("none", "0", None),
                                ],
                            },
                            "customer_dunning": {
                                "source_complete": True,
                                "results": [
                                    {
                                        "Customer": "17100001",
                                        "CompanyCode": "1710",
                                        "DunningArea": "",
                                        "DunningLevel": "1",
                                        "LastDunnedOn": "2020-11-11T00:00:00Z",
                                    }
                                ],
                            },
                        }
                    },
                }
            },
        }
    )

    report = result["business_report"]
    status = {row["accounting_document"]: row["dunning_as_of_status"] for row in report["records"]}
    metrics = {item["id"]: item["value"] for item in report["metrics"]}
    assert status == {
        "confirmed": "confirmed_before_cutoff",
        "later": "historical_status_unknown",
        "none": "historical_status_unknown",
    }
    assert metrics == {"open_items": 3, "dunned_items": 1, "historical_dunning_unknown": 2}
    assert report["missing_evidence"] == ["historical_dunning_evidence"]
    assert all(row["business_status"] == "capability_blocked" for row in report["records"])


def test_active_ar_manifest_uses_complete_get_only_sources() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "agents" / "FI" / "ar-collection" / "agent.json").read_text(encoding="utf-8")
    )
    execution_steps = {
        step["id"]: step for step in manifest["execution"]["steps"]
    }
    assert execution_steps["read_leading_ledger"]["request"]["plan"]["http_method"] == "GET"
    plan = execution_steps["collect_ar_evidence"]["request"]["plan"]
    assert plan["plan_kind"] == "multi_step"
    steps = {step["step_id"]: step for step in plan["steps"]}
    assert set(steps) == {
        "customer_items",
        "customer_dunning",
        "clearing_document_evidence",
        "clearing_reversal_documents",
    }
    assert all(step["http_method"] == "GET" for step in steps.values())
    item_filters = {(item["field"], item["operator"]): item["value"] for item in steps["customer_items"]["filters"]}
    assert item_filters[("FinancialAccountType", "eq")] == "D"
    assert item_filters[("PostingDate", "le")] == "{{input.as_of}}"
    assert "LastDunningDate" in steps["customer_items"]["select_fields"]
    assert steps["customer_dunning"]["entity_set"] == "A_CustomerDunning"
    historical = execution_steps["read_historical_dunning"]
    assert historical["executor"] == "skill"
    assert historical["skillId"] == "sap-ar-dunning-history-evidence"
    assert historical["readOnly"] is True
