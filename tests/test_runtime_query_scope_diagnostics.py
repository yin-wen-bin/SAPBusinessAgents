"""Saved-evidence contract repairs; synthetic inputs and zero external reads."""
import asyncio
import copy
import json

import pytest

from sap_business_agents_platform.runtime_contract import RuntimeContractError
from sap_business_agents_platform.runtime_query_contract import ReadScope, plans, preserve_grounding


SOURCE = {"service_name": "S", "odata_version": "2.0", "entity_set": "Schedule"}
INPUTS = {"purchase_order": "4500000001", "schedule_line_delivery_date": "2022-11-15"}
PLAN = {**SOURCE, "filters": [{"field": "PurchaseOrder", "value": "4500000001"},
    {"field": "ScheduleLineDeliveryDate", "operator": "eq", "value": "2022-11-15T00:00:00",
     "value_type": "Edm.DateTime"}], "top": 1}


def metadata(**updates):
    return {"ok": True, "data": {"schema_authority": True, "fields_truncated": False,
        "service": {k: SOURCE[k] for k in ("service_name", "odata_version")},
        "fields": [{"entity_set": "Schedule", "field_name": "PurchaseOrder", "data_type": "Edm.String"},
            {"entity_set": "Schedule", "field_name": "ScheduleLineDeliveryDate",
             "data_type": "Edm.DateTime", "display_format": "Date"}], **updates}}


def scope(response=None, inputs=None):
    value = ReadScope("Frozen public inputs", inputs or INPUTS)
    value.record_schema(response or metadata())
    return value


def issues(value, plan):
    with pytest.raises(RuntimeContractError) as failure:
        value.check(plan)
    assert failure.value.code == "runtime_query_scope_rejected"
    return failure.value.detail["validation_issues"]


def test_authoritative_date_only_midnight_is_equal_without_rewriting_anything():
    from sap_business_agents_platform.sap_read.embedded_odata import EmbeddedODataProvider
    original_plan, original_inputs, original_schema = copy.deepcopy(PLAN), copy.deepcopy(INPUTS), metadata()
    value = scope(original_schema, original_inputs)
    value.check(original_plan)
    value.check({**PLAN, "filters": [PLAN["filters"][0], {**PLAN["filters"][1], "value": "2022-11-15"}]})
    value.check({**PLAN, "filters": [PLAN["filters"][0], {**PLAN["filters"][1], "operator": "in",
        "value": ["2022-11-15", "2022-11-15T00:00:00"]}]})
    assert original_plan == PLAN and original_inputs == INPUTS and original_schema == metadata()
    assert EmbeddedODataProvider._odata_literal("2022-11-15", "Edm.DateTime", "2.0") == \
        EmbeddedODataProvider._odata_literal("2022-11-15T00:00:00", "Edm.DateTime", "2.0")


@pytest.mark.parametrize("invalid", ["2022-11-16T00:00:00", "2022-11-15T00:00:01",
    "2022-11-15T23:59:59", "2022-11-15T00:00:00Z", "2022-11-15T00:00:00+08:00",
    "2022-11-15T00:00:00.001", "2022-11-15garbage", "secret-raw-value", []])
def test_date_similarity_never_authorizes_a_different_value(invalid):
    plan = copy.deepcopy(PLAN)
    plan["filters"][1]["value"] = invalid
    result = issues(scope(), plan)
    assert result[0]["path"] == "/filters/1/value"
    assert result[0]["scope_issue"] == "value_outside_scope"
    assert "secret-raw-value" not in json.dumps(result)


@pytest.mark.parametrize("authority,truncated", [(False, False), (None, False), (True, True), (True, None)])
def test_non_authoritative_or_incomplete_metadata_cannot_grant_date_equivalence(authority, truncated):
    value = scope(metadata(schema_authority=authority, fields_truncated=truncated))
    assert not value.schemas and not value.date_fields
    issues(value, PLAN)


@pytest.mark.parametrize("property_patch,source_patch", [
    ({"data_type": "Edm.DateTimeOffset"}, {}), ({"data_type": "Edm.String"}, {}),
    ({"display_format": ""}, {}), ({"display_format": "Date"}, {"odata_version": "4.0"}),
])
def test_model_type_annotation_or_field_name_is_not_date_proof(property_patch, source_patch):
    response = metadata()
    response["data"]["fields"][1].update(property_patch)
    response["data"]["service"].update(source_patch)
    issues(scope(response), {**PLAN, **source_patch})


def test_bad_calendar_input_is_not_converted_and_later_metadata_can_revoke_equivalence():
    invalid = scope(inputs={**INPUTS, "schedule_line_delivery_date": "2022-02-30"})
    plan = copy.deepcopy(PLAN)
    plan["filters"][1]["value"] = "2022-02-30T00:00:00"
    issues(invalid, plan)
    value = scope()
    value.check(PLAN)
    changed = metadata()
    changed["data"]["fields"][1]["display_format"] = ""
    value.record_schema(changed)
    issues(value, PLAN)


