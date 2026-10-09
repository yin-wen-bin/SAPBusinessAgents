"""Offline WorkBuddy admission and formatting; no SDK/network/SAP calls."""
import asyncio
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest

from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner, WorkBuddyRuntimeError
from sap_business_agents_platform.workbuddy_query_contract import ReadScope, plans, preserve_grounding
from sap_business_agents_platform.workbuddy_diagnostics import checked_output
from sap_business_agents_platform.workbuddy_role_contract import decode, prompt, COLLECTIONS

PLAN = {"service_name": "API_PURCHASEORDER_PROCESS_SRV", "odata_version": "2.0", "entity_set": "A_PurchaseOrder",
        "http_method": "GET", "filters": [{"field": "PurchaseOrder", "operator": "eq", "value": "4500001466"}],
        "select_fields": ["PurchaseOrder", "Supplier", "CompanyCode"], "top": 1}


def feedback_arguments(workflow):
    from tests.runtime_trace_fixture import cases
    return {**cases()['review_workflow_feedback'], 'workflow': workflow}


def feedback_terminal(**updates):
    from tests.runtime_trace_fixture import response
    return {**response('review_workflow_feedback', ''), **updates}


@pytest.mark.parametrize("key", ["filter", "keys", "select", "skip", "url", "headers"])
def test_unknown_query_members_rejected_at_any_step(key):
    bad = {**PLAN, key: "synthetic-sensitive-value"}
    for value in (bad, {"plan_kind": "multi_step", "steps": [bad]}):
        with pytest.raises(WorkBuddyError) as failure:
            plans(value)
        assert failure.value.code == "workbuddy_plan_contract_invalid"
        assert "synthetic" not in json.dumps(failure.value.detail)


def test_nested_binding_function_tuple_and_partition_contracts():
    first = {**PLAN, "step_id": "po"}
    second = {**PLAN, "step_id": "items", "entity_set": "A_PurchaseOrderItem", "filters": [],
              "filter_from_previous": [{"field": "PurchaseOrder", "source_step_id": "po", "source_field": "PurchaseOrder"}]}
    assert len(plans({"plan_kind": "multi_step", "steps": [first, second]})) == 2
    assert plans({**PLAN, "plan_kind": "function_import", "function_parameters": [{"name": "CompanyCode", "value": "1010"}]})
    assert plans({**PLAN, "tuple_filters": [{"fields": ["Material", "Plant"], "values": [["M", "P"]]}]})
    assert plans({**PLAN, "partition": {"field": "Date", "strategy": "adaptive_date", "from": "2022-01-01", "to": "2022-01-02"}})
    with pytest.raises(WorkBuddyError):
        plans({**PLAN, "steps": [second]})


@pytest.mark.parametrize("patch", [{"filters": []}, {"filters": [{"field": "PurchaseOrder", "value": "other"}]},
    {"entity_set": "A_Other"}, {"select_fields": ["Supplier"]}, {"top": None}, {"top": 2}])
def test_grounding_cannot_weaken_frozen_plan(patch):
    with pytest.raises(WorkBuddyError, match="grounding_scope_changed"):
        preserve_grounding(PLAN, {**PLAN, **patch})
    preserve_grounding(PLAN, {**PLAN, "select_fields": [*PLAN["select_fields"], "PurchaseOrderDate"]})


def test_equivalent_declared_string_filter_defaults_are_allowed():
    preserve_grounding(PLAN, {**PLAN, "filters": [{**PLAN["filters"][0], "value_type": "string"}]})


def test_user_confirmed_scope_and_unknown_input_clarification():
    scope = ReadScope("只读查询采购订单4500001466的抬头")
    scope.check(PLAN)
    with pytest.raises(WorkBuddyError, match="query_scope_rejected"):
        scope.check({**PLAN, "filters": []})
    with pytest.raises(WorkBuddyError, match="query_scope_rejected"):
        scope.check({**PLAN, "filters": [{"field": "PurchaseOrder", "operator": "contains", "value": "4500001466"}]})
    unresolved = ReadScope("查询", {"unknown_business_parameter": "1"})
    assert unresolved.unresolved
    with pytest.raises(WorkBuddyError, match="clarification_required"):
        unresolved.check(PLAN)
    for prefix in ("Confirmed public inputs: ", "Canonical query inputs: "):
        bound = ReadScope("业务问题\n\n" + prefix + '{"purchase_order":"4500001466"}')
        bound.check(PLAN)


