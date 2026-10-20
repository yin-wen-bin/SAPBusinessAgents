"""Offline discovery admission; no SDK client, model or SAP connection."""
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.runtime_sample import _discover
from sap_business_agents_platform.sample_discovery import (
    SAMPLE_MODEL, SampleDiscoveryContext, SampleDiscoveryError,
)
from tests.test_agent_sample_discovery import context, manifest, plan, setup_broker


def split_scope_manifest(*, same_entity=False):
    value = manifest()
    value["execution"]["inputSchema"]["properties"]["region"] = {"type": "string"}
    original = value["execution"]["steps"][0]["inputMapping"]["plan"]
    other = copy.deepcopy(original)
    other["entity_set"] = original["entity_set"] if same_entity else "Regions"
    other["select_fields"] = ["CompanyCode", "Region"]
    other["filters"] = [{"field": "CompanyCode", "value": "{{input.company_code}}"},
                        {"field": "Region", "value": "{{input.region}}"}]
    value["execution"]["steps"].append({"id": "scope", "executor": "sap", "inputMapping": {"plan": other}})
    return value


@pytest.mark.parametrize("same_entity", [False, True])
def test_readiness_does_not_union_scope_from_different_declarations(same_entity):
    value = split_scope_manifest(same_entity=same_entity)
    before = copy.deepcopy(value)
    ctx = SampleDiscoveryContext(value, {"company_code": "1710", "region": "R1"}, 2)
    readiness = ctx.readiness()
    assert readiness["can_start"] is False
    assert readiness["blocking_fields"] == ["customer"]
    customer = next(item for item in readiness["fields"] if item["field"] == "customer")
    assert customer["reason"] == "cross_source_scope_unproven"
    assert readiness["codes"] == ["sample_cross_source_scope_unproven"]
    assert value == before
    with pytest.raises(SampleDiscoveryError, match="sample_scope_not_preserved"):
        ctx.check_plan(plan())  # Existing execution scope guard remains strict.


def test_valid_single_source_scope_remains_discoverable():
    value = split_scope_manifest()
    original = value["execution"]["steps"][0]["inputMapping"]["plan"]
    original["select_fields"].append("Region")
    original["filters"].append({"field": "Region", "value": "{{input.region}}"})
    ctx = SampleDiscoveryContext(value, {"company_code": "1710", "region": "R1"}, 2)
    assert ctx.readiness()["can_start"] is True
    assert ctx.readiness()["codes"] == []
    actual = plan()
    actual["select_fields"].append("Region")
    actual["filters"].append({"field": "Region", "value": "R1"})
    assert ctx.check_plan(actual) == original


def test_unknown_unmapped_supplied_input_is_not_an_invented_scope_requirement():
    value = manifest()
    value["execution"]["inputSchema"]["properties"]["display_limit"] = {"type": "integer"}
    ctx = SampleDiscoveryContext(value, {"company_code": "1710", "display_limit": 5}, 2)
    assert ctx.readiness()["can_start"] is True
    assert ctx.check_plan(plan())


def test_requested_optional_scope_is_checked_but_unselected_fields_are_not_blockers():
    value = split_scope_manifest()
    value["execution"]["inputSchema"]["properties"]["optional_customer"] = {"type": "string"}
    value["execution"]["steps"][0]["inputMapping"]["plan"]["filters"].append(
        {"field": "Customer", "value": "{{input.optional_customer}}"})
    ctx = SampleDiscoveryContext(value, {"company_code": "1710", "region": "R1"}, 2, selected_fields=["optional_customer"])
    assert ctx.readiness()["blocking_fields"] == ["optional_customer"]
    assert ctx.readiness()["requested_fields"] == ["optional_customer"]


@pytest.mark.parametrize("provider", ["codex", "workbuddy"])
def test_cross_source_preflight_returns_before_runtime_or_broker(tmp_path, provider):
    settings, store, broker, sap = setup_broker(tmp_path)
    service = SimpleNamespace(settings=settings, store=store, broker=broker)
    driver = SimpleNamespace(provider_id=provider, sample_turn=AsyncMock(side_effect=AssertionError("model must not start")))
    result = asyncio.run(_discover(service, "run_sample", split_scope_manifest(),
        {"company_code": "1710", "region": "R1"}, revision=2,
        model=SAMPLE_MODEL if provider == "codex" else "deepseek-v4.1-flash",
        started=None, selected_fields=None, driver=driver, binding={}))
    assert result["status"] == "needs_input"
    assert result["codes"] == ["sample_input_requires_manual_entry", "sample_cross_source_scope_unproven"]
    assert result["input"] == {"company_code": "1710", "region": "R1"}
    assert result["field_sources"] == {} and result["query_count"] == 0
    driver.sample_turn.assert_not_awaited()
    sap.validate_plan.assert_not_awaited()
    sap.execute_plan.assert_not_awaited()
    assert not broker._sample_contexts and not broker._tokens


