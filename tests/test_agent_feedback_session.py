"""Draft assistant context, annotation and queued-turn boundaries (offline)."""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from tests.test_agent_authoring import Runtime, _service, create
from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError
from sap_business_agents_platform.models import AgentFeedbackRequest


def request(draft, text, request_id, **extra):
    return AgentFeedbackRequest(
        baseTurn=max((turn["turn"] for turn in draft["conversation"]), default=0),
        baseRevision=draft["revision"], feedback=text, requestId=request_id, **extra,
    )


def test_annotation_is_bound_to_existing_manifest_field_and_revision(tmp_path):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime("reply")
    draft = create(service)
    valid = {"kind": "manifest_field", "path": "/manifest/title/zh", "revision": draft["revision"],
             "comment": "核对中文名称"}

    async def scenario():
        response = await service.submit_feedback(draft["draft_id"], request(draft, "请解释名称", "field", annotations=[valid]))
        await service._feedback_tasks[response["task_id"]]

    asyncio.run(scenario())
    assert service.runtime.calls[0]["feedback_context"]["annotations"][0]["path"] == "/manifest/title/zh"
    for annotation in ({**valid, "path": "/manifest/not-a-field"},
                       {**valid, "revision": draft["revision"] + 1}):
        current = service.get_draft(draft["draft_id"])
        with pytest.raises(AgentLifecycleError) as failure:
            asyncio.run(service.submit_feedback(draft["draft_id"], request(current, "检查", "bad-" + annotation["path"] + str(annotation["revision"]), annotations=[annotation])))
        assert failure.value.code == "agent_feedback_annotation_invalid"
    assert store.get_agent_operation(draft["draft_id"]) is None


def test_short_reply_uses_only_current_explicit_clarification(tmp_path):
    service, _, _ = _service(tmp_path)
    service.runtime = Runtime("clarify")
    draft = create(service)

    async def scenario():
        first = await service.submit_feedback(draft["draft_id"], request(draft, "需要哪个范围？", "question"))
        await service._feedback_tasks[first["task_id"]]
        current = service.get_draft(draft["draft_id"])
        clarification = current["conversation"][-1]["decision"]["pending_clarification"]
        service.runtime.action = "reply"
        second = await service.submit_feedback(draft["draft_id"], request(current, "可以", "answer", replyToClarificationId=clarification["id"]))
        await service._feedback_tasks[second["task_id"]]
        return clarification

    clarification = asyncio.run(scenario())
    assert service.runtime.calls[-1]["feedback_context"]["clarification"]["id"] == clarification["id"]
    current = service.get_draft(draft["draft_id"])
    with pytest.raises(AgentLifecycleError) as failure:
        asyncio.run(service.submit_feedback(draft["draft_id"], request(current, "可以", "orphan")))
    assert failure.value.code == "agent_feedback_clarification_required"


def test_waiting_turn_does_not_claim_active_slot_and_dispatches_in_order(tmp_path):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime("reply", delay=0.03)
    draft = create(service)

    async def scenario():
        first = await service.submit_feedback(draft["draft_id"], request(draft, "先解释", "first"))
        current = service.get_draft(draft["draft_id"])
        queued_request = request(current, "再解释", "second", enqueueIfBusy=True)
        second = await service.submit_feedback(draft["draft_id"], queued_request)
        assert second["status"] == "waiting" and second["task_id"] is None
        assert store.get_agent_operation(draft["draft_id"])["operation_id"] == first["task_id"]
        assert (await service.submit_feedback(draft["draft_id"], queued_request))["turn"] == second["turn"]
        await service._feedback_tasks[first["task_id"]]
        await asyncio.sleep(0.001)
        task = store.get_agent_operation(draft["draft_id"])
        assert task is not None and task["operation_id"] != first["task_id"]
        await service._feedback_tasks[task["operation_id"]]

    asyncio.run(scenario())
    turns = service.get_draft(draft["draft_id"])["conversation"]
    assert [turn["status"] for turn in turns[-2:]] == ["completed", "completed"]
    events = store.list_agent_conversation_events(draft["draft_id"])
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))


def test_changed_revision_pauses_following_message(tmp_path):
    service, store, _ = _service(tmp_path)
    def mutate(package):
        package["manifest"]["title"]["zh"] = "新名称"
    service.runtime = Runtime("revise_agent", mutate=mutate, delay=0.03)
    draft = create(service)

    async def scenario():
        first = await service.submit_feedback(draft["draft_id"], request(draft, "修改名称", "change"))
        current = service.get_draft(draft["draft_id"])
        await service.submit_feedback(draft["draft_id"], request(current, "继续", "following", enqueueIfBusy=True))
        await service._feedback_tasks[first["task_id"]]

    asyncio.run(scenario())
    current = service.get_draft(draft["draft_id"])
    assert current["revision"] == draft["revision"] + 1
    assert current["conversation"][-1]["status"] == "needs_review"
    assert store.get_agent_operation(draft["draft_id"]) is None


