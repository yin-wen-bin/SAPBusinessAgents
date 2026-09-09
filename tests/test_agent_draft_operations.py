from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.agent_discovery_jobs import AgentDiscoveryJobs
from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.models import AgentSampleDiscoveryRequest, RunCreate, RunMode, RunStatus


def seed(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    draft = {"draft_id": "draft-test", "agent_id": "sample-test", "source_type": "blank",
             "status": "draft", "revision": 1, "path": str(tmp_path / "draft"), "metadata": {}}
    manifest = {"slug": "sample-test", "module": "FI", "version": "0.1.0", "execution": {
        "inputSchema": {"type": "object", "properties": {
            "company_code": {"type": "string"},
            "receipt_reference": {"type": "string", "x-sapba-sensitive": True},
        }}, "steps": [],
    }}
    store.save_agent_authoring_draft(draft, package={"manifest": manifest, "rules": None})
    return store, draft


def test_operation_exclusion_and_idempotency(tmp_path):
    store, draft = seed(tmp_path)
    def reserve(_):
        try:
            return store.reserve_agent_operation(draft["draft_id"], 1, "feedback")
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(isinstance(item, dict) for item in results) == 1
    assert results.count("agent_draft_operation_active") == 7
    operation = next(item for item in results if isinstance(item, dict))
    with pytest.raises(ValueError, match="agent_draft_operation_active"):
        store.save_agent_authoring_draft({**draft, "revision": 2}, expected_revision=1)
    store.finish_agent_operation(draft["draft_id"], operation["operation_id"])
    original = store.reserve_agent_operation(draft["draft_id"], 1, "trial", "request-one", "hash")
    assert store.reserve_agent_operation(draft["draft_id"], 1, "trial", "request-one", "hash")["reused"]
    with pytest.raises(ValueError, match="agent_request_conflict"):
        store.reserve_agent_operation(draft["draft_id"], 1, "trial", "request-one", "different")
    store.finish_agent_operation(draft["draft_id"], original["operation_id"])


def test_revisions_immutable_and_stale_updates_rejected(tmp_path):
    store, draft = seed(tmp_path)
    with pytest.raises(ValueError, match="agent_revision_immutable"):
        store.save_agent_authoring_draft(draft, package={"manifest": {"tampered": True}})
    assert store.get_agent_authoring_revision(draft["draft_id"], 1)["package"]["manifest"]["slug"] == "sample-test"
    store.save_agent_authoring_draft({**draft, "revision": 2}, package={"manifest": {}}, expected_revision=1)
    with pytest.raises(ValueError, match="agent_draft_conflict"):
        store.save_agent_authoring_draft(draft, expected_revision=1)


def test_restart_interrupts_discovery_without_general_query_replay(tmp_path):
    store, draft = seed(tmp_path)
    operation = store.reserve_agent_operation(draft["draft_id"], 1, "sample_discovery")
    store.create_run(operation["operation_id"], RunCreate(mode=RunMode.free_query, query="Find sample"))
    store.recover_agent_operations()
    assert store.get_agent_operation(draft["draft_id"]) is None
    assert store.get_run(operation["operation_id"]).status == RunStatus.failed
    assert store.list_recoverable_runs() == []
    assert store.get_agent_authoring_revision(draft["draft_id"], 1)


def test_delete_refuses_active_draft_operation(tmp_path):
    store, draft = seed(tmp_path)
    store.reserve_agent_operation(draft["draft_id"], 1, "feedback")
    with pytest.raises(ValueError, match="agent_draft_operation_active"):
        store.delete_agent_authoring_draft(draft["draft_id"], expected_revision=1,
            audit_event_id="deleted", agent_id=draft["agent_id"], detail={})
    assert store.get_agent_authoring_draft(draft["draft_id"])


class Runtime:
    def runtime_snapshot_for_model(self, provider_id, model_id):
        assert provider_id == "codex" and model_id == "gpt-5.6-sol"
        return {"provider_id": provider_id, "sdk_id": "openai-codex", "model": model_id,
                "configuration_digest": "frozen-test"}


def test_discovery_persists_result_and_reuses_request_without_new_calls(tmp_path):
    async def scenario():
        store, draft = seed(tmp_path)
        calls = []
        async def discover(run_id, manifest, supplied, *, revision, model):
            calls.append(run_id)
            return {"status": "ready", "input": supplied, "field_sources": {}, "source_complete": None}
        jobs = AgentDiscoveryJobs(None, store, None, Runtime(), SimpleNamespace(discover=discover))
        request = AgentSampleDiscoveryRequest(expectedRevision=1, input={"company_code": "1710"}, requestId="one")
        started = jobs.start(draft["draft_id"], request)
        await asyncio.gather(*list(jobs.tasks.values()))
        result = jobs.get(draft["draft_id"], started["run_id"])
        assert result["status"] == "ready" and result["input"] == request.input
        assert jobs.start(draft["draft_id"], request)["run_id"] == started["run_id"]
        assert len(calls) == 1
        assert store.get_run(started["run_id"]).runtime.model == "gpt-5.6-sol"
        assert store.get_agent_operation(draft["draft_id"]) is None
        assert store.get_agent_authoring_draft(draft["draft_id"])["validation"] == {}
    asyncio.run(scenario())


def test_discovery_rejects_private_input_before_persistence(tmp_path):
    store, draft = seed(tmp_path)
    jobs = AgentDiscoveryJobs(None, store, None, Runtime(), None)
    with pytest.raises(AgentLifecycleError, match="cannot be sent"):
        jobs.start(draft["draft_id"], AgentSampleDiscoveryRequest(expectedRevision=1,
            input={"receipt_reference": "do-not-save"}))
    assert store.get_agent_operation(draft["draft_id"]) is None
    with store._connect() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def test_discovery_cancel_releases_operation(tmp_path):
    async def scenario():
        store, draft = seed(tmp_path)
        async def discover(*args, **kwargs):
            await asyncio.Event().wait()
        jobs = AgentDiscoveryJobs(None, store, None, Runtime(), SimpleNamespace(discover=discover))
        result = jobs.start(draft["draft_id"], AgentSampleDiscoveryRequest(expectedRevision=1, input={}))
        await asyncio.sleep(0)
        cancelled = await jobs.cancel(draft["draft_id"], result["run_id"])
        assert cancelled["status"] == "cancelled"
        assert store.get_agent_operation(draft["draft_id"]) is None
        assert store.get_run(result["run_id"]).status == RunStatus.cancelled
    asyncio.run(scenario())
