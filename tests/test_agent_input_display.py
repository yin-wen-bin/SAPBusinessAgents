"""Synthetic authoring regression; no SAP, SDK calls or existing draft writes."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest

from sap_business_agents_platform.manifests import ManifestError, derive_input_display, validate_execution
from sap_business_agents_platform.models import AgentDraftUpdate
from tests.test_agent_authoring import Runtime, _service, create, feedback_payload, send_and_wait


def _proposal(package):
    manifest = package["manifest"]
    fields = [
        ("delivery_date", "计划行交货日期", "Schedule line delivery date"),
        ("purchasing_organization", "采购组织", "Purchasing organization"),
        ("purchasing_group", "采购组", "Purchasing group"),
        ("plant", "工厂", "Plant"),
        ("storage_location", "库存地点", "Storage location"),
        ("purchase_order", "采购订单号", "Purchase order"),
    ]
    manifest["execution"]["inputSchema"] = {
        "type": "object", "additionalProperties": False, "required": ["delivery_date"],
        "properties": {key: {"type": "string", "title": {"zh": zh, "en": en}} for key, zh, en in fields},
    }
    manifest["execution"]["inputSchema"]["properties"]["delivery_date"]["format"] = "date"
    manifest["inputs"] = {
        "zh": [zh + ("（必填）" if index == 0 else "（可选）") for index, (_, zh, _) in enumerate(fields)],
        "en": [en + (" (Required)" if index == 0 else " (Optional)") for index, (_, _, en) in enumerate(fields)],
    }


@pytest.mark.parametrize("targeted", [False, True])
def test_feedback_derives_display_before_validation_diff_and_persistence(tmp_path, targeted):
    service, store, _ = _service(tmp_path)
    draft = create(service)
    original = copy.deepcopy(draft["package"])
    proposed = copy.deepcopy(original)
    _proposal(proposed)
    unchanged_execution = copy.deepcopy(proposed["manifest"]["execution"])

    class ProposedRuntime(Runtime):
        async def review_agent_feedback(self, **kwargs):
            decision = {"action": "revise_agent", "summary": {"zh": "调整输入", "en": "Revise inputs"}}
            if targeted:
                decision["edits"] = [
                    {"op": "replace", "path": "/manifest/inputs", "value": proposed["manifest"]["inputs"]},
                    {"op": "replace", "path": "/manifest/execution/inputSchema", "value": unchanged_execution["inputSchema"]},
                ]
            else:
                decision["package"] = proposed
            return decision

    service.runtime = ProposedRuntime()
    _, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert result["conversation"][-1]["status"] == "completed"
    assert result["revision"] == 2
    saved = result["package"]
    assert saved["manifest"]["inputs"] == derive_input_display(unchanged_execution["inputSchema"])
    assert saved["manifest"]["execution"] == unchanged_execution
    assert saved["manifest"]["outputs"] == original["manifest"]["outputs"]
    assert saved["manifest"]["validation"] == original["manifest"]["validation"]
    assert store.get_agent_authoring_revision(draft["draft_id"], 1)["package"] == original
    assert "（可选）" not in json.dumps(result["diff"], ensure_ascii=False)
    on_disk = json.loads((Path(result["path"]) / "agent.json").read_text(encoding="utf-8"))
    assert on_disk == saved["manifest"]
    assert proposed["manifest"]["inputs"]["en"][1].endswith(" (Optional)"), "Runtime response is not mutated"
    assert store.get_agent_operation(draft["draft_id"]) is None


def test_manual_save_derives_display_and_undo_preserves_original_package(tmp_path):
    service, store, _ = _service(tmp_path)
    draft = create(service)
    package = copy.deepcopy(draft["package"])
    _proposal(package)
    result = service.update(draft["draft_id"], AgentDraftUpdate(expectedRevision=1, manifest=package["manifest"]))
    assert result["package"]["manifest"]["inputs"] == derive_input_display(package["manifest"]["execution"]["inputSchema"])
    assert package["manifest"]["inputs"]["en"][0].endswith(" (Required)")
    restored = service.undo(draft["draft_id"], expected_revision=2, target_revision=1)
    assert restored["package"] == draft["package"]
    assert store.get_agent_authoring_revision(draft["draft_id"], 2)["package"] == result["package"]


def test_redundant_display_only_proposal_does_not_create_revision(tmp_path):
    service, _, _ = _service(tmp_path)
    draft = create(service)
    service.runtime = Runtime("revise_agent", lambda package: package["manifest"].update(inputs={"zh": ["虚构字段（可选）"], "en": ["Invented (Optional)"]}))
    _, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert result["revision"] == 1
    assert result["package"] == draft["package"]
    assert result["conversation"][-1]["decision"]["changed"] is False


@pytest.mark.parametrize("invalid,code,path", [
    ({"title": {"en": "Private value"}}, "input_title_missing", "/title"),
    ({"title": {"zh": "Private value", "en": "Name"}}, "input_title_zh_invalid", "/title/zh"),
    ({"title": {"zh": "名称", "en": "Private value 中文"}}, "input_title_en_invalid", "/title/en"),
    ({"type": "Private value"}, "json_schema_invalid", "/type"),
])
def test_invalid_schema_fails_with_safe_reason_and_no_saved_revision(tmp_path, invalid, code, path):
    service, store, _ = _service(tmp_path)
    draft = create(service)
    def mutate(package):
        _proposal(package)
        package["manifest"]["execution"]["inputSchema"]["properties"]["plant"].update(invalid)
    service.runtime = Runtime("revise_agent", mutate)
    _, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    turn = result["conversation"][-1]
    assert turn["status"] == "failed"
    assert turn["decision"]["error_code"] == "agent_definition_invalid"
    assert turn["decision"]["validation_issues"] == [{"code": code, "path": "/manifest/execution/inputSchema/properties/plant" + path}]
    assert "Private value" not in json.dumps(turn)
    assert result["revision"] == 1 and result["package"] == draft["package"]
    assert turn.get("result_revision") is None
    assert store.get_agent_operation(draft["draft_id"]) is None


def test_manual_editor_may_still_save_incomplete_titles_for_static_checks(tmp_path):
    service, _, _ = _service(tmp_path)
    draft = create(service)
    package = copy.deepcopy(draft["package"])
    _proposal(package)
    del package["manifest"]["execution"]["inputSchema"]["properties"]["plant"]["title"]
    result = service.update(draft["draft_id"], AgentDraftUpdate(expectedRevision=1, manifest=package["manifest"]))
    assert result["revision"] == 2
    with pytest.raises(ManifestError, match="title must be bilingual"):
        validate_execution(result["package"]["manifest"])


def test_hidden_subtrees_excluded_but_public_nested_titles_checked():
    schema = {"type": "object", "properties": {
        "rows": {"type": "array", "title": {"zh": "需求行", "en": "Rows"}, "items": {"type": "object", "properties": {
            "material": {"type": "string", "title": {"zh": "物料", "en": "Material"}},
        }}},
        "internal": {"type": "object", "x-sapba-internal": True, "properties": {"bad": {"type": "string"}}},
        "workflow": {"type": "object", "x-sapba-workflow-only": True, "properties": {"bad": {"type": "string"}}},
    }}
    original = copy.deepcopy(schema)
    assert derive_input_display(schema) == {"zh": ["需求行"], "en": ["Rows"]}
    assert schema == original
    schema["properties"]["rows"]["items"]["properties"]["material"]["title"]["zh"] = "material"
    with pytest.raises(ManifestError) as error:
        derive_input_display(schema)
    assert error.value.public_issue() == {"code": "input_title_zh_invalid", "path": "/manifest/execution/inputSchema/properties/rows/items/properties/material/title/zh"}


def test_official_manifest_gate_stays_strict(tmp_path):
    service, _, _ = _service(tmp_path)
    manifest = copy.deepcopy(create(service)["package"]["manifest"])
    _proposal({"manifest": manifest})
    with pytest.raises(ManifestError, match="inputs must mirror") as error:
        validate_execution(manifest)
    assert error.value.public_issue()["code"] == "input_display_mismatch"
    manifest["inputs"] = derive_input_display(manifest["execution"]["inputSchema"])
    manifest["outputs"]["en"][0] += " (Optional)"
    with pytest.raises(ManifestError, match="outputs must mirror") as error:
        validate_execution(manifest)
    assert error.value.public_issue()["code"] == "output_display_mismatch"


def test_definition_errors_never_become_model_errors_or_echo_exception_values():
    from sap_business_agents_platform.agent_authoring import AgentAuthoringMixin
    error = ManifestError("authentication failed Private value", issue_code="arbitrary-secret", path="/manifest/<private value>")
    assert AgentAuthoringMixin._feedback_error_code(error) == "agent_definition_invalid"
    assert error.public_issue() == {"code": "definition_invalid", "path": "/manifest"}


def test_unsafe_execution_still_fails_after_display_normalization(tmp_path):
    service, _, _ = _service(tmp_path)
    draft = create(service)
    def mutate(package):
        _proposal(package)
        package["manifest"]["execution"]["mode"] = "arbitrary"
    service.runtime = Runtime("revise_agent", mutate)
    _, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert result["revision"] == 1
    assert result["conversation"][-1]["decision"]["validation_issues"] == [{"code": "execution_mode_invalid", "path": "/manifest/execution/mode"}]
