import copy

import pytest

from sap_business_agents_platform.authoring_checks import candidate_input_issues
from sap_business_agents_platform.engine import _render_template


def candidate():
    return {"execution": {"inputSchema": {"type": "object", "required": ["date"],
        "properties": {"date": {"type": "string"}, "purchase_order": {"type": "string"}}},
        "steps": [{"request": {"filters": [
            {"field": "Date", "operator": "eq", "value": "{{input.date}}"},
            {"field": "PurchaseOrder", "operator": "eq", "value": "{{input.purchase_order}}"}
        ]}}]}}


def test_optional_schema_is_not_an_execution_guard():
    issues = candidate_input_issues(candidate())
    assert issues == [{"code": "agent_optional_input_unguarded", "field": "purchase_order",
                       "path": "/manifest/execution/steps/0/request/filters/1/value"}]


def test_optional_lookup_alone_does_not_guard_filter():
    value = candidate()
    value["execution"]["steps"][0]["request"]["filters"][1]["value"] = "{{input.purchase_order?}}"
    assert candidate_input_issues(value)[0]["code"] == "agent_optional_filter_unguarded"


@pytest.mark.parametrize("supplied", [{}, {"purchase_order": None}, {"purchase_order": ""},
                                     {"purchase_order": "  "}, {"purchase_order": []}])
def test_omit_entire_filter_in_real_engine(supplied):
    value = candidate()
    request = value["execution"]["steps"][0]["request"]
    request["filters"][1]["omitIfEmpty"] = "{{input.purchase_order?}}"
    before = copy.deepcopy(request)
    assert candidate_input_issues(value) == []
    rendered = _render_template(request, {"input": {"date": "2026-09-10", **supplied}})
    assert rendered["filters"] == [{"field": "Date", "operator": "eq", "value": "2026-09-10"}]
    assert request == before


@pytest.mark.parametrize("provided", ["4500000001", 0, False])
def test_present_value_is_kept_without_platform_directive(provided):
    request = candidate()["execution"]["steps"][0]["request"]
    request["filters"][1]["omitIfEmpty"] = "{{input.purchase_order?}}"
    result = _render_template(request, {"input": {"date": "2026-09-10", "purchase_order": provided}})
    assert result["filters"][1] == {"field": "PurchaseOrder", "operator": "eq", "value": provided}


def test_missing_required_still_fails_and_legacy_render_is_unchanged():
    with pytest.raises(ValueError, match="input.date"):
        _render_template(candidate()["execution"]["steps"][0]["request"], {"input": {}})
    assert _render_template("{{input.purchase_order?}}", {"input": {}}) is None


@pytest.mark.parametrize("marker", ["{{steps.result.output?}}", "{{input.other?}}", True, "{{input.purchase_order}}"])
def test_invalid_omission_fails_closed(marker):
    value = candidate()
    request = value["execution"]["steps"][0]["request"]
    request["filters"][1]["omitIfEmpty"] = marker
    assert candidate_input_issues(value)[0]["code"] == "agent_optional_filter_invalid"
    with pytest.raises(ValueError, match="agent_optional_filter_invalid"):
        _render_template(request, {"input": {"date": "2026-09-10"}})


def test_defaulted_input_is_not_missing():
    value = candidate()
    value["execution"]["inputSchema"]["properties"]["purchase_order"]["default"] = "4500000001"
    assert candidate_input_issues(value) == []


def test_omission_cannot_remove_arbitrary_execution_entries():
    with pytest.raises(ValueError, match="agent_optional_filter_invalid"):
        _render_template([{"field": "X", "value": "{{input.x?}}", "omitIfEmpty": "{{input.x?}}"}], {"input": {}})
