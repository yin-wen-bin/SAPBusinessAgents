from __future__ import annotations

import json
from pathlib import Path


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
        assert all(
            step["skillId"] == "sap-adt-table-export"
            and step.get("when")
            and "connection_profile" not in step.get("inputMapping", {})
            for step in steps
            if step["executor"] == "skill"
        )


def test_co_agents_have_eight_bilingual_workflow_steps_and_reports() -> None:
    for manifest in _manifests():
        assert len(manifest["workflow"]) == 8
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
