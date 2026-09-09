"""No network: per-turn binding, deadlines and retry history for draft authoring."""
from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_agent_authoring import Runtime, _service, create, feedback_payload, send_and_wait
from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError
from sap_business_agents_platform.models import AgentFeedbackRequest


class ConfigurableRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.effort = "medium"

    def snapshot_for_model(self, provider, model):
        return {"provider_id": provider, "model": model, "reasoning_effort": self.effort,
                "configuration_digest": f"digest-{model}-{self.effort}"}

    def snapshot(self):
        return self.snapshot_for_model("codex", "gpt-5.6-sol")

    @contextmanager
    def pin(self, provider, model, reasoning_effort=None):
        self.bindings.append((provider, model, reasoning_effort))
        yield


def test_feedback_freezes_at_submission_and_next_round_uses_latest_effort(tmp_path):
    service, store, _ = _service(tmp_path)
    runtime = service.runtime = ConfigurableRuntime()
    draft = create(service)
    original_snapshot = copy.deepcopy(draft["metadata"]["runtime_snapshot"])

    async def scenario():
        payload = feedback_payload(draft)
        task = await service.submit_feedback(draft["draft_id"], payload)
        runtime.effort = "high"
        await service._feedback_tasks[task["task_id"]]
        first = service.get_draft(draft["draft_id"])
        duplicate = await service.submit_feedback(draft["draft_id"], payload)
        assert duplicate["task_id"] == task["task_id"]
        await send_and_wait(service, first, feedback_payload(first, request="next-round"))

    asyncio.run(scenario())
    assert runtime.bindings == [("codex", "gpt-5.6-sol", "medium"), ("codex", "gpt-5.6-sol", "high")]
    final = service.get_draft(draft["draft_id"])
    assert final["metadata"]["runtime_snapshot"] == original_snapshot
    first, second = final["conversation"][-2:]
    assert first["decision"]["runtime_snapshot"]["reasoning_effort"] == "medium"
    assert second["decision"]["runtime_snapshot"]["reasoning_effort"] == "high"
    for turn in (first, second):
        timing = turn["decision"]["execution"]
        assert timing["timeout_seconds"] == 3600
        assert timing["started_at"] <= timing["completed_at"]
        assert timing["deadline_at"] > timing["started_at"]
        assert timing["elapsed_seconds"] >= 0
    assert store.get_agent_operation(draft["draft_id"]) is None


@pytest.mark.parametrize("seconds,expected", [(181, "completed"), (3599, "completed"), (3600, "failed")])
def test_deadline_with_controlled_clock_no_hour_wait(tmp_path, seconds, expected):
    service, _, _ = _service(tmp_path)
    runtime = service.runtime = Runtime("revise_agent")
    clock = [0.0]
    service._feedback_clock = lambda: clock[0]
    original = runtime.review_agent_feedback

    async def review(**kwargs):
        result = await original(**kwargs)
        result["package"]["manifest"]["title"]["zh"] = "修改后的名称"
        clock[0] = seconds
        return result

    runtime.review_agent_feedback = review
    draft = create(service)
    _, final = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    turn = final["conversation"][-1]
    assert turn["status"] == expected
    assert turn["decision"]["execution"]["elapsed_seconds"] == seconds
    if expected == "failed":
        assert turn["decision"]["error_code"] == "agent_feedback_timeout"
        assert final["revision"] == draft["revision"]
        assert final["package"] == draft["package"]


def test_retry_after_rename_uses_current_identity_and_fresh_thread(tmp_path):
    service, store, _ = _service(tmp_path)
    runtime = service.runtime = ConfigurableRuntime()
    runtime.action = "bogus"
    draft = create(service)
    _, failed = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    failed_turn = copy.deepcopy(failed["conversation"][-1])
    renamed = service.set_technical_id(draft["draft_id"], SimpleNamespace(expected_revision=1, agent_id="customer-receivables-check"))
    runtime.action, runtime.effort = "reply", "high"
    payload = feedback_payload(renamed, request="retry-1")
    payload.retry_of_turn = failed_turn["turn"]
    _, final = asyncio.run(send_and_wait(service, renamed, payload))
    assert store.list_agent_conversation_turns(draft["draft_id"])[1] == failed_turn
    assert runtime.calls[-1]["thread_id"] is None
    assert runtime.calls[-1]["package"]["manifest"]["slug"] == "customer-receivables-check"
    turn = final["conversation"][-1]
    assert turn["base_revision"] == renamed["revision"]
    assert turn["decision"]["retry_of_turn"] == failed_turn["turn"]
    assert turn["decision"]["agent_id"] == renamed["agent_id"]
    assert turn["decision"]["runtime_snapshot"]["reasoning_effort"] == "high"


