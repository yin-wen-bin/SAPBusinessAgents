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
