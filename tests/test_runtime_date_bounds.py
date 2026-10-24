"""Calendar-day authorization, never a plan rewrite or a live SAP read."""
import copy
import json

import pytest

from sap_business_agents_platform.runtime_contract import RuntimeContractError
from sap_business_agents_platform.runtime_query_contract import ReadScope

SOURCE = {"service_name": "OfflineInvoices", "odata_version": "2.0", "entity_set": "Items"}
INPUT = {"company_code": "1710", "supplier": "17300001", "posting_date_to": "2022-12-31"}


def metadata(version="2.0", **changes):
    return {"ok": True, "data": {"schema_authority": True, "fields_truncated": False,
        "service": {"service_name": SOURCE["service_name"], "odata_version": version},
        "fields": [{"entity_set": "Items", "field_name": name, "data_type": "Edm.String"}
                   for name in ("CompanyCode", "Supplier")] + [{"entity_set": "Items", "field_name": "PostingDate",
                   "data_type": "Edm.Date" if version == "4.0" else "Edm.DateTime", "display_format": "Date"}], **changes}}


def plan(operator="le", value="2022-12-31T00:00:00", **changes):
    return {**SOURCE, "filters": [{"field": "CompanyCode", "value": "1710"},
        {"field": "Supplier", "value": "17300001"},
        {"field": "PostingDate", "operator": operator, "value": value}], "top": 10, **changes}


def scope(inputs=None, response=None):
    result = ReadScope("Frozen input", INPUT if inputs is None else inputs)
    result.record_schema(metadata() if response is None else response)
    return result


@pytest.mark.parametrize("operator,value", [("le", "2022-12-31T00:00:00"), ("le", "2022-12-30"),
    ("lt", "2023-01-01T00:00:00"), ("lt", "2022-12-31"), ("eq", "2022-12-31"),
    ("in", ["2022-12-01", "2022-12-31T00:00:00"])])
def test_upper_bound_accepts_only_subsets_and_does_not_rewrite(operator, value):
    candidate = plan(operator, value)
    frozen = copy.deepcopy(candidate)
    scoped = scope()
    scoped.require_resolved()
    scoped.check(candidate)
    assert candidate == frozen and INPUT["posting_date_to"] == "2022-12-31"
    assert scoped.bounds[0]["operator"] == "le"


@pytest.mark.parametrize("operator,value", [("le", "2023-01-01"), ("lt", "2023-01-02"),
    ("eq", "2023-01-01"), ("in", ["2022-12-31", "2023-01-01"]), ("ge", "2022-12-31"),
    ("ne", "2022-12-31"), ("contains", "2022-12"), ("le", []), ("le", ["2022-12-31"]), ("in", "2022-12-31"),
    ("le", "2022-12-31T23:59:59"), ("le", "2022-12-31T00:00:00Z"),
    ("le", "2022-12-31T00:00:00+08:00"), ("le", "2022-12-31T00:00:00.000"),
    ("le", "2022-02-30"), ("le", "private-invalid-value"), ("le", None)])
def test_wider_or_unproven_date_is_rejected_without_value_disclosure(operator, value):
    candidate = plan(operator, value)
    before = copy.deepcopy(candidate)
    with pytest.raises(RuntimeContractError) as caught:
        scope().check(candidate)
    assert caught.value.code == "runtime_query_scope_rejected"
    assert caught.value.detail["validation_issues"][0]["scope_issue"] == "date_bound_not_proven"
    assert "private-invalid-value" not in json.dumps(caught.value.detail)
    assert candidate == before


def test_removing_date_filter_or_using_partition_alone_cannot_authorize():
    candidate = plan()
    candidate["filters"].pop()
    candidate["partition"] = {"field": "PostingDate", "strategy": "adaptive_date", "from": "2020-01-01", "to": "2022-12-31"}
    with pytest.raises(RuntimeContractError, match="runtime_query_scope_rejected"):
        scope().check(candidate)


@pytest.mark.parametrize("operator,value", [("ge", "2022-12-01"), ("gt", "2022-11-30"),
    ("eq", "2022-12-15"), ("in", ["2022-12-01", "2022-12-31"])])
def test_lower_and_upper_bounds_are_both_required(operator, value):
    candidate = plan(operator, value)
    if operator in {"ge", "gt"}:
        candidate["filters"].append({"field": "PostingDate", "operator": "le", "value": "2022-12-31"})
    scoped = scope({**INPUT, "posting_date_from": "2022-12-01"})
    scoped.check(candidate)
    if operator in {"ge", "gt"}:
        candidate["filters"].pop()
        with pytest.raises(RuntimeContractError):
            scoped.check(candidate)