def test_scope_multistep_and_derived_rows_keep_pairs():
    scope = ReadScope("采购订单4500001466")
    first = {**PLAN, "step_id": "po"}
    second = {**PLAN, "step_id": "item", "filters": [],
              "filter_from_previous": [{"field": "PurchaseOrder", "source_step_id": "po", "source_field": "PurchaseOrder"}]}
    scope.check({"plan_kind": "multi_step", "steps": [first, second]})
    with pytest.raises(WorkBuddyError):
        scope.check({"plan_kind": "multi_step", "steps": [second, first]})
    with pytest.raises(WorkBuddyError):
        scope.check({"plan_kind": "multi_step", "steps": [first, {**second,
            "filter_from_previous": [{"field": "Supplier", "source_step_id": "po", "source_field": "Supplier"}]}]})
    both = ReadScope("采购订单4500001466", {"plant": "1010"})
    paired = {**first, "filters": [*first["filters"], {"field": "Plant", "value": "1010"}]}
    with pytest.raises(WorkBuddyError):
        both.check({"plan_kind": "multi_step", "steps": [paired, second]})
    scope.schemas[(PLAN["service_name"], "2.0", "A_MaterialDocumentHeader")] = {"MaterialDocument", "MaterialDocumentYear"}
    scope.remember([{"MaterialDocument": "A", "MaterialDocumentYear": "2022"}, {"MaterialDocument": "B", "MaterialDocumentYear": "2023"}])
    derived = {**PLAN, "entity_set": "A_MaterialDocumentHeader", "filters": [
        {"field": "MaterialDocument", "value": "A"}, {"field": "MaterialDocumentYear", "value": "2022"}]}
    scope.check(derived)
    with pytest.raises(WorkBuddyError):
        scope.check({**derived, "filters": [{"field": "MaterialDocument", "value": "A"}, {"field": "MaterialDocumentYear", "value": "2023"}]})


def test_skill_cannot_expand_scope_or_substitute_raw_expression():
    scope = ReadScope("采购订单4500001466")
    scope.check_skill({"input": {"purchase_order": "4500001466"}})
    scope.check_skill({"input": {"filters": [{"field": "PurchaseOrder", "option": "eq", "sign": "I", "low": "4500001466"}]}})
    for value in ({"purchase_order": "4500001467"}, {"where": "PurchaseOrder='4500001466'"}, {},
                  {"purchase_order": "4500001466", "where": "other"},
                  {"filters": [{"field": "PurchaseOrder", "option": "eq", "sign": "E", "low": "4500001466"}]}):
        with pytest.raises(WorkBuddyError, match="skill_scope_rejected"):
            scope.check_skill({"input": value})


def test_strict_json_and_safe_schema_errors():
    schema = {"type": "object", "additionalProperties": False, "required": ["status"],
              "properties": {"status": {"enum": ["normal"]}}}
    assert checked_output('{"status":"normal"}', schema, operation="free_query")["status"] == "normal"
    for text in ('```json\n{}\n```', 'prefix {}', '{"unknown-secret":"value"}', '{"status":"secret"}'):
        with pytest.raises(WorkBuddyError) as error:
            checked_output(text, schema, operation="free_query")
        detail = error.value.detail
        assert detail["output_length"] == len(text.encode()) and len(detail["output_sha256"]) == 64
        assert "secret" not in json.dumps(detail)
        assert detail["validation_issues"]


def test_role_references_missing_unknown_cross_document_and_valid():
    data = {"documents": [{"document_id": "d1", "chunks": [{"chunk_id": "c1"}]},
                          {"document_id": "d2", "chunks": [{"chunk_id": "c2"}]}]}
    value = {**{name: [] for name in COLLECTIONS}, "document_issues": [], "catalog_evaluation": {}}
    value["operations"] = [{"evidence_refs": [{"document_id": "d1", "chunk_id": "c1"}]}]
    assert decode(json.dumps(value), data, "analyze_role_matching") == value
    assert "available_document_refs" in prompt(data)
    for ref in ([], [{"document_id": "d1", "chunk_id": "c2"}], [{"document_id": "invented", "chunk_id": "c1"}]):
        broken = copy.deepcopy(value)
        broken["operations"][0]["evidence_refs"] = ref
        with pytest.raises(WorkBuddyError):
            decode(json.dumps(broken), data, "analyze_role_matching")


