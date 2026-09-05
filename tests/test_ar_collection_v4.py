from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform.managed_rules import execute_managed_rule
from sap_business_agents_platform.manifests import validate_manifest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "agents" / "FI" / "ar-collection" / "versions" / "1.2.0"


def _manifest() -> dict:
    return json.loads((PACKAGE / "agent.json").read_text(encoding="utf-8"))


def _item(
    document: str,
    *,
    due_date: str | None,
    amount: str = "100.00",
    debit_credit: str = "S",
    special_gl: str = "",
    dunning_level: str = "0",
    last_dunning_date: str | None = None,
    dunning_block: str = "",
) -> dict:
    return {
        "CompanyCode": "1710",
        "Ledger": "0L",
        "FiscalYear": "2026",
        "AccountingDocument": document,
        "AccountingDocumentItem": "1",
        "Customer": "USCU_L09",
        "FinancialAccountType": "D",
        "IsOpenItemManaged": True,
        "PostingDate": "2026-01-01",
        "NetDueDate": due_date,
        "AmountInTransactionCurrency": amount,
        "TransactionCurrency": "USD",
        "DebitCreditCode": debit_credit,
        "SpecialGLCode": special_gl,
        "DunningLevel": dunning_level,
        "LastDunningDate": last_dunning_date,
        "DunningBlockingReason": dunning_block,
    }


def _collection(items: list[dict], master: list[dict] | None = None) -> dict:
    return {
        "source_complete": True,
        "step_results": {
            "customer_items": {"source_complete": True, "results": items},
            "clearing_document_evidence": {"source_complete": True, "results": []},
            "clearing_reversal_documents": {"source_complete": True, "results": []},
            "customer_dunning": {
                "source_complete": True,
                "results": master or [],
            },
        },
    }


