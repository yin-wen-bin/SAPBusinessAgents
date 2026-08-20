from __future__ import annotations

from datetime import date

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


def test_required_adt_topic_does_not_become_complete_when_dependency_skip_occurs() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "internal-order-project-control",
            "run_input": {
                "object_type": "INTERNAL_ORDER",
                "object_id": "1002199",
                "company_code": "1000",
                "fiscal_year": "2026",
            },
            "assessment": {
                "api_complete": {
                    "actual": True,
                    "plan": True,
                    "master": False,
                    "budget": False,
                    "commitment": False,
                },
                "needs_adt": {"master": True, "budget": True, "commitment": True},
            },
            "evidence": {
                "actual": _embedded_response({"AmountInCompanyCodeCurrency": "1"}),
                "plan": _embedded_response(),
            },
            "fallbacks": {
                "master": {"status": "skipped", "reason": "condition_false", "required": False},
                "budget": {"status": "skipped", "reason": "condition_false", "required": False},
                "commitment": {"status": "skipped", "reason": "condition_false", "required": False},
            },
            "known_gaps": [],
        }
    )

    assert result["source_complete"] is False
    assert set(result["missing_evidence"]) >= {
        "master_evidence",
        "budget_evidence",
        "commitment_evidence",
    }


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
                "as_of": date.today().isoformat(),
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


def test_material_shortage_report_keeps_context_sources_out_of_primary_records() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "material-shortage-procurement-response",
            "run_input": {"material": "TG10", "plant": "1710", "as_of": "2026-08-20"},
            "evidence": {
                "mrp": _embedded_response(
                    {
                        "Material": "TG10",
                        "MRPPlant": "1710",
                        "MaterialShortageProfile": "SAP000000001",
                        "MaterialShortageProfileCount": "001",
                        "MRPPlanningSegmentType": "02",
                        "MRPPlanningSegmentNumber": "",
                        "MaterialShortageQuantity": "0.000",
                        "MaterialBaseUnit": "ST",
                    }
                ),
                "pr": _embedded_response(
                    {
                        "PurchaseRequisition": "10001162",
                        "PurchaseRequisitionItem": "20",
                        "Material": "TG10",
                        "Plant": "1710",
                        "DeliveryDate": "2021-11-16",
                        "BaseUnit": "ST",
                        "ProcessingStatus": "N",
                        "PurReqnReleaseStatus": "02",
                        "IsClosed": False,
                        "IsDeleted": False,
                    }
                ),
                "po_schedule": {
                    "ok": True,
                    "source_complete": True,
                    "step_results": {
                        "schedule_po_items": {
                            "source_complete": True,
                            "results": [{"PurchaseOrder": "4500000134", "PurchaseOrderItem": "10", "Material": "TG10", "Plant": "1710"}],
                        },
                        "po_schedules": {
                            "source_complete": True,
                            "results": [{"PurchasingDocument": "4500000134", "PurchasingDocumentItem": "10", "ScheduleLine": "1", "ScheduleLineDeliveryDate": "2018-04-14", "ScheduleLineOrderQuantity": "10", "ScheduleLineCommittedQuantity": "5", "PurchaseOrderQuantityUnit": "ST"}],
                        },
                    },
                },
                "source": _embedded_response(
                    {
                        "PurchasingInfoRecord": "5300000100",
                        "PurchasingInfoRecordCategory": "0",
                        "PurchasingOrganization": "1710",
                        "Plant": "1710",
                        "Material": "TG10",
                        "PurgDocOrderQuantityUnit": "ST",
                        "IsMarkedForDeletion": False,
                    }
                ),
            },
            "fallbacks": {},
            "known_gaps": [],
        }
    )

    record_ids = {row["requirement_id"] for row in result["business_report"]["records"]}
    assert record_ids == {"SAP000000001|001|1710|(blank)|02"}
    metrics = {item["id"]: item["value"] for item in result["metrics"]}
    assert metrics == {
        "shortage_quantity": "0",
        "pending_pr": 1,
        "expedite_po": 1,
        "valid_source_candidates": 1,
    }
    assert result["business_status"] == "attention"
