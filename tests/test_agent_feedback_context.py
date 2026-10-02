"""Offline regressions for the platform-owned assistant context and safety gates."""
import asyncio
import json
import os
import time
from contextlib import contextmanager

import pytest

from sap_business_agents_platform.agent_feedback_context import conversation_context, pending_questions, safe_context
from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError
from tests.test_agent_authoring import Runtime, _service, create
from tests.test_agent_feedback_session import request


def test_inherited_report_restrictions_and_string_credentials_are_redacted(tmp_path):
    service, _, _ = _service(tmp_path)
    report = service._limited_feedback_report({
        "business_report": {"restricted": True, "nested": {"rows": ["synthetic-private-row"],
            "deeper": {"records": ["synthetic-private-row"], "status": "incomplete"}},
            "summary": "Authorization: Bearer synthetic-private-credential"},
    })
    assert "synthetic-private" not in json.dumps(report)
    assert report["business_report"]["nested"]["deeper"]["status"] == "incomplete"
    assert "synthetic-private" not in str(safe_context("密码：synthetic-private-password https://user:synthetic-private-secret@example.test/path"))
    assert "rows" not in service._limited_feedback_report({"restricted": True, "business_report": {"nested": {"rows": [1]}}})["business_report"]["nested"]


def test_long_context_preserves_unanswered_options_and_confirmed_decisions():
    question = {"id": "question-old", "revision": 1, "question": {"zh": "选择范围", "en": "Choose scope"},
                "answer_mode": "choice", "options": [{"id": "one", "label": {"zh": "范围一", "en": "Scope one"}}]}
    turns = [{"turn": 1, "kind": "feedback", "status": "completed", "decision": {"pending_clarification": question}}]
    turns += [{"turn": i, "kind": "feedback", "status": "completed", "user_message": f"message {i}",
               "decision": {"action": "reply"}} for i in range(2, 33)]
    turns[1]["decision"]["context"] = {"clarification": {"id": "answered-question", "question": "Keep the rule?", "selected_option": None}}
    history = conversation_context(turns, 1, 33)
    assert len(history) == 21
    assert history[0]["pending_clarifications"][0]["options"] == question["options"]
    assert history[0]["decisions"][0]["user_answer"] == "message 2"
    assert turns[0]["decision"]["pending_clarification"] == question  # No rewrite of original history.
    assert pending_questions(turns, 2)[0]["state"] == "expired"


def test_multi_annotation_batch_produces_one_reviewable_revision(tmp_path):
    service, store, _ = _service(tmp_path)
    class Edits(Runtime):
        async def review_agent_feedback(self, **kwargs):
            self.calls.append(kwargs)
            return {"action": "revise_agent", "summary": {"zh": "已更新两个名称", "en": "Updated both names"},
                    "edits": [{"op": "replace", "path": "/manifest/title/zh", "value": "新名称"},
                              {"op": "replace", "path": "/manifest/title/en", "value": "New name"}]}
    service.runtime = Edits()
    draft = create(service)
    annotations = [{"kind": "manifest_field", "path": f"/manifest/title/{lang}", "revision": draft["revision"],
                    "comment": f"Update {lang}"} for lang in ("zh", "en")]
    async def scenario():
        response = await service.submit_feedback(draft["draft_id"], request(draft, "调整两处名称", "batch", annotations=annotations))
        await service._feedback_tasks[response["task_id"]]
    asyncio.run(scenario())
    revised = service.get_draft(draft["draft_id"])
    assert revised["revision"] == draft["revision"] + 1
    assert len(service.runtime.calls) == 1 and len(service.runtime.calls[0]["feedback_context"]["annotations"]) == 2
    assert service.runtime.calls[0]["thread_id"] is None
    assert {change["path"] for change in revised["conversation"][-1]["diff"]} == {"/manifest/title/zh", "/manifest/title/en"}
    assert store.get_agent_operation(draft["draft_id"]) is None


def test_choices_require_an_explicit_choice_not_bare_yes(tmp_path):
    service, _, _ = _service(tmp_path)
    class Choices(Runtime):
        async def review_agent_feedback(self, **kwargs):
            result = await super().review_agent_feedback(**kwargs)
            if self.action == "clarify":
                result["clarification"] = {"question": {"zh": "请选择范围", "en": "Choose scope"}, "answer_mode": "choice",
                    "options": [{"id": key, "label": {"zh": label, "en": key}} for key, label in (("one", "范围一"), ("two", "范围二"))]}
            return result
    service.runtime = Choices("clarify")
    draft = create(service)
    async def scenario():
        first = await service.submit_feedback(draft["draft_id"], request(draft, "请帮我确定范围", "ask"))
        await service._feedback_tasks[first["task_id"]]
        current = service.get_draft(draft["draft_id"])
        pending = current["pending_clarifications"][0]
        with pytest.raises(AgentLifecycleError, match="Choose an option"):
            await service.submit_feedback(draft["draft_id"], request(current, "可以", "ambiguous", replyToClarificationId=pending["id"]))
        service.runtime.action = "reply"
        second = await service.submit_feedback(draft["draft_id"], request(current, "范围二", "answer",
            replyToClarificationId=pending["id"], clarificationOptionId="two"))
        await service._feedback_tasks[second["task_id"]]
    asyncio.run(scenario())
    assert service.runtime.calls[-1]["feedback_context"]["clarification"]["selected_option"]["id"] == "two"
    assert not service.get_draft(draft["draft_id"])["pending_clarifications"]


