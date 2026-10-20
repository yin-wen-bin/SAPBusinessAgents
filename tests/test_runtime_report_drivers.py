"""Real driver code over simulated SDK boundaries; no model/SAP processes."""
import ast
import asyncio
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_runtime_report_contract import make_broker, report
from sap_business_agents_platform.acceptance_projection import output_schema
from sap_business_agents_platform.harness import CodexHarnessController
from sap_business_agents_platform.runtime_contract import RuntimeRequest, cleanup_binding
from sap_business_agents_platform.runtime_harness_contract import _HARNESS_OUTPUT_SCHEMA
from sap_business_agents_platform.runtime_report_convergence import ReportCorrectionStopped


def request_context(broker, run, token, provider):
    schema = output_schema(_HARNESS_OUTPUT_SCHEMA, None)
    request = RuntimeRequest(provider, "free_query", "harness", {"provider_id": provider,
        "model": "offline-model", "reasoning_effort": "max"}, "Synthetic query", schema,
        cwd=str(broker.settings.data_root), instructions="Preserve existing task permissions.",
        permissions={"full_access": False, "acceptance_direct_baseline": False}, deadline=time.monotonic() + 60)
    context = {"run_id": run, "capability": token, "workspace": broker.settings.data_root,
        "state": {}, "thread_id": None, "turn_count": 1, "cleanup_state": {}, "final_response": "",
        "read_scope": None, "report_contract": broker.report_contract(run)}
    return request, context


@pytest.mark.parametrize("stalled", [False, True])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_codex_actual_driver_keeps_early_close_or_stops_stalled_report(tmp_path, monkeypatch, stalled, cleanup_fails):
    from sap_business_agents_platform.codex_driver import CodexHarnessDriver
    from sap_business_agents_platform.runtime_execution import RuntimeExecutionError
    from sap_business_agents_platform import harness
    broker, store, run, token = make_broker(tmp_path)
    owner = CodexHarnessController(broker.settings, store, broker)
    request, context = request_context(broker, run, token, "codex")
    class Client:
        id = "offline-thread"
        native_terminal_seen = False
        interrupted = 0
        closed = False
        options = None
        async def __aenter__(self): return self
        async def thread_start(self, **options):
            assert options["model"] == "offline-model"
            return self
        async def turn(self, prompt, **options):
            self.options = options
            assert prompt == request.prompt and options["effort"] == "max"
            return self
        async def interrupt(self): self.interrupted += 1
        async def close(self):
            self.closed = True
            if cleanup_fails: raise RuntimeError("synthetic_cleanup_error")
        async def stream(self):
            for index in range(3 if stalled else 1):
                await broker.handle(run, token, "sap_final_report_validate", {
                    "report": report(blocks={}) if stalled else report(), "tool_call_id": "driver_" + str(index)})
                item = {"type": "mcpToolCall", "tool": "sap_final_report_validate", "server": "sap_business_agents", "status": "completed"}
                yield SimpleNamespace(method="item/completed", payload=SimpleNamespace(item=SimpleNamespace(
                    root=SimpleNamespace(model_dump=lambda **_: item))))
            self.native_terminal_seen = True
            raise AssertionError("Codex should already have handed off or stopped")
    client = Client()
    monkeypatch.setattr(harness, "_safe_codex", lambda *_, **__: client)
    driver = CodexHarnessDriver(owner)
    async def scenario():
        try:
            return await driver.harness_turn(request, context)
        finally:
            await driver.cleanup(run, context)
    with cleanup_binding():
        if stalled:
            with pytest.raises(ReportCorrectionStopped) as failure:
                asyncio.run(scenario())
            assert bool(failure.value.detail.get("cleanup_failure_code")) == cleanup_fails
        elif cleanup_fails:
            with pytest.raises(RuntimeExecutionError, match="runtime_cleanup_incomplete"):
                asyncio.run(scenario())
        else:
            result = asyncio.run(scenario())
            assert result.cleanup_complete and result.output["presentation"]["validation_ref"]
            from jsonschema import Draft202012Validator
            Draft202012Validator(request.output_schema).validate(result.output)
    assert client.closed and client.interrupted == 1 and not client.native_terminal_seen
    assert context["cleanup_state"]["complete"] is (not cleanup_fails)
    assert client.options["output_schema"] == request.output_schema
    assert not owner._active_turns


