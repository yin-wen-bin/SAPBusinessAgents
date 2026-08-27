from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.rules import (
    prepare_control_object_lookup,
    resolve_control_object_master,
)


ROOT = Path(__file__).resolve().parents[1]
CO_ROOT = ROOT / "agents" / "CO"
AGENTS = {
    "cost-center-expense-anomaly",
    "co-month-end-allocation-settlement",
    "product-cost-variance",
    "budget-rolling-forecast",
    "internal-order-project-control",
}


def _manifests() -> list[dict[str, object]]:
    return [
        json.loads((CO_ROOT / slug / "agent.json").read_text(encoding="utf-8"))
        for slug in sorted(AGENTS)
    ]


def test_five_schema_v2_co_agents_use_only_embedded_adt_and_rules() -> None:
    manifests = _manifests()
    assert {str(item["slug"]) for item in manifests} == AGENTS
    for manifest in manifests:
        assert manifest["schemaVersion"] == 2
        assert manifest["module"] == "CO"
        steps = manifest["execution"]["steps"]
        assert {step["executor"] for step in steps} <= {"sap_read", "skill", "rule"}
        assert all(step.get("readOnly") is True for step in steps if step["executor"] in {"sap_read", "skill"})
        assert all(
            step["request"]["plan"]["http_method"] == "GET"
            for step in steps
            if step["executor"] == "sap_read"
        )
        approved_co_skills = {
            "sap-adt-table-export",
            "sap-production-order-cost-analysis",
            "sap-wbs-object-resolver",
            "sap-control-object-commitment-evidence",
        }
        assert all(
            step["skillId"] in approved_co_skills
            and step.get("when")
            and "connection_profile" not in step.get("inputMapping", {})
            for step in steps
            if step["executor"] == "skill"
        )


def test_co_agents_have_execution_mapped_bilingual_workflow_steps_and_reports() -> None:
    for manifest in _manifests():
        execution_ids = {step["id"] for step in manifest["execution"]["steps"]}
        mapped_ids = [
            step_id
            for step in manifest["workflow"]
            for step_id in step["executionStepIds"]
        ]
        assert set(mapped_ids) == execution_ids
        assert len(mapped_ids) == len(set(mapped_ids))
        for step in manifest["workflow"]:
            assert step["title"]["zh"] and step["title"]["en"]
            assert step["description"]["zh"] and step["description"]["en"]
        report = CO_ROOT / str(manifest["slug"]) / str(manifest["validation"]["reportPath"])
        assert report.is_file()


def test_co_manifests_use_only_embedded_reads_and_no_gui_executors() -> None:
    for manifest in _manifests():
        serialized = json.dumps(manifest["execution"], ensure_ascii=False).lower()
        assert all(step["executor"] != "sap_read" or step.get("readOnly") is True for step in manifest["execution"]["steps"])
        assert "se16n" not in serialized


def test_control_object_skills_are_registered_with_truthful_validation_state() -> None:
    registry = json.loads((ROOT / "config" / "skills.json").read_text(encoding="utf-8"))
    skills = {item["skill_id"]: item for item in registry["skills"]}
    assert skills["sap-wbs-object-resolver"]["validated"] is True
    assert skills["sap-control-object-commitment-evidence"]["validated"] is False

    manifest = json.loads(
        (CO_ROOT / "internal-order-project-control" / "agent.json").read_text(encoding="utf-8")
    )
    steps = {step["id"]: step for step in manifest["execution"]["steps"]}
    assert steps["wbs_object_resolver"]["skillId"] == "sap-wbs-object-resolver"
    assert steps["commitment_evidence"]["skillId"] == "sap-control-object-commitment-evidence"
    serialized = json.dumps(manifest["execution"], ensure_ascii=False)
    assert '"object": "COOI"' not in serialized
    assert '"object": "PRPS"' not in serialized


def test_internal_order_empty_optional_sources_remain_explicit_business_gaps() -> None:
    def evidence(results: list[dict[str, object]]) -> dict[str, object]:
        return {"ok": True, "source_complete": True, "data": {"results": results}}

    report = evaluate_business_agent(
        {
            "agent_id": "internal-order-project-control",
            "run_input": {
                "company_code": "1710",
                "object_type": "internal_order",
                "object_id": "100001",
                "fiscal_year": "2018",
            },
            "resolved_object": {
                "status": "inconclusive",
                "ready": False,
                "source_complete": True,
                "object_type": "INTERNAL_ORDER",
                "issues": ["control_object_not_found"],
            },
            "evidence": {
                "order_actual": evidence([{"AmountInCompanyCodeCurrency": "-10", "CompanyCodeCurrency": "USD"}]),
                "order_plan": evidence([]),
            },
            "fallbacks": {"budget": {}, "commitment": {}},
        }
    )

    assert set(report["business_report"]["missing_evidence"]) >= {
        "master_evidence",
        "plan_evidence",
        "budget_evidence",
        "commitment_evidence",
        "control_object_not_found",
    }


