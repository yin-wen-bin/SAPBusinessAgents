"""Protocol 2, opt-in WorkBuddy case qualification. No SDK or live calls.

Legacy records are immutable. A protocol fence deliberately makes old readers
fail closed instead of silently accepting a stale PASS after an upgrade.
"""
from __future__ import annotations

import copy
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .runtime_validation import Registry, ValidationCase, ValidationError, check_result, digest
from .runtime_validation_store import read, verify_archive, verify_artifacts

PROTOCOL = "sapba.validation.qualification/2"
POLICY_VERSION = "sapba.validation.qualification-policy/2.1"
REUSE_RULES = {
    "identical_execution/1": {"allowed": {"placement"}, "requires": {"target_startup"}},
    "non_execution_projection/1": {"allowed": {"display", "audit_projection"},
        "requires": {"target_startup", "projection_regression", "codex_controls"}},
    "identical_operation/1": {"allowed": {"outside_verified_operation_closure", "checker_extraction"},
        "requires": {"target_startup", "dependency_regression", "codex_controls"}},
}
# Runtime-side diagnostics, retries, success/error handling and cleanup are NOT
# non-execution projections. Unknown changed files fail closed.
NON_EXECUTION_FILES = frozenset({"docs/runtime-validation.md", "docs/workbuddy-qualification.md",
    "skills/validate-ai-runtime/SKILL.md"})


def active(state):
    return state.get("qualification_protocol") == PROTOCOL


def ledger(state):
    return state.get("validation_ledger", {})


def append(state, body):
    records = state.setdefault("validation_ledger", {})
    for key, value in records.items():
        if {k: v for k, v in value.items() if k not in {"id", "sequence", "created_at"}} == body:
            return key
    value = {**copy.deepcopy(body), "sequence": max((r["sequence"] for r in records.values()), default=0) + 1,
        "created_at": time.time()}
    identifier = digest(value)
    records[identifier] = {**value, "id": identifier}
    return identifier


def valid(record):
    return isinstance(record, dict) and record.get("id") == digest({k: v for k, v in record.items() if k != "id"})


def activate(environment, clearance):
    """Internal maintenance action. Caller must independently verify all writers.

    There is no automatic activation command in the public/private inspection
    CLIs. A timestamp or 'no tasks' boolean is not process clearance.
    """
    with environment.maintenance():
        state = environment.state()
        proof = clearance()
        if (not proof.get("all_writers_checked") or proof.get("old_writers") != []
                or proof.get("active_jobs") != [] or proof.get("cleanup_pending") != []
                or proof.get("state_digest") != digest(state) or not proof.get("process_inventory_digest")
                or proof.get("checked_at", 0) < time.time() - 30):
            raise ValidationError("validation_writer_clearance_required")
        if active(state):
            return
        def change(value):
            value["qualification_protocol"] = PROTOCOL
            value.setdefault("validation_ledger", {})
            fence = {"kind": "protocol_fence", "sequence": max((r.get("sequence", 0) for r in value.get("qualification_records", {}).values()), default=0) + 1,
                "binding": {"protocol": PROTOCOL}, "created_at": time.time(), "clearance_digest": digest(proof)}
            value.setdefault("qualification_records", {})[digest(fence)] = {**fence, "id": digest(fence)}
        environment.mutate(change)


def identity(snapshot):
    # Ignore timestamps and display capabilities, not account/model/site scopes.
    required = ("provider_id", "model", "actual_model", "model_identity_source", "service_site",
        "service_site_revision", "service_site_digest", "authentication_revision")
    if any(snapshot.get(k) is None for k in required) or snapshot.get("provider_id") != "workbuddy":
        raise ValidationError("validation_runtime_identity_missing")
    return {**{k: snapshot[k] for k in required}, "reasoning_effort": snapshot.get("reasoning_effort"),
        "reasoning_effort_source": snapshot.get("reasoning_effort_source", "sdk_default")}


def binding(snapshot, case, dependencies, execution_digest):
    return {"runtime": identity(snapshot), "case_id": case.id, "case_digest": case.digest,
        "mode": case.mode, "operations": list(case.operations), "dependencies": copy.deepcopy(dependencies),
        "execution_digest": execution_digest}


def subject(value):
    return {k: value.get(k) for k in ("runtime", "case_id", "mode")}


