"""Persistent, revision-bound orchestration for explicitly requested sample discovery."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from typing import Any

from .agent_lifecycle import AgentLifecycleError
from .models import RunCreate, RunMode, RunResult, RunStatus, RuntimeSnapshot, utc_now
from .sample_discovery import SAMPLE_MODEL, SampleDiscoveryContext, SampleDiscoveryError


class AgentDiscoveryJobs:
    def __init__(self, settings: Any, store: Any, lifecycle: Any, sdk_manager: Any, service: Any) -> None:
        self.settings, self.store, self.lifecycle = settings, store, lifecycle
        self.sdk_manager, self.service = sdk_manager, service
        self.tasks: dict[str, asyncio.Task] = {}

    def start(self, draft_id: str, payload: Any) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        self.lifecycle.require_technical_identity(draft)
        revision = int(payload.expected_revision)
        if int(draft["revision"]) != revision:
            raise AgentLifecycleError("The draft has changed. Reload it before finding samples.", code="agent_draft_conflict")
        package = self.store.get_agent_authoring_revision(draft_id, revision)["package"]
        manifest = copy.deepcopy(package["manifest"])
        supplied = copy.deepcopy(payload.input)
        _check_public_input(supplied, manifest.get("execution", {}).get("inputSchema", {}))
        try:
            SampleDiscoveryContext(manifest, supplied, revision)
        except SampleDiscoveryError as exc:
            raise AgentLifecycleError("Invalid or private sample input. Use the secure trial form instead.", code=exc.code) from exc
        fingerprint = hashlib.sha256(json.dumps(supplied, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest()
        try:
            operation = self.store.reserve_agent_operation(
                draft_id, revision, "sample_discovery", payload.request_id, fingerprint,
            )
        except ValueError as exc:
            raise AgentLifecycleError("This draft is busy or has changed.", code=str(exc)) from exc
        run_id = operation["operation_id"]
        if operation.get("reused"):
            return self.get(draft_id, run_id)
        created_run = False
        try:
            runtime = self.sdk_manager.runtime_snapshot_for_model("codex", SAMPLE_MODEL)
            if runtime.get("provider_id") != "codex" or runtime.get("model") != SAMPLE_MODEL:
                raise AgentLifecycleError("The sample Runtime binding is inconsistent.", code="sample_discovery_model_mismatch")
            RuntimeSnapshot.model_validate(runtime)
            request = RunCreate(mode=RunMode.free_query, query="Find validation sample inputs for the isolated Agent draft.", input=supplied)
            self.store.create_run(run_id, request, runtime=runtime)
            created_run = True
            self.store.save_agent_run_snapshot(run_id, manifest, rules_source=package.get("rules"),
                                               draft_id=draft_id, revision=revision)
            self.store.update_agent_operation(draft_id, run_id, detail={
                "run_id": run_id, "revision": revision, "model": "gpt-5.6-sol",
                "input": supplied, "status": "running", "bounded_discovery": True,
            })
            self.store.set_progress(run_id, phase="preparing", hard_limit_seconds=300)
            self.tasks[run_id] = asyncio.create_task(self._run(draft_id, run_id, revision, manifest, supplied))
            self.tasks[run_id].add_done_callback(lambda _task: self.tasks.pop(run_id, None))
        except Exception as exc:
            if created_run:
                self._finish(draft_id, run_id, revision, supplied, {
                    "status": "unavailable", "codes": ["sample_discovery_start_failed"],
                })
            else:
                self.store.update_agent_operation(draft_id, run_id, detail={
                    "run_id": run_id, "revision": revision, "status": "unavailable",
                    "codes": ["sample_discovery_unavailable"], "completed_at": utc_now(),
                })
                self.store.finish_agent_operation(draft_id, run_id, "unavailable")
            raise AgentLifecycleError(
                "Sample discovery requires an enabled, compatible GPT-5.6 Sol Runtime. You can enter parameters manually.",
                code=str(getattr(exc, "code", "sample_discovery_unavailable")),
            ) from exc
        return self.get(draft_id, run_id)

    async def _run(self, draft_id: str, run_id: str, revision: int, manifest: dict, supplied: dict) -> None:
        outcome: dict[str, Any]
        try:
            self.store.update_run(run_id, status=RunStatus.running, started_at=utc_now())
            self.store.append_event(run_id, "sample_discovery_started", {"draft_id": draft_id, "revision": revision, "model": "gpt-5.6-sol"})
            outcome = await asyncio.wait_for(self.service.discover(
                run_id, manifest, supplied, revision=revision, model="gpt-5.6-sol",
            ), timeout=300)
            if self.store.get_run(run_id).cancel_requested:
                outcome = {"status": "cancelled", "codes": ["sample_discovery_cancelled"]}
            elif not self.store.assert_agent_operation(draft_id, run_id, revision):
                outcome = {"status": "stale", "codes": ["agent_draft_conflict"]}
        except asyncio.CancelledError:
            outcome = {"status": "cancelled", "codes": ["sample_discovery_cancelled"]}
        except TimeoutError:
            outcome = {"status": "timed_out", "codes": ["sample_discovery_timeout"]}
        except Exception:
            # Runtime and SAP exceptions can contain business values: never persist their text.
            outcome = {"status": "unavailable", "codes": ["sample_discovery_failed"]}
        self._finish(draft_id, run_id, revision, supplied, outcome)

    def _finish(self, draft_id: str, run_id: str, revision: int, supplied: dict, outcome: dict) -> None:
        current = self.store.get_agent_operation_by_id(draft_id, run_id)
        if current["status"] not in {"queued", "running", "cancelling"}:
            return
        outcome = {**outcome, "run_id": run_id, "draft_id": draft_id, "revision": revision,
                   "model": "gpt-5.6-sol", "bounded_discovery": True, "completed_at": utc_now()}
        status = str(outcome.get("status") or "inconclusive")
        terminal = (RunStatus.completed if status in {"ready", "found"}
                    else RunStatus.cancelled if status == "cancelled" else RunStatus.inconclusive)
        summary = ({"zh": "已找到并核对验证样本，请确认参数后试运行。", "en": "A sample was found and verified. Confirm inputs before trying it."}
                   if status in {"ready", "found"} else
                   {"zh": "验证样本查找已取消，未启动试运行。", "en": "Sample discovery was cancelled. No trial was started."}
                   if status == "cancelled" else
                   {"zh": "未能确认完整验证样本，请手工补充参数。", "en": "A complete sample could not be confirmed. Fill the inputs manually."})
        result = RunResult(run_id=run_id, mode=RunMode.free_query, input=supplied,
                           summary=summary,
                           completed_at=outcome["completed_at"])
        self.store.update_run(run_id, status=terminal, result_json=result.model_dump(mode="json"), completed_at=outcome["completed_at"])
        self.store.set_progress(run_id, phase="preparing_result", state=terminal.value)
        self.store.append_event(run_id, "sample_discovery_finished", {"status": status, "revision": revision, "codes": outcome.get("codes", [])})
        self.store.update_agent_operation(draft_id, run_id, detail=outcome)
        self.store.finish_agent_operation(draft_id, run_id, status)

    def get(self, draft_id: str, run_id: str) -> dict[str, Any]:
        operation = self.store.get_agent_operation_by_id(draft_id, run_id)
        if operation["kind"] != "sample_discovery":
            raise KeyError(run_id)
        return {**operation["detail"], "run_id": run_id, "draft_id": draft_id,
                "revision": operation["revision"], "status": operation["status"]}

    async def cancel(self, draft_id: str, run_id: str) -> dict[str, Any]:
        current = self.get(draft_id, run_id)
        if current["status"] not in {"queued", "running", "cancelling"}:
            return current
        task = self.tasks.get(run_id)
        self.store.update_agent_operation(draft_id, run_id, status="cancelling")
        self.store.update_run(run_id, cancel_requested=True)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # A task cancelled before its first instruction never enters _run's
        # exception handler; complete its run and release the SQL lock here.
        self._finish(draft_id, run_id, current["revision"], current.get("input") or {}, {
            "status": "cancelled", "codes": ["sample_discovery_cancelled"],
        })
        return self.get(draft_id, run_id)

    async def stop(self) -> None:
        for run_id in list(self.tasks):
            snapshot = self.store.get_agent_run_snapshot(run_id)
            await self.cancel(snapshot["validation_draft_id"], run_id)


def _check_public_input(value: Any, schema: dict[str, Any], path: str = "") -> None:
    if schema.get("x-sapba-secret-kind") or any(schema.get(flag) is True for flag in ("x-sapba-sensitive", "x-sapba-internal", "x-sapba-workflow-only")):
        raise AgentLifecycleError("This field cannot be sent to sample discovery.", code="sample_discovery_private_input", detail={"field": path})
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for key, child in value.items():
            if key not in properties:
                raise AgentLifecycleError("Unknown sample input field.", code="sample_discovery_input_invalid")
            _check_public_input(child, properties[key], f"{path}.{key}".strip("."))
    elif isinstance(value, list):
        for child in value:
            _check_public_input(child, schema.get("items") or {}, path + "[]")