def _adt_master(rows: list[dict[str, object]], *, complete: bool = True) -> dict[str, object]:
    return {
        "status": "complete" if complete else "partial",
        "read_only": True,
        "validated": True,
        "source_complete": complete,
        "completeness": {"source_complete": complete, "paging_complete": complete},
        "validation_issues": [],
        "rows": rows,
    }


def test_internal_order_lookup_applies_alpha_and_requires_true_internal_order() -> None:
    lookup = prepare_control_object_lookup(
        {
            "object_type": " internal_order ",
            "object_id": " 600468 ",
            "planning_category": " plan ",
        }
    )
    assert lookup["lookup_id"] == "000000600468"
    assert lookup["planning_category"] == "PLAN"
    assert lookup["has_planning_category"] is True

    resolved = resolve_control_object_master(
        {
            "lookup": lookup,
            "company_code": "1310",
            "order_master": _adt_master(
                [
                    {
                        "AUFNR": "000000600468",
                        "OBJNR": "OR000000600468",
                        "KOKRS": "1300",
                        "BUKRS": "1310",
                        "AUTYP": "01",
                        "AUART": "Z601",
                    }
                ]
            ),
        }
    )
    assert resolved["ready"] is True
    assert resolved["external_id"] == "600468"
    assert resolved["internal_id"] == "000000600468"
    assert resolved["object_number"] == "OR000000600468"
    assert resolved["can_read_order_plan"] is True
    assert resolved["can_discover_order_plan"] is False

    production_order = resolve_control_object_master(
        {
            "lookup": lookup,
            "company_code": "1310",
            "order_master": _adt_master(
                [
                    {
                        "AUFNR": "000000600468",
                        "OBJNR": "OR000000600468",
                        "KOKRS": "1300",
                        "BUKRS": "1310",
                        "AUTYP": "10",
                    }
                ]
            ),
        }
    )
    assert production_order["ready"] is False
    assert "object_type_mismatch" in production_order["issues"]


def test_wbs_resolver_preserves_external_id_and_proves_internal_relationship() -> None:
    lookup = prepare_control_object_lookup({"object_type": "WBS", "object_id": " p-100.1 "})
    resolved = resolve_control_object_master(
        {
            "lookup": lookup,
            "company_code": "1310",
            "wbs_resolver": {
                "status": "complete",
                "resolution_status": "resolved",
                "read_only": True,
                "validated": True,
                "completeness": {
                    "source_complete": True,
                    "paging_complete": True,
                    "evidence_complete": True,
                },
                "resolved_object": {
                    "object_type": "WBS",
                    "external_id": "p-100.1",
                    "internal_id": "00000123",
                    "object_number": "PR000000000123",
                    "company_code": "1310",
                    "controlling_area": "1300",
                    "project_internal_id": "00000042",
                    "project_external_id": "p-100",
                },
                "validation_issues": [],
            },
        }
    )
    assert resolved["ready"] is True
    assert resolved["external_id"] == "p-100.1"
    assert resolved["internal_id"] == "00000123"
    assert resolved["object_number"] == "PR000000000123"
    assert resolved["project_internal_id"] == "00000042"

    spaced = prepare_control_object_lookup({"object_type": "WBS", "object_id": " DEMO1   WBS2000 "})
    assert spaced["lookup_id"] == "DEMO1   WBS2000"


def test_internal_order_plan_category_is_filtered_or_discovered_server_side() -> None:
    manifest = json.loads(
        (CO_ROOT / "internal-order-project-control" / "agent.json").read_text(encoding="utf-8")
    )
    steps = {step["id"]: step for step in manifest["execution"]["steps"]}

    requested = steps["read_order_plan"]
    requested_filters = requested["request"]["plan"]["filters"]
    assert requested["when"]["source"] == "{{steps.object_numbers.output.can_read_order_plan}}"
    assert any(
        item["field"] == "PlanningCategory"
        and item["value"] == "{{steps.object_numbers.output.planning_category}}"
        for item in requested_filters
    )

    discovery = steps["discover_order_plan"]
    discovery_fields = {item["field"] for item in discovery["request"]["plan"]["filters"]}
    assert discovery["when"]["source"] == "{{steps.object_numbers.output.can_discover_order_plan}}"
    assert "PlanningCategory" not in discovery_fields