def test_retry_rejects_non_failed_or_unknown_turn(tmp_path):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime()
    draft = create(service)
    for source_turn in (1, 999):
        payload = feedback_payload(draft, request=f"retry-{source_turn}")
        payload.retry_of_turn = source_turn
        with pytest.raises(AgentLifecycleError, match="failed or interrupted"):
            asyncio.run(service.submit_feedback(draft["draft_id"], payload))
        assert store.get_agent_operation(draft["draft_id"]) is None


def test_shorter_sdk_timeout_not_reported_as_platform_deadline(tmp_path):
    service, _, _ = _service(tmp_path)
    service.runtime = Runtime()

    async def timeout(**kwargs):
        raise TimeoutError("private SDK response not for UI")

    service.runtime.review_agent_feedback = timeout
    draft = create(service)
    _, final = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    turn = final["conversation"][-1]
    assert turn["decision"]["error_code"] == "runtime_agent_feedback_connection_timeout"
    assert "private SDK" not in str(turn)


def test_legacy_history_does_not_invent_limit_and_cancel_retains_snapshot(tmp_path):
    service, _, _ = _service(tmp_path)
    service.runtime = ConfigurableRuntime()
    service.runtime.delay = 30
    draft = create(service)

    async def scenario():
        response = await service.submit_feedback(draft["draft_id"], feedback_payload(draft))
        await asyncio.sleep(0.01)
        live = service.conversation(draft["draft_id"])["turns"][-1]
        assert live["decision"]["execution"]["elapsed_seconds"] > 0
        await service.cancel_feedback(draft["draft_id"], response["turn"])

    asyncio.run(scenario())
    turn = service.get_draft(draft["draft_id"])["conversation"][-1]
    assert turn["status"] == "cancelled"
    assert turn["decision"]["runtime_snapshot"]["reasoning_effort"] == "medium"
    assert turn["decision"]["execution"]["elapsed_seconds"] > 0
    legacy = {"status": "failed", "decision": {"error_code": "agent_feedback_timeout"}}
    assert service._public_feedback_turn(legacy) == legacy


def test_timeout_cannot_apply_a_provider_late_return(tmp_path):
    service, store, settings = _service(tmp_path)
    service.runtime = Runtime("revise_agent")
    service.settings = replace(settings, agent_feedback_cleanup_seconds=0.05)
    service.feedback_timeout_seconds = 0.005

    async def ignores_cancel(**kwargs):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            return {"action": "revise_agent", "summary": {"zh": "迟到", "en": "Late"},
                    "package": {**kwargs["package"], "readme": "late mutation"}}

    service.runtime.review_agent_feedback = ignores_cancel
    draft = create(service)
    _, final = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert final["revision"] == draft["revision"]
    assert final["package"] == draft["package"]
    assert final["conversation"][-1]["decision"]["error_code"] == "agent_feedback_timeout"
    assert store.get_agent_operation(draft["draft_id"]) is None


@pytest.mark.parametrize(("error", "code"), [
    (RuntimeError("Unauthorized PRIVATE"), "runtime_model_authentication_failed"),
    (RuntimeError("Model requires a newer version PRIVATE"), "runtime_model_incompatible"),
    (RuntimeError("Access denied PRIVATE"), "runtime_model_access_unavailable"),
    (ConnectionError("PRIVATE"), "runtime_agent_feedback_connection_failed"),
    (ValueError("runtime_agent_feedback_invalid"), "runtime_agent_feedback_invalid"),
])
def test_safe_failure_categories_are_persisted_without_raw_sdk_messages(tmp_path, error, code):
    service, _, _ = _service(tmp_path)
    service.runtime = Runtime()
    async def fail(**kwargs):
        raise error
    service.runtime.review_agent_feedback = fail
    draft = create(service)
    _, final = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    turn = final["conversation"][-1]
    assert turn["decision"]["error_code"] == code
    assert "PRIVATE" not in str(turn)
    assert final["revision"] == draft["revision"]
