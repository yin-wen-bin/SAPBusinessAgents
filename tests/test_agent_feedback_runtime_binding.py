"""Real API -> plugin -> router -> SDK settings, with no SAP or model requests."""
from __future__ import annotations

import asyncio
import copy
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.plugins import AgentRuntimeCapability, PluginManager
from sap_business_agents_platform.runtime import RuntimeRouter
from sap_business_agents_platform.sdk_manager import SDKManagerError
from tests.test_reasoning_effort import EffortProbe, manager_fixture
from tests.test_sdk_manager import HealthyEmbeddedProvider


class CatalogProbe(EffortProbe):
    async def list_models(self, definition):
        result = await super().list_models(definition)
        model = result["models"][0]
        model.update(id="gpt-5.6-sol", supportedReasoningEfforts=[
            {"reasoningEffort": value} for value in ("medium", "high", "max", "ultra")
        ])
        result["models"].append({**model, "id": "another-model", "is_default": False})
        return result


@pytest.fixture
def wired_app(tmp_path, monkeypatch):
    manager, probe = manager_fixture(tmp_path, CatalogProbe())
    asyncio.run(manager.set_default_model("codex", "gpt-5.6-sol"))
    calls = []
    release = threading.Event()
    release.set()

    class RecordingPlanner:
        def __init__(self, _root, *, model, reasoning_effort, data_root=None):
            self.model, self.reasoning_effort = model, reasoning_effort
            self.data_root = data_root

        async def review_agent_feedback(self, **kwargs):
            calls.append({"model": self.model, "effort": self.reasoning_effort, **kwargs})
            while not release.is_set():
                await asyncio.sleep(0.001)
            return {"action": "reply", "summary": {"zh": "已核对", "en": "Reviewed"},
                    "thread_id": "synthetic-thread"}

    # Do not pass planner= here: that would bypass the production RuntimeRouter.
    monkeypatch.setattr("sap_business_agents_platform.app.CodexPlanner", RecordingPlanner)
    settings = Settings(repository_root=Path(__file__).resolve().parents[1],
                        data_root=tmp_path / "data", draft_root=tmp_path / "drafts",
                        skillhub_root=tmp_path / "skillhub")
    app = create_app(settings, embedded_provider=HealthyEmbeddedProvider(), sdk_manager=manager)
    with TestClient(app) as client:
        runtime = app.state.agent_runtime
        assert isinstance(runtime, AgentRuntimeCapability)
        assert isinstance(runtime.manager, PluginManager)
        assert isinstance(runtime._planner(), RuntimeRouter)
        assert runtime._planner().manager is manager
        response = client.post("/api/authoring/agents", json={
            "source": "blank", "agentId": "synthetic-feedback-check", "module": "Common",
        })
        assert response.status_code == 201, response.text
        try:
            yield client, app, manager, probe, calls, release, response.json()
        finally:
            release.set()


def _submit(client, draft, *, request_id, retry_of_turn=None):
    payload = {"baseRevision": draft["revision"],
               "baseTurn": max(item["turn"] for item in draft["conversation"]),
               "feedback": "Explain this synthetic definition only.", "requestId": request_id}
    if retry_of_turn is not None:
        payload["retryOfTurn"] = retry_of_turn
    response = client.post(f'/api/authoring/agents/{draft["draft_id"]}/feedback', json=payload)
    assert response.status_code == 202, response.text
    return response.json()


def _finish(client, app, draft, submitted):
    async def wait():
        task = app.state.agent_lifecycle._feedback_tasks.get(submitted["task_id"])
        if task is not None:
            await task
    client.portal.call(wait)
    return client.get(f'/api/authoring/agents/{draft["draft_id"]}').json()


def _effort(client, effort):
    response = client.put('/api/system/sdk-runtimes/codex/models/gpt-5.6-sol/reasoning-effort',
                          json={"reasoning_effort": effort})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("legacy", [False, True])
