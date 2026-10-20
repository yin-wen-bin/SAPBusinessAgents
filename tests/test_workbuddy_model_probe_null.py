"""Reject null-looking strings without coercion, before native capture or admission."""
import asyncio
import copy
import json

import pytest
from jsonschema import Draft202012Validator

from sap_business_agents_platform import workbuddy_model_protocol as protocol
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
from tests.test_workbuddy_model_probe_diagnostics import output
from tests.test_workbuddy_formatter_guard import exercise


@pytest.mark.parametrize("value", [None, "null", "", "None"])
def test_probe_schema_and_strict_verification_agree_on_null(value):
    challenge = protocol.Challenge()
    asyncio.run(challenge.handle(protocol.TOOL, {"challenge": challenge.challenge}))
    candidate = {**output(challenge), "optional": value}
    errors = list(Draft202012Validator(protocol.SCHEMA).iter_errors(candidate))
    if value is None:
        assert not errors
        assert challenge.verify(candidate)["tool_calls"] == 1
    else:
        assert errors and all(list(error.path) == ["optional"] for error in errors)
        assert {error.validator for error in errors} == {"type", "enum"}
        with pytest.raises(WorkBuddyError, match="echo_invalid"):
            challenge.verify(candidate)
        assert candidate["optional"] == value  # Neither check coerces the input.


def test_probe_requires_optional_field_and_explicit_null_guidance():
    challenge = protocol.Challenge()
    candidate = output(challenge)
    candidate.pop("optional")
    assert any(error.validator == "required" for error in Draft202012Validator(protocol.SCHEMA).iter_errors(candidate))
    payload = challenge.payload("probe-workspace")
    assert '"optional": null' in payload["prompt"] and 'never the string "null"' in payload["prompt"]
    assert protocol.probe_scope("model_check", payload, challenge.handle)


def test_null_only_type_preserves_the_previous_enum_contract():
    previous = Draft202012Validator({"type": ["string", "null"], "enum": [None]})
    current = Draft202012Validator(protocol.SCHEMA["properties"]["optional"])
    for value in (None, "null", "None", "", 0, 1, False, True, {}, [], [None]):
        assert current.is_valid(value) == previous.is_valid(value) == (value is None)


def test_json_prompt_template_requires_the_real_tool_receipt():
    challenge = protocol.Challenge()
    prompt = challenge.prompt()
    example = json.loads(prompt.split("Object template: ", 1)[1].split("\n", 1)[0])
    assert example["optional"] is None and example["challenge"] == challenge.challenge
    assert Draft202012Validator(protocol.SCHEMA).is_valid(example)
    with pytest.raises(WorkBuddyError, match="echo_invalid"):
        challenge.verify(example)
    receipt = asyncio.run(challenge.handle(protocol.TOOL, {"challenge": challenge.challenge}))
    with pytest.raises(WorkBuddyError, match="echo_invalid"):
        challenge.verify(example)
    example["receipt"] = receipt["receipt"]
    assert challenge.verify(example)["tool_calls"] == 1


@pytest.mark.parametrize("bad", ["null", "", "None"])
def test_native_formatter_rejects_string_then_captures_model_supplied_null(monkeypatch, bad):
    challenge = protocol.Challenge()
    asyncio.run(challenge.handle(protocol.TOOL, {"challenge": challenge.challenge}))
    valid = output(challenge)
    invalid = {**valid, "optional": bad}
    original = copy.deepcopy(invalid)
    run, observed, decisions, checks, _, captured = exercise(
        monkeypatch, [invalid, valid], wire_schema=protocol.SCHEMA)
    result = asyncio.run(run())
    assert decisions[0]["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert '"path":"/optional"' in decisions[0]["hookSpecificOutput"]["permissionDecisionReason"]
    assert decisions[1] == {} and captured == [valid]
    assert not checks[0]["schema_valid"] and checks[-1]["schema_valid"]
    assert json.loads(observed[0].extra_args["json-schema"]) == protocol.SCHEMA
    assert invalid == original and invalid["optional"] == bad
    assert result["output_format"] == "native_json_schema"
    assert json.loads(result["text"])["optional"] is None
    assert challenge.verify(json.loads(result["text"]))["tool_calls"] == 1


@pytest.mark.parametrize("bad", ["null", "", "None"])
def test_invalid_native_terminal_still_fails_without_coercion(monkeypatch, bad):
    candidate = {**output(protocol.Challenge()), "optional": bad}
    run, _, _, checks, _, _ = exercise(
        monkeypatch, [candidate], bypass_hook=True, wire_schema=protocol.SCHEMA)
    with pytest.raises(Exception) as failure:
        asyncio.run(run())
    assert failure.value.code == "workbuddy_structured_output_invalid"
    assert not checks[-1]["schema_valid"] and candidate["optional"] == bad
