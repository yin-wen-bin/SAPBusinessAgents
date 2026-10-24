"""Bounded SDK-free process proxy. Each worker belongs to one task/attempt."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import signal
import time
import uuid
from pathlib import Path
from typing import Any
from contextvars import ContextVar
from contextlib import contextmanager, asynccontextmanager, nullcontext

from .workbuddy_environment import PROTOCOL, WorkBuddyEnvironment, WorkBuddyError, atomic_json, digest
from .workbuddy_record_io import read_record
from .workbuddy_inspection import current as inspection
from .workbuddy_job import WindowsJob, process_identity
from .runtime_policy import OPERATIONS, ORCHESTRATION_VERSION, orchestration_digest

FRAME_LIMIT = 1024 * 1024
OUTPUT_LIMIT = 8 * FRAME_LIMIT


class WorkBuddySupervisor:
    def __init__(self, environment: WorkBuddyEnvironment):
        self.environment = environment
        self.execution_lock = asyncio.Lock()
        self.worker_lock = asyncio.Lock()
        self.probe_lock = asyncio.Lock()
        self.dispatch_lock = asyncio.Lock()
        from .runtime_capacity import RuntimeCapacity
        self.query_capacity = RuntimeCapacity()
        self.active: dict[str, dict] = {}
        self._dispatching: dict[str, dict] = {}
        self.queued: set[str] = set()
        self.cancelled: set[str] = set()
        self.closed: set[str] = set()
        self.cleanup_deadlines: dict[str, float] = {}
        self.authentication_failed = None
        self._verification = ContextVar("workbuddy_internal_verification", default=None)
        self._reserved = ContextVar("workbuddy_execution_reservation", default=False)

    @property
    def reserved(self):
        value = self._reserved.get()
        return value if value and value.get('active') else False

    @asynccontextmanager
    async def reserve(self, *, task_id=None, parallel=False):
        """Business entry points acquire capacity before starting their clocks."""
        if self.reserved and (not parallel or self.reserved.get('task_id') == task_id):
            if task_id is not None and self.reserved.get('parallel') and self.reserved['task_id'] != task_id:
                raise WorkBuddyError('workbuddy_reservation_owner_mismatch')
            yield
            return
        async with self.query_capacity.acquire(shared=parallel):
            reservation = {'task_id': task_id, 'parallel': parallel, 'active': True,
                           'worker_lock': asyncio.Lock() if parallel else self.worker_lock}
            token = self._reserved.set(reservation)
            try:
                yield
            finally:
                reservation['active'] = False
                self._reserved.reset(token)

    @contextmanager
    def verification(self, snapshot: dict, operations: set[str]):
        """Internal staging only, never exposed by public Runtime/business routes."""
        from .workbuddy_environment import OPERATIONS
        if not operations or not operations.issubset(OPERATIONS) or snapshot.get("provider_id") != "workbuddy":
            raise WorkBuddyError("workbuddy_validation_scope_invalid")
        self.environment.assert_verification_binding(snapshot,
            formal=bool(operations & {"acceptance_baseline", "acceptance_free_query"}))
        token = self._verification.set((digest(snapshot), frozenset(operations)))
        try:
            yield
        finally:
            self._verification.reset(token)

    def check_operation(self, snapshot: dict, operation: str, mode: str = "bounded") -> dict:
        verification = self._verification.get()
        if (verification and verification[0] == digest(snapshot) and operation in verification[1]
                and mode in {"bounded", "trusted_local"}):
            return self.environment.assert_verification_binding(snapshot,
                formal=operation in {"acceptance_baseline", "acceptance_free_query"})
        return self.environment.assert_operation(snapshot, operation, mode=mode)

    @asynccontextmanager
    async def _capacity(self, probe: bool, task_id: str, operation: str = ''):
        if task_id in self.queued:
            raise WorkBuddyError("workbuddy_task_busy")
        self.queued.add(task_id)
        try:
            if probe:
                async with self.probe_lock:
                    yield
            else:
                async with (nullcontext() if self.reserved else self.reserve(
                        task_id=task_id, parallel=operation == 'free_query')):
                    reservation = self.reserved
                    if reservation['parallel'] and reservation['task_id'] != task_id:
                        raise WorkBuddyError('workbuddy_reservation_owner_mismatch')
                    # Reservations propagate to business child tasks. They may not
                    # create a second concurrent worker even if contexts are shared.
                    async with reservation['worker_lock']:
                        yield
        finally:
            self.queued.discard(task_id)
            self._dispatching.pop(task_id, None)

    def pending(self) -> list[dict]:
        scope = inspection(self.environment)
        if scope is not None:
            return scope.memo(('pending_jobs',), lambda: self._pending(scope))
        return self._pending()

    def _pending(self, scope=None) -> list[dict]:
        result = []
        directory = self.environment.root / 'jobs'
        from .workbuddy_job_index import enabled, pending
        if enabled(self.environment.root):
            return pending(self.environment.root, scope=scope, owned_dispatch=self._owned_dispatch)
        paths = list(directory.glob('*.json'))
        if scope:
            scope.guard(('job_names',), lambda: frozenset(p.name for p in directory.glob('*.json')),
                        frozenset(p.name for p in paths))
        for path in paths:
            try:
                if scope:
                    scope.watch(path)
                item = read_record(path)
            except (OSError, ValueError):
                raise WorkBuddyError("workbuddy_job_record_invalid") from None
            if item.get("status") in {"cleanup_pending", "dispatching"}:
                if not self._owned_dispatch(item):
                    result.append(item)
        return result

    def _owned_dispatch(self, record):
        if record.get('status') != 'dispatching':
            return False
        owner = self._dispatching.get(record.get('task_id'))
        if owner is None:
            active = self.active.get(record.get('task_id'))
            owner = active['record'] if active else None
        return bool(owner and record.get('attempt_id') == owner.get('attempt_id')
                    and record.get('runtime_digest') == owner.get('runtime_digest')
                    and record.get('environment_digest') == owner.get('environment_digest')
                    and record.get('protocol') == PROTOCOL
                    and record.get('deadline') == owner.get('deadline')
                    and record.get('dispatch_phase') in {'claimed', 'spawning', 'owned'}
                    and record.get('dispatch_owner_pid') == os.getpid()
                    and record.get('dispatch_owner_identity') not in {None, 'unknown'}
                    and record.get('dispatch_owner_identity') == process_identity(os.getpid()))

    def reconcile(self) -> list[dict]:
        """Kill-on-close closes orphan jobs on API death; uncertainty stays blocked."""
        pending = []
        from .workbuddy_job_index import enabled, paths as unfinished
        indexed = enabled(self.environment.root)
        records = unfinished(self.environment.root) if indexed else [(p, None) for p in (self.environment.root / 'jobs').glob('*.json')]
        for path, item in records:
            try:
                item = item if item is not None else read_record(path)
            except (ValueError, OSError):
                raise WorkBuddyError("workbuddy_job_record_invalid") from None
            if item.get("status") not in {"dispatching", "running", "cleanup_pending"}:
                continue
            if self._owned_dispatch(item):
                continue
            if item.get('status') == 'dispatching':
                pid = item.get('pid') or item.get('dispatch_owner_pid')
                birth = item.get('process_identity') if item.get('pid') else item.get('dispatch_owner_identity')
                identity = process_identity(int(pid or 0))
                if not item.get('pid') and item.get('dispatch_phase') != 'claimed':
                    pending.append(item)  # Unknown spawn window needs explicit reconciliation.
                elif birth and (identity is None or identity != 'unknown' and identity != birth):
                    item.update(status='interrupted', cleanup_complete=True)
                    atomic_json(path, item)
                else:
                    pending.append(item)
                continue
            if item.get("task_id") in self.active:
                if item.get("status") == "cleanup_pending":
                    pending.append(item)
                continue
            identity = process_identity(int(item.get("pid") or 0))
            if identity is None or identity != item.get("process_identity") and identity != "unknown":
                item.update(status="interrupted", cleanup_complete=True)
            else:
                # Never terminate an unverified PID. Operator may recheck after exit.
                item.update(status="cleanup_pending", cleanup_complete=False)
                pending.append(item)
            atomic_json(path, item)
        return pending

    def mark_cleanup_pending(self, task_ids: set[str]) -> None:
        """Fence only a caller's owned jobs when cancellation cannot be drained."""
        for task_id in task_ids:
            item = self.active.get(task_id)
            if item is None:
                continue
            item.update(cancelled=True, failure="runtime_cleanup_incomplete")
            record = item["record"]
            record.update(status="cleanup_pending", cleanup_complete=False)
            atomic_json(item["record_path"], record)

    async def cancel(self, task_id: str) -> bool:
        item = self.active.get(task_id)
        if item is None:
            if task_id in self.queued:
                self.cancelled.add(task_id)
                return True  # No process has been dispatched; queued job is revoked.
            return task_id in self.closed
        item["cancelled"] = True
        from .runtime_contract import cleanup_deadline
        deadline = min(item.get("cleanup_deadline", float("inf")), cleanup_deadline())
        item["cleanup_deadline"] = deadline
        self.cleanup_deadlines[task_id] = deadline
        try:
            item["job"].terminate()
        except OSError:
            return False
        if os.name != "nt":
            try:
                os.killpg(item["process"].pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(item["process"].wait(), max(0.001, deadline - time.monotonic()))
        except TimeoutError:
            return False
        while not item["job"].empty():
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.02)
        item["job"].close()
        return True

    async def close(self):
        self.cancelled.update(self.queued - self.active.keys())
        await asyncio.gather(*(self.cancel(task_id) for task_id in list(self.active)), return_exceptions=True)

    async def run(self, *, task_id: str | None, snapshot: dict, operation: str,
                  payload: dict, seconds: float, mode: str = "bounded", probe: bool = False,
                  tool_handler: Any = None, emit: Any = None, deadline: float | None = None,
                  process_cwd: str | None = None) -> dict:
        from .runtime_validation import check_native_call, current_scope
        check_native_call(snapshot, operation, mode, tools=bool(tool_handler or payload.get("tools")))
        validation_scope = current_scope()
        task_id = task_id or uuid.uuid4().hex
        from .workbuddy_model_protocol import probe_scope
        memory_probe = probe_scope(operation, payload, tool_handler)
        if probe and (operation not in {"authentication", "model_check", "models"} or mode != "bounded"
                      or (payload.get("tools") or tool_handler) and not memory_probe):
            raise WorkBuddyError("workbuddy_probe_scope_invalid")
        if process_cwd is not None and (not probe or operation != "authentication"):
            raise WorkBuddyError("workbuddy_probe_scope_invalid")
        if not probe:
            from .runtime_policy import require_operation
            from .runtime_contract import RuntimeContractError
            try:
                require_operation(operation)
            except RuntimeContractError as error:
                raise WorkBuddyError(error.code) from None
        verification = self._verification.get()
        validating = bool(verification and verification[0] == digest(snapshot) and operation in verification[1])
        if mode not in {"bounded", "trusted_local"}:
            raise WorkBuddyError("workbuddy_windows_restricted_unverified")
        from .workbuddy_site import assert_binding, routing
        assert_binding(self.environment.state(), snapshot)
        async with self._capacity(probe, task_id, operation):  # Queue time is outside the execution budget.
            # Publish queue ownership before yielding to disk work so cancel()
            # can revoke a waiting task. Cleanup still precedes every dispatch.
            if await asyncio.to_thread(self.reconcile):
                raise WorkBuddyError("workbuddy_cleanup_pending")
            from .runtime_contract import current_deadline
            inherited = current_deadline()
            deadline = min(deadline, inherited) if deadline is not None and inherited is not None else deadline if deadline is not None else inherited
            deadline = deadline if deadline is not None else time.monotonic() + seconds
            payload = copy.deepcopy(payload)
            terminal_checks = None
            if payload.get("terminal_contract") is not None:
                from .runtime_terminal_contract import VERSION as terminal_version, TerminalChecks
                if payload["terminal_contract"] != terminal_version or operation not in {"free_query", "acceptance_baseline", "acceptance_free_query"}:
                    raise WorkBuddyError("workbuddy_terminal_protocol_invalid")
                terminal_checks = TerminalChecks(payload["output_schema"])
            async with self.dispatch_lock:
                # Cleanup can become uncertain while this request waits for
                # capacity. Recheck at dispatch, not just before entering queue.
                if await asyncio.to_thread(self.reconcile):
                    raise WorkBuddyError("workbuddy_cleanup_pending")
                if task_id in self.cancelled:
                    raise WorkBuddyError("workbuddy_cancelled")
                from .workbuddy_dispatch_admission import prepare
                service_site, release, qualification_binding, host_digest = await prepare(self,
                    snapshot=snapshot, operation=operation, mode=mode, probe=probe,
                    validating=validating, validation_scope=validation_scope, task_id=task_id, deadline=deadline)
                if task_id in self.active:
                    raise WorkBuddyError("workbuddy_task_busy")
                self.closed.discard(task_id)
                seconds = min(seconds, deadline - time.monotonic())
                if seconds <= 0:
                    raise WorkBuddyError("workbuddy_deadline_exceeded")
                if "timeout_ms" in payload:
                    payload["timeout_ms"] = max(1, int(seconds * 1000))
                binding = {"protocol": PROTOCOL, "task_id": task_id, "attempt_id": uuid.uuid4().hex,
                           "operation": operation, "environment_digest": release["environment_digest"],
                           "runtime_digest": digest(snapshot), "deadline": time.time() + seconds}
                request = {**binding, "snapshot": snapshot, "payload": payload, "mode": mode,
                           "cli_path": release["cli_path"], "service_site_config": routing(service_site)}
                if terminal_checks is not None:
                    request["terminal_contract"] = terminal_version
                if payload.get("output_schema") is not None:
                    from .workbuddy_diagnostics import NATIVE_DIAGNOSTICS
                    # Additive negotiation: older locked workers ignore this;
                    # newer workers never send checks to an older supervisor.
                    request["native_output_diagnostics"] = NATIVE_DIAGNOSTICS
                encoded = self._encode(request)
                self._dispatching[task_id] = binding
                # Credential-free allowlist, rather than inheriting SAP/.env/secrets.
                allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "APPDATA",
                           "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PATH"}
                env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
                env.update(PYTHONUTF8="1", CODEBUDDY_CODE_PATH=release["cli_path"])
                from .workbuddy_job_index import claim, spawning, owned
                job = WindowsJob()
                try:
                    claim(self.environment.root, binding, deadline=deadline)
                    spawning(self.environment.root, binding, deadline=deadline)
                    worker = Path(release["directory"]) / release["worker"]
                    if release["worker"] not in release["files"]:
                        raise WorkBuddyError("workbuddy_environment_invalid")
                    async with asyncio.timeout(max(0, deadline - time.monotonic())):
                        process = await asyncio.create_subprocess_exec(release["python_path"], "-I", "-B", str(worker),
                            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                            env=env, cwd=process_cwd, limit=FRAME_LIMIT + 1, start_new_session=os.name != "nt",
                            creationflags=0x08000000 if os.name == "nt" else 0)
                    job.attach(process.pid)  # Worker waits for request; SDK cannot spawn before attach.
                    owned(self.environment.root, binding, process.pid, process_identity(process.pid), deadline=deadline)
                except TimeoutError:
                    self._dispatching.pop(task_id, None)
                    job.close()
                    from .workbuddy_job_index import enabled
                    if enabled(self.environment.root) and 'process' not in locals():
                        atomic_json(self.environment.root / 'jobs' / (digest(task_id) + '.json'),
                                    {**binding, 'status': 'interrupted', 'cleanup_complete': True})
                    raise WorkBuddyError("workbuddy_deadline_exceeded") from None
                except BaseException:
                    self._dispatching.pop(task_id, None)
                    job.close()
                    if "process" in locals() and process.returncode is None:
                        process.kill()
                        await process.wait()
                    from .workbuddy_job_index import enabled
                    if enabled(self.environment.root) and ('process' not in locals() or process.returncode is not None):
                        atomic_json(self.environment.root / 'jobs' / (digest(task_id) + '.json'),
                                    {**binding, 'status': 'interrupted', 'cleanup_complete': True})
                    raise
                record = {**binding, **service_site, "pid": process.pid, "process_identity": process_identity(process.pid),
                          "status": "running", "cleanup_complete": False, "permission_mode": mode,
                          "validation_job": validating, "authentication_revision": snapshot.get("authentication_revision", 0),
                          "runtime_model": snapshot.get("model"), "management_digest": host_digest}
                if process_cwd is not None:
                    record['process_cwd_policy'] = 'isolated_authentication'
                if qualification_binding:
                    record["qualification_binding"] = qualification_binding
                if validation_scope:
                    from .workbuddy_validation import case_bindings
                    record["validation_case_binding"] = case_bindings(snapshot, release)[validation_scope["case"].id]
                    record["validation_parent_job"] = validation_scope["job_id"]
                from .runtime_stage import stage_metadata
                record["stage"] = stage_metadata()
                prompt = payload.get("prompt")
                if isinstance(prompt, str):
                    prompt_bytes = prompt.encode("utf-8")
                    record["request_metadata"] = {"input_length": len(prompt_bytes),
                        "input_sha256": hashlib.sha256(prompt_bytes).hexdigest(), "budget_ms": int(seconds * 1000)}
                    if isinstance(payload.get("output_schema"), dict):
                        record["request_metadata"]["schema_sha256"] = digest(payload["output_schema"])
                # Stored locally, not added to the locked worker wire protocol.
                if operation in OPERATIONS:
                    record.update(orchestration_version=ORCHESTRATION_VERSION,
                                  orchestration_digest=orchestration_digest(operation))
                record_path = self.environment.root / "jobs" / (digest(task_id) + ".json")
                if record_path.parent.resolve() != (self.environment.root / "jobs").resolve():
                    job.close()
                    raise WorkBuddyError("workbuddy_task_id_invalid")
                item = {"process": process, "job": job, "cancelled": False,
                        "record": record, "record_path": record_path}
                self.active[task_id] = item
                self._dispatching.pop(task_id, None)
            total = 0
            launched_at = time.time()
            from .workbuddy_auth_signals import StderrAuthenticationSignal
            from .workbuddy_failure_watch import OwnedFailureWatch
            auth_signal = asyncio.get_running_loop().create_future()
            stderr_signals = StderrAuthenticationSignal()
            failure_watch = OwnedFailureWatch(job, release['cli_path'], record.get('request_metadata', {}), launched_at,
                workspace=payload.get('cwd'))
            trace_signal = asyncio.create_task(failure_watch.wait()) if release.get('cli_version') == '2.141.0' else None
            async def drain_stderr():
                nonlocal total
                while chunk := await process.stderr.read(8192):
                    total += len(chunk)
                    if total > OUTPUT_LIMIT:
                        item["failure"] = "workbuddy_output_limit"
                        await self.cancel(task_id)
                        raise WorkBuddyError("workbuddy_output_limit")
                    # SDK logs may contain secrets; never persist raw stderr.
                    signal = stderr_signals.feed(chunk)
                    if signal and not auth_signal.done():
                        auth_signal.set_result(signal)
            stderr = asyncio.create_task(drain_stderr())
            status = "failed"
            primary = None
            protocol_progress = {"protocol_phase": "job_record_write"}

            def failure_context():
                detail = {**record.get("last_progress", {}), **record.get("stage", {}), **protocol_progress}
                if record.get("last_output_check"):
                    detail["last_output_check"] = record["last_output_check"]
                    detail["last_schema_valid"] = record["last_output_check"]["schema_valid"]
                for key in ("terminal_contract", "terminal_convergence", "last_rejected_candidate"):
                    if key in record:
                        detail[key] = record[key]
                return detail

            async def read_owned():
                reader = asyncio.create_task(self._read(process.stdout))
                try:
                    pending = {reader, auth_signal}
                    if trace_signal is not None:
                        pending.add(trace_signal)
                    done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    signal = auth_signal.result() if auth_signal in done else trace_signal.result() if trace_signal in done else None
                    if signal:
                        detail = {**failure_context(), **signal}
                        record.update(failure_diagnostic=detail, failure_observed_at=time.time())
                        if emit:
                            emit('workbuddy_native_failure', {'provider_id': 'workbuddy', **detail})
                        error = WorkBuddyError(signal['failure_code'])
                        error.detail = detail
                        raise error
                    return reader.result()
                finally:
                    if not reader.done():
                        reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)

            try:
                async with asyncio.timeout(max(0, deadline - time.monotonic())):
                    # The process is already owned. Initial record I/O must be
                    # inside the same failure/cleanup boundary as protocol I/O.
                    atomic_json(record_path, record, deadline=deadline)
                    protocol_progress["protocol_phase"] = "request_write"
                    process.stdin.write(encoded)
                    protocol_progress["protocol_phase"] = "request_drain"
                    await process.stdin.drain()
                    protocol_progress["protocol_phase"] = "handshake_read"
                    handshake = await read_owned()
                    expected_hello = {"kind": "hello", **binding}
                    if terminal_checks is not None:
                        expected_hello["terminal_contract"] = terminal_version
                        record["terminal_contract"] = terminal_version
                    if handshake != expected_hello:
                        raise WorkBuddyError("workbuddy_handshake_mismatch")
                    while True:
                        protocol_progress["protocol_phase"] = "message_read"
                        message = await read_owned()
                        total += len(self._encode(message))
                        if total > OUTPUT_LIMIT:
                            raise WorkBuddyError("workbuddy_output_limit")
                        if message.get("task_id") != task_id or message.get("attempt_id") != binding["attempt_id"]:
                            raise WorkBuddyError("workbuddy_attempt_mismatch")
                        if item["cancelled"]:
                            raise WorkBuddyError("workbuddy_cancelled")
                        kind = message.get("kind")
                        if kind in {"tool_call", "schema_check", "event", "result", "error"}:
                            protocol_progress["last_frame_kind"] = kind
                        if kind == "tool_call":
                            protocol_progress["protocol_phase"] = "tool_handler"
                            if tool_handler is None:
                                value = {"ok": False, "code": "tool_not_enabled_for_task"}
                            else:
                                value = await tool_handler(str(message.get("tool") or ""), message.get("arguments") or {})
                            response = {"kind": "tool_result", "call_id": message["call_id"], "result": value}
                            protocol_progress["protocol_phase"] = "tool_reply_write"
                            process.stdin.write(self._encode(response))
                            protocol_progress["protocol_phase"] = "tool_reply_drain"
                            await process.stdin.drain()
                            if value.get("_workbuddy_stop_after_response"):
                                raise WorkBuddyError(value.get("_runtime_report_stop_code") or "acceptance_report_validation_failed")
                        elif kind == "schema_check":
                            protocol_progress["protocol_phase"] = "schema_validation"
                            from .workbuddy_diagnostics import native_output_diagnostic, NATIVE_DIAGNOSTICS
                            from jsonschema.exceptions import SchemaError
                            schema = payload.get("output_schema")
                            phase = message.get("phase")
                            if (request.get("native_output_diagnostics") != NATIVE_DIAGNOSTICS
                                    or not isinstance(schema, dict) or not isinstance(phase, str)
                                    or phase not in {"native_candidate", "native_terminal"}
                                    or not isinstance(message.get("call_id"), str)
                                    or not re.fullmatch(r"[a-f0-9]{32}", message["call_id"])):
                                raise WorkBuddyError("workbuddy_protocol_invalid")
                            try:
                                diagnosis = native_output_diagnostic(message.get("value"), schema,
                                    operation=operation, phase=phase)
                            except SchemaError:
                                raise WorkBuddyError("workbuddy_output_schema_invalid") from None
                            diagnosis["schema_sha256"] = digest(schema)
                            terminal_reply = {}
                            if terminal_checks is not None:
                                try:
                                    terminal_reply = terminal_checks.check(message.get("value"),
                                        submission_id=message.get("submission_id"),
                                        origin=message.get("origin"), phase=phase)
                                except ValueError:
                                    raise WorkBuddyError("workbuddy_terminal_protocol_invalid") from None
                                record["terminal_convergence"] = terminal_reply["convergence"]
                                if phase == "native_candidate" and not diagnosis["schema_valid"]:
                                    record["last_rejected_candidate"] = diagnosis
                            # Keep the authoritative last check even if the SDK
                            # never produces a terminal and must be terminated.
                            record["last_output_check"] = diagnosis
                            protocol_progress.update(schema_check_phase=phase,
                                schema_check_count=protocol_progress.get("schema_check_count", 0) + 1,
                                schema_reply_state="prepared")
                            record["protocol_progress"] = dict(protocol_progress)
                            atomic_json(record_path, record, deadline=deadline)
                            if emit:
                                protocol_progress["protocol_phase"] = "schema_check_event"
                                emit("workbuddy_native_output_checked", diagnosis)
                            protocol_progress["protocol_phase"] = "schema_reply_write"
                            process.stdin.write(self._encode({"kind": "schema_check_result",
                                "call_id": message["call_id"], "task_id": task_id,
                                "attempt_id": binding["attempt_id"], "schema_sha256": digest(schema),
                                "schema_valid": diagnosis["schema_valid"],
                                  "validation_issues": diagnosis["validation_issues"], **terminal_reply}))
                            protocol_progress.update(protocol_phase="schema_reply_drain", schema_reply_state="written")
                            await process.stdin.drain()
                            # Drained means the host transport accepted the frame,
                            # NOT that the SDK received it or produced a terminal.
                            protocol_progress["schema_reply_state"] = "drained"
                            record["protocol_progress"] = dict(protocol_progress)
                            atomic_json(record_path, record, deadline=deadline)
                            if terminal_reply.get("stop"):
                                raise WorkBuddyError("workbuddy_terminal_correction_stalled")
                        elif kind == "event":
                            protocol_progress["protocol_phase"] = "event_record_write"
                            from .workbuddy_diagnostics import safe_progress
                            event_kind = str(message.get("event") or "workbuddy_progress")
                            safe = safe_progress(event_kind, message.get("data") or {})
                            if safe:
                                record["last_progress"] = {**record.get("last_progress", {}), **safe}
                                record["last_progress_at"] = time.time()
                            atomic_json(record_path, record, deadline=deadline)
                            if emit:
                                protocol_progress["protocol_phase"] = "event_dispatch"
                                # Preserve internal native event delivery. Persistence
                                # and public projections above/below are allowlisted.
                                emit(event_kind, message.get("data") or {})
                        elif kind == "result":
                            protocol_progress["protocol_phase"] = "result_validation"
                            if not isinstance(message.get("result"), dict):
                                raise WorkBuddyError("workbuddy_result_invalid")
                            if time.monotonic() >= deadline:
                                raise TimeoutError
                            answer = message["result"]
                            if terminal_checks is not None and (answer.get("output_format") != "native_json_schema"
                                    or not terminal_checks.accepts_native_result(answer.get("text"))):
                                raise WorkBuddyError("workbuddy_structured_output_missing")
                            reported_model = answer.get("actual_model")
                            if not isinstance(reported_model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", reported_model):
                                reported_model = None
                            record["result_metadata"] = {
                                "structured_terminal": answer.get("output_format") == "native_json_schema",
                                "actual_model": reported_model,
                                "identity_source": answer.get("model_identity_source") if answer.get("model_identity_source") in {"assistant_message", "system_metadata"} else None,
                                "output_format": "native_json_schema" if answer.get("output_format") == "native_json_schema" else None,
                                "output_sha256": digest(answer.get("text")),
                                "authenticated": answer.get("authenticated") is True}
                            if operation in {"acceptance_baseline", "acceptance_free_query"}:
                                actual = message["result"].get("actual_model")
                                from .workbuddy_identity import checked_identity, is_concrete_model
                                if (not checked_identity(snapshot, flag="model_identity_known")
                                        or not is_concrete_model(actual)
                                        or message["result"].get("model_identity_source") != "assistant_message"):
                                    raise WorkBuddyError("workbuddy_model_identity_unknown")
                                if actual != snapshot.get("actual_model"):
                                    raise WorkBuddyError("workbuddy_model_identity_changed")
                            assert_binding(self.environment.state(), snapshot)
                            status = "completed"
                            return message["result"]
                        elif kind == "error":
                            # Only bounded error codes cross the worker boundary.
                            code = str(message.get("code") or "workbuddy_execution_failed")
                            if not code.replace("_", "").isalnum() or len(code) > 120:
                                code = "workbuddy_execution_failed"
                            from .workbuddy_diagnostics import safe_native_failure
                            detail = {**record.get("last_progress", {}), **safe_native_failure(message.get("diagnostic"))}
                            if record.get("last_output_check"):
                                detail["last_output_check"] = record["last_output_check"]
                            detail["failure_code"] = code
                            record["failure_diagnostic"] = detail
                            record["failure_observed_at"] = time.time()
                            if emit:
                                protocol_progress["protocol_phase"] = "failure_event"
                                emit("workbuddy_native_failure", {"provider_id": "workbuddy", **detail})
                            error = WorkBuddyError(code)
                            error.detail = detail
                            raise error
                        else:
                            raise WorkBuddyError("workbuddy_protocol_invalid")
            except TimeoutError:
                status = "timed_out"
                record["failure_code"] = "workbuddy_deadline_exceeded"
                primary = WorkBuddyError("workbuddy_deadline_exceeded")
                primary.detail = {**failure_context(),
                    "failure_code": primary.code, "primary_failure_code": primary.code}
                record["failure_diagnostic"] = primary.detail
                raise primary from None
            except WorkBuddyError as error:
                record["failure_code"] = item.get("failure") or error.code
                error.detail = {**failure_context(), **(getattr(error, "detail", None) or {}),
                                "failure_code": record["failure_code"]}
                record["failure_diagnostic"] = error.detail
                record.setdefault("failure_observed_at", time.time())
                primary = error
                if record['failure_code'] == 'workbuddy_authentication_required' and callable(self.authentication_failed):
                    try:
                        await asyncio.to_thread(self.authentication_failed, snapshot, error.detail, started_at=launched_at)
                    except Exception:
                        error.detail['authentication_sync_failed'] = True
                if item.get("failure"):
                    primary = WorkBuddyError(item["failure"])
                    primary.detail = error.detail
                    raise primary from None
                raise
            except asyncio.CancelledError as error:
                status = "cancelled"
                error.detail = failure_context()
                record["failure_diagnostic"] = error.detail
                primary = error
                raise
            except Exception as error:
                from .workbuddy_diagnostics import host_failure
                # Protocol I/O and event callbacks can raise ordinary exceptions.
                # Never lose their stage or export OS/SDK text via an untyped error.
                detail = {**failure_context(), **host_failure(error, protocol_progress["protocol_phase"])}
                # If reporting a native error failed, retain that primary error;
                # event delivery is a separate failure, not a new upstream cause.
                if record.get("failure_diagnostic", {}).get("failure_code"):
                    detail = {**failure_context(), **record["failure_diagnostic"],
                              "failure_event_delivery_failed": True,
                              "delivery_error_class": detail["error_class"]}
                record.update(failure_code=detail["failure_code"], failure_diagnostic=detail,
                              failure_observed_at=record.get("failure_observed_at", time.time()))
                primary = WorkBuddyError(detail["failure_code"])
                primary.detail = detail
                raise primary from None
            except BaseException as error:
                # Do not convert process-level exit/interrupt signals into success.
                primary = error
                raise
            finally:
                from .runtime_contract import cleanup_deadline as inherited_cleanup_deadline
                deadline = min(item.get("cleanup_deadline", float("inf")), inherited_cleanup_deadline())
                item["cleanup_deadline"] = deadline
                self.cleanup_deadlines[task_id] = deadline
                record["cleanup_started_at"] = time.time()
                async def finish_owned_job():
                    complete = False
                    try:
                        complete = await self.cancel(task_id)
                    except Exception:
                        # Owned cleanup failure must not replace the primary SDK
                        # error with a native exception or sensitive OS text.
                        complete = False
                    finally:
                        stderr.cancel()
                        if trace_signal is not None:
                            trace_signal.cancel()
                        auth_signal.cancel()
                        await asyncio.gather(stderr, *([trace_signal] if trace_signal is not None else []), return_exceptions=True)
                        self.active.pop(task_id, None)
                        if complete:
                            self.closed.add(task_id)
                        record.update(status=status if complete else "cleanup_pending", cleanup_complete=complete)
                        record["protocol_progress"] = dict(protocol_progress)
                        record["cleanup_completed_at"] = time.time()
                        if not complete:
                            record["cleanup_failure_code"] = "runtime_cleanup_incomplete"
                        record_persisted = True
                        try:
                            atomic_json(record_path, record, deadline=deadline)
                        except (OSError, WorkBuddyError) as storage_error:
                            from .workbuddy_diagnostics import host_failure
                            failure = host_failure(storage_error, 'cleanup_record_write') if isinstance(storage_error, OSError) else {
                                'failure_code':storage_error.code, 'error_class':'WorkBuddyError',
                                'failure_source':'host_journal', 'protocol_phase':'cleanup_record_write'}
                            detail = {**failure_context(), **failure,
                                      "record_persistence_failed": True}
                            if primary is not None:
                                # The original failure remains authoritative even
                                # when its durable cleanup update cannot be saved.
                                primary.detail = {**detail, **(getattr(primary, "detail", {}) or {}),
                                                  'cleanup_storage_failure_code':failure['failure_code'],
                                                  "record_persistence_failed": True}
                                record_persisted = False
                            else:
                                # A received terminal without a durable job record
                                # cannot return successfully or obtain qualification.
                                error = WorkBuddyError(detail["failure_code"])
                                error.detail = detail
                                raise error from None
                        if record_persisted and validating and qualification_binding and record["status"] != "completed" and validation_scope is None and not self.environment.state().get("qualification_protocol"):
                            from .workbuddy_qualification import add_record
                            try:
                                self.environment.mutate(lambda s: add_record(s, {"kind": "failure",
                                    "binding": qualification_binding, "environment_digest": release["environment_digest"],
                                    "job_id": task_id, "job_digest": digest(record),
                                    "failure_code": "workbuddy_qualification_execution_failed"}))
                            except (WorkBuddyError, OSError):
                                # Preserve primary failure. The owned job remains
                                # the blocking evidence until explicitly reconciled.
                                record["qualification_recording_error"] = "workbuddy_qualification_busy"
                                try:
                                    atomic_json(record_path, record, deadline=deadline)
                                except OSError:
                                    if primary is not None:
                                        primary.detail = {**(getattr(primary, "detail", {}) or {}),
                                                          "record_persistence_failed": True}
                    return complete
                cleanup = asyncio.create_task(finish_owned_job())
                try:
                    done, _ = await asyncio.wait({cleanup}, timeout=max(0, deadline - time.monotonic()))
                except asyncio.CancelledError:
                    # asyncio.wait leaves owned cleanup running on cancellation.
                    # Persist its outcome within the original cutoff; never
                    # replace an existing failure with a cleanup exception.
                    status = "cancelled"
                    try:
                        done, _ = await asyncio.wait({cleanup}, timeout=max(0, deadline - time.monotonic()))
                    except asyncio.CancelledError:
                        self.mark_cleanup_pending({task_id})
                        cleanup.add_done_callback(_consume_cleanup)
                        if primary is None:
                            primary = asyncio.CancelledError()
                        primary.detail = dict(getattr(primary, "detail", {}) or {},
                                              cleanup_failure_code="runtime_cleanup_incomplete")
                        raise primary
                    if not done:
                        self.mark_cleanup_pending({task_id})
                        cleanup.add_done_callback(_consume_cleanup)
                        if primary is None:
                            primary = asyncio.CancelledError()
                        primary.detail = dict(getattr(primary, "detail", {}) or {},
                                              cleanup_failure_code="runtime_cleanup_incomplete")
                        raise primary
                    # Persist the owned cleanup before propagating cancellation.
                    if primary is None:
                        primary = asyncio.CancelledError()
                complete = bool(done and cleanup.result())
                if not complete:
                    self.mark_cleanup_pending({task_id})
                    cleanup.add_done_callback(_consume_cleanup)
                    detail = {**getattr(primary, "detail", {}),
                              "primary_failure_code": record.get("failure_code"),
                              "cleanup_failure_code": "runtime_cleanup_incomplete"}
                    if primary is not None:
                        primary.detail = detail
                    else:
                        error = WorkBuddyError("runtime_cleanup_incomplete")
                        error.detail = detail
                        raise error
                if isinstance(primary, asyncio.CancelledError):
                    raise primary

    @staticmethod
    def _encode(value: dict) -> bytes:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        if len(raw) > FRAME_LIMIT:
            raise WorkBuddyError("workbuddy_message_limit")
        return raw

    @staticmethod
    async def _read(stream: Any) -> dict:
        try:
            line = await stream.readline()
        except ValueError:
            raise WorkBuddyError("workbuddy_message_limit") from None
        try:
            if not line:
                raise WorkBuddyError("workbuddy_worker_exited")
            if len(line) > FRAME_LIMIT:
                raise WorkBuddyError("workbuddy_message_limit")
            value = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise WorkBuddyError("workbuddy_protocol_invalid") from exc
        if not isinstance(value, dict):
            raise WorkBuddyError("workbuddy_protocol_invalid")
        return value


def _consume_cleanup(task):
    try:
        task.result()
    except BaseException:
        pass