def test_saved_po_case_requires_declared_three_source_proof_not_single_source_scope():
    root = Path(__file__).resolve().parents[1]
    value = AgentRepository(root / "agents").get("mm-po-gr-status")
    ctx = SampleDiscoveryContext(value, {"schedule_line_delivery_date": "2022-11-15",
        "purchasing_organization": "1010", "purchase_order": "4500001466"}, 1, selected_fields=["plant"])
    readiness = ctx.readiness()
    assert readiness["blocking_fields"] == []
    assert readiness["codes"] == []
    assert ctx.scope_graph.active
    assert [n.step_id for n in ctx.scope_graph.nodes.values()] == [
        "target_schedule_lines", "purchase_order_items", "purchase_order_headers"]
    mapped = {name for item in ctx.plans for _, name, _ in ctx._bindings(item)}
    supplied = set(ctx.supplied_inputs) & mapped
    assert supplied == {"purchase_order", "purchasing_organization", "schedule_line_delivery_date"}
    # Definition-only reproduction, not a reconstruction of lost model arguments.
    assert not any(supplied.issubset({name for _, name, _ in ctx._bindings(item)}) for item in ctx.plans)


@pytest.mark.parametrize("tool", ["sap_query_validate", "sap_query_execute"])
@pytest.mark.parametrize("change,code,path", [
    ({"top": 1000}, "sample_candidate_limit", "/plan/top"),
    ({"filters": []}, "sample_scope_not_preserved", "/plan/filters"),
    ({"order_by": ["CompanyCode asc"]}, "invalid_order_by_expression", "/plan/order_by"),
])
def test_early_denial_is_safe_persisted_feedback_and_never_an_external_read(tmp_path, tool, change, code, path):
    async def scenario():
        _, store, broker, sap = setup_broker(tmp_path)
        ctx = context()
        broker._sample_contexts["run_sample"] = ctx
        ctx.on_progress = lambda value: store.update_harness_state("run_sample", {"sample_execution": value})
        token = broker.open_session("run_sample")
        output = await broker.handle("run_sample", token, tool, {"plan": {**plan(), **change}, "query": "PRIVATE-SENTINEL"})
        assert output["ok"] is False and output["code"] == code
        assert output["validation_issues"] == [{"code": code, "path": path, "constraint": "sample_scope"}]
        state = store.get_harness_state("run_sample")["sample_execution"]
        assert state["denied_tool_count"] == 1
        assert state["last_denied_tool"] == {"tool": tool, "code": code}
        assert state["failure_codes"] == [code]
        events = [event for event in store.events_after("run_sample") if event.type == "sample_tool_denied"]
        assert len(events) == 1
        assert "PRIVATE-SENTINEL" not in str(events) + str(state) + str(output)
        assert not store.list_harness_tool_calls("run_sample")
        assert ctx.read_count == 0
        sap.validate_plan.assert_not_awaited()
        sap.execute_plan.assert_not_awaited()
    asyncio.run(scenario())


def test_unknown_denial_metadata_is_redacted_and_closed_session_does_not_update_state():
    ctx = context()
    output = ctx.denied_tool("PRIVATE-SENTINEL", "SECRET-CODE")
    assert output["code"] == "sample_request_denied"
    assert ctx.last_denied_tool == {"tool": "unrecognized_tool", "code": "sample_request_denied"}
    assert "PRIVATE-SENTINEL" not in str(ctx.diagnostics()) + str(output)
    ctx.closed = True
    before = copy.deepcopy(ctx.diagnostics())
    ctx.denied_tool("sap_query_execute", "sample_discovery_closed")
    assert ctx.denied_tool_count == before["denied_tool_count"]
    assert ctx.last_denied_tool == before["last_denied_tool"]


def test_successful_followup_keeps_denial_history_without_turning_valid_result_into_failure():
    ctx = context()
    ctx.denied_tool("sap_query_validate", "sample_scope_not_preserved")
    ctx.tool_completed("tool_catalog_search", {"ok": True}, lambda _: None)
    assert ctx.validation_issues == []
    assert ctx.last_denied_tool["code"] == "sample_scope_not_preserved"
    assert ctx.diagnostics()["denied_tool_count"] == 1
    assert ctx.result("ready")["codes"] == []
