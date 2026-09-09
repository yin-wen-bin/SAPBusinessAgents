from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.sdk_manager import SDKManager, SDKManagerError, _normalize_model
from sap_business_agents_platform.runtime import RuntimeRouter
from sap_business_agents_platform.codex_planner import CodexPlanner, _run_plan_turn
from tests.test_sdk_manager import FakeAdapter, FakeRuntimeProbe, _write_runtime_registry
from tests.test_runtime_router import FakeManager


class EffortProbe(FakeRuntimeProbe):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.default = "medium"
        self.efforts = ["none", "medium", "high", "xhigh"]

    async def list_models(self, definition):
        result = await super().list_models(definition)
        model = result["models"][0]
        model.pop("supported_reasoning_efforts")
        model.pop("default_reasoning_effort")
        model.update(supportedReasoningEfforts=[{"reasoningEffort": value} for value in self.efforts],
                     defaultReasoningEffort=self.default)
        return result

    async def check_model(self, definition, model_id, workspace, *, reasoning_effort):
        assert workspace.is_dir()
        self.calls.append((model_id, reasoning_effort))
        return {"compatible": self.compatible, "status": "compatible" if self.compatible else "runtime_model_probe_timeout"}


def manager_fixture(tmp_path, probe=None):
    registry = tmp_path / "sdks.json"
    _write_runtime_registry(registry)
    probe = probe or EffortProbe()
    manager = SDKManager(registry, tmp_path, adapters={"python": FakeAdapter()},
                         runtime_probes={"codex": probe}, selection_path=tmp_path / "default.json")
    asyncio.run(manager.check_provider("codex"))
    asyncio.run(manager.refresh_models("codex"))
    return manager, probe


def test_effort_defaults_save_without_switching_model_and_survives_restart(tmp_path):
    manager, probe = manager_fixture(tmp_path)
    asyncio.run(manager.set_default_model("codex", "test-model"))
    before = manager.runtime_snapshot()
    assert before["reasoning_effort"] == "medium"
    assert before["reasoning_effort_source"] == "sdk_default"
    asyncio.run(manager.set_reasoning_effort("codex", "test-model", "high"))
    item = manager.models("codex")["items"][0]
    assert item["default_reasoning_effort"] == "medium"
    assert item["saved_reasoning_effort"] == "high"
    assert item["reasoning_effort"] == "high"
    after = manager.runtime_snapshot()
    assert after["model"] == before["model"]
    assert after["configuration_digest"] != before["configuration_digest"]
    assert after["model_check_digest"] != before["model_check_digest"]
    assert probe.calls == [("test-model", "medium"), ("test-model", "high")]
    reloaded, _ = manager_fixture(tmp_path, probe)
    assert reloaded.reasoning_configuration("codex", "test-model") == ("high", "system_settings")


