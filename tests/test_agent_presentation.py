from __future__ import annotations

import copy
import json
from pathlib import Path

from sap_business_agents_platform.acceptance import agent_execution_digest
from sap_business_agents_platform.agent_presentation import (
    authoring_presentation_guidance,
    inspect_agent_presentation,
)


ROOT = Path(__file__).resolve().parents[1]


def _agent() -> dict:
    return json.loads((ROOT / "agents" / "Common" / "mm-po-gr-status" / "agent.json").read_text(encoding="utf-8"))


def test_python_bridge_returns_same_nested_scope_and_actionable_paths() -> None:
    projection = inspect_agent_presentation(_agent())
    assert projection["status"] == "needs_input"
    assert [item["entity"] for item in projection["odata_objects"]] == [
        "A_PurchaseOrderScheduleLine",
        "A_PurchaseOrderItem",
        "A_PurchaseOrder",
        "A_MaterialDocumentItem",
        "A_MaterialDocumentHeader",
    ]
    assert {item["path"] for item in projection["issues"]} >= {
        "owner", "sapModules", "tables[0]", "workflow[0].operations", "workflow[1].operations",
    }


def test_documentation_repair_keeps_execution_and_acceptance_digest() -> None:
    before = _agent()
    after = copy.deepcopy(before)
    after["owner"] = "MM-PUR / MM-IM"
    after["sapModules"] = ["MM-PUR", "MM-IM"]
    after["tables"] = []
    for step in after["workflow"]:
        step["operations"] = {
            "zh": ["核对输入、处理证据并输出结果。"],
            "en": ["Check inputs, process evidence and produce output."],
        }
    rules = (ROOT / "agents" / "Common" / "mm-po-gr-status" / "rules.py").read_text(encoding="utf-8")
    assert inspect_agent_presentation(after)["status"] == "ready"
    assert agent_execution_digest(after, rules) == agent_execution_digest(before, rules)
    assert after["execution"]["acceptance"] == before["execution"]["acceptance"]


def test_sdk_authoring_guidance_is_sourced_from_versioned_contract() -> None:
    guidance = authoring_presentation_guidance()
    assert "2026-09-15.1" in guidance
    assert "business domain" in guidance
    assert "never guess" in guidance
