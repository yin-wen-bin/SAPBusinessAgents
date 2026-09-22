from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.harness import HarnessToolBroker, _mcp_overrides
from sap_business_agents_platform.models import RunCreate, RunMode
from sap_business_agents_platform.sample_discovery import (
    SAMPLE_MODEL, SampleDiscoveryContext, SampleDiscoveryError, SampleDiscoveryService,
)


def manifest() -> dict:
    return {"id": "test-sample", "execution": {"inputSchema": {
        "type": "object", "additionalProperties": False,
        "required": ["company_code", "customer"], "properties": {
            "company_code": {"type": "string"}, "customer": {"type": "string"},
            "receipt_reference": {"type": "string", "x-sapba-sensitive": True},
        }}, "steps": [{"id": "read", "executor": "sap", "inputMapping": {"plan": {
            "service_name": "API_TEST", "odata_version": "2.0", "entity_set": "Items", "http_method": "GET",
            "select_fields": ["CompanyCode", "Customer", "Item", "FinancialAccountType", "PayerName"],
            "order_by": ["CompanyCode", "Item"], "filters": [
                {"field": "CompanyCode", "operator": "eq", "value": "{{input.company_code}}"},
                {"field": "Customer", "operator": "eq", "value": "{{input.customer}}"},
                {"field": "FinancialAccountType", "operator": "eq", "value": "D"},
            ]}}}]}}


def plan() -> dict:
    return {"schema_version": "1.0", "service_name": "API_TEST", "odata_version": "2.0",
            "entity_set": "Items", "http_method": "GET", "select_fields": ["CompanyCode", "Customer", "Item", "FinancialAccountType"],
            "order_by": ["CompanyCode", "Item"], "top": 100,
            "filters": [{"field": "CompanyCode", "operator": "eq", "value": "1710"},
                        {"field": "FinancialAccountType", "operator": "eq", "value": "D"}]}


def context() -> SampleDiscoveryContext:
    return SampleDiscoveryContext(manifest(), {"company_code": "1710"}, 2)


def proof(*, value="C1", ref="ev_a", field="Customer", name="customer", indexes=None) -> dict:
    return {"input_field": name, "value": value, "evidence_ref": ref, "source_field": field,
            "row_indices": indexes if indexes is not None else [0]}


def reader(ref: str):
    return ({"rows": [{"CompanyCode": "1710", "Customer": "C1", "Item": "1", "FinancialAccountType": "D"},
                      {"CompanyCode": "1710", "Customer": "C2", "Item": "2", "FinancialAccountType": "D"}]}, {"source_type": "sap_live"})


def test_context_accepts_bounded_declared_source_and_preserves_explicit_input():
    ctx = context()
    ctx.check_plan(plan())
    ctx.remember("ev_a", plan=plan())
    result = ctx.validate_result({"suggestions": [proof()]}, reader)
    assert result["status"] == "ready"
    assert result["input"] == {"company_code": "1710", "customer": "C1"}
    assert result["bounded_discovery"] is True
    assert result["source_complete"] is None
    assert result["field_sources"]["customer"]["row_hashes"]
    assert result["revision"] == 2
    assert result["model"] == SAMPLE_MODEL


@pytest.mark.parametrize('order', [['CompanyCode asc'], ['Item desc'], ['Item;drop'], [None]])
def test_sample_sort_contract_matches_provider(order):
    from sap_business_agents_platform.sap_read.embedded_odata import _IDENTIFIER
    assert not all(isinstance(item, str) and _IDENTIFIER.fullmatch(item) for item in order)
    with pytest.raises(SampleDiscoveryError, match='invalid_order_by_expression'):
        context().check_plan({**plan(), 'order_by': order})


