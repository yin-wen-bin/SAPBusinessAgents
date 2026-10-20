"""Live SDK, real draft lifecycle, isolated database/package; no production edits."""
import asyncio
import copy
import json
import time
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from sap_business_agents_platform.acceptance import agent_execution_digest
from sap_business_agents_platform.agent_authoring import package_changes
from sap_business_agents_platform.agent_lifecycle import AgentLifecycleService
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.manifests import AgentRepository, validate_manifest
from sap_business_agents_platform.managed_rules import validate_managed_rule
from sap_business_agents_platform.models import AgentFeedbackRequest
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner
from sap_business_agents_platform.workbuddy_verification import validate_operation
from sap_business_agents_platform.workflows import WorkflowRepository, agent_digest


ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / ".local-data/workbuddy-validation"
DIAGNOSTICS = "--native-diagnostics" in sys.argv
EXPLAIN_ONLY = "--explain-only" in sys.argv
SCHEMA_STAGE = "--schema-stage" in sys.argv
NATIVE_FEEDBACK = "--feedback-native" in sys.argv
TEST_CACHE_FIX = "--test-cache-fix" in sys.argv
EXACT_SUFFIX = "--exact-suffix" in sys.argv
PRODUCTION_BUDGET = "--production-budget" in sys.argv or TEST_CACHE_FIX or EXACT_SUFFIX
BUDGET_SECONDS = 3600
FILE_MODE = "--workspace-candidate" in sys.argv or DIAGNOSTICS or (SCHEMA_STAGE or NATIVE_FEEDBACK or PRODUCTION_BUDGET) and not EXPLAIN_ONLY
REPORT = HERE / ('agent-feedback-' + uuid.uuid4().hex + '.json')
RUN_ROOT = HERE / 'agent-feedback-data' / uuid.uuid4().hex
SUFFIX = "\n\n验证说明：此样例仅验证草稿编写，不执行 SAP。\nValidation note: this sample only checks draft authoring; no SAP is executed.\n"


class InternalValidationRuntime:
    """Staging uses the frozen checked binding without enabling the public Runtime."""
    def __init__(self, manager, snapshot):
        self.manager, self.binding = manager, snapshot
        self.planner = WorkBuddyPlanner(ROOT, snapshot["model"], supervisor=manager.supervisor,
                                       runtime_snapshot=snapshot)
        self.last_operation_id = None

    def snapshot(self):
        return copy.deepcopy(self.binding)

    def snapshot_for_binding(self, value):
        return self.manager.bound_snapshot(value)

    @contextmanager
    def pin_snapshot(self, value):
        assert value == self.binding
        yield

    def supports(self, operation):
        return operation == "review_agent_feedback"

    def feedback_capabilities(self, *_):
        return self.planner.feedback_capabilities()

    def workbuddy_reserved(self):
        return self.planner.workbuddy_reserved()

    def workbuddy_reservation(self):
        return self.planner.workbuddy_reservation()

    def bind_events(self, sink):
        def diagnostic(kind, data):
            if (DIAGNOSTICS or NATIVE_FEEDBACK or PRODUCTION_BUDGET) and kind in {"agent_runtime_tool_started", "agent_runtime_tool_finished",
                    "agent_runtime_result_received", "agent_runtime_sdk_closing", "agent_runtime_sdk_closed"}:
                print(json.dumps({"event": kind, **data}, ensure_ascii=False), flush=True)
            sink(kind, data)
        return self.planner.bind_events(diagnostic)

    async def review_agent_feedback(self, **kwargs):
        self.last_operation_id = kwargs["operation_id"]
        return await self.planner.review_agent_feedback(**kwargs)

    async def abort_agent_feedback(self, operation_id):
        return await self.planner.abort_agent_feedback(operation_id)