def test_real_plugin_chain_refreshes_effort_and_keeps_draft_model(wired_app, legacy):
    client, app, manager, probe, calls, _, draft = wired_app
    if legacy:
        stored = app.state.store.get_agent_authoring_draft(draft["draft_id"])
        stored["metadata"]["runtime_snapshot"].pop("reasoning_effort", None)
        stored["metadata"]["runtime_snapshot"].pop("reasoning_effort_source", None)
        app.state.store.save_agent_authoring_draft(stored, expected_revision=draft["revision"])
        draft = client.get(f'/api/authoring/agents/{draft["draft_id"]}').json()
    original = copy.deepcopy(draft)
    _effort(client, "max")
    submitted = _submit(client, draft, request_id="first")
    first = _finish(client, app, draft, submitted)
    assert first["conversation"][-1]["status"] == "completed"
    assert calls[-1]["model"] == "gpt-5.6-sol"
    assert calls[-1]["effort"] == "max"
    snapshot = first["conversation"][-1]["decision"]["runtime_snapshot"]
    assert snapshot["model_source"] == "agent_draft_binding"
    assert snapshot["reasoning_effort_source"] == "system_settings"
    assert snapshot["runtime_configuration_revision"] == manager.configuration_revision

    _effort(client, "ultra")
    response = client.put('/api/system/sdk-runtimes/codex/default-model',
                          json={"model_id": "another-model"})
    assert response.status_code == 200, response.text
    assert _submit(client, draft, request_id="first")["task_id"] == submitted["task_id"]
    assert len(calls) == 1, "network retransmission must retain its original frozen binding"
    second = _finish(client, app, first, _submit(client, first, request_id="second"))
    assert second["conversation"][-1]["status"] == "completed"
    assert calls[-1]["model"] == "gpt-5.6-sol", "system default changes do not rebind this draft"
    assert calls[-1]["effort"] == "ultra"
    assert second["metadata"]["runtime_snapshot"] == original["metadata"]["runtime_snapshot"]
    assert second["conversation"][:-1] == first["conversation"]
    assert second["revision"] == original["revision"]
    assert second["package"] == original["package"]
    assert manager.runtime_snapshot()["model"] == "another-model"
    assert probe.calls[-1] == ("another-model", "medium")


def test_active_turn_keeps_submission_effort_after_settings_change(wired_app):
    client, app, _, _, calls, release, draft = wired_app
    _effort(client, "max")
    release.clear()
    submitted = _submit(client, draft, request_id="in-flight")
    _effort(client, "ultra")
    release.set()
    final = _finish(client, app, draft, submitted)
    assert final["conversation"][-1]["status"] == "completed"
    assert calls[-1]["effort"] == "max"
    assert final["conversation"][-1]["decision"]["runtime_snapshot"]["reasoning_effort"] == "max"


@pytest.mark.parametrize("error,expected", [
    (SDKManagerError("PRIVATE configuration", code="runtime_model_check_required"), "runtime_model_check_required"),
    (AttributeError("PRIVATE internal attribute"), "agent_runtime_snapshot_failed"),
    (SDKManagerError("PRIVATE detail", code="PRIVATE invalid code"), "agent_runtime_snapshot_failed"),
])
def test_binding_failures_are_safe_and_retry_uses_latest_config(wired_app, monkeypatch, error, expected):
    client, app, manager, _, calls, _, draft = wired_app
    with monkeypatch.context() as patch:
        def fail(*_args):
            raise error
        patch.setattr(manager, "runtime_snapshot_for_model", fail)
        failed = _finish(client, app, draft, _submit(client, draft, request_id="fail"))
    turn = copy.deepcopy(failed["conversation"][-1])
    assert turn["status"] == "failed"
    assert turn["decision"]["error_code"] == expected
    assert "PRIVATE" not in str(turn)
    assert not calls
    assert failed["package"] == draft["package"]
    assert app.state.store.get_agent_operation(draft["draft_id"]) is None
    _effort(client, "max")
    retried = _finish(client, app, failed, _submit(client, failed, request_id="retry", retry_of_turn=turn["turn"]))
    assert retried["conversation"][-1]["status"] == "completed"
    assert retried["conversation"][-2] == turn
    assert calls[-1]["effort"] == "max"
    assert calls[-1]["thread_id"] is None
