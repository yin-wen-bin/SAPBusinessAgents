"""SDK-independent validation cases, authorization and deterministic planning.

This registry describes verification, not production permissions. Dependencies
expand the plan; they never expand the caller's authorization.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


VERSION = "sapba.runtime-validation/1"


class ValidationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ValidationCase:
    id: str
    operations: tuple[str, ...]
    mode: str = "bounded"
    version: str = "1"
    layer: str = "business"
    requires: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    resources: tuple[str, ...] = ("provider_worker",)
    assertions: tuple[str, ...] = ("structured_terminal", "business_contract", "saved_result", "cleanup_complete", "model_identity")
    diagnostic: bool = False
    sap: bool = False
    fixture: str = ""

    @property
    def digest(self):
        # Per-case identity. Adding an unrelated case cannot invalidate this one.
        return digest(asdict(self))


def cases():
    from .runtime_validation_registry import cases as declared_cases
    return declared_cases()


class Registry:
    def __init__(self, entries=None):
        self.entries = dict(cases() if entries is None else entries)
        for key, case in self.entries.items():
            if key != case.id or case.mode not in {"bounded", "trusted_local"} or case.layer not in {
                    "environment", "infrastructure", "business", "acceptance"}:
                raise ValidationError("validation_registry_invalid")
            if any(not r or not r.replace('_', '').isalnum() for r in case.resources):
                raise ValidationError('validation_resource_invalid')
        self.closure(tuple(self.entries))

    def closure(self, selected):
        ordered, seen = [], set()
        def visit(key, ancestors=()):
            if key not in self.entries or key in ancestors:
                raise ValidationError("validation_dependency_invalid")
            if key in seen:
                return
            case = self.entries[key]
            for prerequisite in case.requires:
                visit(prerequisite, (*ancestors, key))
            seen.add(key)
            ordered.append(key)
        for key in selected:
            visit(key)
        return ordered

    def requirements(self, operation, mode=None):
        return [c for c in self.entries.values() if not c.diagnostic and operation in c.operations
                and (mode is None or mode == c.mode)]


@dataclass(frozen=True)
class Authorization:
    case_id: str
    case_digest: str
    mode: str
    target: str
    parameters_digest: str
    runtime_digest: str
    expires_at: float
    request_id: str
    sap: bool = False

    def check(self, case, binding, target, parameters, now=None):
        if (self.case_id != case.id or self.case_digest != case.digest or self.mode != case.mode
                or self.runtime_digest != digest(binding) or self.target != target or not self.target
                or self.parameters_digest != digest(parameters) or not self.request_id
                or self.sap != case.sap):
            raise ValidationError("validation_authorization_mismatch")
        if (time.time() if now is None else now) >= self.expires_at:
            raise ValidationError("validation_authorization_expired")


@dataclass
class ValidationPlan:
    binding: dict
    selected: list[str]
    cases: list[dict]
    version: str = VERSION


@dataclass
class ValidationCaseResult:
    case_id: str
    case_digest: str
    status: str
    job_id: str
    mode: str
    binding_digest: str
    checks: dict = field(default_factory=dict)
    artifacts: list[dict] = field(default_factory=list)
    failure_code: str | None = None
    failure_scope: str | None = None
    stages: list[dict] = field(default_factory=list)


def plan(registry, selected, binding, grants=None, parameters=None, targets=None, retained=None):
    grants, parameters, targets, retained = grants or {}, parameters or {}, targets or {}, retained or {}
    result = []
    for key in registry.closure(selected):
        case = registry.entries[key]
        action, reason = "run", None
        if retained.get(key):
            action = "retain"
        elif case.layer in {"environment", "infrastructure"}:
            action = "inspect_saved_checks"
        elif key not in grants:
            action, reason = "blocked", "validation_not_authorized"
        else:
            try:
                grants[key].check(case, binding, targets.get(key), parameters.get(key))
            except ValidationError as exc:
                action, reason = "blocked", exc.code
        result.append({"case_id": key, "case_digest": case.digest, "operations": list(case.operations),
            "mode": case.mode, "requires": list(case.requires), "action": action, "reason": reason})
    return ValidationPlan(copy.deepcopy(binding), list(selected), result)


def check_result(case, result):
    if result.get("case_id") != case.id or result.get("case_digest") != case.digest or result.get("mode") != case.mode:
        raise ValidationError("validation_result_binding_mismatch")
    if result.get("status") not in {"passed", "failed", "blocked", "cancelled", "interrupted", "cleanup_pending"}:
        raise ValidationError("validation_result_status_invalid")
    if result.get("status") == "passed" and (not result.get("artifacts") or any(
            result.get("checks", {}).get(name) is not True for name in case.assertions)):
        raise ValidationError("validation_checks_incomplete")
    return result


_scope = ContextVar("runtime_validation_scope", default=None)


@contextmanager
def execution_scope(case, grant, binding, target, parameters, job_id):
    grant.check(case, binding, target, parameters)
    value = {"case": case, "grant": grant, "binding": copy.deepcopy(binding), "target": target,
        "parameters": copy.deepcopy(parameters), "job_id": job_id}
    token = _scope.set(value)
    try:
        yield value
    finally:
        _scope.reset(token)


def current_scope():
    return _scope.get()


def check_native_call(snapshot, operation, mode, *, tools=False):
    scope = current_scope()
    if scope is None:
        return  # Production and legacy SDK behavior is unchanged.
    case, grant = scope["case"], scope["grant"]
    grant.check(case, scope["binding"], scope["target"], scope["parameters"])
    # The adapter supplies the exact frozen Runtime snapshot, not a new default.
    if digest(snapshot) != digest(scope["binding"]) or operation not in case.operations or mode != case.mode:
        raise ValidationError("validation_native_scope_mismatch")
    if tools and not case.sap:
        raise ValidationError("validation_sap_not_authorized")


class Adapter(Protocol):
    def check_binding(self, binding: dict) -> None: ...
    async def execute(self, case: ValidationCase, scope: dict) -> dict: ...


async def run(plan_value, registry, store, adapter, grants, parameters, targets, *, retained=None):
    """One sequential executor; failed dependencies do not stop other branches."""
    results = dict(retained or {})
    for item in plan_value.cases:
        key = item["case_id"]
        case = registry.entries[key]
        if item["action"] == "retain":
            if key not in results:
                raise ValidationError("validation_retained_evidence_missing")
            continue
        blocked = next((p for p in case.requires if results.get(p, {}).get("status") != "passed"), None)
        if item["action"] == "blocked" or blocked:
            results[key] = {"case_id": key, "status": "blocked", "reason": item["reason"] or "validation_prerequisite_failed"}
            continue
        # Read-only checks are never model/auth probes.
        if case.layer in {"environment", "infrastructure"}:
            try:
                results[key] = adapter.inspect(case, plan_value.binding)
            except (ValidationError, ValueError, OSError) as exc:
                results[key] = {'case_id': key, 'status': 'blocked', 'reason': getattr(exc, 'code', 'validation_environment_unproven')}
            continue
        grant = grants[key]
        try:
            grant.check(case, plan_value.binding, targets[key], parameters[key])
            adapter.check_binding(plan_value.binding)
            if hasattr(adapter, 'check_case'):
                adapter.check_case(case)
        except (ValidationError, ValueError, OSError) as exc:
            results[key] = {'case_id': key, 'status': 'blocked', 'reason': getattr(exc, 'code', 'validation_preflight_failed')}
            continue
        claimed, record = store.claim(case, plan_value.binding, grant, targets[key], parameters[key])
        if not claimed:
            results[key] = store.existing_result(record)
            if results[key].get("status") in {"claimed", "running", "cleanup_pending", "unknown"}:
                break  # Unknown ownership is not permission to retry or overlap.
            if results[key].get('status') == 'passed' and hasattr(adapter, 'recover_registration'):
                try:
                    adapter.recover_registration(case, results[key])
                except (ValidationError, ValueError, OSError) as exc:
                    results[key] = {**results[key], 'qualification_error': getattr(exc, 'code', 'validation_recovery_unproven')}
            continue
        try:
            with execution_scope(case, grant, plan_value.binding, targets[key], parameters[key], record["job_id"]) as scope:
                result = await adapter.execute(case, scope)
                adapter.check_binding(plan_value.binding)
                grant.check(case, plan_value.binding, targets[key], parameters[key])
                check_result(case, result)
                if result.get("binding_digest") != digest(plan_value.binding) or result.get("job_id") != record["job_id"]:
                    raise ValidationError("validation_result_binding_mismatch")
                store.finish(record, result)
                results[key] = result
        except BaseException as exc:
            import asyncio
            import traceback
            code = getattr(exc, 'code', None) or ("validation_cancelled" if isinstance(exc, asyncio.CancelledError) else "validation_execution_error")
            # A raised exception does not prove that an owned SDK process exited.
            cleanup = adapter.cleanup_confirmed(record["job_id"])
            result = asdict(ValidationCaseResult(key, case.digest, "failed" if cleanup else "cleanup_pending",
                record["job_id"], case.mode, digest(plan_value.binding), failure_code=code,
                failure_scope="attempt" if code == "validation_cancelled" else "unknown"))
            # Never persist exception text/arguments, which may contain secrets.
            from pathlib import Path
            result['diagnostics'] = {'exception_type':type(exc).__name__,
                'frames':[{'file':Path(f.filename).name, 'function':f.name, 'line':f.lineno}
                    for f in traceback.extract_tb(exc.__traceback__)[-8:]]}
            store.finish(record, result)
            results[key] = result
            if not cleanup or isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                break
        if hasattr(adapter, "record_result"):
            try:
                adapter.record_result(case, results[key])
            except (ValidationError, OSError, ValueError) as exc:
                # Execution and registration are separate outcomes. A lost
                # qualification response must not rewrite or rerun the job.
                results[key] = {**results[key], 'qualification_error': getattr(exc, 'code', 'validation_registration_failed')}
    return results