@pytest.mark.parametrize('typed_schema', [True, False])
def test_sap_date_sample_is_iso_after_evidence_validation(typed_schema):
    m = manifest()
    m['execution']['inputSchema']['properties']['customer'] = {'type': 'string', **({'format': 'date'} if typed_schema else {})}
    ctx = SampleDiscoveryContext(m, {'company_code': '1710'}, 2, selected_fields=['customer'])
    ctx.record_schema({'ok': True, 'data': {'schema_authority': True, 'entities': [
        {**{key: plan()[key] for key in ('service_name', 'odata_version', 'entity_set')}, 'key_fields': ['Item']}],
        'fields': [{**{key: plan()[key] for key in ('service_name', 'odata_version', 'entity_set')},
                    'field_name': 'Customer', 'data_type': 'Edm.DateTime'}]}})
    ctx.remember('ev_a', plan=plan())
    def dates(ref):
        raw, meta = reader(ref)
        raw['rows'][0]['Customer'] = '/Date(1507420800000)/'
        return raw, meta
    result = ctx.deterministic_result('ev_a', dates)
    assert result['input']['customer'] == '2017-10-08'
    assert result['field_sources']['customer']['normalization'] == 'sap_date_to_iso_date'
    assert dates('ev_a')[0]['rows'][0]['Customer'] == '/Date(1507420800000)/'
    with pytest.raises(SampleDiscoveryError, match='sample_value_not_in_evidence'):
        ctx.validate_result({'suggestions': [proof(value='2026-01-01')]}, dates)


def test_selected_optional_and_partial_inputs_do_not_fill_unselected_fields():
    m = manifest()
    m['execution']['inputSchema']['properties']['item'] = {'type': 'string'}
    m['execution']['steps'][0]['inputMapping']['plan']['filters'].append({'field': 'Item', 'operator': 'eq', 'value': '{{input.item}}'})
    ctx = SampleDiscoveryContext(m, {'company_code': '1710'}, 2, selected_fields=['item'])
    ctx.live_keys[('API_TEST', '2.0', 'Items')] = ['Item']
    ctx.remember('ev_a', plan=plan())
    result = ctx.deterministic_result('ev_a', reader)
    assert result['status'] == 'needs_input'
    assert result['input'] == {'company_code': '1710', 'item': '1'}
    assert result['missing_fields'] == ['customer']
    with pytest.raises(SampleDiscoveryError, match='sample_field_not_selected'):
        ctx.validate_result({'suggestions': [proof()]}, reader)


@pytest.mark.parametrize('selected', [[], ['customer', 'customer'], ['receipt_reference'], ['unknown'], ['company_code']])
def test_invalid_sample_selection_rejected(selected):
    with pytest.raises(SampleDiscoveryError, match='sample_selected_fields_invalid'):
        SampleDiscoveryContext(manifest(), {'company_code': '1710'}, 2, selected_fields=selected)


def test_independent_deadline_and_finalization(monkeypatch):
    ctx = context()
    # This starting value exposes float subtraction rounding just below 600.
    # Both the hard cutoff and remaining budget must use the same deadline.
    ctx.started = 4193799.9403939885
    monkeypatch.setattr('sap_business_agents_platform.sample_discovery.time.monotonic', lambda: ctx.started + 540)
    with pytest.raises(SampleDiscoveryError, match='sample_finalization_only'):
        ctx.allow_tool('sap_schema_get', {})
    assert ctx.remaining() == 60
    assert ctx.phase == 'finalizing'
    monkeypatch.setattr('sap_business_agents_platform.sample_discovery.time.monotonic', lambda: ctx.started + 600)
    with pytest.raises(SampleDiscoveryError, match='sample_discovery_timeout'):
        ctx.allow_tool('sap_evidence_assess', {})


def test_deterministic_selection_requires_verified_coherent_public_row():
    ctx = context()
    ctx.live_keys[('API_TEST', '2.0', 'Items')] = ['CompanyCode', 'Item']
    ctx.remember('ev_a', plan=plan())
    result = ctx.deterministic_result('ev_a', reader)
    assert result['status'] == 'ready' and result['selection_method'] == 'deterministic'
    assert result['input'] == {'company_code': '1710', 'customer': 'C1'}
    assert result['field_sources']['customer']['row_indices'] == [0]
    def incomplete(ref):
        raw, meta = reader(ref)
        return {**raw, 'upstream_incomplete': True}, meta
    assert ctx.deterministic_result('ev_a', incomplete) is None
    ctx.properties['customer']['type'] = 'array'
    assert ctx.deterministic_result('ev_a', reader) is None


