"""Non-interactive Campaign policy narrows encoding; no model or SAP."""
import copy
import itertools

from jsonschema import Draft202012Validator

from sap_business_agents_platform.acceptance_projection import output_schema
from sap_business_agents_platform.runtime_harness_contract import _HARNESS_OUTPUT_SCHEMA, terminal_schema, turn_prompt
from sap_business_agents_platform.workbuddy_harness import native_terminal_instructions
from tests.test_workbuddy_harness_nullable import envelope

SPEC = {"record_fields": ["company_code"], "metric_fields": ["count"], "required_assessments": []}


def test_interactive_free_queries_retain_exact_schema_and_secure_reference_capability():
    assert terminal_schema() == output_schema(_HARNESS_OUTPUT_SCHEMA, None)
    value = {**envelope(), "status": "waiting_input", "input_kind": "secure_business_reference", "input_field": "receipt_reference"}
    Draft202012Validator(terminal_schema()).validate(value)


def test_formal_contract_changes_only_two_interaction_types_and_keeps_all_checks():
    before = output_schema(_HARNESS_OUTPUT_SCHEMA, SPEC)
    unchanged = copy.deepcopy(_HARNESS_OUTPUT_SCHEMA)
    formal = terminal_schema(SPEC)
    reconstructed = copy.deepcopy(formal)
    for name in ("input_kind", "input_field"):
        assert formal["properties"][name]["type"] == "null" and formal["properties"][name]["enum"] == [None]
        reconstructed["properties"][name] = before["properties"][name]
    assert reconstructed == before
    assert _HARNESS_OUTPUT_SCHEMA == unchanged
    assert formal["required"] == before["required"] and formal["additionalProperties"] is False
    assert "acceptance_projection" in formal["required"] and formal["properties"]["presentation"] == before["properties"]["presentation"]


def test_null_only_formal_contract_is_a_strict_subset_never_a_decoder():
    before, formal = Draft202012Validator(output_schema(_HARNESS_OUTPUT_SCHEMA, SPEC)), Draft202012Validator(terminal_schema(SPEC))
    for status, kind, field in itertools.product(["completed", "inconclusive", "waiting_input"],
            [None, "secure_business_reference", "null", "supplier", ""], [None, "receipt_reference", "null", "supplier", ""]):
        value = {**envelope(), "status": status, "input_kind": kind, "input_field": field, "acceptance_projection": None}
        original = copy.deepcopy(value)
        valid = formal.is_valid(value)
        assert valid == (kind is None and field is None)
        assert not valid or before.is_valid(value)
        assert value == original
    for field in ("input_kind", "input_field", "presentation", "acceptance_projection"):
        value = {**envelope(), "acceptance_projection": None}; value.pop(field)
        assert not formal.is_valid(value)
    assert not formal.is_valid(None)
    assert not formal.is_valid({"validation_ref": "candidate-is-not-a-terminal"})


def test_formal_guidance_and_schema_agree_without_limiting_investigation():
    prompt = turn_prompt("Frozen case", continuing=False, acceptance_spec=SPEC)
    instructions = native_terminal_instructions(terminal_schema(SPEC))
    assert "non-interactive" in prompt and '"input_kind": null' in prompt and '"input_field": null' in prompt
    assert "waiting_input" in prompt and "frozen case inputs" in prompt
    assert "No string is allowed" in instructions and "Keep the validated report unchanged" in instructions
    assert "non-interactive" not in turn_prompt("Ordinary query", continuing=False)