def _supersedes_unknown_attempt(failure, passed):
    """Link opaque wrapper failures only to a later, same-case live result.

    Callers must also verify the PASS's full provenance. This is not a generic
    override for unknown faults, component failures or unconfirmed cleanup.
    """
    if (not valid(failure) or failure.get("kind") != "failure"
            or failure.get("scope") != "unknown"
            or failure.get("failure_code") not in {"validation_execution_error", "validation_fixture_failed"}
            or failure.get("cleanup_pending") is True
            or not valid(passed) or passed.get("kind") != "case_pass"
            or passed.get("execution_kind") != "live"
            or passed.get("sequence", 0) <= failure["sequence"]
            or subject(passed.get("binding", {})) != subject(failure.get("binding", {}))
            or passed.get("environment_digest") != failure.get("environment_digest")):
        return False
    before, after = failure.get("binding", {}), passed.get("binding", {})
    if not before.get("case_digest") or before["case_digest"] != after.get("case_digest"):
        return False
    if failure["failure_code"] == "validation_fixture_failed":
        # Fixture assertions have no finer legacy failure category. Require
        # confirmed cleanup and the identical frozen business fixture data;
        # a different case/version/data must never clear that diagnostic.
        data = before.get("dependencies", {}).get("case_data")
        if (failure.get("cleanup_pending") is not False or not data
                or data != after.get("dependencies", {}).get("case_data")):
            return False
    return True


def blocks(state, target, environment_digest, after=0, *, environment=None):
    # A resolution cannot affect another subject/component/environment. Select
    # the relevant failures before opening their evidence; never validate every
    # historical Campaign while inspecting an unrelated authoring case.
    records = ledger(state)
    failures = []
    for record in records.values():
        if not valid(record) or record.get("kind") != "failure":
            continue
        scope = record.get("scope", "unknown")
        same_env = record.get("environment_digest") == environment_digest
        same_subject = subject(record.get("binding", {})) == subject(target)
        if scope == "attempt":
            affected = same_env and record.get("cleanup_pending") is True
        elif scope == "environment":
            affected = same_env
        elif scope == "component":
            affected = any(target.get("dependencies", {}).get(k) == v for k, v in record.get("components", {}).items())
        else:
            affected = same_subject
        if affected and not (scope == "case" and record["sequence"] < after):
            failures.append(record)
    if not failures:
        return []
    relevant = {record["id"] for record in failures}
    resolutions = set()
    verified_passes = {}
    for r in records.values():
        if r.get('failure_id') not in relevant:
            continue
        if valid(r) and r.get('kind') == 'resolution' and r.get('resolution') == 'unknown_attempt_superseded_by_live_case':
            failure = records.get(r.get('failure_id'), {})
            passed = records.get(r.get('pass_id'), {})
            if _supersedes_unknown_attempt(failure, passed) and environment is not None:
                # One live PASS can explain several legacy attempts. Verify
                # its full evidence once per inspection, never cache across
                # calls or use a display projection as execution authority.
                if passed['id'] not in verified_passes:
                    verified_passes[passed['id']] = verify_pass(environment, passed)
                if verified_passes[passed['id']]:
                    resolutions.add(r['failure_id'])
        if valid(r) and r.get('kind') == 'resolution' and r.get('resolution') == 'attempt_cleanup_confirmed':
            try:
                verify_artifacts(r['evidence_root'], r['artifacts'])
                failure = records.get(r.get('failure_id'), {})
                proof = read(Path(r['evidence_root']) / r['artifacts'][0]['path'])
                if failure.get('scope') == 'attempt' and proof.get('job_id') == failure.get('job_id') and proof.get('cleanup_complete') is True and proof.get('owned_process_start_id'):
                    resolutions.add(r['failure_id'])
            except (OSError, ValueError, KeyError):
                pass
    return [record["id"] for record in failures if record["id"] not in resolutions]


def verify_pass(environment, record):
    from .runtime_validation_checker import verify_pass as check_live_evidence
    return check_live_evidence(environment, record)


