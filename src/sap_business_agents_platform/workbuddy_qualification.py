"""SDK-free, model-scoped qualification ledger. Never rewrites a historical PASS.

The installed release inventory remains authoritative for integrity. This ledger
answers a different question: which exact operation execution has been tested?
No model, installer, authentication or SAP calls occur in this module.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .runtime_policy import OPERATIONS, OPERATION_BUDGETS
from .workbuddy_environment import CONTRACT, PROTOCOL, WorkBuddyError, digest
from .workbuddy_identity import checked_identity
from .workbuddy_record_io import read_record
from . import workbuddy_site as site

VERSION = "sapba.workbuddy.qualification/1"
DEPENDENCY_VERSION = "sapba.workbuddy.dependencies/1"
CASE_VERSION = "sapba.workbuddy.live-cases/1"

# Explicit, conservative transitive components. A monolithic file is never
# sliced by AST/name or ignored because a change is described as cosmetic.
# Common transport/security changes intentionally invalidate all consumers.
COMPONENTS = {
    "platform": ((), ("runtime_contract.py", "runtime_policy.py", "runtime_execution.py",
        "runtime_stage.py", "runtime_prompts.py", "shared_planner.py", "runtime.py", "engine.py", "factory.py",
        "assistant_tools.py", "tool_gateway.py", "models.py")),
    "workbuddy_host": ((), ("workbuddy_driver.py", "workbuddy_planner.py", "workbuddy_supervisor.py",
        "workbuddy_environment.py", "workbuddy_manager.py", "workbuddy_job.py", "workbuddy_identity.py",
        "workbuddy_site.py", "workbuddy_record_io.py", "workbuddy_inspection.py", "workbuddy_diagnostics.py", "workbuddy_verification.py", "workbuddy_qualification.py")),
    "evidence": ((), ("sap_read/embedded_odata.py", "runtime_diagnostics.py", "skills.py")),
    "query": (("evidence",), ("runtime_query_contract.py", "runtime_harness.py", "runtime_harness_contract.py",
        "runtime_harness_result.py", "harness.py", "workbuddy_harness.py", "runtime_query_history.py",
        "runtime_report_contract.py", "runtime_report_convergence.py")),
    "author": (("evidence",), ("runtime_agent_authoring.py", "agent_authoring.py", "workbuddy_authoring.py", "assistant_ddic.py",
        "authoring_harness.py", "authoring_checks.py", "authoring_workspace.py", "agent_presentation.py", "acceptance_contract.py")),
    "workflow": ((), ("runtime_workflow_contract.py", "workflow_factory.py", "workflow_assistant.py",
        "workflow_candidate.py", "workflows.py", "workflow_composer.py", "workflow_presentation.py", "workflow_integration_capabilities.py")),
    "workflow_v2": (("workflow",), ("runtime_workflow_authoring.py", "workflow_authoring_runtime.py")),
    "role": (("workflow",), ("runtime_role_contract.py", "runtime_role_consolidation.py",
        "role_matching.py", "role_matching_documents.py")),
    "sample": (("query",), ("runtime_sample.py", "sample_discovery.py", "sample_scope_graph.py",
        "sample_read_budget.py", "workbuddy_sample.py")),
    "acceptance": (("query",), ("acceptance.py", "acceptance_contract.py")),
}
ROOTS = {
    "author_draft": ("author",), "review_agent_feedback": ("author",),
    "compose_workflow": ("workflow",), "review_workflow": ("workflow",),
    "repair_workflow": ("workflow",), "review_workflow_feedback": ("workflow",),
    "workflow_authoring.v2": ("workflow_v2",),
    "analyze_role_matching": ("role",), "review_role_matching_feedback": ("role",),
    "review_free_query_feedback": ("query",), "revise_free_query_presentation": ("query",),
    "free_query": ("query",), "sample_discovery": ("sample",),
    "acceptance_baseline": ("acceptance",), "acceptance_free_query": ("acceptance",),
}


def required_modes(operation):
    return ("bounded", "trusted_local") if operation == "review_agent_feedback" else (
        ("trusted_local",) if operation == "workflow_authoring.v2" else ("bounded",))


def dependency_manifests(root=None):
    """One read per file per projection, with per-operation transitive closure."""
    if set(ROOTS) != set(OPERATIONS):
        raise WorkBuddyError("workbuddy_dependency_registry_invalid")
    root = root or Path(__file__).parent
    cache = {}
    def closure(name, stack=()):
        if name in stack or name not in COMPONENTS:
            raise WorkBuddyError("workbuddy_dependency_registry_invalid")
        parents, files = COMPONENTS[name]
        result = {name: {"requires": list(parents), "files": {}}}
        for file in files:
            try:
                if file not in cache:
                    cache[file] = hashlib.sha256((root / file).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            except OSError as exc:
                raise WorkBuddyError("workbuddy_dependency_unavailable") from exc
            result[name]["files"][file] = cache[file]
        for parent in parents:
            result.update(closure(parent, (*stack, name)))
        return result
    result = {}
    for op in OPERATIONS:
        components = {}
        for name in ("platform", "workbuddy_host", *ROOTS[op]):
            components.update(closure(name))
        body = {"version": DEPENDENCY_VERSION, "operation": op, "roots": list(ROOTS[op]),
                "budget_seconds": OPERATION_BUDGETS[op], "components": components}
        result[op] = {**body, "digest": digest(body)}
    return result


def execution_components(release):
    """Release placement is not execution identity; all executable bytes are.

    Only CPython's known pyvenv.cfg 'command' installation-history entry is
    normalized. home/executable/system-site-packages and unknown keys are not
    discarded. The original file remains in the full release integrity check.
    """
    files = dict(release.get("files") or {})
    if not files or any(name not in files for name in (release.get("worker"), release.get("python"), release.get("cli"))):
        raise WorkBuddyError("workbuddy_environment_invalid")
    if "pyvenv.cfg" in files:
        try:
            config = {}
            for line in (Path(release["directory"]) / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                key, separator, value = line.partition("=")
                key = key.strip()
                if not separator or key in config or key not in {
                        "home", "include-system-site-packages", "version", "executable", "command"}:
                    raise WorkBuddyError("workbuddy_environment_equivalence_unknown")
                config[key] = value.strip()
            config.pop("command", None)
            files["pyvenv.cfg"] = digest(config)
        except (OSError, KeyError) as exc:
            raise WorkBuddyError("workbuddy_environment_equivalence_unknown") from exc
    body = {"protocol": PROTOCOL, "service_site_contract": release.get("service_site_contract"),
        "sdk_version": release.get("sdk_version"), "cli_version": release.get("cli_version"),
        "sdk_wheel_digest": release.get("sdk_wheel_digest"),
        "dependency_lock_digest": release.get("dependency_lock_digest"),
        "python_bootstrap_digest": release.get("python_bootstrap_digest"), "files": files,
        "entrypoints": {name: release[name] for name in ("python", "cli", "worker")}}
    return {**body, "digest": digest(body)}


def management_digest():
    """The host which constructs auth/model requests also needs equivalence."""
    root = Path(__file__).parent
    return digest({name: hashlib.sha256((root / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for name in ("workbuddy_manager.py", "workbuddy_supervisor.py", "workbuddy_site.py", "workbuddy_environment.py", "workbuddy_record_io.py", "workbuddy_identity.py")})


def model_binding(snapshot):
    requested = snapshot.get("model")
    if not isinstance(requested, str) or not requested:
        raise WorkBuddyError("workbuddy_qualification_model_missing")
    return {"requested_model": requested, "actual_model": snapshot.get("actual_model"),
            "identity_known": checked_identity(snapshot, flag="model_identity_known"),
            "identity_source": snapshot.get("model_identity_source"),
            "reasoning_effort": snapshot.get("reasoning_effort"),
            "reasoning_effort_source": snapshot.get("reasoning_effort_source", "sdk_default")}


def runtime_context(state, model=None):
    model = model or state.get("default_model_id")
    check = state.get("models", {}).get(model, {})
    return {"provider_id": "workbuddy", "model": model, "actual_model": check.get("actual_model"),
        "model_identity_known": checked_identity(check), "model_identity_source": check.get("model_identity_source"),
        "reasoning_effort": None, "reasoning_effort_source": "sdk_default",
        "authentication_revision": state.get("authentication_revision", 0), **site.configuration(state)}


def binding(snapshot, release, operation, mode, dependencies=None):
    if operation not in ROOTS or mode not in {"bounded", "trusted_local"}:
        raise WorkBuddyError("workbuddy_capability_invalid")
    dependencies = dependencies or dependency_manifests()[operation]
    return {"version": VERSION, "provider_id": "workbuddy", "model": model_binding(snapshot),
        "site": site.binding(snapshot), "authentication_revision": snapshot.get("authentication_revision", 0),
        "operation": operation, "permission_mode": mode, "contract_version": CONTRACT,
        "case_version": CASE_VERSION, "dependency_digest": dependencies["digest"],
        "execution_digest": execution_components(release)["digest"]}


def subject(value):
    return {key: value.get(key) for key in ("provider_id", "model", "site", "authentication_revision",
        "operation", "permission_mode", "contract_version", "case_version")}


def differences(old, new):
    return [key for key in sorted(set(old) | set(new)) if old.get(key) != new.get(key)]


def add_record(state, value):
    """Caller holds environment maintenance lock. Identical registrations are idempotent."""
    records = state.setdefault("qualification_records", {})
    body = copy.deepcopy(value)
    for record in records.values():
        if {k: v for k, v in record.items() if k not in {"id", "sequence", "created_at"}} == body:
            return record["id"]
    sequence = max((record.get("sequence", 0) for record in records.values()), default=0) + 1
    body.update(sequence=sequence, created_at=datetime.now(timezone.utc).isoformat())
    identifier = digest(body)
    records[identifier] = {**body, "id": identifier}
    return identifier


def _record_valid(record):
    return isinstance(record, dict) and record.get("id") == digest({k: v for k, v in record.items() if k != "id"})


def verify_origin(environment, state, record, seen=(), verified_releases=None):
    """Fail closed on missing/changed owned jobs or broken reuse provenance."""
    if not _record_valid(record) or record["id"] in seen or len(seen) > 32:
        return False
    if record.get("kind") == "reuse":
        source = state.get("qualification_records", {}).get(record.get("source_record_id"), {})
        target = record.get("binding", {})
        context = {**target.get("site", {}), "authentication_revision": target.get("authentication_revision", 0)}
        try:
            receipt = environment_check(environment, state, environment.release(record["environment_digest"]), context)
        except (WorkBuddyError, KeyError):
            return False
        return (source.get("kind") == "live_pass" and source.get("binding") == record.get("binding")
                and receipt is not None and digest(receipt) == record.get("environment_check_id")
                and verify_origin(environment, state, source, (*seen, record["id"]), verified_releases))
    if record.get("kind") != "live_pass":
        return False
    try:
        path = environment.root / "jobs" / (digest(record["job_id"]) + ".json")
        job = read_record(path)
        verified_releases = verified_releases if verified_releases is not None else {}
        if record["environment_digest"] not in verified_releases:
            verified_releases[record["environment_digest"]] = environment.release(record["environment_digest"])
        return (digest(job) == record["job_digest"] and job.get("status") == "completed"
            and job.get("cleanup_complete") is True and job.get("validation_job") is True
            and job.get("qualification_binding") == record["binding"]
            and job.get("result_metadata", {}).get("structured_terminal") is True)
    except (OSError, ValueError, KeyError, WorkBuddyError):
        return False


def latest_failure(state, target):
    return max((r.get("sequence", 0) for r in state.get("qualification_records", {}).values()
        if _record_valid(r) and r.get("kind") == "failure" and subject(r.get("binding", {})) == subject(target)), default=0)


def select_record(environment, state, target, environment_digest, verified_releases=None):
    candidates = sorted((r for r in state.get("qualification_records", {}).values()
        if _record_valid(r) and r.get("binding") == target and r.get("environment_digest") == environment_digest
        and r.get("kind") in {"live_pass", "reuse"}), key=lambda r: r["sequence"], reverse=True)
    barrier = latest_failure(state, target)
    for record in candidates:
        if record["sequence"] > barrier and verify_origin(environment, state, record, verified_releases=verified_releases):
            return record
    return None


def projection(environment, state, release, snapshot, manifests=None, operations=OPERATIONS):
    if state.get("qualification_protocol") == "sapba.validation.qualification/2":
        from .workbuddy_validation import projection as case_projection
        return case_projection(environment, state, release, snapshot, operations)
    manifests = manifests or dependency_manifests()
    result = {}
    verified_releases = {release["environment_digest"]: release}
    for op in operations:
        modes, origins, issues, changes = [], {}, [], set()
        for mode in required_modes(op):
            try:
                target = binding(snapshot, release, op, mode, manifests[op])
            except (WorkBuddyError, KeyError):
                issues.append("workbuddy_qualification_model_missing")
                continue
            record = select_record(environment, state, target, release["environment_digest"], verified_releases)
            if record:
                modes.append(mode)
                origins[mode] = {"record_id": record["id"], "kind": record["kind"],
                    "source_record_id": record.get("source_record_id"), "checked_at": record["created_at"]}
            else:
                issue = "workbuddy_qualification_validation_required"
                if latest_failure(state, target):
                    issue = "workbuddy_qualification_latest_failure"
                for prior in state.get("qualification_records", {}).values():
                    if prior.get("kind") in {"live_pass", "reuse"} and prior.get("binding", {}).get("operation") == op:
                        changes.update(differences(prior["binding"], target))
                        if prior["binding"].get("model") == target["model"]:
                            old = prior.get("dependency_manifest", {}).get("components", {})
                            for name, component in manifests[op]["components"].items():
                                for file, sha in component["files"].items():
                                    if old.get(name, {}).get("files", {}).get(file) != sha:
                                        changes.add("dependency:" + file)
                if issue == "workbuddy_qualification_validation_required" and any(
                        ":" + op + ":" in key for key in state.get("capabilities", {})):
                    issue = "workbuddy_qualification_legacy_binding_missing"
                issues.append(issue)
        result[op] = {"status": "validated" if modes else "unverified", "implemented": True,
            "environment_digest": release.get("environment_digest"), "contract_version": CONTRACT,
            "qualification_version": VERSION, "model": snapshot.get("model"),
            "permission_modes": modes, "required_permission_modes": list(required_modes(op)),
            "validation_origins": origins, "dependency_digest": manifests[op]["digest"],
            "reason": issues[0] if issues else None, "change_reasons": sorted(changes),
            "recovery": "platform_history"}
    return result


def environment_check(environment, state, release, snapshot):
    """A completed authentication probe proves target startup/handshake/cleanup.
    It neither performs a model probe nor grants business capability.
    """
    for value in state.get("qualification_environment_checks", {}).values():
        if value.get("environment_digest") != release["environment_digest"] or value.get("site") != site.binding(snapshot):
            continue
        if value.get("authentication_revision") != snapshot.get("authentication_revision", 0):
            continue
        try:
            job = read_record(environment.root / "jobs" / (digest(value["job_id"]) + ".json"))
            if (digest(job) == value["job_digest"] and job.get("status") == "completed" and job.get("cleanup_complete") is True
                    and job.get("operation") == "authentication" and job.get("result_metadata", {}).get("authenticated") is True
                    and job.get("management_digest") == value.get("management_digest") == management_digest()
                    and value.get("execution_digest") == execution_components(release)["digest"]):
                return value
        except (OSError, ValueError, KeyError):
            continue
    return None


def reuse_candidates(environment, state, release, snapshot, manifests=None):
    manifests = manifests or dependency_manifests()
    result = []
    check = environment_check(environment, state, release, snapshot)
    for op in OPERATIONS:
        for mode in required_modes(op):
            try:
                target = binding(snapshot, release, op, mode, manifests[op])
            except (WorkBuddyError, KeyError):
                continue
            if select_record(environment, state, target, release["environment_digest"]):
                continue
            for record in sorted(state.get("qualification_records", {}).values(), key=lambda r: r.get("sequence", 0), reverse=True):
                if (record.get("kind") != "live_pass" or record.get("binding") != target
                        or record.get("environment_digest") == release["environment_digest"]
                        or record.get("sequence", 0) <= latest_failure(state, target)):
                    continue
                if target["model"]["identity_known"] and check and verify_origin(environment, state, record):
                    result.append({"operation": op, "permission_mode": mode, "source_record_id": record["id"],
                        "source_environment_digest": record["environment_digest"], "target_environment_digest": release["environment_digest"],
                        "binding": target, "environment_check_id": digest(check), "rule": VERSION})
                    break
    return result


def effective_model_check(environment, state, release, model):
    """Read-only projection of explicitly audited model-check reuse."""
    check = state.get("models", {}).get(model, {})
    if check.get('status')=='authentication_changed':
        from .workbuddy_auth_recovery import historical_model_check
        recovered=historical_model_check(environment,state,model)
        if recovered is not None:
            return recovered
    if check.get("environment_digest") == release["environment_digest"]:
        return check
    receipt = environment_check(environment, state, release, runtime_context(state, model))
    for value in state.get("model_check_reuse", {}).values():
        if (value.get("model") != model or value.get("target_environment_digest") != release["environment_digest"]
                or value.get("source_check_digest") != check.get("check_digest")
                or not receipt or value.get("environment_check_id") != digest(receipt)):
            continue
        try:
            source = environment.release(check["environment_digest"])
            if (check.get("compatible") is True and checked_identity(check) and site.model_matches(check, state)
                    and model_check_origin(environment, check, model)
                    and check.get("management_digest") == value.get("management_digest") == management_digest()
                    and execution_components(source)["digest"] == execution_components(release)["digest"]
                    == check.get("execution_digest") == value.get("execution_digest")):
                return {**check, "environment_digest": release["environment_digest"],
                        "reused_from_check_digest": check["check_digest"], "check_digest": digest(value)}
        except (WorkBuddyError, KeyError):
            continue
    return check


def model_check_origin(environment, check, model):
    try:
        job = read_record(environment.root / "jobs" / (digest(check["source_job_id"]) + ".json"))
        result = job.get("result_metadata", {})
        return (digest(job) == check.get("source_job_digest") and job.get("operation") == "model_check"
            and job.get("management_digest") == check.get("management_digest")
            and job.get("runtime_model") == model and job.get("status") == "completed" and job.get("cleanup_complete") is True
            and job.get("environment_digest") == check.get("environment_digest") and site.matches(job, site.configuration(check))
            and job.get("authentication_revision", 0) == check.get("authentication_revision", 0)
            and result.get("structured_terminal") is True and result.get("actual_model") == check.get("actual_model")
            and result.get("identity_source") == check.get("model_identity_source"))
    except (OSError, ValueError, KeyError, WorkBuddyError):
        return False


def accepts_check_binding(environment, state, release, snapshot, current):
    """Refresh is not a config change, but arbitrary stale digests are rejected."""
    if snapshot.get("model_check_digest") == current.get("check_digest"):
        return bool(current.get("check_digest"))
    if not checked_identity(current):
        return False
    for prior in state.get("model_check_history", []):
        if (not isinstance(prior, dict) or prior.get("check_digest") != snapshot.get("model_check_digest")
                or prior.get("model_id") != snapshot.get("model") or not checked_identity(prior)
                or prior.get("compatible") is not True or not site.model_matches(prior, state)
                or prior.get("environment_digest") != release["environment_digest"]
                or prior.get("actual_model") != current.get("actual_model")
                or prior.get("model_identity_source") != current.get("model_identity_source")
                or not model_check_origin(environment, prior, snapshot["model"])):
            continue
        return True
    return False


def migrate_history(state, recovered=None):
    """Legacy evidence lacks model/dependency bindings. Archive only; never grant."""
    history = state.setdefault("qualification_legacy_history", {})
    recovered = recovered or {}
    for key, value in state.get("capabilities", {}).items():
        identifier = digest({"key": key, "record": value})
        entry = {"source_key": key, "source_record_digest": digest(value),
            "status": value.get("status"), "job_id": value.get("job_id"), "model": recovered.get(key),
            "reason": "workbuddy_qualification_legacy_dependencies_missing" if recovered.get(key) else "workbuddy_qualification_legacy_binding_missing"}
        # A supplemental, independently verified index never modifies a prior entry.
        history.setdefault(digest({"legacy_id": identifier, "recovered": recovered.get(key)}), entry)
    return len(history)


def validation_plan(environment, state, release, snapshot):
    if state.get("qualification_protocol") == "sapba.validation.qualification/2":
        from .runtime_validation_history import qualification_decisions
        decisions = qualification_decisions(environment, state, release, snapshot, include_previous=False)
        operations = decisions['operations'] or projection(environment, state, release, snapshot)
        return {"version": state["qualification_protocol"], "model": snapshot.get("model"),
            "actual_model": snapshot.get("actual_model"),
            "environment_digest": release["environment_digest"], "operations": operations,
            "source_audit": decisions.get('source_audit', 'unavailable'),
            "cases": decisions['cases'], "reuse_candidates": decisions['reuse_candidates'],
            "effective_pass_count": sum(v["status"] == "validated" for v in operations.values()),
            "historical_pass_count": len(decisions['historical_operations']),
            "legacy_record_count": len(state.get("capabilities", {})), "legacy_pass_count": 0}
    manifests = dependency_manifests()
    operations = projection(environment, state, release, snapshot, manifests)
    reuse = reuse_candidates(environment, state, release, snapshot, manifests)
    candidates = {(r["operation"], r["permission_mode"]) for r in reuse}
    cases = []
    for op, value in operations.items():
        for mode in required_modes(op):
            status = ("retain" if mode in value["permission_modes"] else
                "auditable_reuse" if (op, mode) in candidates else "revalidate")
            cases.append({"operation": op, "permission_mode": mode, "action": status,
                "reason": value["reason"], "changes": value["change_reasons"]})
    historical_ops = {r.get("binding", {}).get("operation") for r in state.get("qualification_records", {}).values()
        if r.get("kind") == "live_pass" and r.get("binding", {}).get("model", {}).get("requested_model") == snapshot.get("model")}
    for value in state.get("qualification_legacy_history", {}).values():
        model = value.get("model") or {}
        operation = value.get("source_key", "").split(":")[-2:-1]
        if value.get("status") == "validated" and model.get("requested_model") == snapshot.get("model") and operation and operation[0] in ROOTS:
            historical_ops.add(operation[0])
    return {"version": VERSION, "model": snapshot.get("model"), "actual_model": snapshot.get("actual_model"),
        "environment_digest": release["environment_digest"], "cases": cases,
        "reuse_candidates": reuse, "operations": operations,
        "historical_pass_count": len(historical_ops),
        "legacy_pass_count": sum(v.get("status") == "validated" for key, v in state.get("capabilities", {}).items()
            if key.startswith(release["environment_digest"] + ":" + snapshot["service_site_digest"] + ":")),
        "legacy_record_count": len(state.get("capabilities", {})),
        "effective_pass_count": sum(set(required_modes(op)).issubset(v["permission_modes"]) for op, v in operations.items()),
        "legacy_reason": "workbuddy_qualification_legacy_binding_missing" if state.get("capabilities") else None}
