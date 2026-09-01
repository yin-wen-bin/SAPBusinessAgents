from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.rules import (
    resolve_demand_forecast_context,
    resolve_mrp_analysis_context,
    resolve_new_sales_demand_context,
)


MRP_INPUT = {
    "plant": "1010",
    "mrp_area": "1010",
    "material": "SG21",
    "shortage_profile": "SAP000000001",
    "shortage_counter": "001",
}


def _payload(rows: list[dict[str, object]], *, complete: bool = True) -> dict[str, object]:
    return {
        "ok": complete,
        "source_complete": complete,
        "data": {"results": rows},
    }


def _master(**overrides: object) -> dict[str, object]:
    return {
        "Material": "SG21",
        "MRPPlant": "1010",
        "MRPArea": "1010",
        "BaseUnit": "PC",
        **overrides,
    }


def _coverage(**overrides: object) -> dict[str, object]:
    return {
        "Material": "SG21",
        "MRPPlant": "1010",
        "MRPArea": "1010",
        "MaterialShortageProfile": "SAP000000001",
        "MaterialShortageProfileCount": "001",
        "MaterialShortageQuantity": "0.000",
        "MaterialShortageStartDate": None,
        "MaterialShortageEndDate": None,
        "DaysOfSupplyDuration": 30,
        "MaterialReplnmtLeadDurnEndDate": "2026-09-30",
        "TimeHorizonInDays": 999,
        "HasAcceptedShortage": "",
        "MaterialBaseUnit": "PC",
        **overrides,
    }


def _element(**overrides: object) -> dict[str, object]:
    return {
        "Material": "SG21",
        "MRPPlant": "1010",
        "MRPArea": "1010",
        "MaterialShortageProfile": "SAP000000001",
        "MaterialShortageProfileCount": "001",
        "MRPElement": "4500000001",
        "MRPElementItem": "10",
        "MRPElementScheduleLine": "1",
        "MRPElementCategory": "BE",
        "MRPElementCategoryShortName": "采购订单",
        "MRPElementAvailyOrRqmtDate": "2026-09-15",
        "MRPElementReschedulingDate": None,
        "MRPElementOpenQuantity": "10.000",
        "MRPAvailableQuantity": "20.000",
        "MaterialBaseUnit": "PC",
        "ExceptionMessageNumber": "",
        "ExceptionMessageText": "",
        "ExceptionMessageNumber2": "",
        "ExceptionMessageText2": "",
        **overrides,
    }


def _evaluate_mrp(
    *,
    coverage: list[dict[str, object]] | None = None,
    elements: list[dict[str, object]] | None = None,
    master: list[dict[str, object]] | None = None,
    master_complete: bool = True,
    coverage_complete: bool = True,
    elements_complete: bool = True,
) -> dict[str, object]:
    return evaluate_business_agent(
        {
            "agent_id": "mrp-exception-analysis",
            "run_input": dict(MRP_INPUT),
            "analysis_context": {"analysis_date": "2026-08-25"},
            "evidence": {
                "mrp_material": _payload(master if master is not None else [_master()], complete=master_complete),
                "material_coverages": _payload(coverage if coverage is not None else [_coverage()], complete=coverage_complete),
                "supply_demand_items": _payload(elements if elements is not None else [], complete=elements_complete),
            },
        }
    )


def test_mrp_analysis_context_captures_local_business_date() -> None:
    context = resolve_mrp_analysis_context({"run_input": MRP_INPUT})
    assert context["rule_id"] == "mrp_analysis_context_v1"
    assert context["analysis_date"] == date.today().isoformat()


def test_mrp_complete_zero_shortage_without_messages_is_normal() -> None:
    result = _evaluate_mrp(elements=[_element()])

    output = result["workflow_output"]
    assert result["rule_id"] == "mrp_exception_analysis_deterministic_v2"
    assert output["shortage_status"] == "none"
    assert output["priority_level"] == "none"
    assert output["business_status"] == "normal"
    assert output["exception_count"] == 0
    assert output["evidence_complete"] is True


