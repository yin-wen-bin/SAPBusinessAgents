"""Actual WorkBuddy workflow v2 through isolated persistent orchestration; no SAP."""
import asyncio
import copy
import json
import time
import uuid
import sys
from contextlib import contextmanager
from pathlib import Path

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.models import WorkflowFeedbackRequest
from sap_business_agents_platform.workflow_assistant import state, fixed_bindings
from sap_business_agents_platform.workflow_factory import WorkflowDraftService, _json_diff
from sap_business_agents_platform.workflows import validate_workflow
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / ".local-data/workbuddy-validation"
PRODUCTION_BUDGET = "--production-budget" in sys.argv
BUDGET_SECONDS = 3600
REPORT = HERE / ('workflow-v2-' + uuid.uuid4().hex + '.json')
RUN_ROOT = HERE / 'workflow-v2-data' / uuid.uuid4().hex

class ValidationRuntime:
    current_provider_id = "workbuddy"
    def __init__(self, manager, snapshot):
        self.binding = snapshot
        self.planner = WorkBuddyPlanner(ROOT, snapshot["model"], supervisor=manager.supervisor, runtime_snapshot=snapshot)
        self.calls = []
        self.output_shapes = []
        original_run = manager.supervisor.run
        async def observe(**kwargs):
            result = await original_run(**kwargs)
            text = result.get("text", "")
            try:
                strict_object = isinstance(json.loads(text), dict)
            except (TypeError, ValueError):
                strict_object = False
            self.output_shapes.append({"request_id": kwargs.get("task_id"),
                "fenced": isinstance(text, str) and text.lstrip().startswith(chr(96) * 3),
                "strict_json_object": strict_object, "output_format": result.get("output_format")})
            return result
        manager.supervisor.run = observe
    def snapshot(self, *_):
        return copy.deepcopy(self.binding)
    @contextmanager
    def pin_snapshot(self, snapshot):
        assert snapshot == self.binding
        yield
    def workbuddy_reserved(self):
        return self.planner.workbuddy_reserved()
    def workbuddy_reservation(self):
        return self.planner.workbuddy_reservation()
    async def author_workflow_v2(self, **kwargs):
        self.calls.append(kwargs["request_id"])
        if PRODUCTION_BUDGET:
            from sap_business_agents_platform.workbuddy_diagnostics import safe_progress
            original_emit = kwargs["emit"]
            def emit(kind, data):
                safe = safe_progress(kind, data)
                if safe:
                    print(json.dumps({"event": kind, **safe}), flush=True)
                original_emit(kind, data)
            kwargs["emit"] = emit
        return await self.planner.author_workflow_v2(**kwargs)