def test_checks_are_effort_specific_none_is_explicit_and_never_inherit_global(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_REASONING_EFFORT", "xhigh")
    manager, probe = manager_fixture(tmp_path)
    asyncio.run(manager.check_model("codex", "test-model", "high"))
    assert manager.models("codex")["items"][0]["check_status"] == "check_required"
    asyncio.run(manager.set_reasoning_effort("codex", "test-model", "none"))
    assert manager.reasoning_configuration("codex", "test-model") == ("none", "system_settings")
    assert probe.calls[-1] == ("test-model", "none")
    keys = json.loads((tmp_path / "default.json").read_text())["model_checks"]
    assert set(keys) == {"codex:test-model:high", "codex:test-model:none"}


def test_invalid_or_removed_effort_and_timeout_do_not_save(tmp_path):
    manager, probe = manager_fixture(tmp_path)
    with pytest.raises(SDKManagerError, match="supported reasoning"):
        asyncio.run(manager.set_reasoning_effort("codex", "test-model", "ultra"))
    assert not probe.calls
    probe.compatible = False
    with pytest.raises(SDKManagerError) as error:
        asyncio.run(manager.set_reasoning_effort("codex", "test-model", "high"))
    assert error.value.code == "runtime_model_probe_timeout"
    assert manager.reasoning_configuration("codex", "test-model") == ("medium", "sdk_default")
    probe.default = None
    asyncio.run(manager.refresh_models("codex"))
    assert manager.models("codex")["items"][0]["reasoning_effort_error"] == "runtime_reasoning_effort_invalid"
    with pytest.raises(SDKManagerError):
        asyncio.run(manager.check_model("codex", "test-model"))


def test_router_has_isolated_concurrent_effort_instances_and_pins_snapshot():
    manager = FakeManager()
    made = []
    class Planner:
        def __init__(self, model, effort):
            self.model, self.effort = model, effort
        async def plan(self):
            await asyncio.sleep(0)
            return self.model, self.effort
    def factory(model, effort):
        value = Planner(model, effort)
        made.append(value)
        return value
    router = RuntimeRouter(manager, {}, provider_factories={"codex": factory})
    async def run(effort):
        with router.pin("codex", "codex-model", effort):
            return await router.plan()
    async def all_runs():
        return await asyncio.gather(run("high"), run("medium"), run("high"))
    assert asyncio.run(all_runs()) == [("codex-model", "high"), ("codex-model", "medium"), ("codex-model", "high")]
    assert len(made) == 2


def test_planner_repair_and_authoring_resume_explicit_effort(tmp_path):
    efforts = []
    class Thread:
        id = "isolated-test-thread"
        async def run(self, prompt, *, output_schema, effort):
            efforts.append(effort)
            if "action" in output_schema.get("properties", {}):
                return SimpleNamespace(final_response=json.dumps({"action": "reply", "summary": {"zh": "已记录", "en": "Recorded"}}))
            return SimpleNamespace(final_response=json.dumps({"intent": "test", "needs_clarification": False,
                "clarification_question": "", "plan_json": "{" if len(efforts) == 1 else "{}"}))
    thread = Thread()
    asyncio.run(_run_plan_turn(thread, "test", phase="fixture", reasoning_effort="high"))
    class Client:
        async def thread_resume(self, thread_id, **kwargs):
            assert thread_id == "old-thread"
            assert "effort" not in kwargs
            return thread
    planner = CodexPlanner(tmp_path, model="gpt-5.6-sol", reasoning_effort="medium")
    asyncio.run(planner._run_agent_feedback(Client(), "test", {}, "old-thread", str(tmp_path)))
    assert efforts == ["high", "high", "medium"]


def test_legacy_future_binding_keeps_model_and_does_not_mutate_history(tmp_path):
    manager, _ = manager_fixture(tmp_path)
    asyncio.run(manager.set_default_model("codex", "test-model"))
    router = RuntimeRouter(manager, {"codex": object()})
    old = {"provider_id": "codex", "model": "test-model", "configuration_digest": "old"}
    bound = router.resolve_legacy_snapshot(old)
    assert "reasoning_effort" not in old
    assert bound["model"] == "test-model"
    assert bound["reasoning_effort_source"] == "legacy_future_binding"
    asyncio.run(manager.set_reasoning_effort("codex", "test-model", "high"))
    assert router.resolve_legacy_snapshot(bound)["reasoning_effort"] == "medium"


def test_catalog_effort_parser_supports_both_sdk_names():
    for key in ("supportedReasoningEfforts", "supported_reasoning_efforts"):
        normalized = _normalize_model({"id": "test", key: [{"reasoningEffort": "none"}, "high"], "defaultReasoningEffort": "none"})
        assert normalized["supported_reasoning_efforts"] == ["none", "high"]
        assert normalized["default_reasoning_effort"] == "none"


def test_abort_feedback_only_acknowledges_owned_exited_process(tmp_path):
    planner = CodexPlanner(tmp_path, model="gpt-5.6-sol", reasoning_effort="medium")
    assert asyncio.run(planner.abort_agent_feedback("missing")) is False
    planner._authoring_clients["starting"] = {"client": object(), "proc": None}
    assert asyncio.run(planner.abort_agent_feedback("starting")) is False
    exited = SimpleNamespace(poll=lambda: 0)
    done = SimpleNamespace(done=lambda: True, cancelled=lambda: False)
    planner._authoring_clients["done"] = {"client": object(), "proc": exited, "start": done, "cleanup": done}
    assert asyncio.run(planner.abort_agent_feedback("done")) is True


def test_cancel_during_sdk_start_waits_for_owned_startup_and_close(tmp_path, monkeypatch):
    from sap_business_agents_platform import codex_planner
    async def scenario():
        started, finish_start = asyncio.Event(), asyncio.Event()
        process = SimpleNamespace(exited=False)
        process.poll = lambda: 0 if process.exited else None
        class Client:
            def __init__(self):
                self._client = SimpleNamespace(_sync=SimpleNamespace(_proc=None))
            async def __aenter__(self):
                started.set()
                await finish_start.wait()
                self._client._sync._proc = process
                return self
            async def close(self):
                process.exited = True
                self._client._sync._proc = None
        monkeypatch.setattr(codex_planner, "_agent_authoring_codex", lambda _path: Client())
        planner = CodexPlanner(tmp_path, model="gpt-5.6-sol", reasoning_effort="medium")
        async def must_not_run(*args):
            raise AssertionError("Cancelled startup must not submit a turn")
        monkeypatch.setattr(planner, "_run_agent_feedback", must_not_run)
        task = asyncio.create_task(planner.review_agent_feedback(feedback="test", locale="en", package={}, operation_id="owned"))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert await planner.abort_agent_feedback("owned") is False
        finish_start.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.exited
        assert await planner.abort_agent_feedback("owned") is True
    asyncio.run(scenario())