def test_mrp_active_shortage_is_critical() -> None:
    result = _evaluate_mrp(
        coverage=[
            _coverage(
                MaterialShortageQuantity="188.000",
                MaterialShortageStartDate="2026-08-20",
                MaterialShortageEndDate="2026-09-20",
                DaysOfSupplyDuration=-3,
            )
        ]
    )

    output = result["workflow_output"]
    assert output["shortage_status"] == "active"
    assert output["shortage_quantity"] == "188.000"
    assert output["priority_level"] == "critical"
    assert output["business_status"] == "critical"


@pytest.mark.parametrize(
    ("shortage_start", "lead_end", "expected_status", "expected_priority"),
    [
        ("2026-09-10", "2026-09-30", "imminent", "high"),
        ("2026-12-01", "2026-09-30", "future", "medium"),
    ],
)
def test_mrp_future_shortage_uses_replenishment_window(
    shortage_start: str,
    lead_end: str,
    expected_status: str,
    expected_priority: str,
) -> None:
    result = _evaluate_mrp(
        coverage=[
            _coverage(
                MaterialShortageQuantity="25.000",
                MaterialShortageStartDate=shortage_start,
                MaterialReplnmtLeadDurnEndDate=lead_end,
            )
        ]
    )
    assert result["workflow_output"]["shortage_status"] == expected_status
    assert result["workflow_output"]["priority_level"] == expected_priority


@pytest.mark.parametrize(
    ("number", "expected_type", "expected_priority", "expected_rescheduling"),
    [
        ("06", "start_date_past", "high", "none"),
        ("07", "finish_date_past", "high", "none"),
        ("10", "reschedule_in", "high", "bring_forward"),
        ("15", "reschedule_out", "medium", "postpone"),
        ("20", "cancel", "medium", "cancel_or_reduce"),
        ("26", "reduce", "medium", "cancel_or_reduce"),
        ("30", "schedule_adjusted", "low", "none"),
        ("99", "other_exception", "medium", "none"),
    ],
)
def test_mrp_exception_messages_have_deterministic_business_priority(
    number: str,
    expected_type: str,
    expected_priority: str,
    expected_rescheduling: str,
) -> None:
    result = _evaluate_mrp(
        elements=[
            _element(
                ExceptionMessageNumber=number,
                ExceptionMessageText=f"SAP message {number}",
            )
        ]
    )
    output = result["workflow_output"]
    detail = output["exception_details"][0]
    assert detail["exception_type"] == expected_type
    assert detail["priority_level"] == expected_priority
    assert output["priority_level"] == expected_priority
    assert output["rescheduling_status"] == expected_rescheduling
    assert output["business_status"] == "attention"


def test_mrp_preserves_both_exception_slots_and_highest_priority() -> None:
    result = _evaluate_mrp(
        elements=[
            _element(
                ExceptionMessageNumber="15",
                ExceptionMessageText="推迟处理",
                ExceptionMessageNumber2="07",
                ExceptionMessageText2="完成日期在过去",
            )
        ]
    )
    output = result["workflow_output"]
    assert output["exception_count"] == 2
    assert {item["exception_type"] for item in output["exception_details"]} == {
        "reschedule_out",
        "finish_date_past",
    }
    assert output["priority_level"] == "high"
    assert output["rescheduling_status"] == "postpone"


def test_mrp_incomplete_supply_demand_preserves_confirmed_shortage_risk() -> None:
    result = _evaluate_mrp(
        coverage=[
            _coverage(
                MaterialShortageQuantity="188.000",
                MaterialShortageStartDate="2026-08-20",
                DaysOfSupplyDuration=-3,
            )
        ],
        elements=[_element(ExceptionMessageNumber="10")],
        elements_complete=False,
    )
    output = result["workflow_output"]
    assert output["shortage_status"] == "active"
    assert output["priority_level"] == "critical"
    assert output["business_status"] == "inconclusive"
    assert output["source_complete"] is False
    assert output["evidence_complete"] is False
    assert "mrp_supply_demand_evidence" in result["missing_evidence"]