def register(environment, case, bound, result, *, source_snapshot, artifact_root, checker_report, owned_jobs, execution_kind):
    check_result(case, result)
    if execution_kind != "live" or result.get("status") != "passed":
        raise ValidationError("validation_live_evidence_required")
    report_path = Path(artifact_root) / checker_report["path"]
    if not report_path.resolve().is_relative_to(Path(artifact_root).resolve()):
        raise ValidationError("validation_artifact_path_invalid")
    record = {"kind": "case_pass", "execution_kind": execution_kind, "binding": bound,
        "case_definition": asdict(case),
        "environment_digest": result["environment_digest"], "source_snapshot": source_snapshot,
        "artifact_root": str(artifact_root), "checker_report": checker_report,
        "artifacts": result["artifacts"], "owned_jobs": owned_jobs, "result": result}
    provisional = {**record, "sequence": 1, "created_at": time.time()}
    if not verify_pass(environment, {**provisional, "id": digest(provisional)}):
        raise ValidationError("validation_provenance_missing")
    identifier = []
    def save(state):
        if not active(state):
            raise ValidationError("validation_protocol_not_active")
        identifier.append(append(state, record))
        passed = ledger(state)[identifier[0]]
        # An actual complete live revalidation can resolve this case's previous
        # opaque platform attempt. Preserve the original failure and link its
        # resolution to the verified live evidence, never to a reuse receipt.
        for failure in list(ledger(state).values()):
            if _supersedes_unknown_attempt(failure, passed):
                append(state, {'kind':'resolution', 'resolution':'unknown_attempt_superseded_by_live_case',
                    'failure_id':failure['id'], 'pass_id':passed['id']})
    environment.mutate(save)
    return identifier[0]


@dataclass(frozen=True)
class ReuseReceipt:
    source_record_id: str
    target_binding: dict
    target_environment: str
    target_snapshot: dict
    rule: str
    evidence_root: str
    evidence: tuple[dict, ...]
    request_id: str
    reviewer: str


def audit_reuse(environment, state, source, receipt):
    if not verify_pass(environment, source):
        raise ValidationError("validation_reuse_source_unproven")
    if subject(source["binding"]) != subject(receipt.target_binding) or source["binding"]["case_digest"] != receipt.target_binding["case_digest"]:
        raise ValidationError("validation_reuse_subject_changed")
    if source["binding"]["execution_digest"] != receipt.target_binding["execution_digest"]:
        raise ValidationError("validation_execution_retest_required")
    if blocks(state, receipt.target_binding, receipt.target_environment, after=source["sequence"], environment=environment):
        raise ValidationError("validation_reuse_failure_unresolved")
    if receipt.rule not in REUSE_RULES or not receipt.request_id or not receipt.reviewer:
        raise ValidationError("validation_reuse_rule_invalid")
    rule = REUSE_RULES[receipt.rule]
    old = verify_archive(source["source_snapshot"]["directory"], source["source_snapshot"]["digest"])["files"]
    new = verify_archive(receipt.target_snapshot["directory"], receipt.target_snapshot["digest"])["files"]
    changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    scope = None
    if receipt.rule == 'identical_operation/1':
        from .runtime_validation_dependencies import operation_equivalence
        scope = operation_equivalence(source, receipt)
    if receipt.rule == "identical_execution/1" and (changed or source["binding"] != receipt.target_binding):
        raise ValidationError("validation_reuse_not_identical")
    if receipt.rule == "non_execution_projection/1" and (set(changed) - NON_EXECUTION_FILES
            or source["binding"]["dependencies"] != receipt.target_binding["dependencies"]):
        raise ValidationError("validation_reuse_change_not_allowed")
    verify_artifacts(receipt.evidence_root, receipt.evidence)
    proofs = [read(Path(receipt.evidence_root) / r["path"]) for r in receipt.evidence]
    verified = set()
    for proof in proofs:
        if (proof.get("passed") is not True or proof.get("target_binding_digest") != digest(receipt.target_binding)
                or proof.get("target_source_digest") != receipt.target_snapshot["digest"]
                or proof.get("environment_digest") != receipt.target_environment):
            raise ValidationError("validation_reuse_evidence_binding")
        if proof.get("check") == "target_startup":
            job = read(environment.root / "jobs" / (digest(proof["job_id"]) + ".json"))
            if (digest(job) != proof.get("job_digest") or job.get("status") != "completed"
                    or job.get("cleanup_complete") is not True or job.get("operation") != "authentication"
                    or job.get("environment_digest") != receipt.target_environment
                    or job.get("result_metadata", {}).get("authenticated") is not True):
                raise ValidationError("validation_target_startup_unproven")
            if any(job.get(k) != receipt.target_binding['runtime'].get(k)
                    for k in ('service_site', 'service_site_revision', 'service_site_digest', 'authentication_revision')):
                raise ValidationError('validation_target_startup_binding_changed')
        elif proof.get("check") in {"projection_regression", "dependency_regression", "codex_controls"}:
            if not proof.get("test_report") or not proof.get("checker_digest"):
                raise ValidationError("validation_regression_report_missing")
            verify_artifacts(receipt.evidence_root, [proof["test_report"]])
            test = read(Path(receipt.evidence_root) / proof['test_report']['path'])
            if (test.get('status') != 'passed' or test.get('failed') != 0 or not test.get('tests')
                    or test.get('target_binding_digest') != digest(receipt.target_binding)
                    or test.get('checker_digest') != proof['checker_digest']):
                raise ValidationError('validation_regression_report_invalid')
        verified.add(proof.get("check"))
    if not rule["requires"].issubset(verified):
        raise ValidationError("validation_reuse_evidence_missing")
    return {"status": "auditable", "changed_files": changed, "source_record_id": source["id"],
        "rule": receipt.rule, "receipt_digest": digest(asdict(receipt)),
        **({'operation_scope': scope} if scope is not None else {})}


