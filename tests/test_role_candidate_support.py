"""One pre-generation/final-check rule; no real SDK, model or SAP calls."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.runtime_role_consolidation import (
    candidate_support_issues, candidate_support_projection, checked_candidate, reconcile,
)
from tests.test_role_consolidation_contract import match, operation, suggestion


def catalog(*ids, executable=True, verdict="PASS"):
    return {"items": [{"agent_id": agent_id, "executable": executable,
                       "validation_verdict": verdict} for agent_id in ids]}


@pytest.mark.parametrize("confidence", ["high", "medium", "low", "unknown"])
@pytest.mark.parametrize("executable,verdict", [(True, "PASS"), (False, "PASS"), (True, "BLOCKED")])
def test_projection_and_reconciliation_use_identical_support(confidence, executable, verdict):
    op = operation("one")
    records = [match("one", "a"), {**match("one", "b"), "confidence": confidence}]
    frozen = catalog("a", "b")
    frozen["items"][1].update(executable=executable, validation_verdict=verdict)
    before = copy.deepcopy((op, records, frozen))
    projection = candidate_support_projection([op], records, frozen)["operations"][0]
    checked = [checked_candidate(item, operation=op, agent=agent)
               for item, agent in zip(records, frozen["items"])]
    result = reconcile({"operations": [op], "agent_matches": checked,
        "rejected_candidates": [], "workflow_suggestions": [suggestion("one", ["a", "b"])],
        "agent_gaps": []}, [{**suggestion("one", ["a", "b"]), "validated": True}])
    assert result["complete"] == ("b" in projection["eligible_stage_agent_ids"])
    assert (op, records, frozen) == before
    if "b" not in projection["eligible_stage_agent_ids"]:
        rejected = next(item for item in projection["unsupported_candidates"] if item["agent_id"] == "b")
        assert rejected["reasons"] == candidate_support_issues(checked[1], operation_id="one")


def test_failed_live_case_stays_rejected_even_with_high_stage_self_rating():
    records = [match("one", "mm-po-gr-status", "full"),
               {**match("one", "supplier-performance-risk"), "confidence": "low"}]
    projection = candidate_support_projection([operation("one")], records,
        catalog("mm-po-gr-status", "supplier-performance-risk"))["operations"][0]
    assert projection["eligible_stage_agent_ids"] == ["mm-po-gr-status"]
    assert projection["eligible_single_agent_ids"] == ["mm-po-gr-status"]
    assert projection["unsupported_candidates"] == [{"agent_id": "supplier-performance-risk",
                                                     "reasons": ["match_confidence_insufficient"]}]
    combined = suggestion("one", ["mm-po-gr-status", "supplier-performance-risk"])
    single = suggestion("one", ["mm-po-gr-status"])
    value = {"operations": [operation("one")], "agent_matches": records,
             "workflow_suggestions": [combined], "agent_gaps": []}
    result = reconcile(value, [{**combined, "validated": True}])
    assert not result["complete"] and result["issues"][0]["code"] == "role_matching_combination_unsupported"
    value["workflow_suggestions"] = [single]
    assert reconcile(value, [{**single, "validated": True}])["complete"]


def test_catalog_not_model_controls_execution_and_acceptance():
    forged = match("one", "blocked", "full")
    projection = candidate_support_projection([operation("one")], [forged],
        catalog("blocked", executable=False, verdict="BLOCKED"))["operations"][0]
    assert projection["eligible_stage_agent_ids"] == []
    assert projection["full_coverage_agent_ids"] == ["blocked"]  # coverage != executable
    assert projection["unsupported_candidates"][0]["reasons"] == ["agent_not_executable", "acceptance_not_pass"]
    for frozen in ({"items": []}, {"items": [{"agent_id": "blocked"}]}):
        assert not candidate_support_projection([operation("one")], [forged], frozen)["operations"][0]["eligible_stage_agent_ids"]


def test_support_is_operation_specific_and_partial_is_not_single_agent_coverage():
    records = [match("one", "a"), match("two", "b", "full")]
    projections = candidate_support_projection([operation("one"), operation("two")], records, catalog("a", "b"))["operations"]
    assert projections[0]["eligible_stage_agent_ids"] == ["a"]
    assert projections[0]["eligible_single_agent_ids"] == []
    assert projections[1]["eligible_single_agent_ids"] == ["b"]
    assert "operation_mismatch" in candidate_support_issues(records[0], operation_id="two")
    proposed = suggestion("one", ["a", "b"])
    proposed["operation_ids"] = ["one", "two"]
    result = reconcile({"operations": [operation("one"), operation("two")], "agent_matches": records,
                        "workflow_suggestions": [proposed], "agent_gaps": []}, [{**proposed, "validated": True}])
    assert {item["operation_id"] for item in result["issues"]
            if item["code"] == "role_matching_combination_unsupported"} == {"one", "two"}


def test_existing_catalog_normalization_is_shared_without_loosening_low_partial():
    op = {**operation("one"), "name": "Check PGI status"}
    partial = {**match("one", "a"), "confidence": "low", "uncovered_capabilities": ["PGI status"]}
    frozen = catalog("a")
    frozen["capability_signals"] = {"a": ["pgi_status"]}
    checked = checked_candidate(partial, operation=op, agent=frozen["items"][0], capability_signals=["pgi_status"])
    assert checked["coverage"] == "full" and checked["confidence"] == "high" and not checked["uncovered_capabilities"]
    assert candidate_support_projection([op], [partial], frozen)["operations"][0]["eligible_single_agent_ids"] == ["a"]
    # This prior capability-based correction is not triggered by stage confidence.
    frozen["capability_signals"] = {}
    assert candidate_support_projection([op], [partial], frozen)["operations"][0]["eligible_stage_agent_ids"] == []
    assert partial["confidence"] == "low" and partial["coverage"] == "partial"


@pytest.mark.parametrize("provider", ["codex", "workbuddy"])
@pytest.mark.parametrize("operation_name", ["analyze_role_matching", "review_role_matching_feedback"])
def test_shared_prompt_contains_authoritative_support_and_preserves_audit(monkeypatch, tmp_path, provider, operation_name):
    from sap_business_agents_platform.codex_planner import CodexPlanner
    from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner
    from tests.runtime_trace_fixture import TraceClient, cases, response
    prompts, trace = [], []
    records = [match("one", "a", "full"), {**match("one", "b"), "confidence": "low"}]
    # The native page Schema does not authorize a model to declare these flags.
    records = [{key: value for key, value in item.items() if key not in {"executable", "validation_verdict"}} for item in records]
    ops = [operation("one")]
    frozen = catalog("a", "b")
    frozen["runtime_catalog"] = {"digest": "catalog", "total_agent_count": 2, "page_count": 1,
        "pages": [{"catalog_digest": "catalog", "page_index": 1, "page_count": 1,
                   "total_agent_count": 2, "items": copy.deepcopy(frozen["items"])}]}
    expected = candidate_support_projection(ops, records, frozen)

    class Client(TraceClient):
        async def run(self, prompt, *, output_schema, effort=None, **kwargs):
            prompts.append(prompt)
            native = await super().run(prompt, output_schema=output_schema, effort=effort, **kwargs)
            if prompt.startswith("Finalize"):
                return native
            raw = response(operation_name, prompt)
            value = json.loads(raw["analysis_json"])
            value["operations"] = copy.deepcopy(ops)
            value["catalog_evaluation"].update(total_agent_count=2, evaluated_agent_count=2,
                                              evaluated_pair_count=2, evaluated_agent_ids=["a", "b"])
            if prompt.startswith("Evaluate"):
                value["agent_matches"] = copy.deepcopy(records)
            raw["analysis_json"] = json.dumps(value)
            return SimpleNamespace(final_response=json.dumps(raw))

    if provider == "codex":
        import openai_codex
        monkeypatch.setattr(openai_codex, "AsyncCodex", lambda **options: Client(trace, operation_name, **options))
        planner = CodexPlanner(tmp_path, model="fixture-model", reasoning_effort="max")
    else:
        planner = WorkBuddyPlanner(tmp_path, model="fixture-model")
        planner.reasoning_effort = "max"
        planner._driver = SimpleNamespace(client=lambda **options: Client(trace, operation_name, **options))
    kwargs = copy.deepcopy(cases()[operation_name])
    kwargs["agent_catalog"] = frozen
    result = asyncio.run(getattr(planner, operation_name)(**kwargs))
    final = prompts[-1]
    projection_json = final.split("Candidate support projection: ", 1)[1].split("\n", 1)[0]
    assert json.loads(projection_json) == expected
    assert "Writing confidence=high in a stage cannot upgrade a prior match" in final
    assert '"match_confidence": ["high", "medium"]' in final
    assert result["analysis"]["agent_matches"] == records
    assert not candidate_support_issues(checked_candidate(records[0], operation=ops[0], agent=frozen["items"][0]), operation_id="one")
    # Both SDKs retain original read-only startup, model, effort and phase order.
    for item in trace:
        if item["call"] == "start":
            assert item["options"]["sandbox"] == "read_only"
            assert item["options"]["approval_mode"] == "deny_all"
            assert item["options"]["model"] == "fixture-model"
        if item["call"] == "run":
            assert item["effort"] == "max"
    assert len(prompts) == 3  # understanding, one page, final; no extra calls
