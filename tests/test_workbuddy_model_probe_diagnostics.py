"""Probe mismatch diagnostics: exact field/call causes, no raw output retention."""
import asyncio
import json

import pytest

from sap_business_agents_platform import workbuddy_model_protocol as protocol
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError, digest
from tests.test_workbuddy_three_layers import fixture
from tests.workbuddy_integration_fakes import probe_result


def output(challenge):
    return {"status": "ok", "challenge": challenge.challenge, "receipt": challenge.receipt,
            "optional": None, "count": 1, "nested": {"kind": "echo"}}


@pytest.mark.parametrize("field,replacement,path", [
    ("challenge", "wrong-challenge", "/challenge"),
    ("receipt", "wrong-receipt", "/receipt"),
    ("optional", "", "/optional"),
    ("count", 2, "/count"),
    ("nested", {"kind": "wrong-kind"}, "/nested/kind"),
])
def test_reports_exact_field_and_safe_value_summary(field, replacement, path):
    challenge = protocol.Challenge()
    asyncio.run(challenge.handle(protocol.TOOL, {"challenge": challenge.challenge}))
    value = output(challenge)
    value[field] = replacement
    with pytest.raises(WorkBuddyError, match="echo_invalid"):
        challenge.verify(value)
    diagnostic = challenge.diagnostics(value)
    assert diagnostic["mismatched_fields"] == [path]
    assert diagnostic["tool_calls"]["matches"]
    item = next(item for item in diagnostic["fields"] if item["path"] == path)
    actual = replacement["kind"] if field == "nested" else replacement
    assert item["actual"]["sha256"] == digest(actual)
    if field == "optional":
        assert item["expected"]["type"] == "null"
        assert item["actual"]["type"] == "string" and item["actual"]["length"] == 0
    encoded = json.dumps(diagnostic)
    assert challenge.challenge not in encoded and challenge.receipt not in encoded
    if isinstance(actual, str) and actual:
        assert actual not in encoded


def test_reports_missing_tool_even_when_output_fields_match():
    challenge = protocol.Challenge()
    value = output(challenge)
    with pytest.raises(WorkBuddyError, match="echo_invalid"):
        challenge.verify(value)
    diagnostic = challenge.diagnostics(value)
    assert diagnostic["tool_calls"] == {"expected": 1, "attempted": 0, "successful": 0,
                                       "matches": False, "attempts": []}
    assert diagnostic["output_matches"] and diagnostic["mismatched_fields"] == []


@pytest.mark.parametrize("case", ["name", "argument", "duplicate"])
def test_records_invalid_tool_attempts_before_rejection(case):
    challenge = protocol.Challenge()
    if case == "duplicate":
        asyncio.run(challenge.handle(protocol.TOOL, {"challenge": challenge.challenge}))
    name = "unexpected-private-tool" if case == "name" else protocol.TOOL
    arguments = {"challenge": "private-invalid-argument" if case == "argument" else challenge.challenge}
    with pytest.raises(WorkBuddyError, match="tool_invalid"):
        asyncio.run(challenge.handle(name, arguments))
    diagnostic = challenge.diagnostics()
    calls = diagnostic["tool_calls"]
    assert calls["attempted"] == (2 if case == "duplicate" else 1)
    assert calls["successful"] == (1 if case == "duplicate" else 0)
    assert not calls["matches"]
    last = calls["attempts"][-1]
    assert last["tool_matches"] is (case != "name")
    assert last["arguments_match"] is (case != "argument")
    assert last["duplicate"] is (case == "duplicate")
    assert not diagnostic["output_received"]
    assert "private" not in json.dumps(diagnostic)


def test_detects_receipt_copied_from_challenge():
    challenge = protocol.Challenge()
    asyncio.run(challenge.handle(protocol.TOOL, {"challenge": challenge.challenge}))
    value = {**output(challenge), "receipt": challenge.challenge}
    field = next(item for item in challenge.diagnostics(value)["fields"] if item["path"] == "/receipt")
    assert not field["matches"] and field["matches_challenge"] and not field["matches_receipt"]


@pytest.mark.parametrize("failure", [False, True])
def test_persists_diagnostics_in_check_and_idempotent_request(tmp_path, monkeypatch, failure):
    manager, _ = fixture(tmp_path, monkeypatch)
    async def run(**kwargs):
        result = await probe_result(manager, kwargs, "next-model")
        if failure:
            value = json.loads(result["text"])
            value["receipt"] = "not-the-tool-result"
            result["text"] = json.dumps(value)
        return result
    monkeypatch.setattr(manager.supervisor, "run", run)
    result = asyncio.run(manager.check_model("next-model", request_id="diagnostic-check"))
    check = result["check"]
    assert check["compatible"] is (not failure)
    assert check["probe_diagnostics"]["mismatched_fields"] == (["/receipt"] if failure else [])
    assert check["check_digest"] == digest({k: v for k, v in check.items() if k != "check_digest"})
    saved = manager.environment.state()
    assert saved["models"]["next-model"]["probe_diagnostics"] == check["probe_diagnostics"]
    assert saved["model_check_requests"]["diagnostic-check"]["result"] == check
    assert asyncio.run(manager.check_model("next-model", request_id="diagnostic-check"))["check"] == check