def apply_reuse(environment, receipt, *, authorized=False):
    if not authorized:
        raise ValidationError("validation_reuse_authorization_required")
    result = []
    def change(state):
        if not active(state):
            raise ValidationError("validation_protocol_not_active")
        prior = next((r for r in ledger(state).values() if r.get("kind") == "case_reuse" and r.get("receipt", {}).get("request_id") == receipt.request_id), None)
        body = asdict(receipt)
        if prior and digest(prior["receipt"]) != digest(body):
            raise ValidationError("validation_request_conflict")
        source = ledger(state).get(receipt.source_record_id, {})
        audit_reuse(environment, state, source, receipt)
        result.append(prior["id"] if prior else append(state, {"kind": "case_reuse", "binding": receipt.target_binding,
            "environment_digest": receipt.target_environment, "receipt": body, "source_record_id": source["id"]}))
    environment.mutate(change)
    return result[0]


def select(environment, state, target, environment_digest):
    from .runtime_validation_checker_release import compatibility
    candidates = sorted(ledger(state).values(), key=lambda r: r.get("sequence", 0), reverse=True)
    for record in candidates:
        if not valid(record) or record.get("environment_digest") != environment_digest:
            continue
        source = (record.get('source_snapshot') if record.get('kind') == 'case_pass'
            else record.get('receipt', {}).get('target_snapshot'))
        if not compatibility(record.get('binding', {}), target, source):
            continue
        if record.get("kind") == "case_pass" and not blocks(state, target, environment_digest, record["sequence"], environment=environment) and verify_pass(environment, record):
            return record
        if record.get("kind") == "case_reuse":
            try:
                audit_reuse(environment, state, ledger(state).get(record["source_record_id"], {}), ReuseReceipt(**record["receipt"]))
                return record
            except (ValidationError, OSError, KeyError, ValueError):
                pass
    return None


def inspection(environment, state, release, snapshot, case_bindings, registry=None):
    registry = registry or Registry()
    results, selected = {}, {}
    for operation in sorted({op for c in registry.entries.values() for op in c.operations}):
        required = registry.requirements(operation)
        by_mode, missing, origins = {}, [], {}
        for case in required:
            target = case_bindings[case.id]
            if case.id not in selected:
                selected[case.id] = select(environment, state, target, release["environment_digest"])
            record = selected[case.id]
            by_mode.setdefault(case.mode, []).append(bool(record))
            if record:
                origins[case.id] = {"record_id": record["id"], "kind": record["kind"]}
                if record['binding'] != target:
                    origins[case.id]['checker_compatibility'] = {
                        'rule': 'checker_extraction/1',
                        'source_checker_digest': record['binding']['dependencies']['case_checker'],
                        'target_checker_digest': target['dependencies']['case_checker']}
            else:
                missing.append(case.id)
        modes = [mode for mode, passed in by_mode.items() if all(passed)]
        results[operation] = {"status": "validated" if not missing else "unverified", "implemented": True,
            "permission_modes": modes, "required_permission_modes": list(by_mode), "missing_cases": missing,
            "validation_origins": origins, "environment_digest": release["environment_digest"], "contract_version": "1.0",
            "qualification_version": PROTOCOL, "reason": "validation_cases_required" if missing else None}
    return results, selected


def projection(environment, state, release, snapshot, case_bindings, registry=None):
    return inspection(environment, state, release, snapshot, case_bindings, registry)[0]
