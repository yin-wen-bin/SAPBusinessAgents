"""Bounded SDK-free process proxy. Each worker belongs to one task/attempt."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import time
import uuid
from pathlib import Path
from typing import Any
from contextvars import ContextVar
from contextlib import contextmanager, asynccontextmanager, nullcontext

from .workbuddy_environment import PROTOCOL, WorkBuddyEnvironment, WorkBuddyError, atomic_json, digest
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
        self.active: dict[str, dict] = {}
        self.queued: set[str] = set()
        self.cancelled: set[str] = set()
        self.closed: set[str] = set()
        self.cleanup_deadlines: dict[str, float] = {}
        self._verification = ContextVar("workbuddy_internal_verification", default=None)
        self._reserved = ContextVar("workbuddy_execution_reservation", default=False)

    @property
    def reserved(self):
        return self._reserved.get()

    @asynccontextmanager
    async def reserve(self):
        """Business entry points acquire capacity before starting their clocks."""
        if self.reserved:
            yield
            return
        async with self.execution_lock:
            token = self._reserved.set(True)
            try:
                yield
            finally:
                self._reserved.reset(token)

    @contextmanager
    def verification(self, snapshot: dict, operations: set[str]):
        """Internal staging only, never exposed by public Runtime/business routes."""
        from .workbuddy_environment import OPERATIONS
        if not operations or not operations.issubset(OPERATIONS) or snapshot.get("provider_id") != "workbuddy":
            raise WorkBuddyError("workbuddy_validation_scope_invalid")
        self.environment.release(snapshot.get("environment_digest"))
        token = self._verification.set((digest(snapshot), frozenset(operations)))
        try:
            yield
        finally:
            self._verification.reset(token)

    def check_operation(self, snapshot: dict, operation: str, mode: str = "bounded") -> dict:
        verification = self._verification.get()
        if (verification and verification[0] == digest(snapshot) and operation in verification[1]
                and mode in {"bounded", "trusted_local"}):
            return self.environment.release(snapshot.get("environment_digest"))
        return self.environment.assert_operation(snapshot, operation, mode=mode)

    @asynccontextmanager
    async def _capacity(self, probe: bool, task_id: str):
        if task_id in self.queued:
            raise WorkBuddyError("workbuddy_task_busy")
        self.queued.add(task_id)
        try:
            if probe:
                async with self.probe_lock:
                    yield
            else:
                async with nullcontext() if self.reserved else self.execution_lock:
                    # Reservations propagate to business child tasks. They may not
                    # create a second concurrent worker even if contexts are shared.
                    async with self.worker_lock:
                        yield
        finally:
            self.queued.discard(task_id)

    def pending(self) -> list[dict]:
        result = []
        for path in (self.environment.root / "jobs").glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise WorkBuddyError("workbuddy_job_record_invalid") from None
            if item.get("status") == "cleanup_pending":
                result.append(item)
        return result

    def reconcile(self) -> list[dict]:
        """Kill-on-close closes orphan jobs on API death; uncertainty stays blocked."""
        pending = []
        for path in (self.environment.root / "jobs").glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                raise WorkBuddyError("workbuddy_job_record_invalid") from None
            if item.get("status") not in {"running", "cleanup_pending"}:
                continue
            if item.get("task_id") in self.active:
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

    async def cancel(self, task_id: str) -> bool:
        item = self.active.get(task_id)
        if item is None:
            if task_id in self.queued:
                self.cancelled.add(task_id)
                return True  # No process has been dispatched; queued job is revoked.
            return task_id in self.closed
        item["cancelled"] = True
        deadline = item.setdefault("cleanup_deadline", time.monotonic() + 10)
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
                  tool_handler: Any = None, emit: Any = None) -> dict:
        task_id = task_id or uuid.uuid4().hex
        if probe and (operation not in {"authentication", "model_check", "models"} or mode != "bounded" or payload.get("tools")):
            raise WorkBuddyError("workbuddy_probe_scope_invalid")
        verification = self._verification.get()
        validating = bool(verification and verification[0] == digest(snapshot) and operation in verification[1])
        if mode not in {"bounded", "trusted_local"}:
            raise WorkBuddyError("workbuddy_windows_restricted_unverified")
        if self.reconcile():
            raise WorkBuddyError("workbuddy_cleanup_pending")
        async with self._capacity(probe, task_id):  # Queue time is outside the execution budget.
            async with self.dispatch_lock:
                if task_id in self.cancelled:
                    raise WorkBuddyError("workbuddy_cancelled")
                with self.environment.maintenance():
                    release = (self.environment.release(snapshot.get("environment_digest")) if probe
                               else self.check_operation(snapshot, operation, mode))
                if task_id in self.active:
                    raise WorkBuddyError("workbuddy_task_busy")
                self.closed.discard(task_id)
                binding = {"protocol": PROTOCOL, "task_id": task_id, "attempt_id": uuid.uuid4().hex,
                           "operation": operation, "environment_digest": release["environment_digest"],
                           "runtime_digest": digest(snapshot), "deadline": time.time() + seconds}
                request = {**binding, "snapshot": snapshot, "payload": payload, "mode": mode,
                           "cli_path": release["cli_path"]}
                encoded = self._encode(request)
                # Credential-free allowlist, rather than inheriting SAP/.env/secrets.
                allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "APPDATA",
                           "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PATH"}
                env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
                env.update(PYTHONUTF8="1", CODEBUDDY_CODE_PATH=release["cli_path"])
                job = WindowsJob()
                try:
                    worker = Path(release["directory"]) / release["worker"]
                    if release["worker"] not in release["files"]:
                        raise WorkBuddyError("workbuddy_environment_invalid")
                    process = await asyncio.create_subprocess_exec(release["python_path"], "-I", "-B", str(worker),
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                        env=env, limit=FRAME_LIMIT + 1, start_new_session=os.name != "nt",
                        creationflags=0x08000000 if os.name == "nt" else 0)
                    job.attach(process.pid)  # Worker waits for request; SDK cannot spawn before attach.
                except BaseException:
                    job.close()
                    if "process" in locals() and process.returncode is None:
                        process.kill()
                        await process.wait()
                    raise
                record = {**binding, "pid": process.pid, "process_identity": process_identity(process.pid),
                          "status": "running", "cleanup_complete": False, "permission_mode": mode,
                          "validation_job": validating}
                # Stored locally, not added to the locked worker wire protocol.
                if operation in OPERATIONS:
                    record.update(orchestration_version=ORCHESTRATION_VERSION,
                                  orchestration_digest=orchestration_digest(operation))
                record_path = self.environment.root / "jobs" / (digest(task_id) + ".json")
                if record_path.parent.resolve() != (self.environment.root / "jobs").resolve():
                    job.close()
                    raise WorkBuddyError("workbuddy_task_id_invalid")
                atomic_json(record_path, record)
                item = {"process": process, "job": job, "cancelled": False}
                self.active[task_id] = item
            total = 0
            async def drain_stderr():
                nonlocal total
                while chunk := await process.stderr.read(8192):
                    total += len(chunk)
                    if total > OUTPUT_LIMIT:
                        item["failure"] = "workbuddy_output_limit"
                        await self.cancel(task_id)
                        raise WorkBuddyError("workbuddy_output_limit")
                    # SDK logs may contain secrets; never persist raw stderr.
            stderr = asyncio.create_task(drain_stderr())
            status = "failed"
            try:
                process.stdin.write(encoded)
                await process.stdin.drain()
                async with asyncio.timeout(seconds):
                    handshake = await self._read(process.stdout)
                    if handshake != {"kind": "hello", **binding}:
                        raise WorkBuddyError("workbuddy_handshake_mismatch")
                    while True:
                        message = await self._read(process.stdout)
                        total += len(self._encode(message))
                        if total > OUTPUT_LIMIT:
                            raise WorkBuddyError("workbuddy_output_limit")
                        if message.get("task_id") != task_id or message.get("attempt_id") != binding["attempt_id"]:
                            raise WorkBuddyError("workbuddy_attempt_mismatch")
                        if item["cancelled"]:
                            raise WorkBuddyError("workbuddy_cancelled")
                        kind = message.get("kind")
                        if kind == "tool_call":
                            if tool_handler is None:
                                value = {"ok": False, "code": "tool_not_enabled_for_task"}
                            else:
                                value = await tool_handler(str(message.get("tool") or ""), message.get("arguments") or {})
                            response = {"kind": "tool_result", "call_id": message["call_id"], "result": value}
                            process.stdin.write(self._encode(response))
                            await process.stdin.drain()
                            if value.get("_workbuddy_stop_after_response"):
                                raise WorkBuddyError("acceptance_report_validation_failed")
                        elif kind == "event":
                            if emit:
                                emit(str(message.get("event") or "workbuddy_progress"), message.get("data") or {})
                        elif kind == "result":
                            if not isinstance(message.get("result"), dict):
                                raise WorkBuddyError("workbuddy_result_invalid")
                            if operation in {"acceptance_baseline", "acceptance_free_query"}:
                                actual = message["result"].get("actual_model")
                                from .workbuddy_identity import checked_identity, is_concrete_model
                                if (not checked_identity(snapshot, flag="model_identity_known")
                                        or not is_concrete_model(actual)
                                        or message["result"].get("model_identity_source") != "assistant_message"):
                                    raise WorkBuddyError("workbuddy_model_identity_unknown")
                                if actual != snapshot.get("actual_model"):
                                    raise WorkBuddyError("workbuddy_model_identity_changed")
                            status = "completed"
                            return message["result"]
                        elif kind == "error":
                            # Only bounded error codes cross the worker boundary.
                            code = str(message.get("code") or "workbuddy_execution_failed")
                            if not code.replace("_", "").isalnum() or len(code) > 120:
                                code = "workbuddy_execution_failed"
                            raise WorkBuddyError(code)
                        else:
                            raise WorkBuddyError("workbuddy_protocol_invalid")
            except TimeoutError:
                status = "timed_out"
                raise WorkBuddyError("workbuddy_deadline_exceeded") from None
            except WorkBuddyError:
                if item.get("failure"):
                    raise WorkBuddyError(item["failure"]) from None
                raise
            except asyncio.CancelledError:
                status = "cancelled"
                raise
            finally:
                complete = await asyncio.shield(self.cancel(task_id))
                stderr.cancel()
                await asyncio.gather(stderr, return_exceptions=True)
                self.active.pop(task_id, None)
                if complete:
                    self.closed.add(task_id)
                record.update(status=status if complete else "cleanup_pending", cleanup_complete=complete)
                atomic_json(record_path, record)
                if not complete:
                    raise WorkBuddyError("runtime_cleanup_incomplete")

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