def test_deterministic_selection_skips_bad_values_and_never_overwrites_scope():
    ctx = context()
    ctx.live_keys[('API_TEST', '2.0', 'Items')] = ['CompanyCode', 'Item']
    ctx.remember('ev_a', plan=plan())
    def bad_first(ref):
        raw, meta = reader(ref)
        raw['rows'][0]['Customer'] = None
        return raw, meta
    assert ctx.deterministic_result('ev_a', bad_first)['input']['customer'] == 'C2'
    def wrong_scope(ref):
        raw, meta = reader(ref)
        for row in raw['rows']: row['CompanyCode'] = 'OTHER'
        return raw, meta
    assert ctx.deterministic_result('ev_a', wrong_scope) is None


@pytest.mark.parametrize(("change", "code"), [
    ({"http_method": "POST"}, "sample_bounded_get_required"),
    ({"steps": [plan()]}, "sample_bounded_get_required"),
    ({"top": 101}, "sample_candidate_limit"),
    ({"top": None}, "sample_candidate_limit"),
    ({"order_by": []}, "sample_stable_order_required"),
    ({"service_name": "OTHER"}, "sample_source_outside_draft"),
    ({"select_fields": ["PayerName"]}, "sample_private_fields_forbidden"),
    ({"select_fields": ["*"]}, "sample_private_fields_forbidden"),
    ({"filters": []}, "sample_scope_not_preserved"),
    ({"filters": [{"field": "CompanyCode", "operator": "eq", "value": "9999"}]}, "sample_scope_not_preserved"),
])
def test_query_scope_and_bounds_fail_closed(change, code):
    with pytest.raises(SampleDiscoveryError, match=code):
        context().check_plan({**plan(), **change})


@pytest.mark.parametrize("tool", ["safe_compute", "external_tool_execute", "tool_discovery_search", "sap_month_end_status_assess"])
def test_sample_purpose_disallows_external_or_business_rule_tools(tool):
    with pytest.raises(SampleDiscoveryError, match="sample_tool_not_allowed"):
        context().allow_tool(tool, {})


def test_complete_skill_catalog_remains_available_without_a_runtime_whitelist():
    context().allow_tool("list_all_approved_skills", {})


def test_limit_does_not_count_metadata_and_caps_actual_reads_and_concurrency():
    ctx = context()
    for _ in range(20):
        ctx.allow_tool("sap_catalog_search", {})
    assert ctx.read_count == 0
    ctx.begin_read()
    ctx.begin_read()
    with pytest.raises(SampleDiscoveryError, match="sample_read_budget_exceeded"):
        ctx.begin_read()
    ctx.calls_in_flight = 0
    for _ in range(8):
        ctx.begin_read()
        ctx.calls_in_flight = 0
    with pytest.raises(SampleDiscoveryError, match="sample_read_budget_exceeded"):
        ctx.allow_tool("sap_query_execute", {"plan": plan()})


def test_stable_keys_accept_complete_sortable_key_metadata_even_when_other_fields_are_truncated():
    ctx = context()
    with pytest.raises(SampleDiscoveryError, match="sample_live_stable_key_unproven"):
        ctx.check_stable_keys(plan())
    source = {"service_name": "API_TEST", "odata_version": "2.0", "entity_set": "Items"}
    schema = {"ok": True, "data": {"schema_authority": True, "fields_truncated": True, "entities": [
        {**source, "key_fields": ["CompanyCode", "Item"]}], "fields": [
            {**source, "field_name": "CompanyCode", "selectable": True, "sortable": True},
            {**source, "field_name": "Item", "selectable": True, "sortable": True},
        ]}}
    ctx.record_schema(schema)
    ctx.check_stable_keys(plan())
    assert ctx.stable_fields[("API_TEST", "2.0", "Items")] == ["CompanyCode", "Item"]
    with pytest.raises(SampleDiscoveryError, match="sample_live_stable_key_unproven"):
        ctx.check_stable_keys({**plan(), "order_by": ["CompanyCode"]})