def test_truncated_schema_cannot_hide_a_missing_scope_condition():
    value = scope(metadata(fields_truncated=True), {**INPUTS, "purchasing_organization": "1010"})
    result = issues(value, PLAN)
    assert result[0]["scope_issue"] == "authoritative_schema_required"
    assert result[0]["schema_request"] == {"service_name": "S", "odata_version": "2.0",
        "entity_sets": ["Schedule"], "max_fields": 5000}
    assert "required_fields" not in result[0]  # Existence is not established.


def test_unknown_entity_diagnostic_requires_schema_without_inventing_filters_or_authorizing_reads():
    value = ReadScope("Frozen public inputs", {**INPUTS, "purchasing_organization": "1010"})
    plan = {"service_name": "MaterialService", "odata_version": "2.0", "entity_set": "MaterialItems",
        "filters": [{"field": "PurchaseOrder", "value": "4500000001"}], "top": 10}
    before = copy.deepcopy(plan)
    result = issues(value, plan)
    assert result[0]["scope_issue"] == "authoritative_schema_required"
    assert plan == before
    assert "4500000001" not in json.dumps(result)
    # Only authoritative full metadata can prove date/org are not local fields.
    value.record_schema({"ok": True, "data": {"schema_authority": True, "fields_truncated": False,
        "service": {"service_name": "MaterialService", "odata_version": "2.0"},
        "fields": [{"entity_set": "MaterialItems", "field_name": "PurchaseOrder", "data_type": "Edm.String"}]}})
    value.check(plan)
    plan["filters"][0]["value"] = "different-order"
    assert issues(value, plan)[0]["scope_issue"] == "value_outside_scope"


def test_missing_filter_and_range_instead_of_exact_filter_have_value_free_diagnostics():
    missing = issues(scope(), {**PLAN, "filters": PLAN["filters"][:1]})
    assert missing == [{"code": "runtime_query_scope_rejected", "path": "/filters", "constraint": "scope",
        "scope_issue": "required_filter_missing", "required_fields": ["ScheduleLineDeliveryDate"],
        "allowed_operators": ["eq", "in"]}]
    plan = copy.deepcopy(PLAN)
    plan["filters"][1]["operator"] = "ge"
    ranged = issues(scope(), plan)
    assert ranged[0]["path"] == "/filters/1/operator"
    assert ranged[0]["scope_issue"] == "exact_operator_required"
    assert "2022-11-15" not in json.dumps(ranged)


@pytest.mark.parametrize("value,paths", [
    ({"entity_set": "Schedule", "odata_version": "2.0"}, ["/service_name"]),
    ({"entity_set": "Schedule"}, ["/service_name", "/odata_version"]),
    ({"plan_kind": "multi_step", "steps": [{"entity_set": "Schedule", "odata_version": "2.0"}]}, ["/steps/0/service_name"]),
    ({"plan_kind": "multi_step", "steps": [{}]}, ["/steps/0/entity_set"]),
])
def test_missing_canonical_plan_members_are_named_but_never_filled(value, paths):
    before = copy.deepcopy(value)
    with pytest.raises(RuntimeContractError) as failure:
        plans(value)
    assert [i["path"] for i in failure.value.detail["validation_issues"]] == paths
    assert all(i["constraint"] == "required" for i in failure.value.detail["validation_issues"])
    assert value == before


def test_legal_inheritance_and_private_unknown_keys_still_follow_original_contract():
    result = plans({"service_name": "S", "odata_version": "2.0", "plan_kind": "multi_step",
        "steps": [{"step_id": "first", "entity_set": "Schedule"}]})
    assert result == [{"service_name": "S", "odata_version": "2.0", "step_id": "first", "entity_set": "Schedule"}]
    with pytest.raises(RuntimeContractError) as failure:
        plans({**SOURCE, "secret-private-key": "secret-private-value"})
    assert "secret" not in json.dumps(failure.value.detail)
    assert failure.value.detail["validation_issues"][0]["constraint"] == "additionalProperties"


def test_skill_scope_gaps_remain_blocked_without_inferred_ddic_aliases():
    value = scope(inputs={**INPUTS, "purchasing_organization": "1010"})
    arguments = {"skill_id": "sap-adt-table-export", "input": {
        "object": "EKET", "filters": [{"field": "EBELN", "option": "EQ", "low": "4500000001"}]}}
    before = copy.deepcopy(arguments)
    with pytest.raises(RuntimeContractError) as failure:
        value.check_skill(arguments)
    result = failure.value.detail["validation_issues"]
    assert len(result) == 3
    assert {i["scope_issue"] for i in result} == {"required_scope_not_proven"}
    assert "4500000001" not in json.dumps(result) and arguments == before
    with pytest.raises(RuntimeContractError) as failure:
        value.check_skill({"input": {"where": "secret-raw-expression"}})
    assert failure.value.detail["validation_issues"][0]["constraint"] == "unsupported_scope_expression"