@pytest.mark.parametrize(
    ("coverage_rows", "elements", "expected_gap"),
    [
        ([], [], "mrp_coverage_scope_evidence"),
        (
            [
                _coverage(MaterialShortageQuantity="0.000"),
                _coverage(MaterialShortageQuantity="5.000", MaterialShortageStartDate="2026-09-01"),
            ],
            [],
            "mrp_coverage_conflict",
        ),
        (
            [_coverage()],
            [_element(MaterialBaseUnit="KG")],
            "mrp_supply_demand_unit_conflict",
        ),
    ],
)
def test_mrp_empty_conflicting_or_unit_mismatched_evidence_is_inconclusive(
    coverage_rows: list[dict[str, object]],
    elements: list[dict[str, object]],
    expected_gap: str,
) -> None:
    result = _evaluate_mrp(coverage=coverage_rows, elements=elements)
    assert result["workflow_output"]["business_status"] == "inconclusive"
    assert result["workflow_output"]["evidence_complete"] is False
    assert expected_gap in result["missing_evidence"]


def test_mrp_piece_unit_aliases_do_not_create_false_conflict() -> None:
    result = _evaluate_mrp(
        master=[_master(BaseUnit="PC")],
        coverage=[_coverage(MaterialBaseUnit="ST")],
        elements=[_element(MaterialBaseUnit="EA")],
    )

    assert result["workflow_output"]["business_status"] == "normal"
    assert result["workflow_output"]["evidence_complete"] is True


def test_mrp_accepted_shortage_and_time_horizon_are_disclosed_without_faking_incompleteness() -> None:
    result = _evaluate_mrp(
        coverage=[_coverage(HasAcceptedShortage="X", TimeHorizonInDays=100)]
    )
    assert result["workflow_output"]["business_status"] == "normal"
    assert result["workflow_output"]["evidence_complete"] is True
    assert "accepted_shortage_not_returned_as_first" in result["business_report"]["limitations"]
    assert "sap_shortage_time_horizon_applies" in result["business_report"]["limitations"]


def test_mrp_does_not_parse_rescheduling_date_from_localized_message_text() -> None:
    result = _evaluate_mrp(
        elements=[
            _element(
                ExceptionMessageNumber="10",
                ExceptionMessageText="重新计划（内） (22.02.24)",
                MRPElementReschedulingDate=None,
            )
        ]
    )
    assert result["workflow_output"]["exception_details"][0]["rescheduling_date"] is None


def test_mrp_business_report_is_bilingual_and_labels_business_priority() -> None:
    result = _evaluate_mrp(elements=[_element(ExceptionMessageNumber="10")])
    report = result["business_report"]
    assert report["headline"]["zh"]
    assert report["headline"]["en"]
    assert "SAPBusinessAgents" in report["overview"]["zh"]
    tables = {item["id"]: item for item in report["evidence_tables"]}
    columns = tables["mrp_exception_details"]["columns"]
    priority_column = next(item for item in columns if item["key"] == "priority_label")
    assert priority_column["label"] == {
        "zh": "业务处理优先级",
        "en": "Business priority",
    }