def test_truncated_schema_does_not_trust_missing_or_unsortable_key_metadata():
    source = {"service_name": "API_TEST", "odata_version": "2.0", "entity_set": "Items"}
    for fields in (
        [{**source, "field_name": "CompanyCode", "selectable": True, "sortable": True}],
        [{**source, "field_name": "CompanyCode", "selectable": True, "sortable": True},
         {**source, "field_name": "Item", "selectable": True, "sortable": False}],
    ):
        ctx = context()
        ctx.record_schema({"ok": True, "data": {"schema_authority": True, "fields_truncated": True,
            "entities": [{**source, "key_fields": ["CompanyCode", "Item"]}], "fields": fields}})
        with pytest.raises(SampleDiscoveryError, match="sample_live_stable_key_unproven"):
            ctx.check_stable_keys(plan())


def test_unsortable_entity_key_can_use_stable_requested_input_projection():
    ctx = context()
    source = {"service_name": "API_TEST", "odata_version": "2.0", "entity_set": "Items"}
    ctx.record_schema({"ok": True, "data": {"schema_authority": True, "fields_truncated": False,
        "entities": [{**source, "key_fields": ["ID"]}], "fields": [
            {**source, "field_name": "ID", "selectable": True, "sortable": False},
            {**source, "field_name": "CompanyCode", "selectable": True, "sortable": True},
            {**source, "field_name": "Customer", "selectable": True, "sortable": True},
            {**source, "field_name": "FinancialAccountType", "selectable": True, "sortable": True},
        ]}})
    projection = {**plan(), "select_fields": ["CompanyCode", "Customer", "FinancialAccountType"],
                  "order_by": ["CompanyCode", "Customer"]}
    assert ctx.check_stable_keys(projection) == ["CompanyCode", "Customer"]
    assert ctx.stable_fields[("API_TEST", "2.0", "Items")] == ["CompanyCode", "Customer"]


def test_simple_array_values_keep_per_row_evidence():
    data = manifest()
    data["execution"]["inputSchema"]["properties"]["customer"] = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50}
    ctx = SampleDiscoveryContext(data, {"company_code": "1710"}, 0)
    ctx.remember("ev_a", plan=plan())
    result = ctx.validate_result({"suggestions": [proof(value=["C1", "C2"], indexes=[0, 1])]}, reader)
    assert result["status"] == "ready"
    assert result["input"]["customer"] == ["C1", "C2"]
    assert len(result["field_sources"]["customer"]["row_hashes"]) == 2


def test_real_ap_schema_resolves_public_direct_required_fields():
    actual = json.loads((Path(__file__).resolve().parents[1] / "agents/FI/ap-payment/agent.json").read_text(encoding="utf-8"))
    no_scope = SampleDiscoveryContext(actual, {"as_of": "2026-09-09"}, 1)
    assert no_scope.missing_fields() == ["company_code", "supplier"]
    assert "company_code" in no_scope.preflight_gaps()
    scoped = SampleDiscoveryContext(actual, {"as_of": "2026-09-09", "company_code": "1710"}, 1)
    assert scoped.missing_fields() == ["supplier"]
    assert scoped.preflight_gaps() == []
    assert "ap_payment_scopes" not in scoped.required_fields


def test_ambiguous_public_oneof_requires_manual_branch_selection():
    data = manifest()
    schema = data["execution"]["inputSchema"]
    schema["properties"]["mode"] = {"type": "string", "enum": ["first", "second"]}
    schema["oneOf"] = [{"properties": {"mode": {"const": value}}, "required": ["mode"]} for value in ["first", "second"]]
    ctx = SampleDiscoveryContext(data, {"company_code": "1710"}, 0)
    assert "mode" in ctx.preflight_gaps()
    selected = SampleDiscoveryContext(data, {"company_code": "1710", "mode": "first"}, 0)
    assert selected.conditional_gaps == []


