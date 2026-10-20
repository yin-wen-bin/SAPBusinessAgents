"""One idempotent, memory-tool compatibility job; never business evaluation."""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
import uuid
from pathlib import Path
from datetime import datetime, timezone

from .workbuddy_environment import WorkBuddyError, digest
from .workbuddy_identity import is_concrete_model
from .workbuddy_record_io import read_record
from . import workbuddy_site as site
from . import workbuddy_model_protocol as protocol
from .workbuddy_integration import protocol_digest

SECONDS = protocol.SECONDS


def now():
    return datetime.now(timezone.utc).isoformat()


async def run(manager, model_id, effort=None, request_id=None):
    if effort is not None:
        raise WorkBuddyError("workbuddy_reasoning_effort_sdk_default")
    if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", model_id):
        raise WorkBuddyError("runtime_model_id_invalid")
    request_id = request_id or uuid.uuid4().hex  # Old clients remain compatible.
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
        raise WorkBuddyError("runtime_model_check_request_invalid")
    fingerprint = digest({"model_id": model_id, "reasoning_effort": effort})

    def claim():
        with manager.environment.maintenance():
            state = manager.environment.state()
            prior = state.get("model_check_requests", {}).get(request_id)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise WorkBuddyError("runtime_model_check_request_conflict")
                current = (prior.get("environment_digest") == state.get("environment_digest")
                    and prior.get("authentication_revision") == state.get("authentication_revision", 0)
                    and site.matches(prior, site.configuration(state))
                    and prior.get("model_protocol_digest") == protocol_digest())
                return state, None, {**prior, "binding_current": current}
            release = manager.environment.release()
            current_site = site.configuration(state, required=True)
            if (state.get("authenticated") is not True
                    or state.get("authentication_environment") != release["environment_digest"]
                    or not site.matches(state.get("authentication_site") or {}, current_site)):
                raise WorkBuddyError("workbuddy_authentication_required")
            # A bounded memory-only inspection may bootstrap integration
            # evidence. It never enables business operations or the Runtime.
            # Requiring SDK certification here would create a circular gate.
            if manager.supervisor.pending():
                raise WorkBuddyError("workbuddy_cleanup_pending")
            record = {"fingerprint": fingerprint, "request_id": request_id, "model_id": model_id,
                "job_id": "model_check_" + digest(request_id), "status": "running", "created_at": now(),
                "environment_digest": release["environment_digest"],
                "authentication_revision": state.get("authentication_revision", 0),
                "model_protocol_digest": protocol_digest(), **current_site}
            manager.environment.mutate(lambda value: value.setdefault("model_check_requests", {}).__setitem__(request_id, record))
            return state, release, record

    state, release, request = await asyncio.to_thread(claim)
    if release is None:
        # Unknown/interrupted responses never authorize a second dispatch.
        return {"provider_id": "workbuddy", "request": request,
                "check": request.get("result") if request.get("binding_current") else None}
    challenge = protocol.Challenge()
    result, diagnostic, workspace, cleanup_warning = {}, None, None, None
    probe_diagnostics = None
    cancelled = False
    try:
        workspace = tempfile.TemporaryDirectory(prefix="sapba-workbuddy-model-")
        result = await manager.supervisor.run(task_id=request["job_id"], snapshot={
            "environment_digest": release["environment_digest"], "model": model_id,
            "authentication_revision": request["authentication_revision"], **site.binding(request)},
            operation="model_check", payload=challenge.payload(workspace.name),
            seconds=SECONDS, probe=True, tool_handler=challenge.handle)
        from .runtime_diagnostics import checked_output
        value = checked_output(result["text"], protocol.SCHEMA, operation="model_check", output_format=result.get("output_format"))
        probe_diagnostics = challenge.diagnostics(value)
        if result.get("output_format") != "native_json_schema":
            raise WorkBuddyError("workbuddy_model_probe_terminal_invalid")
        proof = challenge.verify(value)
        job = read_record(manager.environment.root / "jobs" / (digest(request["job_id"]) + ".json"))
        if job.get("status") != "completed" or job.get("cleanup_complete") is not True:
            raise WorkBuddyError("runtime_cleanup_incomplete")
        compatible, code = True, "compatible"
    except asyncio.CancelledError:
        cancelled, compatible, code = True, False, "workbuddy_cancelled"
    except Exception as error:
        compatible, code = False, getattr(error, "code", "workbuddy_model_probe_failed")
        from .runtime_diagnostics import stage_failure
        diagnostic = stage_failure(error, schema=protocol.SCHEMA, operation="model_check", phase="model_check", budget_seconds=SECONDS)
    finally:
        if workspace is not None:
            try:
                workspace.cleanup()
            except OSError:
                cleanup_warning = {"code": "workbuddy_model_workspace_cleanup_failed",
                                   "workspace_id": Path(workspace.name).name}

    actual = result.get("actual_model") if compatible else None
    identity_source = result.get("model_identity_source") if compatible else None
    check = {"provider_id": "workbuddy", "model_id": model_id, "requested_model": model_id,
        "actual_model": actual, "model_identity_source": identity_source,
        "identity_known": bool(is_concrete_model(actual) and identity_source == "assistant_message"),
        "compatible": compatible, "status": code, "error": None if compatible else {"code": code},
        "probe_diagnostics": probe_diagnostics if probe_diagnostics is not None else challenge.diagnostics(),
        "probe_contract": protocol.CONTRACT, "schema_sha256": digest(protocol.SCHEMA),
        "model_protocol_digest": request["model_protocol_digest"], "probe_timeout_seconds": SECONDS,
        "source_job_id": request["job_id"], "reasoning_effort": None, "reasoning_effort_source": "sdk_default",
        "sdk_version": release["sdk_version"], "cli_version": release["cli_version"], "source": "user_verified",
        "checked_at": now(), "environment_digest": release["environment_digest"],
        "authentication_revision": request["authentication_revision"], **site.binding(request)}
    if compatible:
        check.update(proof=proof, source_job_digest=digest(job), output_sha256=digest(result["text"]))
    if diagnostic:
        check["diagnostics"] = diagnostic
    if cleanup_warning:
        check["warnings"] = [cleanup_warning]
    check["check_digest"] = digest(check)

    def finish():
        with manager.environment.maintenance():
            current = manager.environment.state()
            try:
                site.assert_binding(current, request)
                if (current.get("authentication_revision", 0) != request["authentication_revision"]
                        or current.get("environment_digest") != request["environment_digest"]
                        or protocol_digest() != request["model_protocol_digest"]):
                    raise WorkBuddyError("workbuddy_model_check_binding_changed")
            except WorkBuddyError:
                # Keep the attempt trace, never write late output into models.
                manager.environment.mutate(lambda value: value["model_check_requests"].__setitem__(request_id,
                    {**request, "status": "binding_changed", "finished_at": now()}))
                raise
            manager.environment.release(request["environment_digest"])
            def save(value):
                previous = value.setdefault("models", {}).get(model_id)
                if previous:
                    value.setdefault("model_check_history", []).append(previous)
                value["models"][model_id] = check
                value["model_check_requests"][request_id] = {**request, "status": "cancelled" if cancelled else "completed",
                                                           "result": check, "finished_at": now()}
            manager.environment.mutate(save)
    await asyncio.to_thread(finish)
    if cancelled:
        raise asyncio.CancelledError
    return {"provider_id": "workbuddy", "request": {**request, "status": "completed"}, "check": check,
            "item": next(item for item in manager.models(_checked_model=model_id)["items"] if item["model_id"] == model_id)}
