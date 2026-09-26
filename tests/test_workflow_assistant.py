from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.models import WorkflowFeedbackRequest
from sap_business_agents_platform.workflow_factory import WorkflowDraftError, WorkflowDraftService
from sap_business_agents_platform.workflow_assistant import state, safe_context

ROOT = Path(__file__).resolve().parents[1]


class Author:
    def __init__(self):
        self.calls = []
        self.action = "explain"
        self.block = None
        self.mutate = None

    async def author_workflow_v2(self, **kwargs):
        self.calls.append(kwargs)
        kwargs["emit"]("research", {})
        if self.block:
            await self.block.wait()
        candidate = deepcopy(kwargs["workflow"])
        if self.mutate:
            self.mutate(candidate)
        return {"action": self.action, "answer": "**Done**", "question": "Which scope?",
                "workflow": candidate}


def setup(tmp_path):
    settings = Settings(repository_root=ROOT, data_root=tmp_path / "data", draft_root=tmp_path / "drafts")
    author = Author()
    service = WorkflowDraftService(settings, RunStore(tmp_path / "runs.sqlite"), AgentRepository(ROOT / "agents"), None, None, author)
    workflow = json.loads((ROOT / "workflows/Common/p2p-batch-payment-review/workflow.json").read_text(encoding="utf-8"))
    draft = service.create(workflow["title"], workflow["description"], workflow)
    return service, author, draft


def payload(request="r1", intent="explain", revision=1, **extra):
    return WorkflowFeedbackRequest(baseTurn=1, baseRevision=revision, feedback="Why?", requestId=request, intent=intent, **extra).model_dump(mode="json", by_alias=True)


async def finished(assistant):
    for _ in range(100):
        if not assistant.tasks:
            return
        await asyncio.sleep(.01)
    raise AssertionError("assistant did not finish")


def test_explanation_preserves_workflow_qualification_and_idempotency(tmp_path):
    service, author, draft = setup(tmp_path)
    draft.status = "validated"
    draft.validation = {"valid": True, "verdict": "pass"}
    service.store.save_workflow_draft(draft)
    async def check():
        service.assistant.submit(draft.draft_id, payload())
        service.assistant.submit(draft.draft_id, payload())
        await finished(service.assistant)
    asyncio.run(check())
    current = service.get(draft.draft_id)
    assert current.status == "validated" and current.revision == 1 and current.validation == draft.validation
    assert len(author.calls) == 1
    assert len(service.revisions(draft.draft_id)) == 1
    assert state(current)["rounds"][0]["status"] == "completed"
    with pytest.raises(WorkflowDraftError, match="different content"):
        service.assistant.submit(draft.draft_id, payload(intent="revise"))


def test_revise_single_revision_atomic_diff_and_noop(tmp_path):
    service, author, draft = setup(tmp_path)
    author.action = "revise"
    author.mutate = lambda workflow: workflow["title"].update(zh="改名")
    async def check():
        service.assistant.submit(draft.draft_id, payload(intent="revise"))
        await finished(service.assistant)
        current = service.get(draft.draft_id)
        assert current.revision == 2 and current.validation["verdict"] == "pending"
        round_ = state(current)["rounds"][0]
        assert round_["base_revision"] == 1 and round_["result_revision"] == 2 and round_["diff"]
        service.assistant.submit(draft.draft_id, payload("r2", "revise", 2))
        await finished(service.assistant)
    asyncio.run(check())
    assert service.get(draft.draft_id).revision == 2
    assert len(service.revisions(draft.draft_id)) == 2


def test_explanation_cannot_accept_revision(tmp_path):
    service, author, draft = setup(tmp_path)
    author.action = "revise"
    async def check():
        service.assistant.submit(draft.draft_id, payload())
        await finished(service.assistant)
    asyncio.run(check())
    assert service.get(draft.draft_id).revision == 1
    assert state(service.get(draft.draft_id))["rounds"][0]["failure_code"] == "workflow_explain_cannot_modify"


