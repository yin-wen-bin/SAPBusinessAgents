"""Offline full admission-to-baseline tests. No SDK, auth CLI, model or SAP."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sap_business_agents_platform.acceptance_runtime import resolve_acceptance_runtime, AgentAcceptanceCampaignRequest
from sap_business_agents_platform.runtime_authentication import RuntimeAuthenticationService


def make_manager(tmp_path, during=None):
    # A real unified binding service, with synthetic account/context only.
    context = {"service_site": "china", "service_site_revision": 3}
    auth = RuntimeAuthenticationService(tmp_path, {"workbuddy": SimpleNamespace(context=lambda: dict(context))})
    auth.state["accounts"]["workbuddy"] = {"status": "authenticated", "authenticated": True,
        "revision": 9, **context}
    manager = SimpleNamespace(default_provider_id="workbuddy", configuration_revision=3, authentication_service=auth,
        context=context, calls=[])
    def formal(model=None, *, formal=False):
        manager.calls.append((model, formal))
        assert formal is True
        if during:
            during(manager)
        return {"provider_id": "workbuddy", "sdk_id": "workbuddy", "model": model or "saved-model",
            "actual_model": model or "saved-model", "authentication_revision": 27,
            "configuration_digest": "provider-snapshot", **context}
    manager.workbuddy = SimpleNamespace(runtime_snapshot=formal,
        environment=SimpleNamespace(state_path=tmp_path / "provider-state.json",
            assert_operation=lambda snapshot, operation: manager.calls.append(operation)))
    manager.runtime_snapshot = lambda: pytest.fail("Ordinary WorkBuddy snapshot must not run before the formal one")
    return manager


@pytest.mark.parametrize("model", [None, "explicit-model"])
@pytest.mark.parametrize("binding_revision", [0, 9, 42])
def test_formal_snapshot_is_once_and_unified_auth_revision_is_not_provider_revision(tmp_path, model, binding_revision):
    manager = make_manager(tmp_path)
    manager.authentication_service.state["accounts"]["workbuddy"]["revision"] = binding_revision
    snapshot = resolve_acceptance_runtime(manager, model)
    assert snapshot["authentication_binding_revision"] == binding_revision
    assert snapshot["authentication_revision"] == 27
    manager.authentication_service.assert_binding("workbuddy", snapshot)
    assert manager.calls == [(model, True)]


@pytest.mark.parametrize(("change", "code"), [
    (lambda m: m.authentication_service.state["accounts"]["workbuddy"].update(revision=10), "runtime_authentication_binding_changed"),
    (lambda m: m.authentication_service.state["accounts"]["workbuddy"].update(invalidation_pending=True), "runtime_authentication_check_required"),
    (lambda m: m.authentication_service.state["accounts"]["workbuddy"].update(status="signed_out"), "runtime_authentication_check_required"),
    (lambda m: m.context.update(service_site="international"), "runtime_authentication_check_required"),
    (lambda m: setattr(m, "configuration_revision", 4), "agent_acceptance_runtime_changed"),
    (lambda m: setattr(m, "default_provider_id", "codex"), "agent_acceptance_runtime_changed"),
])
def test_changes_during_slow_preparation_are_rejected(tmp_path, change, code):
    with pytest.raises(Exception) as caught:
        resolve_acceptance_runtime(make_manager(tmp_path, change))
    assert caught.value.code == code


def test_missing_binding_is_rejected_before_campaign_creation(tmp_path):
    manager = make_manager(tmp_path)
    manager.authentication_service.bind = lambda provider, snapshot: snapshot
    with pytest.raises(Exception) as caught:
        resolve_acceptance_runtime(manager)
    assert caught.value.code == "runtime_authentication_binding_invalid"
    assert manager.calls == []


def test_codex_uses_original_entry_and_explicit_model_rejection():
    sentinel = {"provider_id": "codex", "model": "unchanged", "authentication_binding_revision": 11}
    calls = []
    manager = SimpleNamespace(default_provider_id="codex", runtime_snapshot=lambda: calls.append("original") or sentinel)
    assert resolve_acceptance_runtime(manager) is sentinel
    with pytest.raises(Exception) as caught:
        resolve_acceptance_runtime(manager, "other")
    assert caught.value.code == "agent_acceptance_runtime_model_invalid" and calls == ["original", "original"]


def test_raw_provider_context_cannot_be_overwritten_by_bind(tmp_path):
    manager = make_manager(tmp_path)
    manager.workbuddy.runtime_snapshot = lambda *a, **k: {"provider_id": "workbuddy", "service_site": "international"}
    with pytest.raises(Exception) as caught:
        resolve_acceptance_runtime(manager)
    assert caught.value.code == "workbuddy_service_site_changed"


@pytest.mark.parametrize(("provider", "model"), [("workbuddy", None), ("workbuddy", "explicit-model"), ("codex", None)])
def test_accepted_campaign_reaches_mock_baseline_with_real_binding_gate(tmp_path, provider, model):
    from sap_business_agents_platform.agent_acceptance_campaigns import AgentAcceptanceJobs, AcceptanceCampaignOperationalError
    from sap_business_agents_platform.database import RunStore
    from tests.test_agent_acceptance_campaigns import _seed_runnable, _Lifecycle, _Coordinator
    async def scenario():
        manager = make_manager(tmp_path / "auth")
        if provider == "codex":
            from tests.test_agent_acceptance_campaigns import _Runtime
            manager.default_provider_id = "codex"
            manager.authentication_service.adapters["codex"] = SimpleNamespace()
            manager.authentication_service.state["accounts"]["codex"] = {"status": "authenticated", "authenticated": True, "revision": 11}
            manager.runtime_snapshot = lambda: manager.authentication_service.bind("codex", _Runtime().runtime_snapshot())
        store = RunStore(tmp_path / "isolated.sqlite3")
        draft = _seed_runnable(store, tmp_path)
        coordinator = _Coordinator()
        submissions = []
        async def submit(payload, **kwargs):
            snapshot = kwargs["runtime"]
            manager.authentication_service.assert_binding(provider, snapshot)
            assert snapshot["model"] == (model or "saved-model" if provider == "workbuddy" else "gpt-5.6-sol")
            if provider == "workbuddy":
                assert snapshot["actual_model"] == (model or "saved-model")
            assert kwargs["direct_baseline"] is True and kwargs["hard_limit_seconds"] == 600
            submissions.append(snapshot)
            return "mock-baseline"
        async def cancel(*args, **kwargs):
            assert kwargs["timeout"] == 10
            return True
        coordinator.submit_acceptance_query = submit
        coordinator.cancel_acceptance_run = cancel
        jobs = AgentAcceptanceJobs(SimpleNamespace(data_root=tmp_path), store, _Lifecycle(), coordinator, manager)
        async def wait(*args, **kwargs):
            raise AcceptanceCampaignOperationalError("offline_baseline_boundary_observed", failure_stage="baseline")
        jobs._wait_run = wait
        jobs._run_diagnostics = lambda *args: {}
        with patch("subprocess.Popen", side_effect=AssertionError("external process forbidden")), patch("httpx.AsyncClient.send", side_effect=AssertionError("network forbidden")):
            created = jobs.start(draft["draft_id"], AgentAcceptanceCampaignRequest(expectedRevision=4, requestId="once", runtimeModelId=model, cases=[{"caseId": "one", "input": {"company_code": "1710"}}]))
            await jobs.tasks[created["campaign_id"]]
        actual = jobs.get(draft["draft_id"], created["campaign_id"])
        assert len(submissions) == 1 and manager.calls.count((model, True)) == (1 if provider == "workbuddy" else 0)
        assert actual["runtime"]["authentication_binding_revision"] == (9 if provider == "workbuddy" else 11)
        assert actual["report"]["error"]["code"] == "offline_baseline_boundary_observed"
        assert actual["report"]["verdict"] == "NOT_TESTED" and actual["report"]["executable"] is False
        assert actual["report"]["diagnostics"]["cleanup_complete"] is True
        assert store.get_agent_operation(draft["draft_id"]) is None
    asyncio.run(scenario())