def _run(items: list[dict], master: list[dict] | None = None) -> dict:
    manifest = _manifest()
    return execute_managed_rule(
        (PACKAGE / "rules.py").read_text(encoding="utf-8"),
        {
            "run_input": {
                "company_code": "1710",
                "customers": ["USCU_L09"],
                "as_of": "2026-09-06",
                "business_date": "2026-09-06",
            },
            "evidence": {
                "ledger_scope": {"ledger": "0L", "evidence_gaps": []},
                "collect_ar_evidence": _collection(items, master),
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


def test_ar_collection_1_2_manifest_and_managed_rule_are_validated() -> None:
    manifest = _manifest()
    validate_manifest(manifest, str(PACKAGE / "agent.json"))
    assert manifest["version"] == "1.2.0"
    assert manifest["validation"]["verdict"] == "PASS"
    assert manifest["validation"]["executable"] is True
    assert manifest["validation"]["fixedAgentComparison"] == "MATCH"
    assert manifest["validation"]["blockingLimitations"] == []


def test_not_due_item_is_monitor_only_and_excluded_from_worklist() -> None:
    result = _run(
        [
            _item("9400000000", due_date="2026-01-01", amount="200.00"),
            _item("9400000001", due_date="2026-10-05", amount="1600.00"),
        ]
    )
    output = result["workflow_output"]
    customer = output["customer_results"][0]
    assert output["action_required_item_count"] == 1
    assert output["monitor_item_count"] == 1
    assert customer["action_required_item_count"] == 1
    assert customer["monitor_item_count"] == 1
    assert output["worklist_artifact"]["row_count"] == 1
    worklist = result["business_report"]["action_tables"][0]["rows"]
    assert [row["accounting_document"] for row in worklist] == ["9400000000"]
    monitor = customer["items"][1]
    assert monitor["action_required"] is False
    assert monitor["action_code"] == "monitor_until_due"
    assert monitor["action_priority"] == "none"


def test_overdue_actions_follow_aging_and_existing_dunning_rules() -> None:
    result = _run(
        [
            _item("1", due_date="2026-08-20"),
            _item("2", due_date="2026-07-15"),
            _item("3", due_date="2026-06-15"),
            _item("4", due_date="2026-01-01"),
            _item(
                "5",
                due_date="2026-01-01",
                dunning_level="2",
                last_dunning_date="2026-08-01",
            ),
        ]
    )
    by_document = {
        item["accounting_document"]: item
        for item in result["workflow_output"]["customer_results"][0]["items"]
    }
    assert by_document["1"]["action_priority"] == "low"
    assert by_document["2"]["action_priority"] == "medium"
    assert by_document["3"]["action_priority"] == "medium"
    assert by_document["4"]["action_priority"] == "high"
    assert by_document["4"]["action_code"] == "initiate_first_dunning"
    assert by_document["5"]["action_code"] == "continue_dunning_follow_up"


def test_block_special_gl_credit_and_missing_due_date_are_distinct_actions() -> None:
    result = _run(
        [
            _item("1", due_date="2026-01-01", dunning_block="A"),
            _item("2", due_date="2026-01-01", special_gl="A"),
            _item("3", due_date="2026-01-01", amount="-50.00", debit_credit="H"),
            _item("4", due_date=None),
        ]
    )
    by_document = {
        item["accounting_document"]: item
        for item in result["workflow_output"]["customer_results"][0]["items"]
    }
    assert by_document["1"]["action_code"] == "resolve_dunning_block"
    assert by_document["2"]["action_code"] == "review_special_gl"
    assert by_document["3"]["action_code"] == "review_credit_balance"
    assert by_document["4"]["action_code"] == "resolve_evidence_gap"
    assert result["business_status"] == "inconclusive"
    assert "due_date_missing" in result["evidence_gaps"]


def test_recursive_envelope_duplicates_do_not_inflate_stage_or_worklist_counts() -> None:
    item = _item("9400000000", due_date="2026-01-01")
    master = {
        "CompanyCode": "1710",
        "Customer": "USCU_L09",
        "DunningArea": "",
        "DunningProcedure": "1001",
        "DunningClerk": "",
        "DunningRecipient": "",
        "DunningBlock": "",
    }
    evidence = _collection([item], [master])
    evidence["customer_items"] = {"source_complete": True, "results": [dict(item)]}
    evidence["customer_dunning"] = {"source_complete": True, "results": [dict(master)]}
    manifest = _manifest()
    result = execute_managed_rule(
        (PACKAGE / "rules.py").read_text(encoding="utf-8"),
        {
            "run_input": {
                "company_code": "1710",
                "customers": ["USCU_L09"],
                "as_of": "2026-09-06",
                "business_date": "2026-09-06",
            },
            "evidence": {
                "ledger_scope": {"ledger": "0L", "evidence_gaps": []},
                "collect_ar_evidence": evidence,
                "historical_dunning": {"status": "skipped", "events": []},
            },
            "known_gaps": [],
        },
        expected_digest=manifest["managedRule"]["sha256"],
    )
    stages = {stage["id"]: stage for stage in result["business_report"]["stages"]}
    customer = result["workflow_output"]["customer_results"][0]
    assert stages["receivables"]["evidence_count"] == 1
    assert stages["dunning"]["evidence_count"] == 1
    assert customer["open_item_count"] == 1
    assert customer["dunning_procedure"] == "1001"
    assert customer["assignment_status"] == "unassigned"


def test_conflicting_dunning_master_key_fails_closed() -> None:
    master_a = {
        "CompanyCode": "1710",
        "Customer": "USCU_L09",
        "DunningArea": "",
        "DunningProcedure": "1001",
    }
    master_b = dict(master_a, DunningProcedure="2001")
    result = _run([_item("1", due_date="2026-01-01")], [master_a, master_b])
    assert result["business_status"] == "inconclusive"
    assert "dunning_master_business_key_conflict" in result["evidence_gaps"]


def test_empty_worklist_has_explicit_business_message() -> None:
    result = _run([_item("9400000001", due_date="2026-10-05", amount="1600.00")])
    table = result["business_report"]["action_tables"][0]
    assert table["rows"] == []
    assert table["empty_state"]["zh"] == "当前没有需要立即处理的项目。"
    assert result["workflow_output"]["business_status"] == "normal"
    assert result["workflow_output"]["action_required_item_count"] == 0
    assert result["workflow_output"]["monitor_item_count"] == 1


def test_worklist_report_contract_has_real_artifact_and_bilingual_actions() -> None:
    result = _run([_item("9400000000", due_date="2026-01-01")])
    report = result["business_report"]
    action_table = report["action_tables"][0]
    evidence_table = report["evidence_tables"][0]
    assert action_table["artifact_name"] == "ar-collection-worklist.csv"
    assert action_table["total_rows"] == 1
    assert evidence_table["total_rows"] == 1
    row = action_table["rows"][0]
    assert row["action_reason"]["zh"]
    assert row["action_reason"]["en"]
    assert row["recommended_action"]["zh"]
    assert row["recommended_action"]["en"]
    assert row["action_code_label"] == {
        "zh": "进入首次催收",
        "en": "Initiate first dunning",
    }
    assert evidence_table["columns"][-1]["key"] == "action_code_label"
    assert "SAPBusinessAgents" in report["overview"]["zh"]
    assert all(stage["label"]["zh"] for stage in report["stages"])
    assert all(stage["state_label"]["en"] for stage in report["stages"])
