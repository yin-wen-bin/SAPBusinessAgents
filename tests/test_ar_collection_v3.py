from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform.managed_rules import execute_managed_rule
from sap_business_agents_platform.manifests import validate_manifest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "agents" / "FI" / "ar-collection" / "versions" / "1.1.0"


def test_customer_gap_is_preserved_in_top_level_report(monkeypatch):
    evidence = _collection()
    evidence["step_results"]["customer_items"]["results"][0]["NetDueDate"] = None
    monkeypatch.setattr(__name__ + "._collection", lambda: evidence)
    result = _run({}, as_of="2026-09-04", business_date="2026-09-04")
    assert "due_date_missing" in result["evidence_gaps"]
    assert "due_date_missing" in result["business_report"]["missing_evidence"]
    assert result["business_status"] == "inconclusive"


def _manifest() -> dict:
    return json.loads((PACKAGE / "agent.json").read_text(encoding="utf-8"))


def _collection() -> dict:
    return {
        "source_complete": True,
        "step_results": {
            "customer_items": {
                "source_complete": True,
                "results": [
                    {
                        "CompanyCode": "1710",
                        "Ledger": "0L",
                        "FiscalYear": "2025",
                        "AccountingDocument": "1800000001",
                        "AccountingDocumentItem": "1",
                        "Customer": "100001",
                        "FinancialAccountType": "D",
                        "IsOpenItemManaged": True,
                        "PostingDate": "2025-01-01",
                        "NetDueDate": "2025-01-15",
                        "AmountInTransactionCurrency": "100.00",
                        "TransactionCurrency": "USD",
                        "DebitCreditCode": "S",
                        "SpecialGLCode": "",
                    }
                ],
            },
            "clearing_document_evidence": {"source_complete": True, "results": []},
            "clearing_reversal_documents": {"source_complete": True, "results": []},
            "customer_dunning": {"source_complete": True, "results": []},
        },
    }


def _run(history: dict, *, as_of: str = "2025-02-01", business_date: str = "2026-09-04") -> dict:
    manifest = _manifest()
    return execute_managed_rule(
        (PACKAGE / "rules.py").read_text(encoding="utf-8"),
        {
            "run_input": {
                "company_code": "1710",
                "customers": ["100001"],
                "as_of": as_of,
                "business_date": business_date,
            },
            "evidence": {
                "ledger_scope": {"evidence_gaps": []},
                "collect_ar_evidence": _collection(),
                "historical_dunning": history,
            },
            "known_gaps": [],
        },
        expected_digest=manifest["managedRule"]["sha256"],
    )


def test_ar_collection_1_1_manifest_and_managed_rule_are_valid() -> None:
    validate_manifest(_manifest(), str(PACKAGE / "agent.json"))


def test_historical_dunning_event_closes_old_evidence_gap() -> None:
    result = _run(
        {
            "status": "complete",
            "completeness": {"source_complete": True, "evidence_complete": True},
            "events": [
                {
                    "customer": "100001",
                    "company_code": "1710",
                    "dunning_area": "",
                    "dunning_run_date": "2025-01-20",
                    "effective_dunning_date": "2025-01-20",
                    "fiscal_year": "2025",
                    "accounting_document": "1800000001",
                    "accounting_document_item": "1",
                    "dunning_level": "2",
                    "dunning_blocking_reason": "",
                    "sequence_status": "ordered",
                }
            ],
        }
    )
    output = result["workflow_output"]
    assert output["business_status"] == "attention"
    assert output["evidence_complete"] is True
    assert output["customer_results"][0]["items"][0]["dunning_as_of_status"] == "confirmed_before_cutoff"
    assert "historical_dunning_evidence" not in result["evidence_gaps"]


def test_incomplete_historical_skill_remains_inconclusive() -> None:
    result = _run(
        {
            "status": "partial",
            "completeness": {"source_complete": False, "evidence_complete": False},
            "events": [],
        }
    )
    assert result["workflow_output"]["business_status"] == "inconclusive"
    assert "historical_dunning_source_incomplete" in result["evidence_gaps"]


def test_current_date_does_not_require_historical_skill() -> None:
    result = _run(
        {
            "status": "skipped",
            "evidence_status": "not_requested",
            "events": [],
        },
        as_of="2026-09-04",
        business_date="2026-09-04",
    )
    assert result["workflow_output"]["source_complete"] is True
    assert "historical_dunning_source_incomplete" not in result["evidence_gaps"]


def test_operational_cube_blank_ledger_uses_resolved_leading_ledger_context() -> None:
    collection = _collection()
    collection["step_results"]["customer_items"]["results"][0]["Ledger"] = ""
    manifest = _manifest()
    result = execute_managed_rule(
        (PACKAGE / "rules.py").read_text(encoding="utf-8"),
        {
            "run_input": {
                "company_code": "1710",
                "customers": ["100001"],
                "as_of": "2026-09-04",
                "business_date": "2026-09-04",
            },
            "evidence": {
                "ledger_scope": {"ledger": "0L", "evidence_gaps": []},
                "collect_ar_evidence": collection,
                "historical_dunning": {
                    "status": "skipped",
                    "evidence_status": "not_requested",
                    "events": [],
                },
            },
            "known_gaps": [],
        },
        expected_digest=manifest["managedRule"]["sha256"],
    )

    item = result["workflow_output"]["customer_results"][0]["items"][0]
    assert item["ledger"] == "0L"
    assert "fi_business_key_conflict" not in result["evidence_gaps"]


def test_sap_v2_dates_are_not_discarded_from_live_ar_rows() -> None:
    collection = _collection()
    row = collection["step_results"]["customer_items"]["results"][0]
    row["PostingDate"] = "/Date(1735689600000)/"
    row["NetDueDate"] = "/Date(1736899200000)/"
    manifest = _manifest()

    result = execute_managed_rule(
        (PACKAGE / "rules.py").read_text(encoding="utf-8"),
        {
            "run_input": {
                "company_code": "1710",
                "customers": ["100001"],
                "as_of": "2025-02-01",
                "business_date": "2026-09-04",
            },
            "evidence": {
                "ledger_scope": {"ledger": "0L", "evidence_gaps": []},
                "collect_ar_evidence": collection,
                "historical_dunning": {
                    "status": "complete",
                    "completeness": {
                        "source_complete": True,
                        "evidence_complete": True,
                    },
                    "events": [],
                },
            },
            "known_gaps": [],
        },
        expected_digest=manifest["managedRule"]["sha256"],
    )

    assert len(result["workflow_output"]["customer_results"][0]["items"]) == 1