def test_mrp_manifest_v2_pins_get_only_evidence_and_public_output_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "agents" / "PP" / "mrp-exception-analysis" / "agent.json").read_text(
            encoding="utf-8"
        )
    )
    steps = {item["id"]: item for item in manifest["execution"]["steps"]}
    assert manifest["version"] == "0.2.0"
    assert steps["resolve_analysis_context"]["operation"] == "resolve_mrp_analysis_context"
    for step in steps.values():
        if step["executor"] == "sap_read":
            assert step["request"]["plan"]["http_method"] == "GET"
            assert step["readOnly"] is True

    master = steps["collect_mrp_master"]["request"]["plan"]
    assert {item["field"] for item in master["filters"]} == {
        "Material",
        "MRPArea",
        "MRPPlant",
    }
    assert {
        "MaterialProcurementCategory",
        "PlanningTimeFenceInDays",
        "TotalReplenishmentLeadDuration",
        "BaseUnit",
    } <= set(master["select_fields"])

    coverage = steps["collect_mrp_coverage"]["request"]["plan"]
    assert {
        "MaterialShortageQuantity",
        "MaterialShortageStartDate",
        "MaterialReplnmtLeadDurnEndDate",
        "TimeHorizonInDays",
        "HasAcceptedShortage",
        "MaterialBaseUnit",
    } <= set(coverage["select_fields"])

    elements = steps["collect_mrp_elements"]["request"]["plan"]
    assert {
        "MRPElementItem",
        "MRPElementScheduleLine",
        "MRPElementCategoryShortName",
        "MRPElementReschedulingDate",
        "MRPElementQuantityIsFirm",
        "MRPElementIsReleased",
    } <= set(elements["select_fields"])

    output_properties = manifest["execution"]["outputSchema"]["properties"]
    assert {
        "analysis_date",
        "shortage_status",
        "shortage_quantity",
        "priority_level",
        "rescheduling_status",
        "evidence_complete",
        "exception_details",
    } <= set(output_properties)
    assert "sap_message_priority" not in output_properties


def test_capacity_schedule_counts_only_operations_inside_requested_period() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "production-scheduling-capacity",
            "run_input": {"date_from": "2017-10-01", "date_to": "2017-10-31"},
            "evidence": {
                "execution": {
                    "source_complete": True,
                    "step_results": {
                        "production_operations": {
                            "results": [
                                {"OpActualExecutionStartDate": "2017-10-23T00:00:00Z"},
                                {"OpActualExecutionStartDate": "2018-01-01T00:00:00Z"},
                                {"OpActualExecutionStartDate": None},
                            ]
                        },
                        "planned_orders": {"results": []},
                        "planned_capacities": {"results": []},
                        "work_centers": {"results": [{"WorkCenterInternalID": "1"}]},
                        "work_center_capacities": {"results": []},
                        "capacity_buckets": {"results": []},
                    },
                }
            },
        }
    )

    metrics = {item["id"]: item["value"] for item in result["metrics"]}
    assert metrics["operation_rows"] == 1
    assert result["business_status"] == "capability_blocked"