async def main():
    if REPORT.exists():
        raise SystemExit("Report exists; do not retry or overwrite automatically.")
    manager = WorkBuddyManager(ROOT)
    snapshot = json.loads((HERE / 'runtime-snapshot.json').read_text(encoding='utf-8'))
    from workbuddy_live_cases.live_guard import attach_validation_monitor
    attach_validation_monitor(manager, snapshot, HERE / 'workflow-v2-events.jsonl')
    runtime = ValidationRuntime(manager, snapshot)
    settings = Settings(repository_root=ROOT, data_root=RUN_ROOT / "data", draft_root=RUN_ROOT / "drafts")
    store = RunStore(settings.database_path)
    agents = AgentRepository(ROOT / "agents")
    service = WorkflowDraftService(settings, store, agents, None, None, runtime)
    original = ROOT / "workflows/Common/p2p-batch-payment-review/workflow.json"
    original_bytes = original.read_bytes()
    workflow = json.loads(original_bytes)
    draft = service.create(workflow["title"], workflow["description"], workflow)
    report = {"phase": "running", "runtime": snapshot, "isolated_data_root": str(RUN_ROOT.relative_to(ROOT)),
              "draft_id": draft.draft_id, "sap_calls": 0, "stages": [], "execution_budget_seconds": BUDGET_SECONDS}
    atomic_json(REPORT, report)

    async def stage(intent, message):
        before = service.get(draft.draft_id)
        before_workflow = copy.deepcopy(before.workflow)
        request_id = "workbuddy-workflow-v2-validation-" + uuid.uuid4().hex
        request = WorkflowFeedbackRequest(baseTurn=1, baseRevision=before.revision, feedback=message,
            intent=intent, requestId=request_id, executionMode="full_access", trustedLocalConfirmed=True,
            budgetSeconds=BUDGET_SECONDS, references=[{"kind": "workflow_field", "draftId": draft.draft_id,
                "revision": before.revision, "path": "/title"}],
            **({"allowedChangePaths": ["/title"]} if intent == "revise" else {})).model_dump(mode="json", by_alias=True)
        entry = {"intent": intent, "request_id": request_id, "phase": "running", "before_revision": before.revision}
        report["stages"].append(entry)
        atomic_json(REPORT, report)
        started = time.monotonic()
        with manager.supervisor.verification(snapshot, {"workflow_authoring.v2"}):
            service.assistant.submit(draft.draft_id, request)
            service.assistant.submit(draft.draft_id, request)  # Exact retry: one owned round.
            tasks = list(service.assistant.tasks.values())
            assert len(tasks) == 1
            await asyncio.wait_for(asyncio.gather(*tasks), BUDGET_SECONDS + 15)
        after = service.get(draft.draft_id)
        round_ = next(item for item in state(after)["rounds"] if item["request_id"] == request_id)
        entry.update(status=round_["status"], after_revision=after.revision,
                     elapsed_seconds=round(time.monotonic() - started, 2),
                     output_shapes=[shape for shape in runtime.output_shapes if shape["request_id"] == request_id])
        if round_["status"] != "completed":
            failure = RuntimeError("workflow_v2_validation_failed")
            failure.code = round_.get("failure_code", "workflow_v2_validation_failed")
            raise failure
        assert runtime.calls.count(request_id) == 1
        assert round_["runtime"]["environment_digest"] == snapshot["environment_digest"]
        assert round_["references"][0]["path"] == "/title"
        assert round_["answer"].strip()
        validate_workflow(after.workflow, agents, require_pins=True)
        assert fixed_bindings(after.workflow) == fixed_bindings(before_workflow)
        diffs = _json_diff(before_workflow, after.workflow)
        if intent == "explain":
            assert after.revision == before.revision and not diffs
        else:
            expected = copy.deepcopy(before_workflow)
            expected["title"] = {"zh": "WorkBuddy 隔离验证工作流", "en": "WorkBuddy Isolated Validation Workflow"}
            assert after.workflow == expected and after.revision == before.revision + 1
            assert round_["result_revision"] == after.revision and round_["diff"] == diffs
        job = json.loads((manager.environment.root / "jobs" / (digest(request_id) + ".json")).read_text(encoding="utf-8"))
        assert job["status"] == "completed" and job["cleanup_complete"] is True
        entry.update(phase="passed", cleanup_complete=True, diff_paths=[d["path"] for d in diffs])
        assert original.read_bytes() == original_bytes
        atomic_json(REPORT, report)
        print(json.dumps(entry, ensure_ascii=False), flush=True)

    try:
        print("Isolated workflow v2 explanation starting; fixed bindings remain immutable.", flush=True)
        await stage("explain", "只用中文解释已保存工作流的业务路径与固定 Agent 绑定；不修改文件或定义、不运行工作流、不访问 SAP。")
        print("One workflow v2 title-only revision starting.", flush=True)
        await stage("revise", "仅将 title 改为 zh=WorkBuddy 隔离验证工作流、en=WorkBuddy Isolated Validation Workflow。"
            "保留其他所有字段、固定 Agent ID/版本/Digest、条件、Schema 与连接。可核对 candidate/workflow.json；"
            "返回完整规范工作流，不运行工作流，不访问 SAP，不发布。")
        manager.record_operation_validation(snapshot, "workflow_authoring.v2", job_id=report["stages"][-1]["request_id"],
            permission_modes=["trusted_local"], evidence={"kind": "live_validation", "platform_contract_passed": True,
                "isolated_workflow_lifecycle": True, "stages": report["stages"], "fixed_bindings_unchanged": True,
                "sap_calls": 0, "request_idempotency": True})
        report.update(phase="passed", runtime_after=manager.snapshot())
    except Exception as exc:
        report.update(phase="failed", failure_code=getattr(exc, "code", type(exc).__name__), runtime_after=manager.snapshot())
        if report["stages"] and report["stages"][-1]["phase"] == "running":
            report["stages"][-1].update(phase="failed", failure_code=report["failure_code"])
    atomic_json(REPORT, report)
    print(json.dumps({"phase": report["phase"], "failure_code": report.get("failure_code"),
        "enabled": report["runtime_after"]["enabled"], "sap_calls": 0}), flush=True)
    if report["phase"] != "passed":
        raise SystemExit(1)

if __name__ == "__main__":
    asyncio.run(main())
