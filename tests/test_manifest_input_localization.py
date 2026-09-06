from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from sap_business_agents_platform.manifests import (
    AgentRepository,
    ManifestError,
    validate_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


def _manifest(module: str, agent_id: str) -> dict[str, object]:
    path = ROOT / "agents" / module / agent_id / "agent.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_all_agent_manifests_have_locale_consistent_public_input_titles() -> None:
    manifests = AgentRepository(ROOT / "agents").list_all()

    assert len(manifests) == 33


def test_manifest_rejects_english_label_in_chinese_input_title() -> None:
    manifest = _manifest("MM", "supplier-performance-risk")
    manifest["execution"]["inputSchema"]["properties"]["supplier"]["title"]["zh"] = "supplier"
    manifest["inputs"]["zh"][0] = "supplier"

    with pytest.raises(ManifestError, match=r"title\.zh must contain a Chinese business label"):
        validate_manifest(manifest)


def test_manifest_rejects_chinese_label_in_english_input_title() -> None:
    manifest = _manifest("MM", "supplier-performance-risk")
    manifest["execution"]["inputSchema"]["properties"]["supplier"]["title"]["en"] = "供应商"
    manifest["inputs"]["en"][0] = "供应商"

    with pytest.raises(ManifestError, match=r"title\.en must not contain Chinese characters"):
        validate_manifest(manifest)


def test_manifest_rejects_locale_mismatch_in_public_object_array_child() -> None:
    manifest = _manifest("SD", "new-sales-demand-coverage")
    material = manifest["execution"]["inputSchema"]["properties"]["demand_items"]["items"]["properties"]["material"]
    material["title"]["zh"] = "material"

    with pytest.raises(ManifestError, match=r"demand_items\.items\.properties\.material\.title\.zh"):
        validate_manifest(manifest)


def test_manifest_skips_workflow_only_input_subtree_language_gate() -> None:
    manifest = deepcopy(_manifest("SD", "new-sales-demand-coverage"))
    demand_items = manifest["execution"]["inputSchema"]["properties"]["demand_items"]
    demand_items["x-sapba-workflow-only"] = True
    demand_items["items"]["properties"]["material"]["title"]["zh"] = "material"
    manifest["inputs"]["zh"].pop()
    manifest["inputs"]["en"].pop()

    validate_manifest(manifest)


def test_manifest_allows_mixed_neutral_acronym_and_chinese_business_label() -> None:
    manifest = _manifest("SD", "new-sales-demand-coverage")

    assert manifest["execution"]["inputSchema"]["properties"]["mrp_area"]["title"]["zh"] == "MRP范围"
    validate_manifest(manifest)
