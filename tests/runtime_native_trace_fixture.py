"""Model-free native seams for the six remaining orchestration operations."""
import asyncio
import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.models import RunCreate, RunMode

OPERATIONS = ("review_agent_feedback", "workflow_authoring.v2", "sample_discovery",
              "free_query", "acceptance_baseline", "acceptance_free_query")


def normalized(value):
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    if isinstance(value, dict):
        return {k: {"sha256": hashlib.sha256(json.dumps(v, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
                if k == "output_schema" else normalized(v) for k, v in value.items()
                if k not in {"cwd", "workspace_id", "elapsed_seconds", "base_digest"}}
    if isinstance(value, (tuple, list)):
        return [normalized(v) for v in value]
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, str) and len(value) > 600:
        return {"sha256": hashlib.sha256(value.encode()).hexdigest(), "length": len(value)}
    return value


class Workspace:
    def __init__(self, repository, root):
        self.root, self.source = root, root / "source"
        self.base_commit, self.base_digest = "frozen-commit", "frozen-digest"

    def prepare(self, *_, **__):
        self.source.mkdir(parents=True, exist_ok=True)

    def _check_links_and_size(self):
        pass


class Client:
    def __init__(self, trace, output):
        self.trace, self.output, self.id = trace, output, "thread-frozen"

    async def __aenter__(self):
        self.trace.append(["open", {}])
        return self

    async def close(self):
        self.trace.append(["close", {}])

    async def thread_start(self, **kwargs):
        self.trace.append(["thread_start", normalized(kwargs)])
        return self

    async def thread_resume(self, thread_id, **kwargs):
        self.trace.append(["thread_resume", {"thread_id": thread_id, **normalized(kwargs)}])
        return self

    async def run(self, prompt, **kwargs):
        self.trace.append(["run", {"prompt": normalized(prompt), **normalized(kwargs)}])
        return SimpleNamespace(final_response=json.dumps(self.output, ensure_ascii=False))

    async def turn(self, prompt, **kwargs):
        self.trace.append(["turn", {"prompt": normalized(prompt), **normalized(kwargs)}])
        return self

    async def steer(self, text):
        self.trace.append(["steer", normalized(text)])

    async def interrupt(self):
        self.trace.append(["interrupt", {}])

    async def stream(self):
        item = {"type": "agentMessage", "text": json.dumps(self.output, ensure_ascii=False)}
        root = SimpleNamespace(model_dump=lambda **_: item)
        yield SimpleNamespace(method="item/completed", payload=SimpleNamespace(item=SimpleNamespace(root=root)))
        yield SimpleNamespace(method="turn/completed", payload=SimpleNamespace(
            model_dump=lambda **_: {"turn": {"status": "completed"}}))


async def trace_operation(operation, monkeypatch, tmp_path, module=None):
    from sap_business_agents_platform import harness, runtime_execution, workflow_authoring_runtime
    from sap_business_agents_platform import codex_planner, sample_discovery
    from sap_business_agents_platform import runtime_workflow_authoring, authoring_workspace, runtime_changesets
    from tests.test_agent_sample_discovery import manifest
    trace = []
    output = {"status": "inconclusive", "intent": "read", "clarification_question": "",
        "input_kind": None, "input_field": None, "summary": {"zh": "证据不足", "en": "Insufficient evidence"},
        "source_complete": False, "business_complete": False, "missing_evidence": ["offline_probe"],
        "evidence_refs": [], "executed_plans": [], "presentation": None}
    if operation == "sample_discovery":
        output = {"suggestions": []}
    elif operation == "workflow_authoring.v2":
        output = {"action": "explain", "answer": "No changes", "question": "", "workflow_json": "null"}
    elif operation == "review_agent_feedback":
        output = {"action": "reply", "summary": {"zh": "未修改", "en": "Unchanged"},
            "manifest_json": "", "readme": "", "rules_source": "", "files_json": "", "edits_json": ""}
    client = Client(trace, output)
    async def probe(*_, **__):
        trace.append(["preflight", {}])
        return {"status": "passed"}
    monkeypatch.setattr(runtime_execution, "command_preflight", probe)
    monkeypatch.setattr(authoring_workspace, "AuthoringWorkspace", Workspace)
    monkeypatch.setattr(runtime_workflow_authoring, "WorkflowWorkspace", Workspace)
    monkeypatch.setattr(runtime_changesets.RuntimeChangeSets, "create", lambda *_, **__: None)
    monkeypatch.setattr(codex_planner, "_tool_authoring_codex", lambda *_, **__: client)
    monkeypatch.setattr(codex_planner, "_agent_authoring_codex", lambda *_, **__: client)
    monkeypatch.setattr(harness, "_safe_codex", lambda *_, **__: client)
    if module is not None:
        for name, value in [("_safe_codex", lambda *_, **__: client), ("WorkflowWorkspace", Workspace),
                            ("_agent_authoring_codex", lambda *_, **__: client)]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    async def collect(*_, **__):
        return SimpleNamespace(final_response=json.dumps(output))
    monkeypatch.setattr(workflow_authoring_runtime, "collect_turn", collect)
    if module is not None and hasattr(module, "collect_turn"):
        monkeypatch.setattr(module, "collect_turn", collect)
    if operation == "review_agent_feedback":
        cls = module.CodexPlanner if module else codex_planner.CodexPlanner
        value = await cls(tmp_path, "fixture-model", "max").review_agent_feedback(
            feedback="Explain without changing", locale="en", package={"manifest": {}, "readme": "# Draft", "files": {}},
            intent="explain", history=[{"user": "Original request"}])
    elif operation == "workflow_authoring.v2":
        adapter = module if module else workflow_authoring_runtime
        planner = codex_planner.CodexPlanner(tmp_path, "fixture-model", "max")
        value = await adapter.run(planner, workflow={"id": "frozen", "nodes": []}, message="Explain",
            intent="explain", execution_mode="full_access", history=[], catalog={}, references=[],
            emit=lambda *_: None, cleanup_state={})
    else:
        settings = Settings(repository_root=tmp_path, data_root=tmp_path / "data")
        store = RunStore(settings.database_path)
        store.create_run("offline", RunCreate(mode=RunMode.free_query, query="Inspect supplier"), runtime={
            "sdk_id": "codex", "provider_id": "codex", "configuration_digest": "fixture-digest",
            "model": "gpt-5.6-sol", "reasoning_effort": "max"})
        store.update_run("offline", status="planning")
        from tests.test_harness import FakeSapRead, FakeSkills
        broker = harness.HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
        if operation == "sample_discovery":
            cls = module.SampleDiscoveryService if module else sample_discovery.SampleDiscoveryService
            value = await cls(settings, store, broker).discover("offline", manifest(), {"company_code": "1710"}, revision=2)
        else:
            if operation != "free_query":
                store.update_harness_state("offline", {"acceptance_spec": {
                    "record_fields": ["object_id"], "metric_fields": ["object_count"], "required_assessments": []},
                    "acceptance_direct_baseline": operation == "acceptance_baseline"})
                output["acceptance_projection"] = None
            cls = module.CodexHarnessController if module else harness.CodexHarnessController
            value = await cls(settings, store, broker).run("offline", "Inspect supplier", None, "fixture-model", "max")
    return {"trace": trace, "result": normalized(value)}
