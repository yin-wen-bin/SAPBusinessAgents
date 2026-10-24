"""Persistent, revision-bound orchestration for explicitly requested sample discovery."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
from datetime import datetime, timedelta
from typing import Any

from .agent_lifecycle import AgentLifecycleError
from .models import RunCreate, RunMode, RunResult, RunStatus, RuntimeSnapshot, utc_now
from .sample_discovery import (SAMPLE_MODEL, SAMPLE_SECONDS, SAMPLE_QUERY_SECONDS,
                               SAMPLE_CLEANUP_SECONDS, SampleDiscoveryContext, SampleDiscoveryError)


class AgentDiscoveryJobs:
    def __init__(self, settings: Any, store: Any, lifecycle: Any, sdk_manager: Any, service: Any) -> None:
        self.settings, self.store, self.lifecycle = settings, store, lifecycle
        self.sdk_manager, self.service = sdk_manager, service
        self.tasks: dict[str, asyncio.Task] = {}

    @staticmethod
    def _selection(runtime: dict[str, Any]) -> dict[str, Any]:
        # selected_at is an observation timestamp, not execution identity. All
        # actual binding fields, including account/site/worker, remain covered.
        frozen = {key: value for key, value in runtime.items() if key != "selected_at"}
        binding = {"contract": "sapba.sample-runtime-selection/1", "operation": "sample_discovery",
                   "permission_mode": "bounded", "snapshot": frozen}
        return {"status": "ready", "provider_id": runtime["provider_id"], "model_id": runtime["model"],
                "binding_digest": hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":")).encode()).hexdigest(),
                "service_site": runtime.get("service_site"),
                "reasoning_effort": runtime.get("reasoning_effort")}

    @staticmethod
    def _failure_code(exc: Exception) -> str:
        code = getattr(exc, "code", None)
        return code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) else "sample_discovery_start_failed"

    def _assert_runtime(self, runtime: dict[str, Any]) -> None:
        auth = getattr(self.sdk_manager, "authentication_service", None)
        if auth:
            auth.assert_binding(runtime["provider_id"], runtime)
        if runtime.get("provider_id") == "workbuddy":
            self.sdk_manager.workbuddy.environment.assert_operation(runtime, "sample_discovery")
        elif runtime.get("provider_id") != "codex" or runtime.get("model") != SAMPLE_MODEL:
            raise AgentLifecycleError("The sample Runtime binding is inconsistent.", code="sample_discovery_model_mismatch")
        RuntimeSnapshot.model_validate(runtime)

    def _current_runtime(self) -> dict[str, Any]:
        provider = self.sdk_manager.default_provider_id
        if provider == "workbuddy":
            runtime = self.sdk_manager.runtime_snapshot("workbuddy")
        elif provider == "codex":
            # Keep Codex's existing sample-model policy and backend gates.
            runtime = self.sdk_manager.runtime_snapshot_for_model("codex", SAMPLE_MODEL)
        else:
            raise AgentLifecycleError("Select an available Runtime in settings.", code="runtime_default_not_configured")
        self._assert_runtime(runtime)
        return copy.deepcopy(runtime)

    def preflight(self, draft_id: str, payload: Any, *, _runtime: bool = True) -> dict[str, Any]:
        """Inspect the saved revision without reserving a task or contacting SAP."""
        draft = self.store.get_agent_authoring_draft(draft_id)
        revision = int(payload.expected_revision)
        if int(draft["revision"]) != revision:
            raise AgentLifecycleError("The draft has changed. Reload it before finding samples.", code="agent_draft_conflict")
        package = self.store.get_agent_authoring_revision(draft_id, revision)["package"]
        manifest = package["manifest"]
        supplied = copy.deepcopy(payload.input)
        _check_public_input(supplied, manifest.get("execution", {}).get("inputSchema", {}))
        try:
            result = SampleDiscoveryContext(manifest, supplied, revision,
                                            selected_fields=payload.selected_fields).readiness()
        except SampleDiscoveryError as exc:
            raise AgentLifecycleError("Invalid or private sample input.", code=exc.code) from exc
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise AgentLifecycleError("The saved discovery definition is invalid.",
                                      code="sample_definition_invalid") from exc
        if payload.prepare_runtime and _runtime:
            # Explicit readiness only: no official auth, SDK or model probe.
            try:
                result["runtime_selection"] = self._selection(self._current_runtime())
            except Exception as exc:
                result["runtime_selection"] = {"status": "unavailable", "code": self._failure_code(exc)}
        return result

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
        # A retransmission must recover its original frozen task before reading
        # today's defaults, even if an old client also sent prepareRuntime.
        readiness = self.preflight(draft_id, payload, _runtime=False)
        if not readiness["can_start"]:
            raise AgentLifecycleError("Supply the missing scope or add a provable discovery mapping before searching.",
                                      code="sample_discovery_preflight_failed", detail=readiness)
        fingerprint_input = supplied if payload.selected_fields is None else {"input": supplied, "selected_fields": sorted(payload.selected_fields)}
        if payload.runtime_selection is not None:
            fingerprint_input = {"scope": fingerprint_input, "runtime_selection": payload.runtime_selection.model_dump()}
        elif payload.runtime_policy == "system_default":
            # Fingerprint the request's intent, not mutable current settings.
            # The resolved execution snapshot is persisted separately, once.
            fingerprint_input = {"scope": fingerprint_input, "runtime_policy": "system_default"}
        fingerprint = hashlib.sha256(json.dumps(fingerprint_input, ensure_ascii=False, sort_keys=True,
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
        verified_runtime = None
        preparation_started = time.monotonic()
        requested_runtime = (payload.runtime_selection.model_dump(exclude={"binding_digest"})
                             if payload.runtime_selection is not None else {})
        binding_source = ("system_default" if payload.runtime_policy == "system_default" else
                          "explicit_selection" if payload.runtime_selection is not None else "draft_binding")
        self.store.update_agent_operation(draft_id, run_id, detail={
            "run_id": run_id, "revision": revision, "request_id": payload.request_id,
            "status": "queued", "phase": "runtime_admission", "execution_started": False,
            "created_at": operation["created_at"], "timeout_seconds": SAMPLE_SECONDS,
            "requested_runtime": requested_runtime,
            "runtime_binding_source": binding_source,
        })
        try:
            auth = getattr(self.sdk_manager, "authentication_service", None)
            if payload.runtime_policy == "system_default":
                runtime = self._current_runtime()
                requested_runtime = {"provider_id": runtime["provider_id"], "model_id": runtime["model"]}
            elif payload.runtime_selection is not None:
                runtime = self._current_runtime()
                expected = payload.runtime_selection
                actual = self._selection(runtime)
                if ((expected.provider_id, expected.model_id, expected.binding_digest)
                        != (actual["provider_id"], actual["model_id"], actual["binding_digest"])):
                    raise AgentLifecycleError("Runtime selection changed. Review it before starting a new request.",
                                              code="sample_runtime_selection_changed")
            else:
                # Old clients remain strict; never silently migrate a saved draft.
                bound = (draft.get("metadata") or {}).get("runtime_snapshot") or {}
                if auth:
                    auth.assert_binding(str(bound.get("provider_id") or "codex"), bound)
                if bound.get("provider_id") == "workbuddy":
                    runtime = self.sdk_manager.workbuddy.bound_snapshot(bound)
                else:
                    runtime = self.sdk_manager.runtime_snapshot_for_model("codex", SAMPLE_MODEL)
            if auth:
                runtime = auth.bind(runtime["provider_id"], runtime)
            self._assert_runtime(runtime)
            verified_runtime = copy.deepcopy(runtime)
            request = RunCreate(mode=RunMode.free_query, query="Find validation sample inputs for the isolated Agent draft.", input=supplied)
            self.store.create_run(run_id, request, runtime=runtime, query_origin="system")
            created_run = True
            self.store.save_agent_run_snapshot(run_id, manifest, rules_source=package.get("rules"),
                                               draft_id=draft_id, revision=revision)
            self.store.update_agent_operation(draft_id, run_id, detail={
                "run_id": run_id, "revision": revision, "model": runtime.get("model"),
                "runtime_snapshot": runtime, "execution_started": False,
                "runtime_selection": self._selection(runtime) if payload.runtime_selection is not None else None,
                "runtime_usage": self._selection(runtime), "runtime_binding_source": binding_source,
                "input": supplied, "status": "queued", "bounded_discovery": True,
                "timeout_seconds": SAMPLE_SECONDS, "phase": "queued",
                "reasoning_effort": runtime.get("reasoning_effort"), "created_at": utc_now(),
                "request_id": payload.request_id,
                "selected_fields": payload.selected_fields,
            })
            self.store.set_progress(run_id, phase="preparing", hard_limit_seconds=SAMPLE_SECONDS)
            self.tasks[run_id] = asyncio.create_task(self._run(draft_id, run_id, revision, manifest, supplied, payload.selected_fields))
            self.tasks[run_id].add_done_callback(lambda _task: self.tasks.pop(run_id, None))
        except Exception as exc:
            code = self._failure_code(exc)
            failure = {"status": "unavailable", "codes": [code], "phase": "runtime_admission",
                       "execution_started": False, "request_id": payload.request_id,
                       "created_at": operation["created_at"], "timeout_seconds": SAMPLE_SECONDS,
                       "startup_elapsed_seconds": round(time.monotonic() - preparation_started, 3),
                       "requested_runtime": requested_runtime, "completed_at": utc_now()}
            failure["runtime_binding_source"] = binding_source
            if verified_runtime is not None:
                failure.update(model=verified_runtime["model"], runtime_snapshot=verified_runtime,
                               runtime_usage=self._selection(verified_runtime),
                               reasoning_effort=verified_runtime.get("reasoning_effort"))
            if created_run:
                self._finish(draft_id, run_id, revision, supplied, failure)
            else:
                self.store.update_agent_operation(draft_id, run_id, detail={
                    "run_id": run_id, "revision": revision, **failure,
                })
                self.store.finish_agent_operation(draft_id, run_id, "unavailable")
            raise AgentLifecycleError(
                "Sample discovery did not start. Review its Runtime binding or enter inputs manually.",
                code=code, detail={"sample_discovery": self.get(draft_id, run_id)},
            ) from exc
        return self.get(draft_id, run_id)

    async def _run(self, draft_id: str, run_id: str, revision: int, manifest: dict, supplied: dict, selected_fields: list[str] | None = None) -> None:
        runtime = self.store.get_run(run_id).runtime
        manager = getattr(self.service, "workbuddy_manager", None)
        if runtime and runtime.provider_id == "workbuddy" and manager and not manager.supervisor.reserved:
            async with manager.supervisor.reserve():
                return await self._run(draft_id, run_id, revision, manifest, supplied, selected_fields)
        outcome: dict[str, Any]
        started = time.monotonic()
        started_at = utc_now()
        deadline_at = (datetime.fromisoformat(started_at) + timedelta(seconds=SAMPLE_SECONDS)).isoformat()
        self.store.update_agent_operation(draft_id, run_id, detail={
            **self.store.get_agent_operation_by_id(draft_id, run_id)["detail"],
            "started_at": started_at, "deadline_at": deadline_at, "phase": "preparing", "status": "running"})
        worker = None
        runtime = self.store.get_run(run_id).runtime
        sample_model = runtime.model if runtime and runtime.provider_id == "workbuddy" else "gpt-5.6-sol"
        try:
            # Recheck the frozen binding, never current defaults, after queuing.
            self._assert_runtime(runtime.model_dump(mode="json", exclude_none=True))
            self.store.update_run(run_id, status=RunStatus.running, started_at=started_at)
            self.store.merge_agent_operation_detail(draft_id, run_id, {"execution_started": True})
            self.store.append_event(run_id, "sample_discovery_started", {"draft_id": draft_id, "revision": revision, "model": sample_model})
            worker = asyncio.create_task(self.service.discover(
                run_id, manifest, supplied, revision=revision, model=sample_model,
                started=started, selected_fields=selected_fields,
            ))
            done, _ = await asyncio.wait({worker}, timeout=max(0, started + SAMPLE_SECONDS - time.monotonic()))
            if not done:
                raise TimeoutError()
            outcome = worker.result()
            if time.monotonic() >= started + SAMPLE_SECONDS:
                outcome = {"status": "timed_out", "codes": ["sample_discovery_timeout"]}
            if self.store.get_run(run_id).cancel_requested:
                outcome = {"status": "cancelled", "codes": ["sample_discovery_cancelled"]}
            elif not self.store.assert_agent_operation(draft_id, run_id, revision):
                outcome = {"status": "stale", "codes": ["agent_draft_conflict"]}
        except asyncio.CancelledError:
            outcome = {"status": "cancelled", "codes": ["sample_discovery_cancelled"]}
        except TimeoutError:
            outcome = {"status": "timed_out", "codes": ["sample_discovery_timeout"]}
        except Exception as exc:
            # Runtime and SAP exceptions can contain business values: never persist their text.
            outcome = {"status": "unavailable", "codes": ["sample_discovery_failed"]}
            if runtime and runtime.provider_id == "workbuddy":
                import re
                code = getattr(exc, "code", None)
                if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
                    outcome["codes"] = [code]
        if worker is not None and not worker.done():
            if hasattr(self.service, "quiesce"):
                self.service.quiesce(run_id)
            self.store.update_agent_operation(draft_id, run_id, status="cancelling", detail={
                **self.store.get_agent_operation_by_id(draft_id, run_id)["detail"],
                "phase": "cleaning_up", "codes": outcome.get("codes", [])})
            worker.cancel()
            done, _ = await asyncio.wait({worker}, timeout=SAMPLE_CLEANUP_SECONDS)
            if not done:
                self.store.update_agent_operation(draft_id, run_id, detail={
                    **self.store.get_agent_operation_by_id(draft_id, run_id)["detail"],
                    "codes": [*outcome.get("codes", []), "sample_cleanup_incomplete"]})
                def finish_after_cleanup(task):
                    if not task.cancelled():
                        task.exception()
                    self._finish(draft_id, run_id, revision, supplied, outcome)
                worker.add_done_callback(finish_after_cleanup)
                return  # Keep the SQL operation lock until cleanup actually finishes.
            if not worker.cancelled():
                worker.exception()
        if "runtime_cleanup_incomplete" in outcome.get("codes", []):
            self.store.update_agent_operation(draft_id, run_id, status="cancelling", detail={
                **self.store.get_agent_operation_by_id(draft_id, run_id)["detail"],
                "phase": "cleaning_up", "codes": ["sample_cleanup_incomplete"]})
            return
        self._finish(draft_id, run_id, revision, supplied, outcome)

    def _finish(self, draft_id: str, run_id: str, revision: int, supplied: dict, outcome: dict) -> None:
        current = self.store.get_agent_operation_by_id(draft_id, run_id)
        if current["status"] not in {"queued", "running", "cancelling"}:
            return
        execution = self.store.get_harness_state(run_id).get("sample_execution", {})
        outcome = {**current["detail"], **execution, **outcome, "run_id": run_id, "draft_id": draft_id, "revision": revision,
                   "model": current["detail"].get("model") or outcome.get("model"), "bounded_discovery": True, "completed_at": utc_now()}
        status = str(outcome.get("status") or "inconclusive")
        if outcome.get("started_at"):
            outcome["elapsed_seconds"] = round((datetime.fromisoformat(outcome["completed_at"]) - datetime.fromisoformat(outcome["started_at"])).total_seconds(), 1)
        if status not in {"ready", "found"}:
            outcome.setdefault("failed_stage", execution.get("phase", outcome.get("phase")))
        outcome["phase"] = "completed"
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
        self.store.set_progress(run_id, phase="preparing_result", state=terminal.value,
                                elapsed_seconds=int(outcome.get("elapsed_seconds", 0)))
        self.store.append_event(run_id, "sample_discovery_finished", {"status": status, "revision": revision, "codes": outcome.get("codes", [])})
        self.store.update_agent_operation(draft_id, run_id, detail=outcome)
        self.store.finish_agent_operation(draft_id, run_id, status)

    def get(self, draft_id: str, run_id: str) -> dict[str, Any]:
        operation = self.store.get_agent_operation_by_id(draft_id, run_id)
        if operation["kind"] != "sample_discovery":
            raise KeyError(run_id)
        value = {**operation["detail"]}
        if operation["status"] in {"queued", "running", "cancelling"}:
            execution = self.store.get_harness_state(run_id).get("sample_execution", {})
            if value.get("phase") != "cleaning_up":
                value.update(execution)
            if value.get("started_at"):
                value["elapsed_seconds"] = max(0, (datetime.fromisoformat(utc_now()) - datetime.fromisoformat(value["started_at"])).total_seconds())
                if value["elapsed_seconds"] >= SAMPLE_QUERY_SECONDS and value.get("phase") != "cleaning_up":
                    value["phase"] = "finalizing"
        return {**value, "run_id": run_id, "draft_id": draft_id,
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
        if self.store.get_agent_operation_by_id(draft_id, run_id)["detail"].get("phase") == "cleaning_up":
            return self.get(draft_id, run_id)
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