async def main():
    if (DIAGNOSTICS or EXPLAIN_ONLY or SCHEMA_STAGE or NATIVE_FEEDBACK or PRODUCTION_BUDGET) and REPORT.exists():
        raise SystemExit("Diagnostic report already exists; no automatic retry or overwrite.")
    manager = WorkBuddyManager(ROOT)
    snapshot = json.loads((HERE / 'runtime-snapshot.json').read_text(encoding='utf-8'))
    from workbuddy_live_cases.live_guard import attach_validation_monitor
    attach_validation_monitor(manager, snapshot, HERE / 'agent-feedback-events.jsonl')
    runtime = InternalValidationRuntime(manager, snapshot)
    settings = Settings(repository_root=ROOT, data_root=RUN_ROOT / "data", draft_root=RUN_ROOT / "drafts",
                        agent_feedback_timeout_seconds=BUDGET_SECONDS)
    store = RunStore(settings.database_path)
    agents = AgentRepository(ROOT / "agents")
    service = AgentLifecycleService(settings, store, agents, WorkflowRepository(ROOT / "workflows", agents),
                                    SimpleNamespace(), runtime)
    source = agents.get("mm-po-gr-status")
    source_before = agents.package("mm-po-gr-status")
    source_digest = agent_execution_digest(source, source_before["rules_source"])
    draft = service.create_version_draft("mm-po-gr-status", bump="patch", expected_version=source["version"],
                                        expected_hash=agent_digest(source))
    report = {"phase": "running", "isolated_data_root": str(RUN_ROOT.relative_to(ROOT)),
        "draft_id": draft["draft_id"], "runtime": snapshot, "sap_calls": 0,
        "stages": [], "business_operations_validated": [], "execution_budget_seconds": BUDGET_SECONDS}
    atomic_json(REPORT, report)

    async def run_stage(intent, message, mode):
        before = service.get_draft(draft["draft_id"])
        before_package = copy.deepcopy(before["package"])
        request_id = "workbuddy-feedback-validation-" + uuid.uuid4().hex
        latest = max((item["turn"] for item in before["conversation"]), default=0)
        payload = AgentFeedbackRequest(baseTurn=latest, baseRevision=before["revision"], intent=intent,
            feedback=message, requestId=request_id, locale="zh",
            executionMode="trusted_local" if mode == "trusted_local" else None,
            trustedLocalConfirmed=mode == "trusted_local")
        stage = {"intent": intent, "permission_mode": mode, "request_id": request_id, "phase": "running"}
        report["stages"].append(stage)
        atomic_json(REPORT, report)
        started = time.monotonic()
        async def execute():
            submitted = await service.submit_feedback(draft["draft_id"], payload)
            tasks = list(service._feedback_tasks.values())
            if not tasks:
                raise RuntimeError("validation_feedback_not_scheduled")
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=BUDGET_SECONDS + 15)
            return service.get_draft(draft["draft_id"])
        async def verify(after):
            turn = after["conversation"][-1]
            stage.update(turn_status=turn["status"], operation_id=runtime.last_operation_id,
                         before_revision=before["revision"], after_revision=after["revision"],
                         elapsed_seconds=round(time.monotonic() - started, 2))
            if turn["status"] != "completed":
                failure = RuntimeError("draft_feedback_validation_failed")
                failure.code = (turn.get("decision") or {}).get("error_code", "draft_feedback_validation_failed")
                raise failure
            package = after["package"]
            validate_manifest(package["manifest"])
            validate_managed_rule(package["rules"], expected_digest=package["manifest"]["managedRule"]["sha256"])
            assert package["manifest"]["execution"] == before_package["manifest"]["execution"]
            assert package["rules"] == before_package["rules"]
            assert package["manifest"]["validation"] == before_package["manifest"]["validation"]
            assert agent_execution_digest(package["manifest"], package["rules"]) == source_digest
            diffs = package_changes(before_package, package)
            if intent == "explain":
                assert not diffs and after["revision"] == before["revision"]
                assert (turn.get("decision") or {}).get("summary", {}).get("zh")
            else:
                assert after["revision"] == before["revision"] + 1
                assert {item["path"] for item in diffs} == {"/readme"}
                assert package["readme"] == before_package["readme"] + SUFFIX
                assert len(turn["diff"]) == 1
            operation_id = runtime.last_operation_id
            job = json.loads((manager.environment.root / "jobs" / (digest(operation_id) + ".json")).read_text(encoding="utf-8"))
            assert job["status"] == "completed" and job["cleanup_complete"] is True
            stage.update(phase="passed", diff_paths=[item["path"] for item in diffs],
                         cleanup_complete=True, elapsed_seconds=round(time.monotonic() - started, 2))
            return {"platform_contract_passed": True, "case": intent, "isolated_lifecycle": True,
                    "revision": after["revision"], "diff_paths": stage["diff_paths"], "sap_calls": 0,
                    "execution_digest": source_digest, "request_id": request_id}
        # The lifecycle generates the actual owned operation ID, so registration
        # happens after full verification rather than guessing that ID up front.
        with manager.supervisor.verification(snapshot, {"review_agent_feedback"}):
            result = await execute()
        evidence = await verify(result)
        manager.record_operation_validation(snapshot, "review_agent_feedback", job_id=runtime.last_operation_id,
            permission_modes=[mode], evidence={**evidence, "kind": "live_validation"})
        report["business_operations_validated"] = ["review_agent_feedback"]
        atomic_json(REPORT, report)
        print(json.dumps({"stage": intent, **stage}, ensure_ascii=False), flush=True)

    try:
        print("Isolated lifecycle created; starting one workspace-candidate revision." if FILE_MODE
              else "Isolated lifecycle created; starting one explanation round.", flush=True)
        if not FILE_MODE:
            await run_stage("explain", "只解释此草稿的业务目标、只读查询及输出；不要修改任何文件、字段或资格，也不要访问 SAP。", "bounded")
        if not EXPLAIN_ONLY:
            await run_stage("revise", "仅在当前 README 的最后原样追加以下双语段落（包含开头两个换行和结尾换行），其余字节保持不变："
                + repr(SUFFIX) + "。请读取隔离 agent-package/README.md，使用原生文件编辑工具只修改该文件；"
                + ("完成后立刻返回符合 Schema 的 revise_agent 简短 JSON；所有包和 edits_json 字段均为空字符串，由平台读取候选，不重复输出 README。"
                   if FILE_MODE else "返回 /readme 的唯一 replace 编辑。")
                + "不得修改 Manifest、规则、身份、验收或平台源码，不访问 SAP。"
                + ("这是字节精确追加：原文件即使已经以换行结尾，也仍须额外追加两个 LF，不能合并、裁剪或规范化既有换行。"
                   "请保存原始内容，追加完整指定字符串，并在隔离副本核对新内容等于原始内容加指定字符串，核对通过后再收尾。"
                   if EXACT_SUFFIX else "")
                + ("完成所需的本地核对后，调用 StructuredOutput 收尾，不启动无关调查。" if NATIVE_FEEDBACK or PRODUCTION_BUDGET else ""), "trusted_local")
        assert agent_execution_digest(agents.get("mm-po-gr-status"), agents.package("mm-po-gr-status")["rules_source"]) == source_digest
        report.update(phase="passed", business_operations_validated=["review_agent_feedback"], runtime_after=manager.snapshot())
    except Exception as exc:
        report.update(phase="failed", failure_code=getattr(exc, "code", type(exc).__name__), runtime_after=manager.snapshot())
        if report["stages"] and report["stages"][-1]["phase"] == "running":
            report["stages"][-1].update(phase="failed", failure_code=report["failure_code"])
        print(json.dumps({"phase": "failed", "failure_code": report["failure_code"]}), flush=True)
    atomic_json(REPORT, report)
    print(json.dumps({"phase": report["phase"], "business_operations_validated": report["business_operations_validated"],
        "enabled": report["runtime_after"]["enabled"], "sap_calls": 0}), flush=True)
    if report["phase"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
