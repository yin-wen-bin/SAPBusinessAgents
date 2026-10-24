"""Campaign -> real scheduler -> shared Harness -> real scope; no vendor/SAP."""
import asyncio
import copy
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sap_business_agents_platform.acceptance import agent_execution_digest
from sap_business_agents_platform.acceptance_runtime import AgentAcceptanceCampaignRequest, acceptance_runtime_digest
from sap_business_agents_platform.agent_acceptance_campaigns import AgentAcceptanceJobs
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.engine import RunCoordinator
from sap_business_agents_platform.harness import HarnessToolBroker, CodexHarnessController
from sap_business_agents_platform.runtime import RuntimeRouter
from sap_business_agents_platform.runtime_authentication import RuntimeAuthenticationService
from sap_business_agents_platform.runtime_contract import RuntimeContractError, RuntimeResult, RuntimeSession
from sap_business_agents_platform.workbuddy_harness import WorkBuddyHarnessController
from sap_business_agents_platform.workbuddy_supervisor import WorkBuddySupervisor
from sap_business_agents_platform.workbuddy_site import make_configuration
from tests.test_agent_acceptance_campaigns import _seed_runnable, _Lifecycle
from tests.test_harness import FakeSapRead, FakeSkills
from tests.test_runtime_date_bounds import INPUT, metadata, plan

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("provider", ["codex", "workbuddy"])
@pytest.mark.parametrize("case", ["valid", "unsupported", "invalid_date", "wider_plan", "business_question", "native_terminal_missing"])
def test_campaign_admission_reaches_real_scope_parser_and_preserves_failure(tmp_path, monkeypatch, provider, case):
    from sap_business_agents_platform.codex_driver import CodexHarnessDriver
    from sap_business_agents_platform.workbuddy_driver import WorkBuddyHarnessDriver
    settings = Settings(repository_root=ROOT, data_root=tmp_path / "data")
    store = RunStore(settings.database_path)
    site = make_configuration("china", 1) if provider == "workbuddy" else {}
    auth = RuntimeAuthenticationService(settings.data_root / "sdk-runtimes", {provider: SimpleNamespace(context=lambda: site)})
    auth.state["accounts"][provider] = {"revision": 9, "status": "authenticated", "authenticated": True, **site}
    snapshot = auth.bind(provider, {"provider_id": provider, "sdk_id": "offline-only", "model": "offline-frozen-model",
        "actual_model": "offline-frozen-model", "reasoning_effort": "high" if provider == "codex" else None,
        "configuration_digest": "offline-unchanged", "execution_flow": "harness", **site})
    trace = []

    class Environment:
        root = tmp_path / "offline-worker"
        def assert_operation(self, bound, operation, **kwargs):
            auth.assert_binding(provider, bound)
            assert operation in {"acceptance_baseline", "acceptance_free_query"}
            trace.append(operation)
    environment = Environment()
    supervisor = WorkBuddySupervisor(environment)
    def bound(value, **kwargs):
        auth.assert_binding(provider, value)
        assert value["model"] == snapshot["model"]
        return copy.deepcopy(value)
    manager = SimpleNamespace(default_provider_id=provider, configuration_revision=1,
        authentication_service=auth, runtime_snapshot=lambda: copy.deepcopy(snapshot),
        runtime_snapshot_for_model=lambda p, m: bound(snapshot))
    manager.workbuddy = SimpleNamespace(environment=environment, supervisor=supervisor, bound_snapshot=bound,
        runtime_snapshot=lambda *a, **k: copy.deepcopy(snapshot))
    @contextmanager
    def events(sink):
        assert router.current_provider_id == provider
        yield
    router = RuntimeRouter(manager, {provider: SimpleNamespace(bind_events=events)})

    class NoSap(FakeSapRead):
        async def validate_plan(self, *a, **k): pytest.fail("SAP validation forbidden")
        async def execute_plan(self, *a, **k): pytest.fail("SAP reads forbidden")
    broker = HarnessToolBroker(settings, store, NoSap(), FakeSkills())
    codex = CodexHarnessController(settings, store, broker)
    workbuddy = WorkBuddyHarnessController(settings, store, broker, manager.workbuddy)
    codex.authentication_service = workbuddy.authentication_service = auth
    coordinator = RunCoordinator(settings, store, SimpleNamespace(), NoSap(), FakeSkills(), router, harness=codex)
    coordinator.workbuddy_harness = workbuddy
    draft = _seed_runnable(store, tmp_path)
    package = store.get_agent_authoring_revision(draft["draft_id"], 4)["package"]
    # A synthetic already-trialled contract; only public input shape is extended.
    schema = package["manifest"]["execution"]["inputSchema"]
    schema["properties"].update({name: {"type": "string"} for name in ("supplier", "posting_date_to", "unsupported_cutoff")})
    schema["required"] = ["company_code", "supplier", "posting_date_to"]
    digest = agent_execution_digest(package["manifest"])
    draft["revision"] = 5
    for name in ("trial", "effective_trial"):
        draft["metadata"][name]["execution_digest"] = digest
        draft["metadata"][name]["revision"] = 5
    store.save_agent_authoring_draft(draft, package=package)
    inputs = dict(INPUT)
    if case == "unsupported": inputs["unsupported_cutoff"] = "private-value"
    if case == "invalid_date": inputs["posting_date_to"] = "2022-02-30"
    native_calls = []

    async def native(driver, request, context):
        # The ONLY mocked execution seam. Scope/preflight/binding/scheduler and
        # Campaign error persistence below remain production implementations.
        native_calls.append(request.operation)
        assert request.operation == "acceptance_baseline"
        assert request.remaining() <= 600 and request.permissions["acceptance_direct_baseline"] is True
        assert request.binding["model"] == snapshot["model"]
        assert not (Path(request.cwd) / "agent-package").exists()
        scoped = context["read_scope"]
        assert broker._read_scopes[context["run_id"]] is scoped and not scoped.unresolved
        assert scoped.bounds[0]["operator"] == "le" and scoped.bounds[0]["day"].isoformat() == INPUT["posting_date_to"]
        scoped.record_schema(metadata())
        scoped.check(plan(value="2023-01-01" if case == "wider_plan" else "2022-12-31T00:00:00"))
        assert request.output_schema["properties"]["input_kind"]["type"] == "null"
        assert request.output_schema["properties"]["input_field"]["type"] == "null"
        if case == "native_terminal_missing":
            from sap_business_agents_platform.runtime_terminal_contract import VERSION
            failure = RuntimeContractError("runtime_structured_output_missing")
            failure.detail = {"terminal_contract": VERSION, "last_rejected_candidate": {"validation_issues": [
                {"code": "runtime_report_schema_invalid", "path": "/input_kind", "constraint": "enum"}]},
                "terminal_convergence": {"without_progress": 2}}
            raise failure
        if case == "business_question":
            value = {"status": "waiting_input", "intent": "read", "clarification_question": "请确认业务范围 / Confirm business scope",
                "input_kind": None, "input_field": None, "summary": {"zh": "需要业务确认", "en": "Business confirmation needed"},
                "source_complete": False, "business_complete": False, "missing_evidence": [], "evidence_refs": [],
                "executed_plans": [], "presentation": None, "acceptance_projection": None}
            context["thread_id"] = "offline-native"
            return RuntimeResult(value, RuntimeSession(provider, "offline-native"), cleanup_complete=True)
        raise RuntimeContractError("offline_native_boundary_observed")
    monkeypatch.setattr(CodexHarnessDriver, "harness_turn", native)
    monkeypatch.setattr(WorkBuddyHarnessDriver, "harness_turn", native)
    jobs = AgentAcceptanceJobs(settings, store, _Lifecycle(), coordinator, manager)

    async def scenario():
        with patch("subprocess.Popen", side_effect=AssertionError("external process forbidden")), patch("httpx.AsyncClient.send", side_effect=AssertionError("network forbidden")):
            await coordinator.start()
            try:
                payload = AgentAcceptanceCampaignRequest(expectedRevision=5, requestId="exactly-once",
                    expectedRuntimeDigest=acceptance_runtime_digest(snapshot), cases=[{"caseId": "one", "input": inputs}])
                created = jobs.start(draft["draft_id"], payload)
                task = jobs.tasks[created["campaign_id"]]
                await task
                result = jobs.get(draft["draft_id"], created["campaign_id"])
                assert jobs.start(draft["draft_id"], payload)["campaign_id"] == result["campaign_id"]
                report = result["report"]
                expected = {"valid": ("offline_native_boundary_observed", "environment"),
                    "unsupported": ("runtime_scope_clarification_required", "scope"),
                    "invalid_date": ("runtime_scope_clarification_required", "input"),
                    "wider_plan": ("runtime_query_scope_rejected", "scope"),
                    "native_terminal_missing": ("runtime_structured_output_missing", "contract"),
                    "business_question": ("agent_acceptance_business_input_required", "input")}[case]
                assert (report["error"]["code"], report["failure_category"]) == expected, report
                assert report["verdict"] == "NOT_TESTED" and report["executable"] is False
                assert report["failure_stage"] == "baseline" and report["diagnostics"]["cleanup_complete"] is True
                if case in {"unsupported", "invalid_date"}:
                    issue = report["validation_issues"][0]
                    field = "unsupported_cutoff" if case == "unsupported" else "posting_date_to"
                    assert issue["input_field"] == field and issue["path"] == "/input/" + field
                    assert not native_calls and "private-value" not in json.dumps(report)
                else:
                    assert native_calls == ["acceptance_baseline"]
                if case == "business_question":
                    assert report["diagnostics"]["clarification_question"] == "请确认业务范围 / Confirm business scope"
                if case == "native_terminal_missing":
                    assert report["validation_issues"][0]["path"] == "/input_kind"
                    assert report["diagnostics"]["terminal_handoff_failure"]["attempts_without_progress"] == 2
                baseline = result["cases"][0]["baseline_run_id"]
                assert store.get_run(baseline).runtime.authentication_binding_revision == 9
                assert not result["cases"][0].get("free_query_run_id")
                assert store.list_harness_tool_calls(baseline) == []
                assert store.get_agent_operation(draft["draft_id"]) is None
                assert auth.leases.get(provider, 0) == 0 and supervisor.reconcile() == []
            finally:
                await coordinator.stop()
    asyncio.run(scenario())