@pytest.mark.parametrize("data", [None, {"schema_authority": False}, {"fields_truncated": True},
    {"fields": [{"entity_set": "Items", "field_name": "PostingDate", "data_type": "Edm.DateTimeOffset"}]},
    {"fields": [{"entity_set": "Items", "field_name": "PostingDate", "data_type": "Edm.String"}]},
    {"fields": [{"entity_set": "Items", "field_name": "PostingDate", "data_type": "Edm.DateTime"}]}])
def test_bound_needs_full_authoritative_calendar_semantics(data):
    scoped = scope(response=metadata(**data) if data is not None else {"ok": False})
    with pytest.raises(RuntimeContractError) as caught:
        scoped.check(plan())
    assert caught.value.detail["validation_issues"][0]["scope_issue"] == "authoritative_date_schema_required"
    assert caught.value.detail["validation_issues"][0]["input_field"] == "posting_date_to"


def test_v4_date_and_metadata_replacement_do_not_reuse_old_type():
    scoped = scope(response=metadata("4.0"))
    scoped.check(plan(value="2022-12-31", odata_version="4.0"))
    with pytest.raises(RuntimeContractError):
        scoped.check(plan(odata_version="4.0"))
    scoped.record_schema(metadata("4.0", fields=[{"entity_set": "Items", "field_name": "PostingDate", "data_type": "Edm.String"}]))
    with pytest.raises(RuntimeContractError):
        scoped.check(plan(value="2022-12-31", odata_version="4.0"))


def test_full_lookup_schema_can_prove_date_is_not_local_but_not_drop_it_on_items():
    scoped = scope()
    lookup = {**SOURCE, "entity_set": "Suppliers", "filters": [{"field": "Supplier", "value": "17300001"}]}
    with pytest.raises(RuntimeContractError):
        scoped.check(lookup)
    scoped.record_schema({"ok": True, "data": {"schema_authority": True, "fields_truncated": False,
        "service": SOURCE, "fields": [{"entity_set": "Suppliers", "field_name": "Supplier", "data_type": "Edm.String"}]}})
    scoped.check(lookup)
    candidate = plan()
    candidate["filters"].pop()
    with pytest.raises(RuntimeContractError):
        scoped.check(candidate)


@pytest.mark.parametrize("inputs,field,code", [
    ({**INPUT, "posting_date_to": "2022-02-30"}, "posting_date_to", "runtime_scope_date_invalid"),
    ({**INPUT, "posting_date_to": ["2022-12-31"]}, "posting_date_to", "runtime_scope_date_invalid"),
    ({**INPUT, "posting_date_from": "2023-01-01"}, "posting_date_from", "runtime_scope_bounds_conflict"),
    ({**INPUT, "unsupported_cutoff": "secret-value"}, "unsupported_cutoff", "runtime_scope_mapping_unsupported"),
])
def test_bad_public_inputs_remain_blocked_with_field_specific_safe_reason(inputs, field, code):
    scoped = ReadScope("Confirmed public inputs: " + json.dumps(inputs))
    with pytest.raises(RuntimeContractError) as caught:
        scoped.require_resolved()
    assert caught.value.detail["validation_issues"] == [{"code": code, "path": "/input/" + field,
        "input_field": field, "constraint": "scope_input"}]
    assert "secret-value" not in json.dumps(caught.value.detail)


def test_date_scope_never_invents_ddic_skill_mapping():
    with pytest.raises(RuntimeContractError) as caught:
        scope().check_skill({"input": {**INPUT, "BUDAT": "20221231"}})
    assert caught.value.code == "runtime_skill_scope_rejected"


def test_legacy_workbuddy_facade_uses_same_range_algorithm():
    from sap_business_agents_platform.workbuddy_query_contract import ReadScope as LegacyScope
    scoped = LegacyScope("Confirmed public inputs: " + json.dumps(INPUT))
    scoped.record_schema(metadata())
    scoped.check(plan())
    with pytest.raises(Exception) as caught:
        scoped.check(plan(value="2023-01-01"))
    assert caught.value.detail["validation_issues"][0]["scope_issue"] == "date_bound_not_proven"


@pytest.mark.parametrize("marker", ["Confirmed public inputs: ", "Canonical query inputs: "])
def test_both_platform_frozen_input_envelopes_keep_the_date_bound(marker):
    scoped = ReadScope("Independent task\n\n" + marker + json.dumps(INPUT))
    scoped.require_resolved()
    assert len(scoped.constraints) == 2 and len(scoped.bounds) == 1
    scoped.record_schema(metadata())
    scoped.check(plan())