def _demand_forecast_evidence(*, include_manual: bool = False) -> dict[str, object]:
    evidence: dict[str, object] = {
        "pir": {
            "ok": True,
            "source_complete": True,
            "data": {
                "step_results": {
                    "pir_headers": {
                        "source_complete": True,
                        "results": [
                            {
                                "Product": "FG-TEST",
                                "Plant": "1710",
                                "MRPArea": "1710",
                                "PlndIndepRqmtType": "VSF",
                                "PlndIndepRqmtVersion": "00",
                                "RequirementPlan": "",
                                "RequirementSegment": "",
                                "PlndIndepRqmtIsActive": "X",
                            }
                        ],
                    },
                    "pir_items": {
                        "source_complete": True,
                        "results": [
                            {
                                "Product": "FG-TEST",
                                "Plant": "1710",
                                "MRPArea": "1710",
                                "PlndIndepRqmtType": "VSF",
                                "PlndIndepRqmtVersion": "00",
                                "RequirementPlan": "",
                                "RequirementSegment": "",
                                "PlndIndepRqmtPeriod": "202609",
                                "PeriodType": "M",
                                "PlndIndepRqmtPeriodStartDate": "2026-09-01",
                                "PlannedQuantity": "100",
                                "WithdrawalQuantity": "0",
                                "UnitOfMeasure": "PC",
                            }
                        ],
                    },
                }
            },
        },
        "sales_demand": {
            "ok": True,
            "source_complete": True,
            "data": {
                "step_results": {
                    "sales_order_items": {
                        "source_complete": True,
                        "results": [
                            {
                                "SalesOrder": "5814",
                                "SalesOrderItem": "10",
                                "Material": "FG-TEST",
                                "ProductionPlant": "1710",
                                "RequestedQuantityUnit": "PC",
                                "SalesDocumentRjcnReason": "",
                            }
                        ],
                    },
                    "sales_schedule_lines": {
                        "source_complete": True,
                        "results": [
                            {
                                "SalesOrder": "5814",
                                "SalesOrderItem": "10",
                                "ScheduleLine": "1",
                                "RequestedDeliveryDate": "2026-09-10",
                                "ScheduleLineOrderQuantity": "100",
                                "OrderQuantityUnit": "PC",
                            }
                        ],
                    },
                }
            },
        },
        "planned_orders": {
            "ok": True,
            "source_complete": True,
            "data": {
                "results": [
                    {
                        "PlannedOrder": "1001",
                        "Material": "FG-TEST",
                        "ProductionPlant": "1710",
                        "TotalQuantity": "25",
                        "BaseUnit": "PC",
                        "PlndOrderPlannedStartDate": "2026-09-12",
                        "PlannedOrderIsFirm": False,
                    }
                ]
            },
        },
        "mrp_material": {
            "ok": True,
            "source_complete": True,
            "data": {
                "results": [
                    {
                        "Material": "FG-TEST",
                        "MRPPlant": "1710",
                        "MRPArea": "1710",
                        "MaterialBaseUnit": "PC",
                    }
                ]
            },
        },
        "supply_demand": {
            "ok": True,
            "source_complete": True,
            "data": {
                "results": [
                    {
                        "Material": "FG-TEST",
                        "MRPPlant": "1710",
                        "MRPArea": "1710",
                        "MRPPlanningSegment": "",
                        "MRPPlanningSegmentType": "",
                        "MRPElement": "REQ-1",
                        "MRPElementItem": "10",
                        "MRPElementScheduleLine": "1",
                        "MRPElementAvailyOrRqmtDate": "2026-09-10",
                        "MRPElementOpenQuantity": "-50000",
                        "MRPAvailableQuantity": "150000",
                        "DemandCategoryGroup": "01",
                        "ReceiptCategoryGroup": "",
                        "MaterialBaseUnit": "PC",
                    },
                    {
                        "Material": "FG-TEST",
                        "MRPPlant": "1710",
                        "MRPArea": "1710",
                        "MRPPlanningSegment": "",
                        "MRPPlanningSegmentType": "",
                        "MRPElement": "REC-1",
                        "MRPElementItem": "10",
                        "MRPElementScheduleLine": "1",
                        "MRPElementAvailyOrRqmtDate": "2026-09-15",
                        "MRPElementOpenQuantity": "30000",
                        "MRPAvailableQuantity": "120000",
                        "DemandCategoryGroup": "",
                        "ReceiptCategoryGroup": "01",
                        "MaterialBaseUnit": "PC",
                    },
                    {
                        "Material": "FG-TEST",
                        "MRPPlant": "1710",
                        "MRPArea": "1710",
                        "MRPPlanningSegment": "",
                        "MRPPlanningSegmentType": "",
                        "MRPElement": "REQ-2",
                        "MRPElementItem": "10",
                        "MRPElementScheduleLine": "1",
                        "MRPElementAvailyOrRqmtDate": "2026-09-20",
                        "MRPElementOpenQuantity": "-30000",
                        "MRPAvailableQuantity": "90000",
                        "DemandCategoryGroup": "01",
                        "ReceiptCategoryGroup": "",
                        "MaterialBaseUnit": "PC",
                    },
                ]
            },
        },
    }
    if include_manual:
        evidence["current_stock"] = {
            "ok": True,
            "source_complete": True,
            "data": {
                "results": [
                    {
                        "Material": "FG-TEST",
                        "Plant": "1710",
                        "InventoryStockType": "01",
                        "InventorySpecialStockType": "",
                        "MatlWrhsStkQtyInMatlBaseUnit": "200000",
                        "MaterialBaseUnit": "PC",
                    }
                ]
            },
        }
    return evidence


