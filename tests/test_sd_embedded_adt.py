from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.rules import evaluate


REPOSITORY = Path(__file__).resolve().parents[1]
SD_ROOT = REPOSITORY / "agents" / "SD"


def _manifests() -> list[dict[str, object]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SD_ROOT.glob("*/agent.json"))
    ]


def _steps(manifest: dict[str, object]) -> list[dict[str, object]]:
    execution = manifest.get("execution")
    assert isinstance(execution, dict)
    steps = execution.get("steps")
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def test_all_eleven_sd_agents_use_schema_v2_embedded_reads() -> None:
    manifests = _manifests()
    assert len(manifests) == 11
    for manifest in manifests:
        assert manifest["schemaVersion"] == 2
        assert {step.get("executor") for step in _steps(manifest)} <= {
            "sap_read",
            "skill",
            "rule",
        }
        assert all(
            step.get("readOnly") is True
            for step in _steps(manifest)
            if step.get("executor") == "sap_read"
        )


def test_sd_acceptance_outputs_use_positive_provider_and_write_safety_evidence() -> None:
    generator = (REPOSITORY / "scripts" / "validate_sd_embedded_adt_live.py").read_text(
        encoding="utf-8"
    )
    assert '"write_operations": 0' in generator
    assert "provider_fallback_calls" not in generator
    assert "自动 Provider 回退调用数" not in generator

    reports = sorted(SD_ROOT.glob("*/docs/live-sap-test-report.md"))
    assert len(reports) == 10
    acceptance_outputs = [SD_ROOT / "EMBEDDED_ADT_LIVE_VALIDATION_SUMMARY.md", *reports]
    retired_provider_name = "SAP" + "Claw"
    for path in acceptance_outputs:
        text = path.read_text(encoding="utf-8")
        assert retired_provider_name not in text
        assert "自动 Provider 回退调用数" not in text
        assert "未执行任何SAP写操作" in text

    current_architecture_docs = [
        REPOSITORY / "README.md",
        REPOSITORY / "docs" / "codex-harness.md",
        REPOSITORY / "docs" / "codex-harness-live-test-report.md",
    ]
    for path in current_architecture_docs:
        assert retired_provider_name not in path.read_text(encoding="utf-8")


def test_sd_adt_steps_are_allowlisted_bounded_and_connection_agnostic() -> None:
    expected_objects = {
        "VBFA", "VBUV", "TVFST", "TVLST", "DD07T", "DD03T", "DD03L", "DD04T"
    }
    found: set[str] = set()
    row_limits: dict[str, set[int]] = {name: set() for name in expected_objects}
    for manifest in _manifests():
        for step in _steps(manifest):
            if step.get("executor") != "skill":
                continue
            assert step["skillId"] == "sap-adt-table-export"
            assert step["readOnly"] is True
            assert step["failurePolicy"] == "record_gap"
            payload = step["inputMapping"]
            assert isinstance(payload, dict)
            assert "connection_profile" not in payload
            assert payload["object"] in expected_objects
            assert payload["filters"]
            assert 1 <= int(payload["max_rows"]) <= 200
            assert "*" not in payload["fields"]
            found.add(str(payload["object"]))
            row_limits[str(payload["object"])].add(int(payload["max_rows"]))
    assert found == expected_objects
    assert all(limits == {2, 200} for limits in row_limits.values())


def _embedded_billing() -> dict[str, object]:
    return {
        "source_complete": True,
        "step_results": {
            "billing_headers": {
                "source_complete": True,
                "results": [{"BillingDocument": "redacted"}],
            }
        },
    }


def _adt(status: str, rows: list[dict[str, object]]) -> dict[str, object]:
    complete = status == "complete"
    return {
        "status": status,
        "read_only": True,
        "validated": True,
        "rows": rows,
        "completeness": {
            "source_complete": complete,
            "paging_complete": complete,
            "truncated": not complete,
        },
        "validation_issues": [],
    }


def test_output_gap_closes_only_for_complete_nonempty_verified_adt() -> None:
    base = {
        "agent_id": "billing-output-monitor",
        "run_input": {"billing_document": "redacted"},
        "evidence": {"billing": _embedded_billing()},
        "known_gaps": [],
    }
    complete = evaluate_business_agent(
        {**base, "fallbacks": {"output_status": _adt("complete", [{"VSTAT": "1"}])}}
    )
    assert complete["status"] == "complete"
    assert complete["missing_evidence"] == []

    for fallback in (_adt("partial", [{"VSTAT": "1"}]), _adt("complete", [])):
        result = evaluate_business_agent({**base, "fallbacks": {"output_status": fallback}})
        assert result["status"] == "inconclusive"
        assert "billing_output_status_evidence" in result["missing_evidence"]


def test_mrp_context_never_substitutes_for_released_atp() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "shortage-allocation-advisor",
            "run_input": {"material": "redacted", "plant": "redacted"},
            "evidence": {
                "demand": {
                    "source_complete": True,
                    "step_results": {"sales_order_items": {"source_complete": True, "results": []}},
                },
                "atp_availability": {
                    "ok": False,
                    "source_complete": False,
                    "error": {"code": "atp_unavailable"},
                },
            },
            "fallbacks": {"mrp_context": _adt("complete", [{"DTNUM": "redacted"}])},
            "known_gaps": [],
        }
    )
    assert result["status"] == "inconclusive"
    assert "atp_availability_evidence" in result["missing_evidence"]


def test_historical_shortage_does_not_treat_current_stock_as_key_date_stock() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "shortage-allocation-advisor",
            "run_input": {
                "material": "TG11",
                "plant": "1710",
                "date_from": "2017-10-06",
                "date_to": "2017-10-06",
            },
            "evidence": {
                "demand": {
                    "source_complete": True,
                    "step_results": {
                        "sales_order_items": {
                            "source_complete": True,
                            "results": [{"SalesOrder": "2", "SalesOrderItem": "10", "Material": "TG11", "ProductionPlant": "1710", "RequestedQuantityUnit": "PC"}],
                        },
                        "schedule_lines": {
                            "source_complete": True,
                            "results": [{"SalesOrder": "2", "SalesOrderItem": "10", "ScheduleLine": "1", "RequestedDeliveryDate": "2017-10-06", "ScheduleLineOrderQuantity": "10", "ConfdOrderQtyByMatlAvailCheck": "0"}],
                        },
                        "material_stock": {
                            "source_complete": True,
                            "results": [{"MatlWrhsStkQtyInMatlBaseUnit": "99"}],
                        },
                    },
                }
            },
        }
    )

    metrics = {item["id"]: item["value"] for item in result["metrics"]}
    assert metrics["stock"] is None
    assert metrics["uncovered"] == "10"
    assert result["source_complete"] is True
    assert result["business_status"] == "capability_blocked"


def test_adt_preflight_gates_formal_query_on_complete_verified_result() -> None:
    assert evaluate("assess_adt_preflight", {"payload": _adt("complete", [])})["proceed"] is True
    assert evaluate("assess_adt_preflight", {"payload": _adt("partial", [])})["proceed"] is False
    assert evaluate(
        "assess_adt_preflight",
        {"payload": {"status": "skipped", "source_complete": True, "required": False}},
    )["proceed"] is False
