from __future__ import annotations

import runpy
from pathlib import Path

from sap_business_agents_platform.agent_lifecycle import AgentLifecycleService
from sap_business_agents_platform.authoring_checks import candidate_business_output_issues, candidate_input_issues
from sap_business_agents_platform.managed_rules import source_digest, validate_managed_rule
from sap_business_agents_platform.manifests import validate_manifest


ROOT = Path(__file__).resolve().parents[1]
BUILD = runpy.run_path(str(ROOT / "tests" / "fixtures" / "mm_po_gr_status_revision.py"))["build_manifest"]
RULE = (ROOT / "tests" / "fixtures" / "managed_rules" / "mm_po_gr_status.py").read_text(encoding="utf-8")


def test_revision_manifest_is_a_valid_business_ready_get_only_agent():
    base = AgentLifecycleService._blank_package("mm-po-gr-status", "Common", {"zh": "草稿", "en": "Draft"})["manifest"]
    manifest = BUILD(base, source_digest(RULE))
    validate_manifest(manifest, "mm-po-gr-status-r5")
    assert candidate_input_issues(manifest) == []
    assert candidate_business_output_issues(manifest) == []
    assert validate_managed_rule(RULE, expected_digest=manifest["managedRule"]["sha256"])["ok"] is True
    assert manifest["execution"]["relationshipPolicy"] == "advisory"
    assert [step["id"] for step in manifest["execution"]["steps"]] == ["collect_po_receipt_evidence", "evaluate_receipt_status"]
    assert [step["step_id"] for step in manifest["execution"]["steps"][0]["request"]["plan"]["steps"]] == [
        "target_schedule_lines", "purchase_order_items", "purchase_order_headers", "all_schedule_lines", "material_document_items", "material_document_headers"
    ]
    assert manifest["execution"]["inputSchema"]["required"] == ["schedule_line_delivery_date"]
    assert manifest["execution"]["outputMapping"]["schedule_line_count"] == (
        "{{steps.evaluate_receipt_status.output.workflow_output.schedule_line_count}}"
    )
    status_schema = manifest["execution"]["outputSchema"]["properties"]["records"]["items"]["properties"]["receipt_status"]
    assert status_schema["x-sapba-display"]["labels"]["not_received"]["zh"] == "未入库"
    optional = manifest["execution"]["steps"][0]["request"]["plan"]["steps"][2]["filters"][0]
    assert optional["value"] == "{{input.purchasing_organization}}"
    assert optional["omitIfEmpty"] == "{{input.purchasing_organization?}}"
