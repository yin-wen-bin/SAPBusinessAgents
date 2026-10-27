"""Model-independent SDK certification. No model calls or implicit migrations.

Certificates are appended only by an explicit maintenance operation. Business
PASS records remain untouched. Whole responsibility components, not AST slices,
define the transport and request-protocol dependencies.
"""
from __future__ import annotations

import copy
from pathlib import Path

from .workbuddy_environment import WorkBuddyError, digest, file_hash, PROTOCOL
from .workbuddy_qualification import execution_components, required_modes
from .runtime_policy import OPERATIONS

POLICY = "sapba.workbuddy.three-layer/1"
CERTIFICATE = "sapba.workbuddy.sdk-integration/1"
CORE = ("messages_terminal", "structured_channel", "tool_bridge", "authorization",
        "cancel", "deadline", "owned_process_cleanup")
CORE_TESTS = {
    "messages_terminal": ("test_workbuddy_isolation", "test_worker_boundaries_and_cleanup"),
    "structured_channel": ("test_workbuddy_three_layers", "test_versioned_probe_idempotent_and_strict"),
    "tool_bridge": ("test_workbuddy_isolation", "test_tool_relay_and_no_connection_credentials"),
    "authorization": ("test_workbuddy_three_layers", "test_hard_gates_remain"),
    "cancel": ("test_workbuddy_isolation", "test_timeout_and_owned_child_tree"),
    "deadline": ("test_workbuddy_isolation", "test_timeout_and_owned_child_tree"),
    "owned_process_cleanup": ("test_workbuddy_isolation", "test_timeout_and_owned_child_tree")}


def checker_manifest():
    root = Path(__file__).parents[2] / "tests"
    return {name + ".py": file_hash(root / (name + ".py")) for name in sorted({value[0] for value in CORE_TESTS.values()})}
# Management/admission/quality/UI are deliberately absent: those have their own
# tests and policy, and cannot change what a model is asked to do.
PROTOCOL_FILES = ("workbuddy_worker.py", "workbuddy_model_protocol.py", "workbuddy_identity.py")
INFRASTRUCTURE_FILES = ("workbuddy_supervisor.py", "workbuddy_environment.py", "workbuddy_job.py",
                        "workbuddy_site.py", "workbuddy_record_io.py", "workbuddy_integration.py",
                        "workbuddy_dispatch_admission.py", "runtime_admission.py", "workbuddy_model_probe.py",
                        "workbuddy_driver.py", "workbuddy_planner.py", "runtime_input_control.py", "runtime_contract.py")
INFRASTRUCTURE_FILES += ('workbuddy_inspection.py',)
INFRASTRUCTURE_FILES += ('workbuddy_failure_watch.py', 'workbuddy_auth_signals.py', 'workbuddy_diagnostics.py', 'runtime_diagnostics.py')
INFRASTRUCTURE_FILES += ('workbuddy_auth_context.py', 'runtime_authentication.py', 'runtime_authentication_adapters.py',
                        '../../scripts/runtime-authentication-helper.py', '../../scripts/login-workbuddy-runtime.py')
INFRASTRUCTURE_FILES += ('workbuddy_auth_recovery.py', 'runtime.py', 'plugins.py')
INFRASTRUCTURE_FILES += ('workbuddy_job_index.py',)
INFRASTRUCTURE_FILES += ('runtime_capacity.py',)
INFRASTRUCTURE_FILES += ('runtime_legacy_protocol.py', 'runtime_terminal_contract.py', 'runtime_report_convergence.py', 'runtime_report_contract.py')


def manifest(names, root=None):
    root = Path(root or Path(__file__).parent)
    return {name: file_hash(root / name) for name in names}


def protocol_digest(root=None):
    return digest({"contract": "sapba.workbuddy.model-check/3", "files": manifest(PROTOCOL_FILES, root)})


def binding(release, root=None):
    return {"contract": CERTIFICATE, "execution_digest": execution_components(release)["digest"],
            "platform_protocol": PROTOCOL,
            "infrastructure": manifest(INFRASTRUCTURE_FILES, root)}