def test_odata_date_cutoff_is_not_compared_lexically():
    from sap_business_agents_platform.sample_discovery import _matches_filter
    from datetime import datetime, timezone
    future_ms = int(datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    past_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    constraint = {"field": "PostingDate", "operator": "le", "value": "2026-09-09", "value_type": "date_end"}
    assert _matches_filter({"PostingDate": f"/Date({past_ms})/"}, constraint)
    assert not _matches_filter({"PostingDate": f"/Date({future_ms})/"}, constraint)
    assert not _matches_filter({"PostingDate": "unknown"}, constraint)


@pytest.mark.parametrize(("suggestion", "code"), [
    (proof(value="FAKE"), "sample_value_not_in_evidence"),
    (proof(ref="ev_elsewhere"), "sample_evidence_unknown"),
    (proof(field="Item"), "sample_field_mapping_unproven"),
    (proof(indexes=[99]), "sample_row_reference_invalid"),
    (proof(indexes=[0, 0]), "sample_duplicate_row_reference"),
    (proof(name="receipt_reference"), "sample_input_overwrite_or_private"),
    (proof(name="company_code", field="CompanyCode", value="1710"), "sample_input_overwrite_or_private"),
])
def test_forged_values_unknown_refs_and_private_values_rejected(suggestion, code):
    ctx = context()
    ctx.remember("ev_a", plan=plan())
    with pytest.raises(SampleDiscoveryError, match=code):
        ctx.validate_result({"suggestions": [suggestion]}, reader)


def test_linked_scalar_fields_cannot_be_combined_from_unrelated_rows():
    ctx = SampleDiscoveryContext(manifest(), {}, 0)
    query = plan()
    query["filters"] = [query["filters"][1]]
    ctx.remember("ev_a", plan=query)
    with pytest.raises(SampleDiscoveryError, match="sample_combination_unproven"):
        ctx.validate_result({"suggestions": [proof(), proof(name="company_code", field="CompanyCode", value="1710", indexes=[1])]}, reader)


def test_linked_scalar_fields_can_be_combined_across_sources_with_shared_input_proof():
    data = manifest()
    schema = data["execution"]["inputSchema"]
    schema["required"] = ["company_code", "controlling_area", "cost_center", "fiscal_year", "planning_category"]
    schema["properties"] = {name: {"type": "string"} for name in schema["required"]}
    master = {
        "service_name": "API_MASTER", "odata_version": "2.0", "entity_set": "CostCenters", "http_method": "GET",
        "select_fields": ["CompanyCode", "ControllingArea", "CostCenter"],
        "order_by": ["CompanyCode", "ControllingArea", "CostCenter"], "top": 100,
        "filters": [
            {"field": "CompanyCode", "operator": "eq", "value": "{{input.company_code}}"},
            {"field": "ControllingArea", "operator": "eq", "value": "{{input.controlling_area}}"},
            {"field": "CostCenter", "operator": "eq", "value": "{{input.cost_center}}"},
        ],
    }
    planning = {
        "service_name": "API_PLAN", "odata_version": "2.0", "entity_set": "PlanItems", "http_method": "GET",
        "select_fields": ["CompanyCode", "ControllingArea", "CostCenter", "FiscalYear", "PlanningCategory"],
        "order_by": ["CompanyCode", "ControllingArea", "CostCenter", "FiscalYear", "PlanningCategory"], "top": 100,
        "filters": [
            {"field": "CompanyCode", "operator": "eq", "value": "{{input.company_code}}"},
            {"field": "ControllingArea", "operator": "eq", "value": "{{input.controlling_area}}"},
            {"field": "CostCenter", "operator": "eq", "value": "{{input.cost_center}}"},
            {"field": "FiscalYear", "operator": "eq", "value": "{{input.fiscal_year}}"},
            {"field": "PlanningCategory", "operator": "eq", "value": "{{input.planning_category}}"},
        ],
    }
    data["execution"]["steps"] = [
        {"id": "master", "executor": "sap", "inputMapping": {"plan": master}},
        {"id": "plan", "executor": "sap", "inputMapping": {"plan": planning}},
    ]
    ctx = SampleDiscoveryContext(data, {"company_code": "1710"}, 0)
    master_execution = {**master, "filters": [{"field": "CompanyCode", "operator": "eq", "value": "1710"}]}
    planning_execution = {**planning, "filters": [{"field": "CompanyCode", "operator": "eq", "value": "1710"}]}
    ctx.evidence_sources = {
        "ev_master": {"plan": master_execution, "key_fields": ["CompanyCode", "ControllingArea", "CostCenter"]},
        "ev_plan": {"plan": planning_execution, "key_fields": ["CompanyCode", "ControllingArea", "CostCenter", "FiscalYear", "PlanningCategory"]},
    }
    evidence = {
        "ev_master": {"rows": [{"CompanyCode": "1710", "ControllingArea": "A000", "CostCenter": "C100"}]},
        "ev_plan": {"rows": [{"CompanyCode": "1710", "ControllingArea": "A000", "CostCenter": "C100",
                                 "FiscalYear": "2026", "PlanningCategory": "PLAN"}]},
    }
    linked_reader = lambda ref: (evidence[ref], {"source_type": "sap_live"})
    result = ctx.validate_result({"suggestions": [
        proof(name="controlling_area", field="ControllingArea", value="A000", ref="ev_master"),
        proof(name="cost_center", field="CostCenter", value="C100", ref="ev_master"),
        proof(name="fiscal_year", field="FiscalYear", value="2026", ref="ev_plan"),
        proof(name="planning_category", field="PlanningCategory", value="PLAN", ref="ev_plan"),
    ]}, linked_reader)
    assert result["status"] == "ready"
    assert set(result["evidence_refs"]) == {"ev_master", "ev_plan"}

    evidence["ev_plan"]["rows"][0]["CostCenter"] = "OTHER"
    with pytest.raises(SampleDiscoveryError, match="sample_combination_unproven"):
        ctx.validate_result({"suggestions": [
            proof(name="controlling_area", field="ControllingArea", value="A000", ref="ev_master"),
            proof(name="cost_center", field="CostCenter", value="C100", ref="ev_master"),
            proof(name="fiscal_year", field="FiscalYear", value="2026", ref="ev_plan"),
            proof(name="planning_category", field="PlanningCategory", value="PLAN", ref="ev_plan"),
        ]}, linked_reader)


def test_empty_result_requires_manual_input_not_fake_default():
    data = manifest()
    data["execution"]["inputSchema"]["properties"]["customer"]["default"] = "FAKE"
    ctx = SampleDiscoveryContext(data, {"company_code": "1710"}, 0)
    assert "FAKE" not in ctx.prompt()
    result = ctx.validate_result({"suggestions": []}, reader)
    assert result["status"] == "needs_input"
    assert result["input"] == {"company_code": "1710"}
    assert result["missing_fields"] == ["customer"]


def test_nested_and_sensitive_missing_inputs_require_manual_without_sap():
    data = manifest()
    schema = data["execution"]["inputSchema"]
    schema["required"].extend(["demand_items", "receipt_reference"])
    schema["properties"]["demand_items"] = {"type": "array", "items": {"type": "object"}}
    ctx = SampleDiscoveryContext(data, {"company_code": "1710"}, 0)
    assert ctx.preflight_gaps() == ["demand_items", "receipt_reference"]
    with pytest.raises(SampleDiscoveryError, match="sample_private_or_unknown_input"):
        SampleDiscoveryContext(data, {"receipt_reference": "SECRET"}, 0)


def test_private_rows_and_diagnostics_excluded_before_evidence_persistence():
    projected = context().project_evidence({"results": [{"CompanyCode": "1710", "Customer": "C1", "FinancialAccountType": "D", "PayerName": "PRIVATE", "BankAccount": "FULL_ACCOUNT"}],
                                            "internal_path": "PRIVATE_FILE"}, plan=plan())
    assert projected["rows"] == [{"CompanyCode": "1710", "Customer": "C1", "FinancialAccountType": "D"}]
    assert "PRIVATE" not in json.dumps(projected)
    assert projected["source_complete"] is False


def test_out_of_scope_returned_rows_are_not_saved_as_samples():
    projected = context().project_evidence({"results": [{"CompanyCode": "9999", "Customer": "OTHER", "FinancialAccountType": "D"}]}, plan=plan())
    assert projected["rows"] == []
    assert projected["upstream_incomplete"] is True


def test_returned_scope_is_reverified_before_field_acceptance():
    ctx = context()
    ctx.remember("ev_a", plan=plan())
    with pytest.raises(SampleDiscoveryError, match="sample_returned_scope_mismatch"):
        ctx.validate_result({"suggestions": [proof()]}, lambda ref: ({"rows": [{"CompanyCode": "9999", "Customer": "C1", "FinancialAccountType": "D"}]}, {"source_type": "sap_live"}))


def setup_broker(tmp_path):
    settings = Settings(repository_root=Path(__file__).resolve().parents[1], data_root=tmp_path / "data", draft_root=tmp_path / "drafts")
    store = RunStore(settings.database_path)
    store.create_run("run_sample", RunCreate(mode=RunMode.free_query, query="Find sample"), runtime={
        "provider_id": "codex", "sdk_id": "openai-codex", "version": "0.147.0", "model": SAMPLE_MODEL,
        "reasoning_effort": "medium", "reasoning_effort_source": "sdk_default", "configuration_digest": "checked-sol-medium",
    })
    sap = SimpleNamespace(validate_plan=AsyncMock(return_value={"ok": True}),
                          execute_plan=AsyncMock(return_value={"ok": True, "results": [{"CompanyCode": "1710", "Customer": "C1", "Item": "1", "FinancialAccountType": "D", "PayerName": "PRIVATE"}]}))
    skills = SimpleNamespace(list_all_approved_skills=lambda: [])
    broker = HarnessToolBroker(settings, store, sap, skills)
    return settings, store, broker, sap


def test_broker_sample_mode_blocks_reads_outside_scope_and_preserves_normal_mode(tmp_path):
    async def scenario():
        settings, store, broker, sap = setup_broker(tmp_path)
        ctx = context()
        ctx.live_keys[("API_TEST", "2.0", "Items")] = ["CompanyCode", "Item"]
        broker._sample_contexts["run_sample"] = ctx
        token = broker.open_session("run_sample")
        denied = await broker.handle("run_sample", token, "sap_query_execute", {"plan": {**plan(), "top": 1000}})
        assert denied["ok"] is False
        assert sap.execute_plan.await_count == 0
        output = await broker.handle("run_sample", token, "sap_query_execute", {"plan": plan()})
        assert output["ok"] is True
        assert ctx.read_count == 1 and ctx.calls_in_flight == 0
        assert sap.execute_plan.await_count == 1
        raw, _ = broker._read_evidence("run_sample", output["evidence_ref"])
        assert "PRIVATE" not in json.dumps(raw)
        assert output["evidence_ref"] in ctx.evidence_sources
        replay = await broker.handle("run_sample", token, "sap_query_execute", {"plan": plan()})
        assert replay["idempotent_replay"] is True
        assert ctx.read_count == 1 and ctx.calls_in_flight == 0
    asyncio.run(scenario())


def test_model_override_is_rejected_before_runtime(tmp_path):
    settings, store, broker, _ = setup_broker(tmp_path)
    service = SampleDiscoveryService(settings, store, broker)
    with pytest.raises(SampleDiscoveryError, match="sample_model_must_be_gpt_5_6_sol"):
        asyncio.run(service.discover("run_sample", manifest(), {}, revision=0, model="gpt-6-astra"))


def test_discovery_mcp_does_not_launch_external_tool_server(tmp_path):
    settings, *_ = setup_broker(tmp_path)
    overrides = _mcp_overrides(settings, "run_sample", "capability", "python", allow_discovery=False)
    assert not any("sap_tool_discovery.enabled=true" in item for item in overrides)
    assert any("sap_business_agents.enabled=true" in item for item in overrides)


def test_runtime_receives_exact_sol_and_no_web_and_errors_do_not_leak_text(tmp_path):
    settings, store, broker, _ = setup_broker(tmp_path)
    runtime = AsyncMock()
    runtime._client = None
    runtime.__aenter__.return_value = runtime
    runtime.thread_start.side_effect = RuntimeError("PRIVATE BANK VALUE")
    with patch("sap_business_agents_platform.harness._safe_codex", return_value=runtime) as factory:
        result = asyncio.run(SampleDiscoveryService(settings, store, broker).discover("run_sample", manifest(), {"company_code": "1710"}, revision=0))
    assert runtime.thread_start.call_args.kwargs["model"] == SAMPLE_MODEL
    assert factory.call_args.kwargs["allow_web"] is False
    assert result["codes"] == ["sample_runtime_unavailable"]
    assert "PRIVATE" not in json.dumps(result)
    assert not broker._sample_contexts


def test_isolated_runtime_projects_only_validated_public_cells(tmp_path):
    settings, store, broker, sap = setup_broker(tmp_path)
    runtime = AsyncMock()
    runtime._client = None
    runtime.__aenter__.return_value = runtime
    thread = AsyncMock()
    thread.id = "sample_thread"
    runtime.thread_start.return_value = thread
    turn = SimpleNamespace(interrupt=AsyncMock())

    async def stream():
        ctx = broker._sample_contexts["run_sample"]
        ctx.live_keys[("API_TEST", "2.0", "Items")] = ["CompanyCode", "Item"]
        response = await broker.handle("run_sample", broker._tokens["run_sample"], "sap_query_execute", {"plan": plan()})
        payload = {"suggestions": [proof(ref=response["evidence_ref"])]}
        item = SimpleNamespace(model_dump=lambda **kw: {"type": "agentMessage", "text": json.dumps(payload)})
        yield SimpleNamespace(method="item/completed", payload=SimpleNamespace(item=item))

    turn.stream = stream
    thread.turn.return_value = turn
    with patch("sap_business_agents_platform.harness._safe_codex", return_value=runtime):
        result = asyncio.run(SampleDiscoveryService(settings, store, broker).discover("run_sample", manifest(), {"company_code": "1710"}, revision=3))
    assert result["status"] == "ready"
    assert result["input"]["customer"] == "C1"
    assert result["query_count"] == 1
    assert result['selection_method'] == 'deterministic'
    assert thread.turn.call_args.kwargs["model"] == SAMPLE_MODEL
    assert thread.turn.call_args.kwargs["effort"] == "medium"
    assert store.get_harness_state("run_sample")["time_budget"]["hard_limit_seconds"] == 600
    assert not broker._tokens and not broker._sample_contexts


def test_deterministic_result_closes_sdk_queue_before_unregister(tmp_path):
    from openai_codex._message_router import MessageRouter
    settings, store, broker, sap = setup_broker(tmp_path)
    runtime = AsyncMock()
    router = MessageRouter()
    runtime._client = SimpleNamespace(_sync=SimpleNamespace(_proc=None, _router=router))
    runtime.__aenter__.return_value = runtime
    thread = AsyncMock()
    thread.id = 'thread-owned'
    runtime.thread_start.return_value = thread
    turn = SimpleNamespace(interrupt=AsyncMock())
    unregistered = []
    async def stream():
        router.register_turn('owned-turn')
        ctx = broker._sample_contexts['run_sample']
        ctx.live_keys[('API_TEST', '2.0', 'Items')] = ['CompanyCode', 'Item']
        await broker.handle('run_sample', broker._tokens['run_sample'], 'sap_query_execute', {'plan': plan()})
        try:
            yield await asyncio.to_thread(router.next_turn_notification, 'owned-turn')
        finally:
            unregistered.append(True)
            router.unregister_turn('owned-turn')
    turn.stream = stream
    thread.turn.return_value = turn
    with patch('sap_business_agents_platform.harness._safe_codex', return_value=runtime):
        result = asyncio.run(SampleDiscoveryService(settings, store, broker).discover('run_sample', manifest(), {'company_code': '1710'}, revision=1))
    assert result['status'] == 'ready'
    assert unregistered == [True]
    assert not broker._tokens