@pytest.mark.parametrize("mode", ["stalled", "missing_native_terminal", "native_terminal"])
def test_workbuddy_actual_driver_requires_native_terminal_and_uses_shared_stop(tmp_path, mode):
    from sap_business_agents_platform.workbuddy_driver import WorkBuddyHarnessDriver
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    broker, store, run, token = make_broker(tmp_path, runtime={"provider_id": "workbuddy", "sdk_id": "offline-sdk",
        "model": "offline-model", "configuration_digest": "offline-frozen"})
    request, context = request_context(broker, run, token, "workbuddy")
    observed = []
    async def native(**kwargs):
        canonical = None
        for index in range(3 if mode == "stalled" else 1):
            result = await kwargs["tool_handler"]("sap_final_report_validate", {
                "report": report(blocks={}) if mode == "stalled" else report(), "tool_call_id": "driver_" + str(index)})
            observed.append(result)
            if result.get("_workbuddy_stop_after_response"):
                raise WorkBuddyError(result["_runtime_report_stop_code"])
            canonical = result.get("validated_report")
        payload = {"status": "inconclusive", "intent": "offline", "clarification_question": "",
            "input_kind": None, "input_field": None, "summary": {"zh": "离线", "en": "Offline"},
            "source_complete": False, "business_complete": False, "missing_evidence": ["synthetic"],
            "evidence_refs": [], "executed_plans": [], "presentation": canonical}
        return {"text": json.dumps(payload), "output_format": "text" if mode == "missing_native_terminal" else "native_json_schema",
            "actual_model": "offline-model"}
    supervisor = SimpleNamespace(run=native, cleanup_deadlines={}, reconcile=lambda: [])
    owner = SimpleNamespace(store=store, broker=broker, manager=SimpleNamespace(supervisor=supervisor))
    driver = WorkBuddyHarnessDriver(owner)
    async def scenario():
        try:
            return await driver.harness_turn(request, context)
        finally:
            await driver.cleanup(run, context)
    with cleanup_binding():
        if mode == "stalled":
            with pytest.raises(ReportCorrectionStopped): asyncio.run(scenario())
            assert len(observed) == 3 and observed[-1]["validation_ref"] is None
        elif mode == "missing_native_terminal":
            with pytest.raises(WorkBuddyError, match="workbuddy_structured_output_missing"): asyncio.run(scenario())
            assert observed[0]["ok"] is True  # Candidate alone never succeeds.
        else:
            result = asyncio.run(scenario())
            assert result.cleanup_complete and result.output["presentation"]["validation_ref"]
    assert context["cleanup_state"]["complete"] is True


@pytest.mark.parametrize("ok", [False, True])
def test_actual_worker_tool_factory_preserves_body_and_sets_mcp_error_flag(ok):
    from sap_business_agents_platform.mcp_server import _result
    source = Path(__file__).parents[1] / "src/sap_business_agents_platform/workbuddy_worker.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    factory = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "make_tool")
    raw = {"ok": ok, "validation_ref": "synthetic-validation" if ok else None,
        "validation_issues": [] if ok else [{"path": "/report/blocks", "expected": "array", "actual_type": "object"}]}
    class Lock:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
    async def to_thread(fn): return fn()
    scope = {"tool_names": set(), "relay_lock": Lock(), "uuid": SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="synthetic")),
        "tool": lambda *_: lambda handler: handler, "json": json, "asyncio": SimpleNamespace(to_thread=to_thread),
        "send": lambda *_, **__: None, "read_frame": lambda: {"kind": "tool_result", "call_id": "synthetic",
            "result": {**raw, "_runtime_report_stop_code": "private-control"}}}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(factory)], type_ignores=[])), "offline_worker_factory", "exec"), scope)
    handler = scope["make_tool"]({"name": "sap_final_report_validate", "inputSchema": {"type": "object"}})
    result = asyncio.run(handler({"report": {}}))
    assert result["isError"] is (not ok)
    assert json.loads(result["content"][0]["text"]) == raw
    assert result["isError"] == _result(raw)["isError"]


@pytest.mark.parametrize("code", ["report_correction_stalled", "acceptance_report_validation_failed"])
def test_owned_protocol_worker_preserves_platform_stop_and_cleanup(tmp_path, monkeypatch, code):
    from tests.test_workbuddy_isolation import supervisor
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError, digest
    owner, binding = supervisor(tmp_path, monkeypatch)
    async def handler(name, arguments):
        assert name == "approved_fixture"  # Hash-whitelisted protocol fixture, not a real SDK.
        return {"ok": False, "code": code, "_workbuddy_stop_after_response": True,
            "_runtime_report_stop_code": code}
    with pytest.raises(WorkBuddyError) as failure:
        asyncio.run(owner.run(task_id="shared-stop", snapshot=binding, operation="review_workflow",
            payload={"scenario": "tool"}, seconds=10, tool_handler=handler))
    assert failure.value.code == code
    record = json.loads((owner.environment.root / "jobs" / (digest("shared-stop") + ".json")).read_text())
    assert record["failure_code"] == code and record["cleanup_complete"]
    assert "result_metadata" not in record and not owner.pending()
