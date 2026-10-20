"""Once-only isolated Codex workflow v2 comparison. No SAP or publication."""
import argparse
import asyncio
import copy
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.codex_planner import CodexPlanner
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.models import WorkflowFeedbackRequest
from sap_business_agents_platform.runtime import CodexRuntimeProbe
from sap_business_agents_platform.runtime_policy import orchestration_digest
from sap_business_agents_platform.sdk_manager import SDKManager
from sap_business_agents_platform.workflow_assistant import state, fixed_bindings
from sap_business_agents_platform.workflow_factory import WorkflowDraftService
from sap_business_agents_platform.workbuddy_environment import atomic_json

ROOT = Path(__file__).resolve().parents[1]


class Runtime:
    current_provider_id = "codex"
    def __init__(self, snapshot, planner):
        self.binding, self.planner = snapshot, planner
    def snapshot(self, *_):
        return copy.deepcopy(self.binding)
    @contextmanager
    def pin(self, provider, model, effort):
        assert provider == "codex" and model == self.binding["model"] and effort == self.binding["reasoning_effort"]
        yield
    async def author_workflow_v2(self, **kwargs):
        return await self.planner.author_workflow_v2(**kwargs)


async def verify(destination):
    directory = Path(destination).resolve()
    allowed = (ROOT / ".local-data/workbuddy-baseline").resolve()
    assert directory.is_relative_to(allowed) and directory != allowed and not directory.exists()
    settings = Settings.from_env(ROOT)
    registry = SDKManager(ROOT / "config/sdks.json", ROOT, runtime_probes={"codex": CodexRuntimeProbe()},
        selection_path=settings.sdk_runtime_state_path, legacy_model=settings.codex_model)
    assert registry.default_provider_id == "codex"
    binding = registry.runtime_snapshot("codex")
    directory.mkdir(parents=True)
    planner = CodexPlanner(ROOT, model=binding["model"], reasoning_effort=binding["reasoning_effort"])
    settings = Settings(repository_root=ROOT, data_root=directory / "data", draft_root=directory / "drafts")
    service = WorkflowDraftService(settings, RunStore(settings.database_path), AgentRepository(ROOT / "agents"), None, None, Runtime(binding, planner))
    source = ROOT / "workflows/Common/p2p-batch-payment-review/workflow.json"
    original_bytes = source.read_bytes()
    workflow = json.loads(original_bytes)
    draft = service.create(workflow["title"], workflow["description"], workflow)
    report = {"provider": "codex", "runtime": binding, "phase": "running", "sap_calls": 0,
        "orchestration_digest": orchestration_digest("workflow_authoring.v2"), "stages": []}
    path = directory / "report.json"
    atomic_json(path, report)
    try:
        for intent, message in [
            ("explain", "只解释已保存工作流和固定 Agent 绑定，不修改、不运行、不访问 SAP。"),
            ("revise", "仅将 title 改为 zh=Codex 隔离验证工作流、en=Codex Isolated Validation Workflow。保留其他全部定义和固定绑定。不运行、不访问 SAP、不发布。"),
        ]:
            before = service.get(draft.draft_id)
            request = uuid.uuid4().hex
            body = WorkflowFeedbackRequest(baseTurn=1, baseRevision=before.revision, requestId=request,
                intent=intent, feedback=message, executionMode="full_access", trustedLocalConfirmed=True,
                references=[{"kind": "workflow_field", "draftId": draft.draft_id, "revision": before.revision, "path": "/title"}],
                **({"allowedChangePaths": ["/title"]} if intent == "revise" else {})).model_dump(mode="json", by_alias=True)
            entry = {"intent": intent, "request_id": request, "phase": "running"}
            report["stages"].append(entry)
            atomic_json(path, report)
            started = time.monotonic()
            service.assistant.submit(draft.draft_id, body)
            service.assistant.submit(draft.draft_id, body)
            await asyncio.wait_for(asyncio.gather(*list(service.assistant.tasks.values())), 3615)
            after = service.get(draft.draft_id)
            saved = next(r for r in state(after)["rounds"] if r["request_id"] == request)
            entry.update(status=saved["status"], failure_code=saved.get("failure_code"),
                validation_issues=saved.get("validation_issues"), elapsed_seconds=round(time.monotonic() - started, 2))
            assert saved["status"] == "completed", saved.get("failure_code") or "codex_workflow_comparison_failed"
            expected = copy.deepcopy(before.workflow)
            if intent == "revise":
                expected["title"] = {"zh": "Codex 隔离验证工作流", "en": "Codex Isolated Validation Workflow"}
            assert after.workflow == expected and after.revision == before.revision + int(intent == "revise")
            assert fixed_bindings(after.workflow) == fixed_bindings(before.workflow)
            assert source.read_bytes() == original_bytes
            assert orchestration_digest("workflow_authoring.v2") == report["orchestration_digest"]
            entry.update(phase="passed", result_revision=after.revision, diff=saved.get("diff"))
            atomic_json(path, report)
            print(json.dumps(entry, ensure_ascii=False), flush=True)
        report["phase"] = "passed"
    except Exception as error:
        report.update(phase="failed", failure_code=getattr(error, "code", "codex_workflow_comparison_failed"))
    finally:
        await service.assistant.stop()
        atomic_json(path, report)
    print(json.dumps({"report": str(path), "phase": report["phase"], "sap_calls": 0}), flush=True)
    return int(report["phase"] != "passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        raise SystemExit(asyncio.run(verify(args.data_root)))
    print(json.dumps({"execute": False, "provider": "codex", "sap_calls": 0}))
