import asyncio
import copy

import pytest

from sap_business_agents_platform.sample_discovery import SampleDiscoveryContext, SampleDiscoveryError
from tests.test_agent_sample_discovery import setup_broker


def fixture():
    filters = lambda field, name: [{"field": field, "operator": "eq", "value": "{{input." + name + "}}"}]
    nodes = [
        {"step_id": "lines", "entity_set": "Lines", "select_fields": ["PO", "Item", "Line", "Date"],
         "filters": filters("PO", "purchase_order") + filters("Date", "delivery_date")},
        {"step_id": "items", "entity_set": "Items", "select_fields": ["PO", "Item", "Plant"],
         "filters": filters("Plant", "plant"), "filter_from_previous": [
             {"source_step_id": "lines", "source_field": "PO", "field": "PO"},
             {"source_step_id": "lines", "source_field": "Item", "field": "Item"}]},
        {"step_id": "headers", "entity_set": "Headers", "select_fields": ["PO", "Org"],
         "filters": filters("Org", "organization"), "filter_from_previous": [
             {"source_step_id": "items", "source_field": "PO", "field": "PO"}]},
        {"step_id": "unrelated", "entity_set": "Materials", "select_fields": ["Material"], "filters": []}]
    for node in nodes:
        node.update(service_name="API_TEST", odata_version="2.0", http_method="GET")
    manifest = {"execution": {"inputSchema": {"type": "object", "additionalProperties": False,
        "required": ["purchase_order"], "properties": {n: {"type": "string"} for n in
        ["purchase_order", "delivery_date", "organization", "plant"]}},
        "steps": [{"id": "read", "executor": "sap_read", "request": {"plan": {
            "plan_kind": "multi_step", "steps": nodes}}}]}}
    ctx = SampleDiscoveryContext(manifest, {"purchase_order": "PO1", "delivery_date": "2022-11-15",
                                 "organization": "1010"}, 3, selected_fields=["plant"])
    keys = {"Lines": ["PO", "Item", "Line"], "Items": ["PO", "Item"], "Headers": ["PO"]}
    for node in nodes[:3]:
        source = ("API_TEST", "2.0", node["entity_set"])
        ctx.live_keys[source] = keys[node["entity_set"]]
        ctx.live_types[source] = {field: "Edm.String" for field in node["select_fields"]}
    evidence = {}
    def reader(ref):
        return evidence[ref], {"source_type": "sap_live"}
    ctx.evidence_reader = reader
    plans = []
    for node in nodes[:3]:
        plan = {k: v for k, v in node.items() if k != "filter_from_previous"}
        plan["filters"] = ctx.scope_graph.local_filters(ctx.scope_graph.resolve(plan))
        plan.update(top=10, order_by=keys[plan["entity_set"]], schema_version="1.0")
        if node["step_id"] == "items":
            plan["filters"] += [{"field": "PO", "value": "PO1"}, {"field": "Item", "value": "10"}]
        if node["step_id"] == "headers":
            plan["filters"] += [{"field": "PO", "value": "PO1"}]
        plans.append(plan)
    return ctx, evidence, reader, plans


ROWS = [
    [{"PO": "PO1", "Item": "10", "Line": "1", "Date": "2022-11-15"},
     {"PO": "PO1", "Item": "20", "Line": "1", "Date": "2022-11-15"}],
    [{"PO": "PO1", "Item": "10", "Plant": "1710"}],
    [{"PO": "PO1", "Org": "1010"}],
]


def save(ctx, evidence, plan, rows, ref):
    ctx.check_plan(plan)
    ctx.check_stable_keys(plan)
    evidence[ref] = ctx.project_evidence({"ok": True, "rows": rows}, plan=plan)
    ctx.remember(ref, plan=plan)


def test_full_chain_required_before_candidate_visibility_or_suggestion():
    ctx, evidence, reader, plans = fixture()
    assert ctx.readiness()["can_start"]
    assert len(ctx.scope_graph.contract()["nodes"]) == 3
    save(ctx, evidence, plans[0], ROWS[0], "lines")
    save(ctx, evidence, plans[1], ROWS[1], "items")
    assert ctx.public_evidence_rows("items", reader) == [{"PO": "PO1"}]
    assert ctx.deterministic_result("items", reader) is None
    save(ctx, evidence, plans[2], ROWS[2], "headers")
    result = ctx.deterministic_result("items", reader)
    assert result["suggested_inputs"] == {**ctx.supplied_inputs, "plant": "1710"}
    assert set(result["field_sources"]) == {"plant"}
    proof = result["field_sources"]["plant"]["scope_proof"]
    assert proof["verified_inputs"] == ["delivery_date", "organization", "purchase_order"]
    assert len(proof["sources"]) == 3 and len(proof["relations"]) == 2
    assert all(s["row_hash"] and s["row_keys"] for s in proof["sources"])
    assert ctx.public_evidence_rows("items", reader)[0]["Plant"] == "1710"
    assert result["source_complete"] is None