def test_restart_never_dispatches_an_orphaned_waiting_message(tmp_path):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime("reply")
    draft = create(service)
    store.save_agent_conversation_turn({
        "draft_id": draft["draft_id"], "turn": 1, "kind": "feedback",
        "status": "waiting", "user_message": "待核对的请求",
        "decision": {"intent": "revise"}, "base_revision": draft["revision"],
    })
    store.recover_agent_operations()
    turn = store.list_agent_conversation_turns(draft["draft_id"])[-1]
    assert turn["status"] == "needs_review"
    assert store.claim_next_agent_feedback(draft["draft_id"]) is None


def test_annotation_rejects_another_drafts_diagnostic_and_blank_comment(tmp_path):
    service, _, _ = _service(tmp_path)
    service.runtime = Runtime("reply")
    draft = create(service)
    with pytest.raises(ValueError):
        request(draft, "检查", "blank", annotations=[{
            "kind": "manifest_field", "path": "/manifest/title/zh",
            "revision": draft["revision"], "comment": "   ",
        }])
    with pytest.raises(AgentLifecycleError) as failure:
        asyncio.run(service.submit_feedback(draft["draft_id"], request(
            draft, "检查诊断", "foreign-report", annotations=[{
                "kind": "trial_issue", "path": "/errors/0",
                "revision": draft["revision"], "comment": "查看此错误",
                "sourceId": "foreign-run", "sourceDigest": "sha256:invalid",
            }])))
    assert failure.value.code == "agent_feedback_annotation_invalid"


def test_screenshot_is_bounded_to_draft_and_passed_as_image_input(tmp_path):
    service, _, _ = _service(tmp_path)
    service.runtime = Runtime("reply")
    draft = create(service)
    png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
           "AAAAC0lEQVR42mP8/x8AAwMCAO+/qz8AAAAASUVORK5CYII=")
    uploaded = service.save_feedback_image(draft["draft_id"], png)

    async def scenario():
        started = await service.submit_feedback(draft["draft_id"], request(
            draft, "解释截图所标位置", "image", imageIds=[uploaded["image_id"]], intent="explain"))
        await service._feedback_tasks[started["task_id"]]

    asyncio.run(scenario())
    assert service.runtime.calls[0]["image_inputs"] == [png]
    path = service._feedback_image_directory(draft["draft_id"]) / f"{uploaded['image_id']}.bin"
    expired = time.time() - 86401
    os.utime(path, (expired, expired))
    with pytest.raises(AgentLifecycleError) as failure:
        service._feedback_image_data(draft["draft_id"], uploaded["image_id"])
    assert failure.value.code == "agent_feedback_image_expired"


def test_selected_text_must_match_the_pinned_definition(tmp_path):
    service, _, _ = _service(tmp_path)
    service.runtime = Runtime("reply")
    draft = create(service)
    title = draft["package"]["manifest"]["title"]["zh"]

    async def scenario():
        started = await service.submit_feedback(draft["draft_id"], request(
            draft, "解释选中文字", "selection", intent="explain",
            selection={"kind": "manifest_field", "path": "/manifest/title/zh", "excerpt": title,
                       "revision": draft["revision"]}))
        await service._feedback_tasks[started["task_id"]]

    asyncio.run(scenario())
    assert service.runtime.calls[0]["feedback_context"]["selection"]["excerpt"] == title
    current = service.get_draft(draft["draft_id"])
    with pytest.raises(AgentLifecycleError) as failure:
        asyncio.run(service.submit_feedback(draft["draft_id"], request(
            current, "解释", "bad-selection", intent="explain",
            selection={"kind": "manifest_field", "path": "/manifest/title/zh",
                       "excerpt": "不是原始资料", "revision": current["revision"]})))
    assert failure.value.code == "agent_feedback_selection_invalid"


def test_history_and_restricted_report_redact_sensitive_values(tmp_path):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime("reply")
    draft = create(service)
    store.save_agent_conversation_turn({
        "draft_id": draft["draft_id"], "turn": 1, "kind": "feedback",
        "status": "completed", "user_message": "password=private-value",
        "decision": {"summary": {"zh": "token=private-value", "en": "token=private-value"}},
        "base_revision": draft["revision"], "result_revision": draft["revision"],
    })

    async def scenario():
        current = service.get_draft(draft["draft_id"])
        started = await service.submit_feedback(draft["draft_id"], request(
            current, "解释目前状态", "redacted", intent="explain"))
        await service._feedback_tasks[started["task_id"]]

    asyncio.run(scenario())
    history = service.runtime.calls[0]["history"]
    assert "private-value" not in str(history)
    assert "[redacted]" in str(history)
    report = service._limited_feedback_report({
        "business_report": {"restricted": True, "rows": [{"secret": "hidden"}],
                            "status": "incomplete"},
    })
    assert "rows" not in report["business_report"]
    assert report["business_report"]["status"] == "incomplete"


def test_declared_provider_image_capability_fails_closed(tmp_path):
    service, _, _ = _service(tmp_path)
    runtime = Runtime("reply")
    runtime.feedback_capabilities = lambda provider_id, model_id: {
        "stream_events": False, "resume": False, "steer": False,
        "image_input": False,
    }
    service.runtime = runtime
    draft = create(service)
    assert service.get_draft(draft["draft_id"])["assistant_capabilities"]["image_input"] is False
    with pytest.raises(AgentLifecycleError) as failure:
        service.save_feedback_image(draft["draft_id"], "data:image/png;base64,AAAA")
    assert failure.value.code == "agent_feedback_image_unsupported"