def test_workflow_compiler_contract_rejects_missing_confidence_and_patch_output(tmp_path, monkeypatch):
    from sap_business_agents_platform.workbuddy_workflow_contract import PROPOSAL_SCHEMA, decode as decode_workflow
    from sap_business_agents_platform.workbuddy_prompts import WORKFLOW_COMPOSITION_OUTPUT_SCHEMA
    proposal = {"title": {"zh": "检查", "en": "Check"}, "stages": [{"id": "check", "agent_id": "fixed",
        "confidence": "high", "capability": {"zh": "检查", "en": "Check"}, "bindings": [], "requested_outputs": ["records"]}]}
    assert decode_workflow(json.dumps(proposal), PROPOSAL_SCHEMA, operation="compose_workflow", path="/proposal_json") == proposal
    missing = copy.deepcopy(proposal)
    missing["stages"][0].pop("confidence")
    for broken, expected in [(missing, "/proposal_json/stages/0/confidence"), ({"patch": []}, "/proposal_json/title")]:
        with pytest.raises(WorkBuddyError) as failure:
            decode_workflow(json.dumps(broken), PROPOSAL_SCHEMA, operation="compose_workflow", path="/proposal_json")
        assert any(i["path"] == expected and i["constraint"] == "required" for i in failure.value.detail["validation_issues"])
    planner = WorkBuddyPlanner(tmp_path, "model")
    async def turn(prompt, schema, **kwargs):
        assert schema == WORKFLOW_COMPOSITION_OUTPUT_SCHEMA and kwargs["native_schema"]
        assert "confidence" in prompt and "COMPLETE compiler proposal" in prompt
        return {"needs_clarification": False, "clarification_question": "", "proposal_json": json.dumps(proposal)}, "session"
    monkeypatch.setattr(planner, "_structured_turn", turn)
    assert asyncio.run(planner.compose_workflow(requirement="Check", catalog={}, locale="en"))["proposal"] == proposal


@pytest.mark.parametrize("source,value,path", [
    ("proposal_json", {"patch": []}, "/proposal_json/title"),
    ("validation_input_patch_json", {"patch": [], "note": "do not retain secret"}, "/validation_input_patch_json"),
    ("candidate_expectations_json", {"must_pass": ["invented text"]}, "/candidate_expectations_json"),
    ("candidate_expectations_json", [{"output": "records", "operator": "invented"}], "/candidate_expectations_json/0/operator")])
def test_workflow_feedback_embedded_contract_and_safe_paths(tmp_path, monkeypatch, source, value, path):
    proposal = {"title": {"zh": "检查", "en": "Check"}, "stages": [{"id": "check", "agent_id": "fixed",
        "confidence": "high", "capability": {"zh": "检查", "en": "Check"}, "bindings": [], "requested_outputs": ["records"]}]}
    raw = feedback_terminal(action="revise_workflow", proposal_json=json.dumps(proposal),
                            validation_input_patch_json="{}", candidate_expectations_json="[]")
    planner = WorkBuddyPlanner(tmp_path, "model")
    async def turn(prompt, schema, **kwargs):
        assert kwargs["native_schema"] and "not JSON Patch" in prompt
        return {**raw, source: json.dumps(value)}, "session"
    monkeypatch.setattr(planner, "_structured_turn", turn)
    with pytest.raises(WorkBuddyRuntimeError) as failure:
        asyncio.run(planner.review_workflow_feedback(**feedback_arguments({"inputSchema": {"properties": {"company_code": {"type": "string"}}}})))
    assert any(i["path"] == path for i in failure.value.detail["validation_issues"])
    assert "secret" not in json.dumps(failure.value.detail) and "invented" not in json.dumps(failure.value.detail)


