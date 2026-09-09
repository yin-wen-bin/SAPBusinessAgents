from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from sap_business_agents_platform.agent_discovery_jobs import AgentDiscoveryJobs
from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.models import AgentSampleDiscoveryRequest, RunStatus
from sap_business_agents_platform.sample_discovery import SampleDiscoveryContext


def package():
    return {"manifest": {"slug": "sample-agent", "version": "0.1.0", "execution": {
        "inputSchema": {"type": "object", "required": ["company_code", "customer"], "additionalProperties": False,
                        "properties": {"company_code": {"type": "string"}, "customer": {"type": "string"},
                                       "safe_reference": {"type": "string", "x-sapba-secret-kind": "business_reference"}}},
        "steps": [{"inputMapping": {"plan": {"service_name": "API_TEST", "odata_version": "2.0", "entity_set": "Items", "http_method": "GET",
                                               "select_fields": ["CompanyCode", "Customer"], "filters": [
                                                   {"field": "CompanyCode", "value": "{{input.company_code}}", "operator": "eq"},
                                                   {"field": "Customer", "value": "{{input.customer}}", "operator": "eq"}]}}}]}}, "rules": None}


def save_draft(store, settings, draft_id="draft_sample", status="draft"):
    store.save_agent_authoring_draft({"draft_id": draft_id, "agent_id": "sample-agent", "source_type": "blank", "status": status,
                                     "revision": 1, "path": str(settings.draft_root / draft_id)}, package=package())


def setup_jobs(tmp_path):
    settings = Settings(repository_root=Path(__file__).resolve().parents[1], data_root=tmp_path / "data", draft_root=tmp_path / "drafts")
    store = RunStore(settings.database_path)
    save_draft(store, settings)
    sdk = SimpleNamespace(runtime_snapshot_for_model=Mock(return_value={"provider_id": "codex", "sdk_id": "openai-codex",
        "model": "gpt-5.6-sol", "version": "0.147.0", "configuration_digest": "checked-sol", "model_check_digest": "model-check"}))
    service = SimpleNamespace(discover=AsyncMock(return_value={"status": "ready", "input": {"company_code": "1710", "customer": "C1"}}))
    return AgentDiscoveryJobs(settings, store, SimpleNamespace(), sdk, service), store, sdk, service, settings


def request(**kwargs):
    return AgentSampleDiscoveryRequest(expectedRevision=1, input={"company_code": "1710"}, **kwargs)


def test_immediate_cancel_before_task_starts_releases_lock_and_finishes_run(tmp_path):
    async def scenario():
        jobs, store, sdk, service, _ = setup_jobs(tmp_path)
        started = jobs.start("draft_sample", request())
        result = await jobs.cancel("draft_sample", started["run_id"])
        assert result["status"] == "cancelled"
        assert store.get_run(started["run_id"]).status == RunStatus.cancelled
        assert store.get_agent_operation("draft_sample") is None
        assert service.discover.await_count == 0
        assert jobs.tasks == {}
    asyncio.run(scenario())


def test_stop_before_task_starts_also_releases_lock(tmp_path):
    async def scenario():
        jobs, store, _, service, _ = setup_jobs(tmp_path)
        started = jobs.start("draft_sample", request())
        await jobs.stop()
        assert jobs.get("draft_sample", started["run_id"])["status"] == "cancelled"
        assert store.get_agent_operation("draft_sample") is None
        assert service.discover.await_count == 0
    asyncio.run(scenario())


def test_success_binds_sol_and_immutable_draft_without_normal_session(tmp_path):
    async def scenario():
        jobs, store, sdk, service, _ = setup_jobs(tmp_path)
        started = jobs.start("draft_sample", request(requestId="same-input"))
        run_id = started["run_id"]
        replay = jobs.start("draft_sample", request(requestId="same-input"))
        assert replay["run_id"] == run_id
        await jobs.tasks[run_id]
        sdk.runtime_snapshot_for_model.assert_called_once_with("codex", "gpt-5.6-sol")
        assert service.discover.call_args.kwargs["model"] == "gpt-5.6-sol"
        assert store.get_run(run_id).runtime.model == "gpt-5.6-sol"
        assert store.get_agent_run_snapshot(run_id)["validation_revision"] == 1
        assert store.get_free_query_session_by_run(run_id) is None
        assert store.get_agent_operation("draft_sample") is None
        assert jobs.get("draft_sample", run_id)["status"] == "ready"
        assert store.get_agent_authoring_draft("draft_sample")["revision"] == 1
    asyncio.run(scenario())


@pytest.mark.parametrize(("error", "expected"), [(TimeoutError(), "timed_out"), (RuntimeError("RAW_BANK_VALUE"), "unavailable")])
def test_timeout_and_exception_finish_without_error_text_leak(tmp_path, error, expected):
    async def scenario():
        jobs, store, _, service, _ = setup_jobs(tmp_path)
        service.discover.side_effect = error
        started = jobs.start("draft_sample", request())
        await jobs.tasks[started["run_id"]]
        result = jobs.get("draft_sample", started["run_id"])
        assert result["status"] == expected
        assert "RAW_BANK_VALUE" not in json.dumps(result)
        assert store.get_agent_operation("draft_sample") is None
        assert store.get_run(started["run_id"]).status == RunStatus.inconclusive
    asyncio.run(scenario())