def test_queue_stale_revision_pauses_and_does_not_hold_lock(tmp_path):
    service, author, draft = setup(tmp_path)
    author.action = "revise"
    author.mutate = lambda workflow: workflow["title"].update(zh="新标题")
    async def check():
        author.block = asyncio.Event()
        service.assistant.submit(draft.draft_id, payload(intent="revise"))
        service.assistant.submit(draft.draft_id, payload("r2"))
        with pytest.raises(WorkflowDraftError, match="active"):
            service.update(draft.draft_id, 1, draft.workflow)
        author.block.set()
        await finished(service.assistant)
    asyncio.run(check())
    rounds = state(service.get(draft.draft_id))["rounds"]
    assert [r["status"] for r in rounds] == ["completed", "needs_review"]
    assert len(author.calls) == 1


def test_cancel_owned_task_and_pause_following_messages(tmp_path):
    service, author, draft = setup(tmp_path)
    async def check():
        author.block = asyncio.Event()
        service.assistant.submit(draft.draft_id, payload(intent="revise"))
        service.assistant.submit(draft.draft_id, payload("r2"))
        await asyncio.sleep(.01)
        service.assistant.cancel(draft.draft_id, "r1")
        await finished(service.assistant)
    asyncio.run(check())
    assert service.get(draft.draft_id).revision == 1
    assert [r["status"] for r in state(service.get(draft.draft_id))["rounds"]] == ["cancelled", "needs_review"]


def test_clarification_bound_to_revision_and_original_intent(tmp_path):
    service, author, draft = setup(tmp_path)
    author.action = "clarify"
    async def check():
        service.assistant.submit(draft.draft_id, payload(intent="revise"))
        await finished(service.assistant)
        round_ = state(service.get(draft.draft_id))["rounds"][0]
        assert round_["status"] == "waiting_input"
        with pytest.raises(WorkflowDraftError, match="applicable"):
            service.assistant.submit(draft.draft_id, payload("r2", replyToClarificationId=round_["clarification_id"]))
        author.action = "explain"
        service.assistant.submit(draft.draft_id, payload("r3", "revise", replyToClarificationId=round_["clarification_id"]))
        await finished(service.assistant)
    asyncio.run(check())
    assert author.calls[-1]["history"][0]["question"] == "Which scope?"
    assert service.get(draft.draft_id).status == "draft"


def test_reference_and_trusted_mode_guards(tmp_path):
    service, _, draft = setup(tmp_path)
    with pytest.raises(WorkflowDraftError, match="confirmation"):
        service.assistant.submit(draft.draft_id, payload(executionMode="full_access"))
    with pytest.raises(WorkflowDraftError, match="another snapshot"):
        service.assistant.submit(draft.draft_id, payload(references=[{"kind": "workflow_field", "draftId": "other", "revision": 1, "path": "/title"}]))
    with pytest.raises(WorkflowDraftError, match="no longer exists"):
        service.assistant.submit(draft.draft_id, payload(references=[{"kind": "workflow_field", "draftId": draft.draft_id, "revision": 1, "path": "/bad"}]))


def test_timeout_cleanup_failure_retains_blocker(tmp_path):
    service, author, draft = setup(tmp_path)
    async def unsafe(**kwargs):
        kwargs["cleanup_state"]["complete"] = False
        raise TimeoutError()
    author.author_workflow_v2 = unsafe
    async def check():
        service.assistant.submit(draft.draft_id, payload(intent="revise"))
        await finished(service.assistant)
    asyncio.run(check())
    assert state(service.get(draft.draft_id))["rounds"][0]["status"] == "cleanup_pending"
    with pytest.raises(WorkflowDraftError):
        service.update(draft.draft_id, 1, draft.workflow)


