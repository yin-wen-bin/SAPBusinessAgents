"""Final-phase output, compiler integration and exhaustive business accounting."""
import asyncio
import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.runtime_role_consolidation import SCHEMA, VERSION, decode, reconcile, match_conflicts
from sap_business_agents_platform.runtime_contract import RuntimeContractError

REF = {"document_id": "synthetic", "chunk_id": "one"}
TEXT = {"zh": "只读能力", "en": "Read-only capability"}

def operation(name):
    return {"operation_id": name, "evidence_refs": [REF]}

def match(op, agent, coverage="partial", executable=True):
    return {"operation_id": op, "agent_id": agent, "coverage": coverage,
        "confidence": "high", "reason": "Declared scope", "uncovered_capabilities": [],
        "executable": executable, "validation_verdict": "PASS" if executable else "BLOCKED", "evidence_refs": [REF]}

def gap(op):
    return {"gap_id": "gap-" + op, "operation_ids": [op], "required_capability": TEXT,
        "required_inputs": [], "required_outputs": [], "safety_boundary": TEXT,
        "business_impact": TEXT, "partial_agent_ids": [], "reason": TEXT, "evidence_refs": [REF]}

def suggestion(op, agents):
    return {"suggestion_id": "combine-" + op, "operation_ids": [op], "title": TEXT,
        "description": TEXT, "intent": TEXT, "evidence_refs": [REF],
        "stages": [{"id": "stage_" + str(i), "agent_id": agent, "confidence": "high",
            "capability": TEXT, "reason": TEXT, "bindings": [], "requested_outputs": ["source_complete"]}
            for i, agent in enumerate(agents)]}

def base():
    return {"operations": [operation("full"), operation("combine"), operation("gap")],
        "agent_matches": [match("full", "a", "full"), match("combine", "a"), match("combine", "b")],
        "rejected_candidates": [], "workflow_suggestions": [suggestion("combine", ["a", "b"])],
        "agent_gaps": [gap("gap")]}

def test_final_phase_has_four_required_fields_and_no_legacy_default():
    raw = {"summary_zh": "总结", "summary_en": "Summary", "workflow_suggestions": [], "agent_gaps": []}
    assert set(SCHEMA["required"]) == set(raw)
    assert decode(SimpleNamespace(final_response=json.dumps(raw)), operation="analyze_role_matching") == raw
    for malformed in ({k: v for k, v in raw.items() if k != "agent_gaps"}, {**raw, "analysis_json": "{}"}):
        with pytest.raises(RuntimeContractError) as error:
            decode(SimpleNamespace(final_response=json.dumps(malformed)), operation="analyze_role_matching")
        assert error.value.detail["validation_issues"]

def test_single_combination_and_genuine_gap_account_for_every_operation():
    value = base()
    compiled = [{**value["workflow_suggestions"][0], "validated": True}]
    result = reconcile(value, compiled)
    assert result["complete"] and result["contract_version"] == VERSION
    assert [item["resolution"] for item in result["operations"]] == [
        "single_agent", "declared_validated_combination", "capability_gap"]

def test_valid_empty_arrays_only_when_every_operation_has_full_coverage():
    value = {"operations": [operation("one")], "agent_matches": [match("one", "a", "full", False)],
        "workflow_suggestions": [], "agent_gaps": [], "rejected_candidates": []}
    result = reconcile(value, [])
    assert result["complete"] and result["operations"][0]["covered"]
    assert not result["operations"][0]["executable"]
    value["agent_matches"] = []
    result = reconcile(value, [])
    assert not result["complete"] and result["issues"][0]["code"] == "role_matching_operation_unresolved"


@pytest.mark.parametrize("coverage,executable,compiled,expected", [
    ("full", True, True, True),
    ("partial", True, True, False),
    ("full", False, True, False),
    ("full", True, False, False),
])
def test_single_agent_suggestion_requires_existing_full_coverage_and_compilation(coverage, executable, compiled, expected):
    value = {"operations": [operation("one")],
        "agent_matches": [match("one", "a", coverage, executable)],
        "workflow_suggestions": [suggestion("one", ["a"])],
        "agent_gaps": [], "rejected_candidates": []}
    result = reconcile(value, [{**value["workflow_suggestions"][0], "validated": compiled}])
    assert result["complete"] is expected
    if expected:
        assert result["operations"][0]["resolution"] == "single_agent"
        assert result["operations"][0]["executable"]
    else:
        assert any(item["code"] == "role_matching_combination_unsupported" for item in result["issues"])
        if coverage == "partial":
            assert result["operations"][0]["resolution"] == "unresolved"

@pytest.mark.parametrize("mutation,code", [
    (lambda v: v["agent_gaps"].append(gap("full")), "role_matching_resolution_conflict"),
    (lambda v: v["agent_gaps"][0].update(operation_ids=["unknown"]), "role_matching_operation_ref_invalid"),
    (lambda v: v["agent_matches"][0].update(evidence_refs=[{"document_id": "synthetic", "chunk_id": "other"}]), "role_matching_operation_evidence_invalid"),
    (lambda v: v["workflow_suggestions"][0]["stages"][0].update(agent_id="unmatched"), "role_matching_combination_unsupported"),
    (lambda v: v["agent_matches"][1].update(executable=False), "role_matching_combination_unsupported"),
])
def test_unresolved_or_conflicting_operation_cannot_be_complete(mutation, code):
    value = base()
    mutation(value)
    result = reconcile(value, [{**value["workflow_suggestions"][0], "validated": True}])
    assert not result["complete"] and any(i["code"] == code for i in result["issues"])

