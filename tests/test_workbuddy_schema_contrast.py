"""Offline semantics, not a model success or qualification grant."""
import copy
import itertools

import pytest
from jsonschema import Draft202012Validator

from scripts.workbuddy_schema_contrast import equivalent_union_schema
from tests.test_workbuddy_harness_nullable import schema, envelope


def validators(original):
    before = copy.deepcopy(original)
    wire = equivalent_union_schema(original)
    assert original == before
    return Draft202012Validator(original), Draft202012Validator(wire), wire


def test_full_required_fields_enum_values_and_canonical_null_are_preserved():
    original, contrast, wire = validators(schema())
    assert len(wire["properties"]) == len(schema()["properties"]) == 12
    assert wire["required"] == schema()["required"] and wire["additionalProperties"] is False
    for name in ("input_kind", "input_field"):
        assert wire["properties"][name]["enum"] == schema()["properties"][name]["enum"]
        assert wire["properties"][name]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    original.validate(envelope()); contrast.validate(envelope())


@pytest.mark.parametrize("field", ["input_kind", "input_field", "status", "summary", "source_complete", "business_complete", "executed_plans", "evidence_refs", "presentation"])
def test_representations_accept_exactly_same_field_variants(field):
    original, contrast, _ = validators(schema())
    alternatives = [None, "null", "", "private-invalid", True, False, 0, 1, 1.5, [], {}, {"zh": "测试", "en": "Test"}]
    alternatives += schema()["properties"][field].get("enum", [])
    for item in alternatives:
        candidate = {**envelope(), field: item}
        assert original.is_valid(candidate) == contrast.is_valid(candidate)
    candidate = envelope(); candidate.pop(field)
    assert not original.is_valid(candidate) and not contrast.is_valid(candidate)


def test_representations_both_reject_saved_three_field_failure_shape():
    original, contrast, _ = validators(schema())
    candidate = {name: envelope()[name] for name in ("status", "intent", "clarification_question")}
    assert not original.is_valid(candidate) and not contrast.is_valid(candidate)
    assert len([error for error in contrast.iter_errors(candidate) if error.validator == "required"]) == 9


def test_existing_anyof_and_allof_are_intersections_not_overwritten():
    source = {"type": "object", "properties": {"value": {"type": ["integer", "number", "null"],
        "anyOf": [{"minimum": 2}, {"type": "null"}], "allOf": [{"not": {"const": 3}}]}},
        "required": ["value"], "additionalProperties": False}
    original, contrast, wire = validators(source)
    assert wire["properties"]["value"]["anyOf"] == source["properties"]["value"]["anyOf"]
    assert wire["properties"]["value"]["allOf"][0] == source["properties"]["value"]["allOf"][0]
    for item in (None, True, 0, 1, 2, 2.5, 3, 4, "2", {}, []):
        assert original.is_valid({"value": item}) == contrast.is_valid({"value": item})


def test_nested_schemas_change_but_schema_like_data_is_not_rewritten():
    source = {"type": "object", "properties": {"rows": {"type": "array", "items": {"$ref": "#/$defs/row"}},
        "data": {"type": "object", "default": {"type": ["not-a-schema"]}, "examples": [{"type": ["unchanged-data"]}]}},
        "$defs": {"row": {"type": "object", "properties": {"id": {"type": ["string", "null"]}}, "required": ["id"]}}}
    original, contrast, wire = validators(source)
    assert wire["properties"]["data"] == source["properties"]["data"]
    assert "anyOf" in wire["$defs"]["row"]["properties"]["id"]
    for item in (None, "", "id", 4, False, {}, []):
        assert original.is_valid({"rows": [{"id": item}]}) == contrast.is_valid({"rows": [{"id": item}]})


def test_all_null_enum_combinations_and_unknown_fields_remain_equivalent():
    original, contrast, _ = validators(schema())
    for kind, field in itertools.product([None, "secure_business_reference", "null", ""],
                                         [None, "receipt_reference", "null", ""]):
        value = {**envelope(), "input_kind": kind, "input_field": field}
        assert original.is_valid(value) == contrast.is_valid(value)
        value["unknown"] = "secret"
        assert not original.is_valid(value) and not contrast.is_valid(value)


def test_candidate_not_imported_by_runtime_or_codex():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src/sap_business_agents_platform"
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert "workbuddy_schema_contrast" not in (node.module or "")