def test_recovery_never_dispatches_queue_and_old_history_unchanged(tmp_path):
    service, author, draft = setup(tmp_path)
    service._initialize_conversation(draft, kind="initial", status="completed")
    before = service.store.list_workflow_conversation_turns(draft.draft_id)
    def seed(current):
        state(current)["rounds"] = [{"request_id": "r1", "status": "running", "intent": "revise"}, {"request_id": "r2", "status": "queued", "intent": "explain"}]
    service.store.mutate_workflow_assistant(draft.draft_id, seed)
    service.assistant.recover()
    assert [r["status"] for r in state(service.get(draft.draft_id))["rounds"]] == ["cleanup_pending", "needs_review"]
    assert not author.calls and service.store.list_workflow_conversation_turns(draft.draft_id) == before


def test_creation_idempotency_and_context_redaction(tmp_path):
    service, _, draft = setup(tmp_path)
    first = service.create(draft.workflow["title"], {}, None, "create-1")
    second = service.create(draft.workflow["title"], {}, None, "create-1")
    assert first.draft_id == second.draft_id
    with pytest.raises(WorkflowDraftError):
        service.create({"zh": "other"}, {}, None, "create-1")
    assert safe_context({"raw_rows": ["private"], "password": "secret", "message": "safe"}) == {"message": "safe"}


def test_published_explain_only(tmp_path):
    service, author, draft = setup(tmp_path)
    draft.status = "published"
    service.store.save_workflow_draft(draft)
    with pytest.raises(WorkflowDraftError):
        service.assistant.submit(draft.draft_id, payload(intent="revise"))
    async def check():
        service.assistant.submit(draft.draft_id, payload())
        await finished(service.assistant)
    asyncio.run(check())
    assert service.get(draft.draft_id).status == "published"


def test_http_v2_and_legacy_route_compatibility(tmp_path):
    from fastapi.testclient import TestClient
    from sap_business_agents_platform.app import create_app
    settings = Settings(repository_root=ROOT, data_root=tmp_path / "http", draft_root=tmp_path / "drafts")
    app = create_app(settings=settings)
    author = Author()
    app.state.workflow_drafts.author = author
    with TestClient(app) as client:
        body = {"requestId": "minimal-1"}
        created = client.post("/api/authoring/workflows", json=body)
        assert created.status_code == 201
        draft_id = created.json()["draft_id"]
        assert client.post("/api/authoring/workflows", json=body).json()["draft_id"] == draft_id
        root = f"/api/authoring/workflows/{draft_id}"
        assert client.get(root + "/conversation").status_code == 200
        request = payload()
        assert client.post(root + "/feedback", json=request).status_code == 202
        import time
        for _ in range(100):
            snapshot = client.get(root + "/assistant").json()
            if snapshot["rounds"][0]["status"] == "completed":
                break
            time.sleep(.01)
        assert snapshot["rounds"][0]["status"] == "completed"
        assert client.post(root + "/feedback", json=request).status_code == 202
        assert len(author.calls) == 1
        assert client.post(root + "/feedback", json=payload(intent="revise")).status_code == 409
        assert client.post(root + "/feedback", json={**request, "requestId": None}).status_code == 422
        assert client.get(root).json()["revision"] == 1
        assert snapshot["events"][0]["id"] == 1
        assert all(b["id"] == a["id"] + 1 for a, b in zip(snapshot["events"], snapshot["events"][1:]))


def test_cancel_before_dispatch_coroutine_and_late_output(tmp_path):
    service, author, draft = setup(tmp_path)
    async def check():
        service.assistant.submit(draft.draft_id, payload(intent="revise"))
        service.assistant.cancel(draft.draft_id, "r1")
        await finished(service.assistant)
    asyncio.run(check())
    assert state(service.get(draft.draft_id))["rounds"][0]["status"] == "cancelled"
    assert not author.calls


