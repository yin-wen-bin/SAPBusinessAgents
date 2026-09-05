import copy

import pytest
from pydantic import ValidationError

from sap_business_agents_platform.acceptance_projection import (
    AcceptanceProjectionSpec, output_schema, validate_projection, visible_projection_issues,
)
from sap_business_agents_platform.harness import _HARNESS_OUTPUT_SCHEMA
from sap_business_agents_platform.models import RunCreate


SPEC = {"record_fields": ["document", "amount"], "metric_fields": ["count"],
        "decimal_fields": ["amount"]}
KNOWN = {"e1": {"source_type": "sap_live", "source_complete": True}}
PROJECTION = {"records": [{"document": "1", "amount": "0.00", "evidence_refs": ["e1"]}],
              "metrics": {"count": 1}, "business_status": "normal", "source_complete": True,
              "evidence_complete": True, "business_complete": True,
              "evidence_gap_codes": [], "evidence_refs": ["e1"]}


def _check_strict(value):
    if isinstance(value, dict):
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
            assert set(value["required"]) == set(value["properties"])
        for child in value.values():
            _check_strict(child)
    elif isinstance(value, list):
        for child in value:
            _check_strict(child)


def test_acceptance_schema_is_closed_and_only_enabled_on_request():
    ordinary = output_schema(_HARNESS_OUTPUT_SCHEMA, None)
    assert "acceptance_projection" not in ordinary["properties"]
    _check_strict(ordinary)
    _check_strict(output_schema(_HARNESS_OUTPUT_SCHEMA, SPEC))
    assert "acceptance_projection" not in _HARNESS_OUTPUT_SCHEMA["properties"]


def test_acceptance_contract_rejects_unknown_schema_and_conflicting_fields():
    with pytest.raises(ValidationError):
        AcceptanceProjectionSpec.model_validate({**SPEC, "schema": {}})
    with pytest.raises(ValidationError):
        AcceptanceProjectionSpec.model_validate({**SPEC, "record_fields": ["x", "x"]})
    with pytest.raises(ValidationError):
        RunCreate(mode="agent", agentId="ar-collection", acceptanceSpec=SPEC)


@pytest.mark.parametrize("mutation", ["unknown_ref", "row_ref", "nan", "extra", "incomplete"])
def test_projection_fails_closed(mutation):
    value, known = copy.deepcopy(PROJECTION), copy.deepcopy(KNOWN)
    if mutation == "unknown_ref":
        value["evidence_refs"] = ["other"]
    elif mutation == "row_ref":
        value["records"][0]["evidence_refs"] = ["other"]
    elif mutation == "nan":
        value["records"][0]["amount"] = "NaN"
    elif mutation == "extra":
        value["records"][0]["payer_name"] = "must not be admitted"
    else:
        known["e1"]["source_complete"] = False
    assert validate_projection(SPEC, value, known)


def test_projection_zero_requires_live_empty_evidence():
    value = {**PROJECTION, "records": [], "metrics": {"count": 0}}
    assert validate_projection(SPEC, value, KNOWN) == []
    assert validate_projection(SPEC, {**value, "evidence_refs": []}, KNOWN)


def test_visible_comparison_checks_both_locales_records_metrics_and_gaps():
    text = lambda value: {"zh": value, "en": value}
    report = {"blocks": [{"columns": [{"key": "document"}, {"key": "amount"}],
                          "rows": [{"values": [text("1"), text("0.00")]}],
                          "metrics": [{"id": key, "value": text(val)} for key, val in
                                      [("count", "1"), ("business_status", "normal"),
                                       ("source_complete", "true"), ("evidence_complete", "true"),
                                       ("business_complete", "true")]]}]}
    assert not visible_projection_issues(SPEC, PROJECTION, report)
    report["blocks"][0]["rows"][0]["values"][0]["zh"] = "2"
    assert visible_projection_issues(SPEC, PROJECTION, report)
