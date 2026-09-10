import asyncio
import copy

import pytest

from sap_business_agents_platform.authoring_harness import AuthoringHarnessError, CheckOutcome, RepairLoop, content_digest


def execute(checker, *, rounds=5, clock=None):
    calls, records = [], []
    async def revise(package, issues, remaining):
        calls.append({"issues": issues, "remaining": remaining})
        return {"action": "revise_agent", "package": {"revision": len(calls)}, "summary": {"zh": "修改", "en": "Change"}}
    async def check(package, remaining):
        return checker(package)
    async def run():
        return await RepairLoop(max_rounds=rounds, **({"clock": clock} if clock else {})).run(
            {}, revise=revise, check=check, checkpoint=lambda v: records.append(copy.deepcopy(v)), assert_current=lambda: None)
    return run, calls, records


def test_failed_check_is_fed_back_then_success_is_checkpointed():
    run, calls, records = execute(lambda p: CheckOutcome("repairable", ({"code": "bad_filter"},))
                                  if p["revision"] == 1 else CheckOutcome("passed", evidence_refs=("verified-ref",)))
    result = asyncio.run(run())
    assert result["package"] == {"revision": 2}
    assert calls[1]["issues"] == ({"code": "bad_filter"},)
    assert records[0]["candidate_digest"] == content_digest({"revision": 1})
    assert records[1]["evidence_refs"] == ["verified-ref"]


def test_identical_failure_stops_even_if_candidate_changed_cosmetically():
    run, calls, records = execute(lambda _: CheckOutcome("repairable", ({"code": "bad_filter"},)))
    with pytest.raises(AuthoringHarnessError, match="agent_harness_no_progress"):
        asyncio.run(run())
    assert len(calls) == len(records) == 2


def test_environment_gap_does_not_trigger_rule_rewrites():
    run, calls, records = execute(lambda _: CheckOutcome("blocked", ({"code": "test_data_gap"},)))
    with pytest.raises(AuthoringHarnessError, match="test_data_gap"):
        asyncio.run(run())
    assert len(calls) == 1


def test_round_limit_is_five_even_if_configured_higher():
    run, calls, _ = execute(lambda p: CheckOutcome("repairable", ({"code": f"failure_{p['revision']}"},)), rounds=100)
    with pytest.raises(AuthoringHarnessError, match="agent_harness_round_limit"):
        asyncio.run(run())
    assert len(calls) == 5


def test_late_response_cannot_checkpoint_or_succeed():
    now, records = [0.0], []
    async def revise(*args):
        now[0] = 3600.0
        return {"action": "revise_agent", "package": {}}
    async def check(*args):
        pytest.fail("No check may start after the global deadline")
    with pytest.raises(AuthoringHarnessError, match="agent_feedback_timeout"):
        asyncio.run(RepairLoop(clock=lambda: now[0]).run({}, revise=revise, check=check,
            checkpoint=records.append, assert_current=lambda: None))
    assert records == []


def test_revision_change_discards_response():
    state, records = [False], []
    def assert_current():
        if state[0]:
            raise AuthoringHarnessError("agent_draft_conflict")
    async def revise(*args):
        state[0] = True
        return {"action": "revise_agent", "package": {}}
    async def check(*args):
        pytest.fail("Stale candidates must not be tested")
    with pytest.raises(AuthoringHarnessError, match="agent_draft_conflict"):
        asyncio.run(RepairLoop().run({}, revise=revise, check=check, checkpoint=records.append, assert_current=assert_current))
    assert records == []


def test_model_can_request_business_clarification_without_claiming_tests():
    async def revise(*args):
        return {"action": "clarify", "summary": {"zh": "请指定公司代码", "en": "Specify company code"}}
    async def check(*args):
        pytest.fail("Clarification must not manufacture a test")
    records = []
    result = asyncio.run(RepairLoop().run({}, revise=revise, check=check, checkpoint=records.append, assert_current=lambda: None))
    assert result["action"] == "clarify" and records == []
