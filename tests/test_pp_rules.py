from __future__ import annotations

from sap_business_agents_platform.agent_rules import evaluate_business_agent


def test_mrp_exception_accepts_complete_direct_supply_demand_topic() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "mrp-exception-analysis",
            "run_input": {},
            "evidence": {
                "material_coverages": {
                    "ok": True,
                    "source_complete": True,
                    "data": {"results": [{"Material": "SG21"}]},
                },
                "supply_demand_items": {
                    "ok": True,
                    "source_complete": True,
                    "data": {
                        "results": [
                            {
                                "Material": "SG21",
                                "MRPPlant": "1010",
                                "MRPElement": "312",
                                "MRPElementCategory": "PA",
                                "MRPElementOpenQuantity": "114",
                            }
                        ]
                    },
                },
            },
        }
    )

    metrics = {item["id"]: item["value"] for item in result["metrics"]}
    assert metrics["supply_demand_rows"] == 1
    assert result["missing_evidence"] == []
    assert result["business_status"] == "attention"


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


def test_demand_forecast_suppresses_unattributable_period_totals() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "demand-forecast-planning",
            "run_input": {
                "material": "MZ-TG-Y120",
                "plant": "1710",
                "date_from": "2017-01-01",
                "date_to": "2017-12-31",
            },
            "evidence": {
                "sales_demand": {
                    "ok": True,
                    "source_complete": True,
                    "data": {
                        "results": [
                            {
                                "Material": "MZ-TG-Y120",
                                "ProductionPlant": "1710",
                                "RequestedQuantity": "10",
                                "RequestedQuantityUnit": "PC",
                            }
                        ]
                    },
                },
                "planned_orders": {
                    "ok": True,
                    "source_complete": True,
                    "data": {"results": []},
                },
            },
        }
    )

    metrics = {item["id"]: item["value"] for item in result["metrics"]}
    assert metrics == {"demand_rows": None, "planned_order_rows": 0}
    assert result["business_report"]["records"][0]["business_status"] == "capability_blocked"
    assert "sales_demand_period_evidence" in result["business_report"]["limitations"]