def test_cancel_after_start_does_not_accept_late_ready_result(tmp_path):
    async def scenario():
        jobs, store, _, service, _ = setup_jobs(tmp_path)
        entered = asyncio.Event()
        async def ignores_cancellation(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {"status": "ready", "input": {"customer": "LATE"}}
        service.discover.side_effect = ignores_cancellation
        started = jobs.start("draft_sample", request())
        await entered.wait()
        result = await jobs.cancel("draft_sample", started["run_id"])
        assert result["status"] == "cancelled"
        assert "LATE" not in json.dumps(result)
        assert store.get_run(started["run_id"]).status == RunStatus.cancelled
    asyncio.run(scenario())


def test_failure_after_creating_run_does_not_orphan_queue(tmp_path, monkeypatch):
    async def scenario():
        jobs, store, _, service, _ = setup_jobs(tmp_path)
        monkeypatch.setattr(store, "save_agent_run_snapshot", Mock(side_effect=OSError("PRIVATE_PATH")))
        with pytest.raises(AgentLifecycleError):
            jobs.start("draft_sample", request(requestId="setup-failure"))
        operation = store.latest_agent_operation("draft_sample", "sample_discovery")
        assert operation["status"] == "unavailable"
        assert store.get_run(operation["operation_id"]).status == RunStatus.inconclusive
        assert service.discover.await_count == 0
        assert "PRIVATE_PATH" not in json.dumps(operation)
    asyncio.run(scenario())


def test_wrong_model_snapshot_is_rejected_before_run_creation(tmp_path):
    jobs, store, sdk, service, _ = setup_jobs(tmp_path)
    sdk.runtime_snapshot_for_model.return_value["model"] = "gpt-6-astra"
    with pytest.raises(AgentLifecycleError) as error:
        jobs.start("draft_sample", request())
    assert error.value.code == "sample_discovery_model_mismatch"
    operation = store.latest_agent_operation("draft_sample", "sample_discovery")
    with pytest.raises(KeyError):
        store.get_run(operation["operation_id"])
    assert service.discover.await_count == 0


def test_private_input_rejected_before_runtime_or_sql_operation(tmp_path):
    jobs, store, sdk, _, _ = setup_jobs(tmp_path)
    payload = AgentSampleDiscoveryRequest(expectedRevision=1, input={"safe_reference": "SECRET"})
    with pytest.raises(AgentLifecycleError, match="cannot be sent"):
        jobs.start("draft_sample", payload)
    assert store.latest_agent_operation("draft_sample", "sample_discovery") is None
    sdk.runtime_snapshot_for_model.assert_not_called()


def test_missing_organisational_scope_prevents_broad_sample_read():
    ctx = SampleDiscoveryContext(package()["manifest"], {}, 1)
    assert "company_code" in ctx.preflight_gaps()


def test_revision_published_and_cross_draft_guards(tmp_path):
    async def scenario():
        jobs, store, _, service, settings = setup_jobs(tmp_path)
        with pytest.raises(AgentLifecycleError) as stale:
            jobs.start("draft_sample", AgentSampleDiscoveryRequest(expectedRevision=2))
        assert stale.value.code == "agent_draft_conflict"
        save_draft(store, settings, "draft_published", status="published")
        with pytest.raises(AgentLifecycleError) as published:
            jobs.start("draft_published", request())
        assert published.value.code == "agent_draft_published"
        started = jobs.start("draft_sample", request())
        with pytest.raises(KeyError):
            jobs.get("draft_published", started["run_id"])
        await jobs.stop()
    asyncio.run(scenario())


def test_api_start_poll_idempotency_conflict_cancel_and_cross_draft(tmp_path):
    from sap_business_agents_platform.app import create_app
    async def scenario():
        jobs, _, sdk, service, settings = setup_jobs(tmp_path)
        app = create_app(settings)
        actual = app.state.agent_sample_discovery
        actual.sdk_manager = sdk
        gate = asyncio.Event()
        async def wait_for_cancel(*args, **kwargs):
            await gate.wait()
        actual.service = SimpleNamespace(discover=AsyncMock(side_effect=wait_for_cancel))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
            payload = {"expectedRevision": 1, "input": {"company_code": "1710"}, "requestId": "api-request"}
            start = await client.post("/api/authoring/agents/draft_sample/sample-discovery", json=payload)
            assert start.status_code == 202, start.text
            run_id = start.json()["run_id"]
            repeated = await client.post("/api/authoring/agents/draft_sample/sample-discovery", json=payload)
            assert repeated.json()["run_id"] == run_id
            conflict = await client.post("/api/authoring/agents/draft_sample/sample-discovery", json={**payload, "input": {"company_code": "9999"}})
            assert conflict.status_code == 409
            poll = await client.get(f"/api/authoring/agents/draft_sample/sample-discovery/{run_id}")
            assert poll.status_code == 200
            other = await client.get(f"/api/authoring/agents/other/sample-discovery/{run_id}")
            assert other.status_code == 404
            cancel = await client.post(f"/api/authoring/agents/draft_sample/sample-discovery/{run_id}/cancel")
            assert cancel.status_code == 200
            assert cancel.json()["status"] == "cancelled"
            forbidden = await client.post("/api/authoring/agents/draft_sample/sample-discovery", json={"expectedRevision": 1, "input": {"safe_reference": "PRIVATE"}})
            assert forbidden.status_code == 409
            assert "PRIVATE" not in forbidden.text
        await actual.stop()
    asyncio.run(scenario())
