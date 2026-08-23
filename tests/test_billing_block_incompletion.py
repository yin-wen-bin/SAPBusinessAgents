from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform import rules
from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.manifests import AgentRepository


ROOT = Path(__file__).resolve().parents[1]


def _sap_read(*, complete: bool = True, items: list[dict[str, object]] | None = None) -> dict[str, object]:
    rows = items if items is not None else [
        {
            "SalesOrder": "2",
            "SalesOrderItem": "10",
            "ItemBillingBlockReason": "",
            "DeliveryStatus": "C",
        },
        {
            "SalesOrder": "2",
            "SalesOrderItem": "20",
            "ItemBillingBlockReason": "",
            "DeliveryStatus": "C",
        },
    ]
    step_rows = {
        "sales_orders": [{"SalesOrder": "2", "TotalCreditCheckStatus": "C"}],
        "sales_order_items": rows,
        "delivery_items": [],
        "delivery_headers": [],
        "billing_items": [],
        "billing_headers": [],
    }
    return {
        "ok": True,
        "data": {
            "source_complete": complete,
            "step_results": {
                step_id: {"source_complete": complete, "results": values}
                for step_id, values in step_rows.items()
            },
        },
    }


def _adt(rows: list[dict[str, object]], *, status: str = "complete") -> dict[str, object]:
    complete = status == "complete"
    return {
        "schema_version": 1,
        "skill_id": "sap-adt-table-export",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "status": status,
        "read_only": True,
        "validated": True,
        "source": {},
        "scope": {},
        "rows": rows,
        "row_count": len(rows),
        "completeness": {
            "source_complete": complete,
            "total_count_known": complete,
            "truncated": not complete,
            "paging_complete": complete,
            "reason": "complete" if complete else "row_limit_reached",
        },
        "validation_issues": [],
        "started_at": "2026-08-23T00:00:00Z",
        "completed_at": "2026-08-23T00:00:01Z",
        "artifacts": [
            {"type": "output_manifest", "sha256": "a" * 64, "verified": True}
        ],
    }


def _assessment(sap_read: dict[str, object]) -> dict[str, object]:
    return rules.evaluate(
        "assess_billing_block_incompletion",
        {"run_input": {"sales_order": "2"}, "sap_read": sap_read},
    )


def _evaluate(
    sap_read: dict[str, object],
    adt: dict[str, object],
) -> dict[str, object]:
    return evaluate_business_agent(
        {
            "agent_id": "billing-block-diagnosis",
            "run_input": {"sales_order": "2"},
            "evidence": {"collect_billing_block_evidence": sap_read},
            "assessment": _assessment(sap_read),
            "fallbacks": {"sales_order_item_incompletion": adt},
            "known_gaps": [],
        }
    )


def _incompletion_rows() -> list[dict[str, object]]:
    return [
        {
            "VBELN": "0000000002",
            "POSNR": "000010",
            "ETENR": "0000",
            "TBNAM": "VBAP",
            "FDNAM": "KWMENG",
            "FEHGR": "01",
            "STATG": "04",
        },
    ]


def test_assessment_requires_adt_only_after_complete_embedded_read_and_formats_keys() -> None:
    result = _assessment(_sap_read())

    assert result["status"] == "fallback_required"
    assert result["needs_adt"] == {"item_incompletion": True}
    assert result["adt_sales_order"] == "0000000002"
    assert result["adt_preflight_item"] == "000010"

    incomplete = _assessment(_sap_read(complete=False))
    assert incomplete["status"] == "inconclusive"
    assert incomplete["needs_adt"] == {"item_incompletion": False}


def test_complete_empty_vbuv_log_removes_gap() -> None:
    result = _evaluate(_sap_read(), _adt([]))

    assert result["status"] == "complete"
    assert result["source_complete"] is True
    assert result["business_status"] == "normal"
    assert result["missing_evidence"] == []
    assert result["metrics"] == [{"id": "blocked_findings", "value": 0}]
    assert {row["incompletion_status"] for row in result["business_report"]["records"]} == {
        "complete_or_not_relevant"
    }


def test_vbuv_missing_field_rows_create_explainable_findings() -> None:
    result = _evaluate(_sap_read(), _adt(_incompletion_rows()))

    assert result["status"] == "complete"
    assert result["business_status"] == "blocked"
    assert result["findings"] == [
        {
            "code": "SalesDocumentIncompletionLog",
            "severity": "high",
            "value": "VBAP.KWMENG",
            "object": "0000000002/000010",
            "status_group": "04",
            "incompletion_group": "01",
        }
    ]
    first = result["business_report"]["records"][0]
    assert first["incompletion_status"] == "incomplete:VBAP.KWMENG"
    assert result["business_report"]["records"][1]["incompletion_status"] == "complete_or_not_relevant"


def test_partial_unverified_or_out_of_scope_vbuv_evidence_keeps_capability_blocked() -> None:
    unverified = _adt([])
    unverified["artifacts"] = []
    variants = [
        _adt(_incompletion_rows(), status="partial"),
        unverified,
        _adt([{**_incompletion_rows()[0], "VBELN": "0000000003"}]),
        _adt([{**_incompletion_rows()[0], "POSNR": "000030"}]),
    ]
    for payload in variants:
        result = _evaluate(_sap_read(), payload)
        assert result["status"] == "inconclusive"
        assert result["business_status"] == "capability_blocked"
        assert result["source_complete"] is False
        assert "sales_order_item_incompletion_evidence" in result["missing_evidence"]


def test_manifest_uses_exact_conditional_vbuv_contract_without_connection_input() -> None:
    manifest = AgentRepository(ROOT / "agents").get("billing-block-diagnosis")
    steps = {step["id"]: step for step in manifest["execution"]["steps"]}

    preflight = steps["preflight_item_incompletion_fallback"]
    formal = steps["read_item_incompletion_fallback"]
    for step, maximum in ((preflight, 2), (formal, 200)):
        assert step["executor"] == "skill"
        assert step["skillId"] == "sap-adt-table-export"
        assert step["readOnly"] is True
        assert step["failurePolicy"] == "record_gap"
        assert step["inputMapping"]["object"] == "VBUV"
        assert step["inputMapping"]["fields"] == [
            "VBELN", "POSNR", "ETENR", "TBNAM", "FDNAM", "FEHGR", "STATG"
        ]
        assert step["inputMapping"]["max_rows"] == maximum
        assert "connection_profile" not in json.dumps(step)
    assert manifest["execution"]["acceptance"]["requiredLimitations"] == []
    assert manifest["execution"]["acceptance"]["blockingLimitations"] == [
        "sales_order_item_incompletion_evidence"
    ]
    assert manifest["execution"]["acceptance"]["businessStatusFromAnyPositiveMetric"] == {
        "metrics": ["blocked_findings"],
        "zero": "normal",
        "positive": "blocked",
    }