def test_multiple_pending_confirmations_reject_unbound_short_answer(tmp_path):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime("reply")
    draft = create(service)
    for number in (2, 3):
        store.save_agent_conversation_turn({"draft_id": draft["draft_id"], "turn": number, "kind": "feedback", "status": "completed",
            "base_revision": draft["revision"], "decision": {"pending_clarification": {
                "id": f"question-{number}", "question": {"zh": "确认吗？", "en": "Confirm?"}, "revision": draft["revision"], "answer_mode": "confirm"}}})
    current = service.get_draft(draft["draft_id"])
    with pytest.raises(AgentLifecycleError) as failure:
        asyncio.run(service.submit_feedback(draft["draft_id"], request(current, "可以", "ambiguous")))
    assert failure.value.code == "agent_feedback_clarification_required"


@pytest.mark.parametrize("kind", ["trial", "acceptance"])
def test_plain_evidence_references_must_match_revision(tmp_path, kind):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime("reply")
    draft = create(service)
    if kind == "trial":
        store.get_agent_validation_attempt = lambda *_: {"revision": draft["revision"] - 1, "report": {}, "status": "completed"}
        extra = {"runId": "old-trial"}
    else:
        store.get_agent_acceptance_campaign = lambda *_: {"revision": draft["revision"] - 1, "report": {}, "cases": []}
        extra = {"acceptanceCampaignId": "old-campaign"}
    with pytest.raises(AgentLifecycleError) as failure:
        asyncio.run(service.submit_feedback(draft["draft_id"], request(draft, "解释报告", "old-reference", **extra)))
    assert failure.value.code == "agent_feedback_context_stale"
    assert not service.runtime.calls


def test_tool_events_are_safe_replayable_and_available_in_completed_turn(tmp_path):
    service, store, _ = _service(tmp_path)
    class Audited(Runtime):
        @contextmanager
        def bind_events(self, sink):
            self.sink = sink
            yield
        async def review_agent_feedback(self, **kwargs):
            self.sink("agent_runtime_turn_started", {"provider_id": "mock", "resumed": False, "prompt": "private-message"})
            store.append_agent_operation_tool_event(draft["draft_id"], kwargs["operation_id"],
                {"tool": "tool_catalog_search", "status": "completed", "code": "ok", "params": {"password": "private-value"}, "rows": ["private-row"]})
            return await super().review_agent_feedback(**kwargs)
    service.runtime = Audited("reply")
    draft = create(service)
    async def scenario():
        started = await service.submit_feedback(draft["draft_id"], request(draft, "解释当前定义", "event"))
        await service._feedback_tasks[started["task_id"]]
    asyncio.run(scenario())
    events = store.list_agent_conversation_events(draft["draft_id"])
    assert "tool_summary" in {event["kind"] for event in events}
    assert "runtime_diagnostic" in {event["kind"] for event in events}
    assert "private-" not in str(events)
    cursor = events[2]["sequence"]
    assert store.list_agent_conversation_events(draft["draft_id"], after=cursor) == events[3:]
    assert service.get_draft(draft["draft_id"])["conversation"][-1]["decision"]["tool_events"][0]["tool"] == "tool_catalog_search"


def test_image_cleanup_runs_without_a_new_upload(tmp_path):
    service, _, _ = _service(tmp_path)
    service.runtime = Runtime("reply")
    draft = create(service)
    png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/qz8AAAAASUVORK5CYII="
    expired = service.save_feedback_image(draft["draft_id"], png)
    fresh = service.save_feedback_image(draft["draft_id"], png)
    folder = service._feedback_image_directory(draft["draft_id"])
    old = folder / f"{expired['image_id']}.bin"
    stamp = time.time() - 86401
    os.utime(old, (stamp, stamp))
    assert service.cleanup_feedback_images() == 1
    assert not old.exists() and (folder / f"{fresh['image_id']}.bin").exists()
    async def lifecycle():
        await service.start_feedback_image_cleanup()
        assert service._feedback_image_cleanup_task is not None
        await service.stop()
        assert service._feedback_image_cleanup_task.done()
    asyncio.run(lifecycle())


@pytest.mark.parametrize("after,last_event_id,expected", [(0, "2", [3, 4]), (3, "1", [4]), (2, "invalid", [3, 4])])
def test_event_stream_reconnect_uses_larger_cursor_without_resubmission(tmp_path, after, last_event_id, expected):
    from sap_business_agents_platform.agent_feedback_events import stream_feedback_events
    service, store, _ = _service(tmp_path)
    draft = create(service)
    for number in range(4):
        store.append_agent_conversation_event(draft["draft_id"], "phase", {"completed_units": number}, 2)
    class Request:
        headers = {"last-event-id": last_event_id}
        checks = 0
        async def is_disconnected(self):
            self.checks += 1
            return self.checks > 1
    async def scenario():
        return [event async for event in stream_feedback_events(Request(), store, draft["draft_id"], after=after, poll_interval=0)]
    frames = asyncio.run(scenario())
    assert [int(frame.splitlines()[0].removeprefix("id: ")) for frame in frames] == expected
    assert store.get_agent_operation(draft["draft_id"]) is None
    assert len(store.list_agent_conversation_events(draft["draft_id"])) == 4