def test_model_binding_and_context_cannot_silently_switch(tmp_path):
    service, author, draft = setup(tmp_path)
    author.snapshot = lambda provider: {"provider_id": provider, "model": "saved-model", "reasoning_effort": "max"}
    async def check():
        service.assistant.submit(draft.draft_id, payload())
        await finished(service.assistant)
        author.snapshot = lambda provider: {"provider_id": "other", "model": "new-model"}
        service.assistant.submit(draft.draft_id, payload("r2"))
        await finished(service.assistant)
    asyncio.run(check())
    rounds = state(service.get(draft.draft_id))["rounds"]
    assert rounds[1]["runtime"]["model"] == "saved-model"
    assert author.calls[1]["history"][0]["answer"] == "**Done**"


def test_stale_snapshot_completion_does_not_overwrite(tmp_path):
    service, author, draft = setup(tmp_path)
    async def check():
        author.block = asyncio.Event()
        service.assistant.submit(draft.draft_id, payload())
        await asyncio.sleep(.01)
        workflow = deepcopy(draft.workflow)
        workflow["title"]["zh"] = "用户新标题"
        service.update(draft.draft_id, 1, workflow)
        author.block.set()
        await finished(service.assistant)
    asyncio.run(check())
    current = service.get(draft.draft_id)
    assert current.revision == 2 and current.workflow["title"]["zh"] == "用户新标题"
    assert state(current)["rounds"][0]["status"] == "needs_review"


def test_sdk_stream_projects_only_tool_metadata_and_closes(monkeypatch):
    from types import SimpleNamespace
    import openai_codex.api
    from sap_business_agents_platform.workflow_authoring_runtime import collect_turn
    class Item:
        def model_dump(self, **kwargs):
            return {"type": "commandExecution", "status": "completed", "command": "password=private", "aggregatedOutput": "private rows"}
    closed = []
    async def stream():
        try:
            yield SimpleNamespace(method="item/completed", payload=SimpleNamespace(item=Item()))
        finally:
            closed.append(True)
    class Handle:
        id = "test-turn"
        def stream(self):
            return stream()
    async def collect(events, *, turn_id):
        assert turn_id == "test-turn"
        async for event_ in events:
            assert event_.method == "item/completed"
        return "final"
    monkeypatch.setattr(openai_codex.api, "_collect_async_turn_result", collect)
    events = []
    result = asyncio.run(collect_turn(Handle(), lambda phase, data: events.append((phase, data))))
    assert result == "final" and closed
    assert events == [("tool_progress", {"kind": "commandExecution", "status": "completed"})]


def test_workflow_preflight_requires_readonly_source_boundary(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sap_business_agents_platform.workflow_authoring_runtime import workflow_preflight, capabilities
    from sap_business_agents_platform.authoring_harness import AuthoringHarnessError
    workspace = SimpleNamespace(source=tmp_path)
    (tmp_path / "src").mkdir()
    async def initial(*args, **kwargs):
        return {"status": "passed", "checks": {"workspace_write": True}}
    monkeypatch.setattr("sap_business_agents_platform.authoring_workspace.sandbox_preflight", initial)
    async def denied(*args, **kwargs):
        return 0, b'{"source_read":true,"source_write_error":{"errno":13,"winerror":5}}', b""
    result = asyncio.run(workflow_preflight(workspace, denied))
    assert result["checks"]["source_write_denied"] is True
    assert capabilities("restricted", result)["file_edit"] == "available"
    async def allowed(*args, **kwargs):
        return 0, b'{"source_read":true,"source_write_error":null}', b""
    with pytest.raises(AuthoringHarnessError, match="source_boundary_failed"):
        asyncio.run(workflow_preflight(workspace, allowed))
    async def missing(*args, **kwargs):
        return 0, b'{"source_read":true,"source_write_error":{"errno":2,"winerror":2}}', b""
    with pytest.raises(AuthoringHarnessError, match="source_boundary_failed"):
        asyncio.run(workflow_preflight(workspace, missing))