def test_grounding_cannot_rewrite_date_even_when_scope_comparison_is_equivalent():
    changed = copy.deepcopy(PLAN)
    changed["filters"][1]["value"] = "2022-11-15"
    with pytest.raises(RuntimeContractError):
        preserve_grounding(PLAN, changed)


def inheritance_scope():
    value = ReadScope("Frozen public inputs", {"purchase_order": "4500000001"})
    value.schemas[("S", "2.0", "Header")] = {"PurchaseOrder", "PurchasingDocument", "Country"}
    value.schemas[("S", "2.0", "Lines")] = {"PurchasingDocument"}
    return value


def inherited_plan(source_field="PurchaseOrder", target_field="PurchasingDocument"):
    return {"service_name": "S", "odata_version": "2.0", "plan_kind": "multi_step", "steps": [
        {"step_id": "header", "entity_set": "Header", "filters": [
            {"field": "PurchaseOrder", "value": "4500000001"}]},
        {"step_id": "lines", "entity_set": "Lines", "filter_from_previous": [
            {"field": target_field, "source_step_id": "header", "source_field": source_field}]}]}


def test_declared_input_alias_can_inherit_only_proven_exact_values_without_plan_rewrite():
    plan = inherited_plan()
    before = copy.deepcopy(plan)
    inheritance_scope().check(plan)
    assert plan == before


@pytest.mark.parametrize('source_field', ['Country', 'PurchasingDocument'])
def test_incidental_filter_cannot_masquerade_as_proven_input(source_field):
    plan = inherited_plan(source_field)
    plan['steps'][0]['filters'].append({'field': source_field, 'value': 'outside-scope'})
    issues(inheritance_scope(), plan)


def test_alias_inheritance_does_not_cross_business_dimensions_or_change_authorized_values():
    value = inheritance_scope()
    value.constraints.append((("CompanyCode",), ["4500000001"]))
    value.schemas[("S", "2.0", "Header")].add('CompanyCode')
    value.schemas[("S", "2.0", "Lines")].add('CompanyCode')
    plan = inherited_plan()
    plan['steps'][0]['filters'].append({'field': 'CompanyCode', 'value': '4500000001'})
    plan['steps'][1]['filter_from_previous'].append({'field': 'CompanyCode',
        'source_step_id': 'header', 'source_field': 'PurchaseOrder'})
    issues(value, plan)  # Equal strings do not prove equal business dimensions.
    plan = inherited_plan()
    plan['steps'][1]['filters'] = [{'field': 'PurchasingDocument', 'value': 'different-order'}]
    issues(inheritance_scope(), plan)


@pytest.mark.parametrize("provider", ["codex", "workbuddy"])
def test_real_broker_returns_and_saves_same_diagnostics_before_external_dispatch(tmp_path, provider):
    from sap_business_agents_platform.database import RunStore
    from sap_business_agents_platform.harness import HarnessToolBroker
    from sap_business_agents_platform.models import RunCreate, RunMode, RuntimeSnapshot
    from tests.test_harness import FakeSapRead, FakeSkills, _settings
    settings = _settings(tmp_path)
    store = RunStore(settings.database_path)
    store.create_run("offline", RunCreate(mode=RunMode.free_query, query="Frozen public inputs", input=INPUTS))
    store.update_run("offline", runtime_json=RuntimeSnapshot(provider_id=provider, model="offline-model",
        sdk_id="codex" if provider == "codex" else "codebuddy-agent-sdk", configuration_digest="offline").model_dump(mode="json"))
    calls = []
    class NoBusinessRead(FakeSapRead):
        async def validate_plan(self, plan, query=""):
            calls.append(copy.deepcopy(plan))
            return {"ok": True, "normalized_plan": plan}
        async def execute_plan(self, *_args, **_kwargs):
            pytest.fail("No business reads allowed in offline replay")
    broker = HarnessToolBroker(settings, store, NoBusinessRead(), FakeSkills())
    token = broker.open_session("offline")
    broker.bind_read_scope("offline", scope())
    async def run():
        rejected = await broker.handle("offline", token, "sap_query_execute", {"plan": {**PLAN, "filters": PLAN["filters"][:1]}})
        assert rejected["ok"] is False and rejected["code"] == "runtime_query_scope_rejected"
        assert rejected["validation_issues"] == rejected["detail"]["validation_issues"]
        saved = store.list_harness_tool_calls("offline")[-1]["output"]
        assert saved["validation_issues"] == rejected["validation_issues"]
        event = store.events_after("offline")[-1]
        assert event.data["validation_issues"] == rejected["validation_issues"]
        assert not calls
        valid = await broker.handle("offline", token, "sap_query_validate", {"plan": copy.deepcopy(PLAN)})
        assert valid["ok"] is True and calls == [PLAN]
    asyncio.run(run())
