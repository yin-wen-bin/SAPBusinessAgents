from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform.agent_rules import evaluate_business_agent


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
            "evidence": {
                "actual": evidence([{"AmountInCompanyCodeCurrency": "-10", "CompanyCodeCurrency": "USD"}]),
                "plan": evidence([]),
                "master": evidence([]),
                "budget": evidence([]),
                "commitment": evidence([]),
            },
        }
    )

    assert set(report["business_report"]["missing_evidence"]) >= {
        "master_evidence",
        "plan_evidence",
        "budget_evidence",
        "commitment_evidence",
        "control_object_not_found",
    }