def active(state):
    return state.get("runtime_admission_policy") == POLICY


def _artifact(environment, reference):
    """Evidence belongs to protected local storage, never an arbitrary path."""
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        raise WorkBuddyError("workbuddy_integration_evidence_invalid")
    root = environment.root.resolve()
    path = (root / reference["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file() or file_hash(path) != reference["sha256"]:
        raise WorkBuddyError("workbuddy_integration_evidence_invalid")
    return path


def _artifact_bytes(environment, reference):
    """Hash exactly the bytes that are used, without a preceding file reread."""
    import hashlib
    from .workbuddy_inspection import current
    if not isinstance(reference, dict) or set(reference) != {'path', 'sha256'}:
        raise WorkBuddyError('workbuddy_integration_evidence_invalid')
    root = environment.root.resolve()
    path = (root / reference['path']).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise WorkBuddyError('workbuddy_integration_evidence_invalid')
    value = path.read_bytes()
    if hashlib.sha256(value).hexdigest() != reference['sha256']:
        raise WorkBuddyError('workbuddy_integration_evidence_invalid')
    scope = current(environment)
    if scope:
        scope.watch_bytes(path, reference['sha256'])
    return value


def _same_execution(environment, source_environment, release):
    if source_environment == release["environment_digest"]:
        return True
    # Installation placement is not SDK identity. Recheck both immutable
    # inventories; this reuses SDK transport evidence, never model quality.
    if not isinstance(source_environment, str):
        return False
    try:
        source = environment.release(source_environment)
        return execution_components(source)["digest"] == execution_components(release)["digest"]
    except (OSError, ValueError, WorkBuddyError):
        return False


def validate_evidence(environment, release, evidence, *, operation=None, mode='bounded', target=None):
    """Explicit audit input; offline tests are infrastructure, not business PASS.

    Each assertion references an inspectable checker report. Operation support
    references an actual completed SDK job or an infrastructure protocol test;
    neither is promoted to a different model's business quality result.
    """
    import json
    import hashlib
    # Per-inspection immutable bytes only. Never cache across calls or use a
    # display cache to authorize execution. Shared reports/checkers are read once.
    checked, documents, suites = {}, {}, {}
    def artifact_bytes(reference):
        key = digest(reference)
        if key not in checked:
            checked[key] = _artifact_bytes(environment, reference)
        return checked[key]
    def document(reference):
        key = digest(reference)
        if key not in documents:
            documents[key] = json.loads(artifact_bytes(reference))
        return documents[key]
    if not isinstance(evidence, dict) or evidence.get("target") != (target if target is not None else binding(release)):
        raise WorkBuddyError("workbuddy_integration_binding_mismatch")
    origin = document(evidence.get("transport_origin"))
    if (origin.get("status") != "completed" or origin.get("cleanup_complete") is not True
            or not _same_execution(environment, origin.get("environment_digest"), release)
            or origin.get("protocol") != PROTOCOL or not origin.get("process_identity")
            or origin.get("result_metadata", {}).get("structured_terminal") is not True):
        raise WorkBuddyError("workbuddy_integration_evidence_invalid")
    for name in CORE:
        report = document(evidence.get("core", {}).get(name))
        if (report.get("target") != evidence["target"] or report.get("assertions", {}).get(name) is not True
                or report.get("kind") != "sdk_infrastructure_check" or report.get("complete") is not True):
            raise WorkBuddyError("workbuddy_integration_evidence_invalid")
        # A boolean assertion is not a test run. Require concrete case results,
        # full checker-source hashes and an inspectable output artifact.
        import xml.etree.ElementTree as xml
        expected_files = {module + ".py" for module, _ in CORE_TESTS.values()}
        if (report.get("checker_contract") != CERTIFICATE
                or set(report.get("checker_files", {})) != expected_files):
            raise WorkBuddyError("workbuddy_integration_evidence_invalid")
        for filename, expected_hash in report["checker_files"].items():
            source = report.get("checker_sources", {}).get(filename)
            if not source or source.get("sha256") != expected_hash:
                raise WorkBuddyError("workbuddy_integration_evidence_invalid")
            artifact_bytes(source)  # Original full checker, not today's source.
        try:
            reference = report.get('test_report')
            key = digest(reference)
            if key not in suites:
                suites[key] = xml.fromstring(artifact_bytes(reference)).findall('.//testcase')
            tests = suites[key]
            module, test_name = CORE_TESTS[name]
            matching = [test for test in tests if test.get("classname", "").split(".")[-1] == module
                        and test.get("name", "").split("[", 1)[0] == test_name]
            if not matching or any(list(test.findall("failure")) + list(test.findall("error")) + list(test.findall("skipped")) for test in matching):
                raise WorkBuddyError("workbuddy_integration_evidence_invalid")
        except xml.ParseError:
            raise WorkBuddyError("workbuddy_integration_evidence_invalid") from None
    operations = evidence.get("operations", {})
    if not operations or any(op not in OPERATIONS for op in operations):
        raise WorkBuddyError("workbuddy_integration_operations_invalid")
    for op, modes in operations.items():
        if not modes or any(mode not in required_modes(op) for mode in modes):
            raise WorkBuddyError("workbuddy_integration_operations_invalid")
        for mode, reference in modes.items():
            if operation is not None and (op, mode) != operation:
                continue
            report = document(reference)
            if (report.get("target") != evidence["target"] or report.get("operation") != op
                    or report.get("permission_mode") != mode or report.get("complete") is not True
                    or report.get("cleanup_complete") is not True
                    or report.get("kind") not in {"sdk_infrastructure_check", "audited_sdk_job"}):
                raise WorkBuddyError("workbuddy_integration_evidence_invalid")
            if report.get("kind") == "audited_sdk_job":
                original = document(report.get("source_job"))
                if (original.get("operation") != op or original.get("status") != "completed"
                        or original.get("cleanup_complete") is not True
                        or not _same_execution(environment, original.get("environment_digest"), release)
                        or original.get("permission_mode", original.get("mode", "bounded")) != mode
                        or original.get("result_metadata", {}).get("structured_terminal") is not True):
                    raise WorkBuddyError("workbuddy_integration_evidence_invalid")
    return copy.deepcopy(evidence)


def audit(environment, release, evidence):
    checked = validate_evidence(environment, release, evidence)
    body = {"contract": CERTIFICATE, "binding": binding(release), "evidence": checked,
            "operations": {op: sorted(modes) for op, modes in checked["operations"].items()}}
    return {**body, "id": digest(body)}


def activate(environment, certificate, *, request_id, clearance):
    """Private, explicit maintenance operation; never called by GET or dispatch."""
    if not isinstance(request_id, str) or not request_id:
        raise WorkBuddyError("workbuddy_integration_request_invalid")
    with environment.maintenance():
        import time
        state = environment.state()
        if certificate.get("id") != digest({k: v for k, v in certificate.items() if k != "id"}):
            raise WorkBuddyError("workbuddy_integration_certificate_invalid")
        prior = state.get("integration_requests", {}).get(request_id)
        if prior is not None:
            if prior != certificate["id"]:
                raise WorkBuddyError("workbuddy_integration_request_conflict")
            return {"request_id": request_id, "certificate_id": prior, "status": "already_applied"}
        proof = clearance()
        if (not proof.get("all_writers_checked") or proof.get("old_writers") != []
                or proof.get("active_jobs") != [] or proof.get("cleanup_pending") != []
                or proof.get("state_digest") != digest(state) or not proof.get("process_inventory_digest")
                or not time.time() - 30 <= proof.get("checked_at", 0) <= time.time() + 1):
            raise WorkBuddyError("workbuddy_integration_writer_clearance_required")
        release = environment.release()
        checked = audit(environment, release, certificate.get("evidence"))
        if checked != certificate:
            raise WorkBuddyError("workbuddy_integration_certificate_invalid")
        # Caller must have stopped old writers; do not silently change protocol.
        if state.get("qualification_protocol") != "sapba.validation.qualification/2":
            raise WorkBuddyError("workbuddy_integration_protocol_required")
        from .workbuddy_supervisor import WorkBuddySupervisor
        if WorkBuddySupervisor(environment).reconcile():
            raise WorkBuddyError("workbuddy_cleanup_pending")
        def save(value):
            requests = value.setdefault("integration_requests", {})
            if request_id in requests and requests[request_id] != certificate["id"]:
                raise WorkBuddyError("workbuddy_integration_request_conflict")
            value.setdefault("sdk_integration_records", {})[certificate["id"]] = certificate
            value["runtime_admission_policy"] = POLICY
            requests[request_id] = certificate["id"]
            value.setdefault("integration_activation_evidence", {})[request_id] = {"clearance_digest": digest(proof),
                "certificate_id": certificate["id"], "policy": POLICY}
        environment.mutate(save)
        return {"request_id": request_id, "certificate_id": certificate["id"], "status": "applied"}


def projection(environment, state, release, *, operation=None, mode='bounded'):
    from .workbuddy_inspection import current
    scope = current(environment)
    if scope is not None:
        target = scope.memo(('integration_binding', release['environment_digest']), lambda: binding(release))
        key = ('sdk_certificate', digest([state.get('runtime_admission_policy'), state.get('sdk_integration_records')]),
               digest(target), operation, mode)
        return scope.memo(key, lambda: _projection(environment, state, release, operation=operation, mode=mode, target=target))
    return _projection(environment, state, release, operation=operation, mode=mode)


def _projection(environment, state, release, *, operation=None, mode='bounded', target=None):
    result = {"contract": CERTIFICATE, "status": "not_certified", "operations": {},
              "reason": "workbuddy_sdk_integration_required"}
    if not active(state):
        return {**result, "reason": "workbuddy_admission_migration_required"}
    target = target if target is not None else binding(release)
    from .workbuddy_inspection import current
    scope = current(environment)
    if scope:
        for filename, expected in target['infrastructure'].items():
            scope.watch_bytes(Path(__file__).parent / filename, expected)
    identifiers, operations = [], {}
    records = state.get('sdk_integration_records', {})
    # Derived lookup only: every selected original certificate/artifact is still
    # hashed. This owner-bound index cannot authorize another request/operation.
    candidates = [(key, value) for key, value in records.items() if value.get('binding') == target]
    for identifier, record in candidates:
        if record.get("binding") != target or record.get("id") != identifier:
            continue
        if digest({k: v for k, v in record.items() if k != "id"}) != identifier:
            continue
        try:
            if record.get('operations') != {op: sorted(modes) for op, modes in record.get('evidence', {}).get('operations', {}).items()}:
                continue
            validate_evidence(environment, release, record.get("evidence"),
                operation=(operation, mode) if operation is not None else None, target=target)
        except (OSError, ValueError, TypeError, WorkBuddyError):
            continue
        identifiers.append(identifier)
        for name, modes in record["operations"].items():
            if operation is None:
                operations[name] = sorted(set(operations.get(name, [])) | set(modes))
            elif name == operation and mode in modes:
                operations[name] = [mode]
    if identifiers:
        return {**result, "status": "certified", "reason": None,
                "certificate_id": digest(sorted(identifiers)), "certificate_ids": sorted(identifiers),
                "operations": operations, "binding": target}
    return result


def require_operation(environment, state, release, operation, mode):
    status = projection(environment, state, release, operation=operation, mode=mode)
    if status["status"] != "certified":
        raise WorkBuddyError(status["reason"])
    if operation not in OPERATIONS or mode not in status["operations"].get(operation, []):
        raise WorkBuddyError("workbuddy_sdk_operation_not_certified")
    return status

INFRASTRUCTURE_FILES += ("workbuddy_online_update.py", "workbuddy_online_evidence.py", "workbuddy_sdk_update.py", "workbuddy_sdk_target_audit.py", "../../scripts/verify-workbuddy-update-infrastructure.py")

INFRASTRUCTURE_FILES += ("../../tests/workbuddy_integration_fakes.py", "../../tests/fixtures/workbuddy_protocol_worker.py")
