from __future__ import annotations

from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.skills import _validate_adt_output


def _adt(status: str) -> dict[str, object]:
    complete = status == "complete"
    return {
        "schema_version": 1,
        "skill_id": "sap-adt-table-export",
        "run_id": f"run-{status}",
        "status": status,
        "read_only": True,
        "validated": status != "failed",
        "source": {},
        "scope": {},
        "rows": [],
        "row_count": 0,
        "completeness": {
            "source_complete": complete,
            "paging_complete": complete,
            "truncated": status == "partial",
            "reason": "bounded_complete" if complete else "row_limit_reached",
        },
        "validation_issues": [] if complete else [{"code": "row_limit_reached" if status == "partial" else "authorization_failed"}],
        "started_at": "2026-08-17T00:00:00Z",
        "completed_at": "2026-08-17T00:00:01Z",
        "artifacts": [],
    }


def _shortage_with(status: str) -> dict[str, object]:
    fallback = _adt(status)
    return {
        "agent_id": "material-shortage-procurement-response",
        "run_input": {"as_of": "2026-08-17"},
        "assessment": {
            "api_complete": {"mrp": False, "pr": False, "po_schedule": False, "source": False}
        },
        "evidence": {},
        "fallbacks": {
            "mrp": fallback,
            "pr": fallback,
            "po_schedule": fallback,
            "source": fallback,
        },
        "known_gaps": [],
    }


def test_complete_adt_can_close_an_explicit_api_gap() -> None:
    result = evaluate_business_agent(_shortage_with("complete"))
    assert result["source_complete"] is True
    assert result["status"] == "complete"
    assert result["missing_evidence"] == []


def test_partial_and_failed_adt_keep_business_result_inconclusive() -> None:
    for status in ("partial", "failed"):
        result = evaluate_business_agent(_shortage_with(status))
        assert result["source_complete"] is False
        assert result["status"] == "inconclusive"
        assert set(result["missing_evidence"]) == {
            "mrp_evidence", "pr_evidence", "po_schedule_evidence", "source_evidence"
        }
        _validate_adt_output(_adt(status))


def _embedded_response(*rows: dict[str, object]) -> dict[str, object]:
    return {
        "ok": True,
        "status": "completed",
        "source_complete": True,
        "source_truncated": False,
        "data": {
            "results": list(rows),
            "source_complete": True,
            "source_truncated": False,
        },
        "step_results": {
            "step_1": {
                "results": list(rows),
                "source_complete": True,
                "source_truncated": False,
            }
        },
    }


def test_inventory_report_consumes_embedded_rows_by_semantic_evidence_alias() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "inventory-health-balancing",
            "run_input": {
                "as_of": "2026-08-17",
                "slow_moving_days": 180,
                "obsolete_days": 365,
                "expiry_days": 90,
            },
            "assessment": {
                "api_complete": {
                    "stock": True,
                    "movement": True,
                    "batch_expiry": True,
                    "parameters": True,
                }
            },
            "evidence": {
                "stock": _embedded_response(
                    {
                        "MatlWrhsStkQtyInMatlBaseUnit": "100",
                        "MaterialBaseUnit": "EA",
                    }
                ),
                "movement": _embedded_response({"PostingDate": "2026-08-01"}),
                "batch": _embedded_response(
                    {
                        "ShelfLifeExpirationDate": "2026-09-01",
                        "MaterialBaseUnit": "EA",
                    }
                ),
                "parameters": _embedded_response({"SafetyStockQuantity": "20"}),
            },
            "fallbacks": {},
            "known_gaps": [],
        }
    )

    stages = {
        item["id"]: item["evidence_count"]
        for item in result["business_report"]["stages"]
    }
    metrics = {item["id"]: item["value"] for item in result["metrics"]}
    assert stages == {"stock": 1, "movement": 1, "batch": 1, "parameters": 1}
    assert metrics["unrestricted_stock"] == "100"
    assert metrics["confirmed_transfer_quantity"] == "80"