@pytest.mark.parametrize("kind", ["wrong_tuple", "independent_sets", "unknown_step", "lost_local_filter", "fanout", "no_schema"])
def test_invalid_join_rejected_before_read(kind):
    ctx, evidence, reader, plans = fixture()
    save(ctx, evidence, plans[0], ROWS[0], "lines")
    plan = copy.deepcopy(plans[1])
    if kind == "wrong_tuple": plan["filters"][0]["value"] = "PO2"
    if kind == "independent_sets": plan["filters"][1].update(operator="in", value=["10", "20"])
    if kind == "unknown_step": plan["step_id"] = "unrelated"
    if kind == "lost_local_filter":
        plan = copy.deepcopy(plans[0]); plan["filters"].pop()
    if kind == "fanout": plan["fanout"] = True
    if kind == "no_schema": ctx.live_types.clear()
    with pytest.raises(SampleDiscoveryError): ctx.check_plan(plan)


@pytest.mark.parametrize("kind", ["wrong_org", "restricted", "partial", "conflicting_key", "cancelled", "no_header", "wrong_item"])
def test_unproven_rows_never_release_candidates(kind):
    ctx, evidence, reader, plans = fixture()
    for i in range(3): save(ctx, evidence, plans[i], ROWS[i], ["lines", "items", "headers"][i])
    if kind == "wrong_org": evidence["headers"]["rows"][0]["Org"] = "9999"
    if kind == "restricted": evidence["headers"]["rows_redacted"] = True
    if kind == "partial": evidence["headers"]["upstream_incomplete"] = True
    if kind == "conflicting_key": evidence["headers"]["rows"].append({"PO": "PO1", "Org": "9999"})
    if kind == "cancelled": ctx.closed = True
    if kind == "no_header": evidence["headers"]["rows"] = []
    if kind == "wrong_item": evidence["items"]["rows"][0]["Item"] = "99"
    if kind == "conflicting_key":
        with pytest.raises(SampleDiscoveryError, match="sample_relationship_conflict"):
            ctx.public_evidence_rows("items", reader)
    else:
        assert "Plant" not in ctx.public_evidence_rows("items", reader)[0]
    if kind == "conflicting_key":
        with pytest.raises(SampleDiscoveryError, match="sample_relationship_conflict"):
            ctx.deterministic_result("items", reader)
    else:
        assert ctx.deterministic_result("items", reader) is None


def test_broker_masks_preview_and_evidence_read_until_scope_verified(tmp_path):
    async def run():
        _, store, broker, sap = setup_broker(tmp_path)
        ctx, _, _, plans = fixture()
        broker._sample_contexts["run_sample"] = ctx
        token = broker.open_session("run_sample")
        outputs = []
        for index, plan in enumerate(plans):
            sap.execute_plan.return_value = {"ok": True, "rows": ROWS[index]}
            output = await broker.handle("run_sample", token, "sap_query_execute", {"plan": plan})
            assert output["ok"], output
            outputs.append(output)
            if index == 1:
                assert "Plant" not in str(output)
                visible = await broker.handle("run_sample", token, "sap_evidence_read", {
                    "evidence_ref": output["evidence_ref"], "fields": ["Plant"], "offset": 0, "limit": 1})
                assert visible["rows"] == [{}]
        assert ctx.ready_result["suggested_inputs"]["plant"] == "1710"
        assert ctx.read_count == sap.execute_plan.await_count == 3
        visible = await broker.handle("run_sample", token, "sap_evidence_read", {"evidence_ref": outputs[1]["evidence_ref"]})
        assert visible["rows"][0]["Plant"] == "1710"
        assert store.list_harness_tool_calls("run_sample")
    asyncio.run(run())


@pytest.mark.parametrize("provider", ["codex", "workbuddy"])
def test_shared_runtime_produces_same_full_chain_proof(provider, tmp_path):
    from types import SimpleNamespace
    from sap_business_agents_platform.runtime_sample import _discover
    from sap_business_agents_platform.sample_discovery import SAMPLE_MODEL
    async def run():
        settings, store, broker, sap = setup_broker(tmp_path)
        original, _, _, plans = fixture()
        async def turn(service, run_id, ctx, token, binding):
            ctx.live_keys = original.live_keys
            ctx.live_types = original.live_types
            outputs = []
            for index, plan in enumerate(plans):
                sap.execute_plan.return_value = {"ok": True, "rows": ROWS[index]}
                outputs.append(await broker.handle(run_id, token, "sap_query_execute", {"plan": plan}))
            import json
            return json.dumps({"suggestions": [{"input_field": "plant", "source_field": "Plant",
                "value": "1710", "row_indices": [0], "evidence_ref": outputs[1]["evidence_ref"]}]})
        service = SimpleNamespace(settings=settings, store=store, broker=broker, _turns={})
        value = await _discover(service, "run_sample", original.manifest, original.supplied_inputs,
            revision=3, model=SAMPLE_MODEL if provider == "codex" else "deepseek-v4.1-flash",
            started=None, selected_fields=["plant"], driver=SimpleNamespace(provider_id=provider, sample_turn=turn), binding={})
        assert value["status"] == "ready" and value["query_count"] == 3
        assert value["field_sources"]["plant"]["scope_proof"]["verified_inputs"] == ["delivery_date", "organization", "purchase_order"]
        assert value["suggested_inputs"] == {**original.supplied_inputs, "plant": "1710"}
        assert not broker._sample_contexts and not broker._tokens
    asyncio.run(run())