def test_demand_forecast_context_validates_multi_material_scope() -> None:
    context = resolve_demand_forecast_context(
        {
            "run_input": {
                "plant": "1710",
                "materials": [" fg-test ", "sg21"],
                "date_from": "2026-09-01",
                "date_to": "2026-09-30",
            }
        }
    )
    assert context["materials"] == ["FG-TEST", "SG21"]
    assert context["mrp_area"] == "1710"
    assert context["deviation_threshold_percent"] == "20"

    with pytest.raises(ValueError, match="duplicates"):
        resolve_demand_forecast_context(
            {
                "run_input": {
                    "plant": "1710",
                    "materials": ["tg10", " TG10 "],
                    "date_from": "2026-09-01",
                    "date_to": "2026-09-30",
                }
            }
        )


def test_demand_forecast_batch_uses_native_pir_period_and_prioritizes_pir() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "demand-forecast-planning",
            "run_input": {
                "materials": ["FG-TEST"],
                "plant": "1710",
                "date_from": "2026-09-01",
                "date_to": "2026-09-30",
            },
            "analysis_context": {
                "analysis_date": "2026-09-01",
                "materials": ["FG-TEST"],
                "date_from": "2026-09-01",
                "date_to": "2026-09-30",
                "pir_version": "00",
                "mrp_area": "1710",
                "deviation_threshold_percent": "20",
            },
            "evidence": _demand_forecast_evidence(),
        }
    )

    output = result["workflow_output"]
    assert output["requested_material_count"] == 1
    assert output["processed_material_count"] == 1
    material = output["material_results"][0]
    assert material["forecast_status"] == "within_tolerance"
    assert material["sales_demand_quantity"] == "100"
    assert material["pir_quantity"] == "100"
    assert material["planned_order_quantity"] == "25"
    assert material["period_results"][0]["material"] == "FG-TEST"
    assert [item["subject"] for item in material["recommendations"]] == ["pir", "planned_order"]
    assert output["source_complete"] is True
    assert output["evidence_complete"] is True
    assert output["business_status"] == "normal"


def test_demand_forecast_reports_no_activity_when_sales_and_pir_are_both_zero() -> None:
    evidence = json.loads(json.dumps(_demand_forecast_evidence()))
    evidence["pir"]["data"]["step_results"]["pir_headers"]["results"] = []
    evidence["pir"]["data"]["step_results"]["pir_items"]["results"] = []
    evidence["sales_demand"]["data"]["step_results"]["sales_order_items"]["results"] = []
    evidence["sales_demand"]["data"]["step_results"]["sales_schedule_lines"]["results"] = []

    result = evaluate_business_agent(
        {
            "agent_id": "demand-forecast-planning",
            "run_input": {
                "materials": ["FG-TEST"],
                "plant": "1710",
                "date_from": "2026-09-01",
                "date_to": "2026-09-30",
            },
            "analysis_context": {
                "analysis_date": "2026-09-01",
                "materials": ["FG-TEST"],
                "date_from": "2026-09-01",
                "date_to": "2026-09-30",
                "pir_version": "00",
                "mrp_area": "1710",
                "deviation_threshold_percent": "20",
            },
            "evidence": evidence,
        }
    )

    material = result["workflow_output"]["material_results"][0]
    assert material["sales_demand_quantity"] == "0"
    assert material["pir_quantity"] == "0"
    assert material["forecast_status"] == "no_activity"
    assert material["business_status"] == "normal"