def test_workflow_feedback_valid_patch_array_and_clarification(tmp_path, monkeypatch):
    planner = WorkBuddyPlanner(tmp_path, "model")
    async def turn(*_, **kwargs):
        return feedback_terminal(action="rerun_validation", proposal_json="null", validation_input_patch_json='{"company_code":"1010"}',
                candidate_expectations_json='[{"output":"business_status","operator":"equals","expected":"normal"}]'), "session"
    monkeypatch.setattr(planner, "_structured_turn", turn)
    value = asyncio.run(planner.review_workflow_feedback(**feedback_arguments({"inputSchema": {"properties": {"company_code": {"type": "string"}}}})))
    assert value["proposal"] is None and value["validation_input_patch"] == {"company_code": "1010"}
    assert value["candidate_expectations"][0]["expected"] == "normal"


def test_workbuddy_proposal_uses_real_compiler_without_inventing_pins():
    from sap_business_agents_platform.manifests import AgentRepository
    from sap_business_agents_platform.workflow_composer import compact_agent_catalog, compile_workflow_proposal
    from sap_business_agents_platform.workbuddy_workflow_contract import PROPOSAL_SCHEMA, decode as decode_workflow
    from sap_business_agents_platform.workflows import validate_workflow
    agents = AgentRepository(Path(__file__).resolve().parents[1] / "agents")
    proposal = {"title": {"zh": "采购检查", "en": "PO check"}, "description": {"zh": "只读", "en": "Read-only"},
        "stages": [{"id": "receipt", "agent_id": "mm-po-gr-status", "confidence": "high",
            "capability": {"zh": "入库状态", "en": "Receipt status"}, "bindings": [],
            "requested_outputs": ["business_status", "records", "business_report"]}]}
    parsed = decode_workflow(json.dumps(proposal), PROPOSAL_SCHEMA, operation="compose_workflow", path="/proposal_json")
    workflow, composition = compile_workflow_proposal(workflow_id="offline-check", requirement="PO receipt", locale="en",
        proposal=parsed, catalog=compact_agent_catalog(agents), agents=agents)
    validate_workflow(workflow, agents, require_pins=True)
    assert not composition["gaps"] and len(workflow["nodes"]) == 1
    node = workflow["nodes"][0]
    assert node["agentId"] == "mm-po-gr-status" and node["agentVersion"] == agents.get("mm-po-gr-status")["version"]
    assert node["agentDigest"] and "agentDigest" not in parsed["stages"][0]


@pytest.mark.parametrize("operation,seconds", [("compose_workflow", 3600), ("review_workflow_feedback", 3600), ("review_workflow", 180)])
def test_workbuddy_operation_budget_and_cleanup_do_not_mask_failure(tmp_path, monkeypatch, operation, seconds):
    observed, events = [], []
    class Supervisor:
        async def run(self, **kwargs):
            observed.append(kwargs)
            raise WorkBuddyError("workbuddy_deadline_exceeded")
    class Workspace:
        name = str(tmp_path)
        def __init__(self, **kwargs):
            pass
        def cleanup(self):
            raise PermissionError("WinError32 synthetic path must not be logged")
    monkeypatch.setattr("sap_business_agents_platform.workbuddy_planner.tempfile.TemporaryDirectory", Workspace)
    planner = WorkBuddyPlanner(tmp_path, "model", supervisor=Supervisor(), runtime_snapshot={"model": "model"})
    token = planner._operation.set(operation)
    try:
        with planner.bind_events(lambda kind, data: events.append((kind, data))):
            with pytest.raises(WorkBuddyRuntimeError, match="deadline_exceeded"):
                from sap_business_agents_platform.runtime_contract import deadline_scope
                with deadline_scope(time.monotonic() + seconds):
                    asyncio.run(planner._query("test", thread_id=None, system_prompt=None))
    finally:
        planner._operation.reset(token)
    assert seconds - 1 < observed[0]["seconds"] <= seconds
    assert (seconds - 1) * 1000 < observed[0]["payload"]["timeout_ms"] <= seconds * 1000
    assert events[-1][0] == "workbuddy_workspace_cleanup_failed"
    assert "synthetic" not in json.dumps(events)