def test_identical_duplicates_are_not_conflicts_but_disagreement_is():
    item = match("one", "a", "full")
    value = {"agent_matches": [item, copy.deepcopy(item)]}
    assert not match_conflicts(value)
    value["agent_matches"][1]["coverage"] = "partial"
    conflicts = match_conflicts(value)
    assert conflicts[0]["code"] == "role_matching_record_conflict"
    result = reconcile({**value, "operations": [operation("one")]}, [], conflicts=conflicts)
    assert not result["complete"]

def test_shared_phase_schema_retains_precise_nested_diagnostics():
    raw = {"summary_zh": "总结", "summary_en": "Summary", "workflow_suggestions": [suggestion("one", ["a", "b"])], "agent_gaps": []}
    del raw["workflow_suggestions"][0]["stages"][1]["bindings"]
    with pytest.raises(RuntimeContractError) as caught:
        decode(SimpleNamespace(final_response=json.dumps(raw)), operation="analyze_role_matching")
    assert any(i["path"] == "/workflow_suggestions/0/stages/1/bindings" for i in caught.value.detail["validation_issues"])
    from sap_business_agents_platform.runtime_diagnostics import stage_failure
    safe = stage_failure(caught.value, schema=SCHEMA)
    assert safe["contract_id"] == VERSION and safe["validation_issues"]

@pytest.mark.parametrize("single_suggestion", [False, True])
def test_real_compiler_suggestions_and_gaps_are_saved_in_immutable_revisions(tmp_path, single_suggestion):
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.database import RunStore
    from sap_business_agents_platform.manifests import AgentRepository
    from sap_business_agents_platform.role_matching import RoleMatchingService
    from tests.test_role_matching import _FakeRuntime, _Drafts
    root = Path(__file__).resolve().parents[1]
    class Runtime(_FakeRuntime):
        async def analyze_role_matching(self, **kwargs):
            raw = await super().analyze_role_matching(**kwargs)
            original = raw["analysis"]
            source_ref = original["operations"][0]["evidence_refs"]
            value = base()
            if single_suggestion:
                value["workflow_suggestions"].append(suggestion("full", ["a"]))
            replacements = {"a": "mm-po-gr-status", "b": "procure-to-pay-status"}
            for item in value["agent_matches"]:
                item["agent_id"] = replacements[item["agent_id"]]
            for proposed in value["workflow_suggestions"]:
                for stage in proposed["stages"]:
                    stage["agent_id"] = replacements[stage["agent_id"]]
            for collection in ("operations", "agent_matches", "workflow_suggestions", "agent_gaps"):
                for item in value[collection]: item["evidence_refs"] = copy.deepcopy(source_ref)
            original.update(value, consolidation_contract=VERSION)
            original["catalog_evaluation"]["evaluated_pair_count"] *= 3
            original["catalog_evaluation"]["consolidation_complete"] = True
            return raw
    settings = replace(Settings(), repository_root=root, data_root=tmp_path / "data", draft_root=tmp_path / "drafts")
    store = RunStore(settings.database_path)
    service = RoleMatchingService(settings, store, AgentRepository(root / "agents"), Runtime(), _Drafts())
    async def run():
        await service.start()
        try:
            session = await service.create(paths=[], role_description="隔离岗位组合与能力缺口检查", locale="zh", consent=True)
            for _ in range(200):
                status = service.get(session["session_id"])
                if status["status"] in {"completed", "failed"}: break
                await asyncio.sleep(.01)
            assert status["status"] == "completed", status.get("error")
            result = service.revision(session["session_id"], 1)["result"]
            assert result["catalog_evaluation"]["consolidation_complete"], result["workflow_validation_issues"]
            assert result["operation_reconciliation"]["complete"]
            assert result["workflow_suggestions"][0]["validated"]
            assert result["workflow_suggestions"][0]["compiled_workflow"]["readOnly"]
            assert result["agent_gaps"][0]["gap_id"] == "gap-gap"
            assert result["change_summary"]["workflow_suggestions"]["added"] == 1 + int(single_suggestion)
            if single_suggestion:
                saved_single = next(s for s in result["workflow_suggestions"] if s["suggestion_id"] == "combine-full")
                assert saved_single["validated"] and len(saved_single["stages"]) == 1
                assert saved_single["stages"][0]["agent_id"] == "mm-po-gr-status"
                assert result["operation_reconciliation"]["operations"][0]["resolution"] == "single_agent"
            assert result["change_summary"]["agent_gaps"]["added"] == 1
            assert result["completeness"]["workflow_validation_complete"]
        finally:
            await service.stop()
    asyncio.run(run())