def test_new_sales_demand_uses_sap_cumulative_balance_without_adding_stock() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "new-sales-demand-coverage",
            "run_input": {
                "plant": "1710",
                "demand_items": [{"material": "FG-TEST", "quantity": 100000, "demand_date": "2026-09-15"}],
            },
            "analysis_context": {
                "analysis_date": "2026-09-01",
                "materials": ["FG-TEST"],
                "mrp_area": "1710",
                "demand_items": [{"material": "FG-TEST", "quantity": "100000", "demand_date": "2026-09-15", "unit": None, "horizon_end_date": "2026-09-30"}],
            },
            "evidence": _demand_forecast_evidence(include_manual=True),
        }
    )

    output = result["workflow_output"]["material_results"][0]
    assert output["current_unrestricted_stock"] == "200000"
    assert output["projected_available_before_demand"] == "120000"
    assert output["projected_available_after_demand"] == "20000"
    assert output["existing_demand_before_request"] == "50000"
    assert output["future_receipts_before_request"] == "30000"
    assert output["demand_coverage_status"] == "covered"
    assert output["horizon_impact_status"] == "creates_shortage"
    assert output["lowest_simulated_available_quantity"] == "-10000"
    assert output["first_simulated_shortage_date"] == "2026-09-20"
    assert output["atp_status"] == "not_assessed"
    assert output["business_status"] == "attention"
    assert output["evidence_complete"] is True


def test_new_sales_demand_context_and_unit_conflict() -> None:
    context = resolve_new_sales_demand_context(
        {
            "run_input": {
                "plant": "1710",
                "horizon_days": 30,
                "demand_items": [{"material": " fg-test ", "quantity": "100000.000", "demand_date": "2026-09-15", "unit": "pc"}],
            }
        }
    )
    assert context["materials"] == ["FG-TEST"]
    assert context["demand_items"][0]["unit"] == "PC"

    result = evaluate_business_agent(
        {
            "agent_id": "new-sales-demand-coverage",
            "run_input": {
                "plant": "1710",
                "demand_items": [{"material": "FG-TEST", "quantity": 100000, "demand_date": "2026-09-15", "unit": "KG"}],
            },
            "analysis_context": {
                "analysis_date": "2026-09-01",
                "materials": ["FG-TEST"],
                "mrp_area": "1710",
                "demand_items": [{"material": "FG-TEST", "quantity": "100000", "demand_date": "2026-09-15", "unit": "KG", "horizon_end_date": "2026-09-30"}],
            },
            "evidence": _demand_forecast_evidence(include_manual=True),
        }
    )

    output = result["workflow_output"]["material_results"][0]
    assert output["demand_coverage_status"] == "unknown"
    assert output["business_status"] == "inconclusive"
    assert "manual_demand_unit_not_comparable" in output["evidence_gaps"]


def test_demand_forecast_batch_preserves_complete_material_when_another_chunk_fails() -> None:
    evidence = _demand_forecast_evidence()
    for topic in evidence.values():
        assert isinstance(topic, dict)
        topic["chunk_results"] = [
            {"filter_values": ["FG-TEST"], "source_complete": True, "source_truncated": False, "error_code": None},
            {"filter_values": ["SG21"], "source_complete": False, "source_truncated": False, "error_code": "sap_http_error"},
        ]
        topic["failed_filter_values"] = ["SG21"]
        topic["source_complete"] = False

    result = evaluate_business_agent(
        {
            "agent_id": "demand-forecast-planning",
            "run_input": {"plant": "1710", "materials": ["FG-TEST", "SG21"], "date_from": "2026-09-01", "date_to": "2026-09-30"},
            "analysis_context": {"analysis_date": "2026-09-01", "materials": ["FG-TEST", "SG21"], "date_from": "2026-09-01", "date_to": "2026-09-30", "pir_version": "00", "mrp_area": "1710", "deviation_threshold_percent": "20"},
            "evidence": evidence,
        }
    )

    output = result["workflow_output"]
    by_material = {item["material"]: item for item in output["material_results"]}
    assert by_material["FG-TEST"]["source_complete"] is True
    assert by_material["SG21"]["source_complete"] is False
    assert by_material["SG21"]["business_status"] == "inconclusive"
    assert output["processed_material_count"] == 2
    assert output["inconclusive_material_count"] == 1
    assert output["business_status"] == "inconclusive"
