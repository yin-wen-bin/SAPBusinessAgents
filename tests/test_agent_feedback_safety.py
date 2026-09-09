"""Feedback deadline, shutdown and immutable-history regression tests; no live Runtime."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError
from sap_business_agents_platform.models import utc_now
from tests.test_agent_authoring import Runtime, _service, create, feedback_payload, send_and_wait


@pytest.mark.parametrize("abort_confirmed", [True, False])
def test_emergency_cleanup_never_applies_late_response_and_blocks_unconfirmed_shutdown(tmp_path, abort_confirmed):
    service, store, settings = _service(tmp_path)
    service.settings = replace(settings, agent_feedback_cleanup_seconds=0.025)
    service.feedback_timeout_seconds = 0.01

    class StubbornRuntime(Runtime):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()
            self.abort_calls = []
            self.active = None

        async def review_agent_feedback(self, **kwargs):
            self.active = asyncio.current_task()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    continue
            package = copy.deepcopy(kwargs["package"])
            package["manifest"]["title"]["zh"] = "迟到结果不应写入"
            return {"action": "revise_agent", "summary": {"zh": "完成", "en": "Done"}, "package": package}

        async def abort_agent_feedback(self, operation_id):
            self.abort_calls.append(operation_id)
            return abort_confirmed

    runtime = StubbornRuntime()
    service.runtime = runtime
    draft = create(service)

    async def scenario():
        submitted = await service.submit_feedback(draft["draft_id"], feedback_payload(draft))
        await service._feedback_tasks[submitted["task_id"]]
        failed = service.get_draft(draft["draft_id"])
        try:
            assert runtime.abort_calls == [submitted["task_id"]]
            assert failed["conversation"][-1]["status"] == "failed"
            assert failed["conversation"][-1]["decision"]["error_code"] == (
                "agent_feedback_timeout" if abort_confirmed else "agent_feedback_cleanup_failed"
            )
            if abort_confirmed:
                assert store.get_agent_operation(draft["draft_id"]) is None
            else:
                assert store.get_agent_operation(draft["draft_id"])["status"] == "cancelling"
                with pytest.raises(AgentLifecycleError) as blocked:
                    await service.submit_feedback(draft["draft_id"], feedback_payload(failed, request="retry"))
                assert blocked.value.code == "agent_draft_operation_active"
        finally:
            runtime.release.set()
            await runtime.active
        assert service.get_draft(draft["draft_id"])["package"] == draft["package"]
        assert service.get_draft(draft["draft_id"])["revision"] == 1

    asyncio.run(scenario())


def test_deadline_at_database_write_rolls_back_projection_and_revision(tmp_path, monkeypatch):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime("revise_agent", mutate=lambda package: package["manifest"]["title"].update(zh="新名字"))
    draft = create(service)
    clock = [0.0]
    service._feedback_clock = lambda: clock[0]
    original_save = store.save_agent_authoring_draft

    def database_wait(item, **kwargs):
        if item["revision"] == 2:
            # Deterministic equivalent of expiry while SQLite waits for its write lock.
            clock[0] = 3601.0
        return original_save(item, **kwargs)

    monkeypatch.setattr(store, "save_agent_authoring_draft", database_wait)
    _, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert result["conversation"][-1]["decision"]["error_code"] == "agent_feedback_timeout"
    assert result["revision"] == 1
    assert result["package"] == draft["package"]
    assert service._read_json(Path(draft["path"]) / "agent.json") == draft["package"]["manifest"]
    assert store.get_agent_operation(draft["draft_id"]) is None


def test_restart_preserves_feedback_runtime_timeout_and_retry_history(tmp_path):
    service, store, _ = _service(tmp_path)
    draft = create(service)
    operation = store.reserve_agent_operation(draft["draft_id"], expected_revision=1, kind="feedback", request_id="restart", input_hash="fingerprint")
    started = (datetime.now(timezone.utc) - timedelta(seconds=15)).isoformat()
    snapshot = {"provider_id": "codex", "model": "gpt-5.6-sol", "reasoning_effort": "medium", "configuration_digest": "frozen"}
    store.save_agent_conversation_turn({
        "draft_id": draft["draft_id"], "turn": 2, "kind": "feedback", "status": "running",
        "user_message": "恢复测试", "base_revision": 1, "result_revision": None,
        "decision": {"task_id": operation["operation_id"], "runtime_snapshot": snapshot, "retry_of_turn": 1,
                     "execution": {"started_at": started, "timeout_seconds": 3600}},
        "created_at": utc_now(),
    })
    store.recover_agent_operations()
    turn = store.list_agent_conversation_turns(draft["draft_id"])[-1]
    assert turn["status"] == "failed"
    assert turn["decision"]["error_code"] == "agent_operation_interrupted"
    assert turn["decision"]["runtime_snapshot"] == snapshot
    assert turn["decision"]["retry_of_turn"] == 1
    assert turn["decision"]["execution"]["timeout_seconds"] == 3600
    assert turn["decision"]["execution"]["elapsed_seconds"] >= 15
    assert turn["decision"]["execution"]["completed_at"] == turn["completed_at"]
    assert store.get_agent_operation(draft["draft_id"]) is None


def test_legacy_timeout_has_no_invented_execution_limit(tmp_path):
    service, store, _ = _service(tmp_path)
    draft = create(service)
    store.save_agent_conversation_turn({
        "draft_id": draft["draft_id"], "turn": 2, "kind": "feedback", "status": "failed",
        "decision": {"error_code": "agent_feedback_timeout"}, "completed_at": utc_now(),
    })
    visible = service.get_draft(draft["draft_id"])["conversation"][-1]
    assert "execution" not in visible["decision"]
    assert "runtime_snapshot" not in visible["decision"]


def test_database_deadline_before_commit_rolls_back_both_rows(tmp_path):
    service, store, _ = _service(tmp_path)
    draft = create(service)
    operation = store.reserve_agent_operation(draft["draft_id"], expected_revision=1, kind="feedback")
    revised = store.get_agent_authoring_draft(draft["draft_id"])
    revised["revision"] = 2
    package = copy.deepcopy(draft["package"])
    package["manifest"]["title"]["zh"] = "不得提交"
    checks = []
    def deadline():
        checks.append(True)
        if len(checks) == 2:
            raise RuntimeError("deadline-after-sql")
    with pytest.raises(RuntimeError, match="deadline-after-sql"):
        store.save_agent_authoring_draft(revised, package=package, expected_revision=1,
                                         operation_id=operation["operation_id"], deadline_check=deadline)
    assert len(checks) == 2
    assert store.get_agent_authoring_draft(draft["draft_id"])["revision"] == 1
    with pytest.raises(KeyError):
        store.get_agent_authoring_revision(draft["draft_id"], 2)


def test_changed_content_and_runtime_thread_share_one_commit(tmp_path, monkeypatch):
    service, store, _ = _service(tmp_path)
    service.runtime = Runtime("revise_agent", mutate=lambda package: package["manifest"]["title"].update(zh="已完成的修订"))
    draft = create(service)
    original_save = store.save_agent_authoring_draft
    writes = []
    def save(item, **kwargs):
        writes.append((item["revision"], item.get("thread_id"), kwargs.get("package") is not None))
        if item["revision"] == 2 and kwargs.get("package") is None:
            raise OSError("Post-commit thread metadata must not create a false failure")
        return original_save(item, **kwargs)
    monkeypatch.setattr(store, "save_agent_authoring_draft", save)
    _, result = asyncio.run(send_and_wait(service, draft, feedback_payload(draft)))
    assert result["revision"] == 2
    assert result["conversation"][-1]["status"] == "completed"
    assert result["conversation"][-1]["result_revision"] == 2
    assert result["thread_id"] == "isolated-thread"
    assert [write for write in writes if write[0] == 2] == [(2, "isolated-thread", True)]


def test_cancellation_resistant_abort_cannot_extend_cleanup_deadline(tmp_path):
    service, store, settings = _service(tmp_path)
    service.settings = replace(settings, agent_feedback_cleanup_seconds=0.025)
    service.feedback_timeout_seconds = 0.01

    class UnresponsiveCleanup(Runtime):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()
            self.active = None

        async def review_agent_feedback(self, **kwargs):
            self.active = asyncio.current_task()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    continue
            return {"action": "reply", "summary": {"zh": "迟到", "en": "Late"}}

        async def abort_agent_feedback(self, operation_id):
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    continue
            return False

    runtime = UnresponsiveCleanup()
    service.runtime = runtime
    draft = create(service)

    async def scenario():
        submitted = await service.submit_feedback(draft["draft_id"], feedback_payload(draft))
        owner = service._feedback_tasks[submitted["task_id"]]
        done, _ = await asyncio.wait({owner}, timeout=0.25)
        runtime.release.set()
        await owner
        await asyncio.gather(runtime.active, return_exceptions=True)
        assert done, "Abort swallowed cancellation and exceeded the configured cleanup deadline"
        turn = store.list_agent_conversation_turns(draft["draft_id"])[-1]
        assert turn["decision"]["error_code"] == "agent_feedback_cleanup_failed"
        assert store.get_agent_operation(draft["draft_id"])["status"] == "cancelling"
    asyncio.run(scenario())


def test_repeated_cancel_does_not_release_unconfirmed_runtime(tmp_path):
    service, store, settings = _service(tmp_path)
    service.settings = replace(settings, agent_feedback_cleanup_seconds=0.05)

    class StubbornCancellation(Runtime):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.cancel_seen = asyncio.Event()
            self.release = asyncio.Event()
            self.active = None

        async def review_agent_feedback(self, **kwargs):
            self.active = asyncio.current_task()
            self.started.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancel_seen.set()
            return {"action": "reply", "summary": {"zh": "迟到", "en": "Late"}}

        async def abort_agent_feedback(self, operation_id):
            return False

    runtime = StubbornCancellation()
    service.runtime = runtime
    draft = create(service)

    async def scenario():
        submitted = await service.submit_feedback(draft["draft_id"], feedback_payload(draft))
        await runtime.started.wait()
        first = asyncio.create_task(service.cancel_feedback(draft["draft_id"], submitted["turn"]))
        await runtime.cancel_seen.wait()
        second = asyncio.create_task(service.cancel_feedback(draft["draft_id"], submitted["turn"]))
        try:
            await asyncio.gather(first, second)
            turn = store.list_agent_conversation_turns(draft["draft_id"])[-1]
            assert turn["decision"]["error_code"] == "agent_feedback_cleanup_failed"
            assert store.get_agent_operation(draft["draft_id"])["status"] == "cancelling"
            assert store.get_agent_authoring_draft(draft["draft_id"])["revision"] == 1
        finally:
            runtime.release.set()
            await asyncio.gather(runtime.active, return_exceptions=True)
    asyncio.run(scenario())