def test_workflow_feedback_reserves_before_shared_3600_deadline(monkeypatch):
    from sap_business_agents_platform.workflow_factory import _await_feedback_runtime
    order = []
    @asynccontextmanager
    async def reserve():
        order.append("reserved")
        yield
    async def request():
        from sap_business_agents_platform.runtime_contract import remaining_budget
        assert 3599 < remaining_budget(None) <= 3600
        order.append("dispatch")
        return "done"
    async def wait_for(value, *, timeout):
        order.append(timeout)
        return await value
    monkeypatch.setattr("sap_business_agents_platform.workflow_factory.asyncio.wait_for", wait_for)
    assert asyncio.run(_await_feedback_runtime(SimpleNamespace(workbuddy_reservation=reserve), "workbuddy", request(), timeout=3600)) == "done"
    assert order == ["reserved", "dispatch"]
    order.clear()
    assert asyncio.run(_await_feedback_runtime(None, "codex", request(), timeout=3600)) == "done"
    assert order == ["dispatch"]


@pytest.mark.parametrize("bad", ["unknown", "missing", "normalized"])
def test_harness_denies_before_business_read_and_uses_native_schema(tmp_path, monkeypatch, bad):
    from sap_business_agents_platform import workbuddy_harness as module
    schema = {"type": "object", "properties": {"status": {"const": "completed"}}, "required": ["status"]}
    monkeypatch.setattr("sap_business_agents_platform.acceptance_projection.output_schema", lambda *_: schema)
    # This fixture exercises native transport admission, not the separately
    # tested persisted-evidence finalizer. Both providers now use that finalizer.
    monkeypatch.setattr("sap_business_agents_platform.runtime_harness_result.finalize",
        lambda *_, **__: SimpleNamespace(status="completed", clarification_question=""))
    state, calls, events = {}, [], []
    record = SimpleNamespace(input={}, query="Bound query", started_at=None,
        runtime=SimpleNamespace(model_dump=lambda **_: {"model": "bound"}))
    store = SimpleNamespace(get_run=lambda _: record, get_harness_state=lambda _: state,
        update_harness_state=lambda _, value: state.update(value), append_event=lambda *value: events.append(value),
        get_free_query_session_by_run=lambda _: None, update_run=lambda *_, **__: None)
    broker = SimpleNamespace(open_session=lambda _: "cap", close_session=lambda _: None,
        budget_snapshot=lambda _: {"hard_limit_seconds": 600, "query_seconds_granted": 540, "finalization_seconds_reserved": 60},
        review_deadline=lambda _: None, _elapsed_seconds=lambda _: 0,
        normalizer=SimpleNamespace(normalize_plan=copy.deepcopy))
    async def handle(run_id, token, name, arguments):
        calls.append(name)
        # Simulate Provider normalization losing a filter: execution must recheck.
        return {"ok": True, "validated_plan": {**PLAN, "filters": []}}
    async def cancel(*_, **__):
        return True
    broker.handle, broker.cancel_tools = handle, cancel
    async def run(**kwargs):
        assert kwargs["payload"]["output_schema"] == schema
        assert 599000 <= kwargs["payload"]["timeout_ms"] <= 600000
        assert kwargs["snapshot"] == {"model": "bound"}
        candidate = {**PLAN, "filter": "private"} if bad == "unknown" else {**PLAN, "filters": []} if bad == "missing" else PLAN
        result = await kwargs["tool_handler"]("sap_query_execute", {"plan": candidate})
        assert result["ok"] is False and result["code"].startswith("workbuddy_")
        assert "private" not in json.dumps(result)
        return {"text": '{"status":"completed"}', "output_format": "native_json_schema"}
    supervisor = SimpleNamespace(check_operation=lambda *_: None, run=run, cleanup_deadlines={}, reconcile=lambda: [])
    manager = SimpleNamespace(supervisor=supervisor, bound_snapshot=lambda value, **_: value)
    controller = module.WorkBuddyHarnessController(SimpleNamespace(data_root=tmp_path, max_harness_turns=10), store, broker, manager)
    controller.conversation_context = lambda *_, **__: {}
    controller.validate_result = lambda *_: SimpleNamespace(status="completed")
    asyncio.run(controller.run("owned", "查询采购订单4500001466"))
    assert "sap_query_execute" not in calls
    assert calls == (["sap_query_validate"] if bad == "normalized" else [])
    assert any(event[1] == "workbuddy_tool_rejected" for event in events)
