"""Adapter to existing, isolated WorkBuddy fixtures, not a second business engine.

Imported only by the frozen private runner. Import performs no model/SAP calls.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from sap_business_agents_platform.runtime_validation import ValidationCaseResult, ValidationError, digest, current_scope
from sap_business_agents_platform.runtime_validation_store import atomic, read, artifact, verify_archive, process_binding

_adapter = ContextVar("validation_fixture_adapter", default=None)


def active_adapter():
    return _adapter.get()


def selected(operation):
    scope = current_scope()
    return scope is None or operation in scope["case"].operations


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkBuddyCases:
    def __init__(self, root, batch, manifest, process):
        from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
        self.root, self.batch, self.manifest, self.process = Path(root), Path(batch), manifest, process
        self.manager = WorkBuddyManager(root)
        self.source = Path(manifest["source_snapshot"]["directory"]) / "source"
        self.proofs, self.jobs, self.reports = {}, {}, {}

    def check_binding(self, snapshot):
        from sap_business_agents_platform.runtime_validation_freeze import require_authentication_binding
        from sap_business_agents_platform.workbuddy_validation import inspect_saved, case_bindings
        from sap_business_agents_platform.runtime_validation_inventory import sha
        from sap_business_agents_platform.runtime_validation_qualification import active
        verify_archive(self.manifest["source_snapshot"]["directory"], self.manifest["source_snapshot"]["digest"])
        process_binding(self.manifest["source_snapshot"])
        state = self.manager.environment.state()
        # This implementation cannot be used to start live validation before the
        # separately approved writer migration. No implicit activation.
        if not active(state):
            raise ValidationError("validation_protocol_not_active")
        if state.get("enabled"):
            raise ValidationError("validation_runtime_not_idle")
        if snapshot != self.manifest["runtime"]:
            raise ValidationError("validation_runtime_changed")
        require_authentication_binding(self.root, snapshot)
        from sap_business_agents_platform.runtime_validation import Registry
        release = inspect_saved(self.manager.environment, snapshot,
            formal=any(Registry().entries[key].layer == 'acceptance' for key in self.manifest['selected']))
        registry = Registry()
        expected_cases = registry.closure(self.manifest['selected'])
        if set(self.manifest['case_bindings']) != set(expected_cases):
            raise ValidationError('validation_case_bindings_incomplete')
        current_bindings = case_bindings(snapshot, release, registry,
            case_ids=expected_cases)
        if any(current_bindings.get(key) != value for key, value in self.manifest["case_bindings"].items()):
            raise ValidationError("validation_dependency_changed")
        for name, value in self.manifest["controls"].items():
            if sha(self.root / name) != value:
                raise ValidationError("validation_control_changed")
        # Working source changes after freeze are detected even though execution
        # uses the immutable copy. Credentials are hashes only, never archived.
        archived = read(Path(self.manifest["source_snapshot"]["directory"]) / "manifest.json")
        if any(sha(self.root / name) != value for name, value in archived["files"].items()):
            raise ValidationError("validation_source_changed")

    def inspect(self, case, snapshot):
        self.check_binding(snapshot)
        return {"case_id": case.id, "status": "passed", "source": "saved_verified_checks", "model_calls": 0}

    def check_case(self, case):
        if case.fixture == 'acceptance':
            from sap_business_agents_platform.runtime_validation_freeze import require_authentication_binding, require_harness_binding
            require_authentication_binding(self.root, self.manifest['runtime'])
            require_harness_binding(self.manifest['runtime'])
            # Resolve the controller's lazy import before claiming a job or
            # creating a Campaign. Import-only: no model or SAP execution.
            try:
                from scripts.run_three_stage_acceptance import (
                    _acceptance_prompt, _compare, _normalize_acceptance_projection,
                    _normalize_run, _projection_spec, _validate_projection_matches_visible_report,
                )
            except ImportError:
                raise ValidationError('validation_acceptance_dependency_missing') from None
        precursors = {'workflow.repair': ('workflows', 'review_workflow-precursor.json'),
            'workflow.feedback': ('workflows', 'compose_workflow-precursor.json'),
            'role.feedback': ('roles', 'analysis-precursor.json')}
        if case.id in precursors:
            folder, file = precursors[case.id]
            if not (self.batch / 'fixtures' / folder / file).is_file():
                raise ValidationError('validation_prerequisite_artifact_required')

    def attach(self, manager, snapshot):
        scope = current_scope()
        if scope is None or snapshot != scope["binding"]:
            raise ValidationError("validation_fixture_scope_missing")
        parent = scope["job_id"]
        original = manager.supervisor.run
        def collect(value, operation, **kwargs):
            if value != snapshot or not selected(operation) or kwargs.get("permission_modes") != [scope["case"].mode]:
                raise ValidationError("validation_registration_scope_mismatch")
            evidence = kwargs.get("evidence", {})
            if evidence.get("platform_contract_passed") is not True:
                raise ValidationError("validation_fixture_contract_failed")
            self.proofs.setdefault(parent, []).append({"operation": operation, **copy.deepcopy(kwargs)})
            return {"pending_case_checks": True}
        manager.record_operation_validation = collect
        async def observe(**kwargs):
            import uuid
            self.check_binding(snapshot)
            task = kwargs.setdefault("task_id", "validation_native_" + uuid.uuid4().hex)
            self.jobs.setdefault(parent, []).append(task)
            handler = kwargs.get('tool_handler')
            if handler:
                async def authorized_tool(name, arguments):
                    scope['grant'].check(scope['case'], snapshot, scope['target'], scope['parameters'])
                    self.check_binding(snapshot)
                    if not scope['case'].sap and name.startswith('sap_'):
                        raise ValidationError('validation_sap_not_authorized')
                    return await handler(name, arguments)
                kwargs['tool_handler'] = authorized_tool
            return await original(**kwargs)
        manager.supervisor.run = observe

    def cleanup_confirmed(self, parent):
        for task in self.jobs.get(parent, []):
            path = self.manager.environment.root / "jobs" / (digest(task) + ".json")
            if not path.exists() or read(path).get("cleanup_complete") is not True:
                return False
        return True

    async def execute(self, case, scope):
        self.check_binding(scope["binding"])
        # A target is an isolated fixture directory, never a production object.
        family = "roles" if case.fixture == "roles" else case.fixture
        destination = self.batch / "fixtures" / family
        if scope["target"] != destination.relative_to(self.root).as_posix():
            raise ValidationError("validation_target_mismatch")
        expected = self.manifest["fixture_parameters"][case.id]
        if expected != scope["parameters"]:
            raise ValidationError("validation_fixture_parameters_changed")
        # Shared fixture policy is frozen alongside the business code. An
        # arbitrary manifest cannot silently change a hard-coded test request.
        policy = load("frozen_fixture_policy", self.source / "scripts/runtime_validation_fixture_policy.py")
        policy.validate(case, expected)
        token = _adapter.set(self)
        argv = sys.argv
        try:
            atomic(self.batch / "runtime-snapshot.json", scope["binding"])
            if case.fixture in {"limited_sample", "complete_unique_key"}:
                module = load("validation_free_query", self.source / "scripts/verify-workbuddy-free-query.py")
                module.ROOT = self.root
                await module.verify(SimpleNamespace(data_root=destination, batch=self.batch, case=case.fixture,
                    purchase_order=expected["purchase_order"]))
                report_path = destination / "report.json"
            elif case.fixture == "roles":
                module = load("validation_roles", self.source / "scripts/verify-runtime-role-matching.py")
                module.ROOT = self.root
                await module.verify(SimpleNamespace(data_root=destination, batch=self.batch, provider="workbuddy"))
                report_path = destination / (case.id + ".json")
            else:
                runner = load("validation_legacy_fixtures", self.source / "scripts/verify-workbuddy-operations.py")
                runner.ROOT = self.root
                module = runner.load_case(case.fixture, self.batch)
                module.RUN_ROOT, module.REPORT = destination, destination / (case.id + ".json")
                if hasattr(module, "create_app"):
                    create = module.create_app
                    def isolated_app(settings):
                        from workbuddy_validation_auth import seed_isolated_authentication
                        seed_isolated_authentication(settings, self.batch)
                        return create(settings)
                    module.create_app = isolated_app
                sys.argv = [case.fixture, *runner.FAMILIES[case.fixture][1]]
                await module.main()
                report_path = module.REPORT
        finally:
            sys.argv = argv
            _adapter.reset(token)
        saved = read(report_path)
        if saved.get("phase") not in {"completed", "passed"} or saved.get("cleanup_pending"):
            raise ValidationError("validation_fixture_failed")
        qualified = {p["operation"] for p in self.proofs.get(scope["job_id"], [])}
        if case.id != "query.limited" and not set(case.operations).issubset(qualified):
            raise ValidationError("validation_fixture_checks_missing")
        if case.id == "query.limited" and (saved.get("phase") != "passed" or saved.get("qualified") is not False):
            raise ValidationError("validation_limited_evidence_invalid")
        if not self.jobs.get(scope["job_id"]) or not self.cleanup_confirmed(scope["job_id"]):
            raise ValidationError("validation_cleanup_unproven")
        expected_stages = [s for s in saved.get('stages', []) if s.get('operation') in case.operations or 'operation' not in s]
        if any(s.get('phase') != 'passed' for s in expected_stages):
            raise ValidationError("validation_fixture_failed")
        # Preserve complete fixture report and owned-job proof, not a boolean
        # registration stripped from its checking context.
        proof_path = self.batch / "checks" / (scope["job_id"] + ".json")
        atomic(proof_path, {"case_id": case.id, "case_digest": case.digest,
            "fixture_report": artifact(self.batch, report_path), "checks": self.proofs.get(scope["job_id"], []),
            "process": self.process})
        result = asdict(ValidationCaseResult(case.id, case.digest, "passed", scope["job_id"], case.mode,
            digest(scope["binding"]), checks=dict.fromkeys(case.assertions, True),
            artifacts=[artifact(self.batch, report_path), artifact(self.batch, proof_path),
                *[artifact(self.batch, p) for p in sorted(destination.glob('*-precursor.json'))]]))
        result["environment_digest"] = scope["binding"]["environment_digest"]
        self.reports[scope["job_id"]] = report_path
        return result

    def record_result(self, case, result):
        from sap_business_agents_platform.runtime_validation import Authorization
        from sap_business_agents_platform import runtime_validation_qualification as qualification
        from sap_business_agents_platform.workbuddy_validation import register
        bound = self.manifest["case_bindings"][case.id]
        if result["status"] != "passed":
            self.manager.environment.mutate(lambda state: qualification.append(state, {
                "kind": "failure", "binding": bound, "environment_digest": self.manifest["runtime"]["environment_digest"],
                "scope": result.get("failure_scope") or "unknown", "job_id": result["job_id"],
                "cleanup_pending": result["status"] == "cleanup_pending", "failure_code": result.get("failure_code")}))
            return
        self.check_binding(self.manifest["runtime"])
        Authorization(**self.manifest['authorizations'][case.id]).check(case, self.manifest['runtime'],
            self.manifest['targets'][case.id], self.manifest['parameters'][case.id])
        report_path = self.batch / "checks" / (result["job_id"] + "-result.json")
        atomic(report_path, result)
        owned = [{"job_id": task, "digest": digest(read(self.manager.environment.root / "jobs" / (digest(task) + ".json")))}
            for task in self.jobs[result["job_id"]]]
        register(self.manager.environment, self.manifest["runtime"], case, bound, result,
            source_snapshot=self.manifest["source_snapshot"], artifact_root=self.batch,
            checker_report={"path": report_path.relative_to(self.batch).as_posix(), "digest": digest(result)},
            owned_jobs=owned, execution_kind="live")

    def recover_registration(self, case, result):
        from sap_business_agents_platform.runtime_validation_store import verify_artifacts
        self.check_binding(self.manifest['runtime'])
        verify_artifacts(self.batch, result['artifacts'])
        owned = []
        for path in (self.manager.environment.root / 'jobs').glob('*.json'):
            job = read(path)
            if job.get('validation_parent_job') == result['job_id']:
                if job.get('status') != 'completed' or job.get('cleanup_complete') is not True:
                    raise ValidationError('validation_recovery_unproven')
                owned.append(job['task_id'])
        if not owned:
            raise ValidationError('validation_recovery_unproven')
        self.jobs[result['job_id']] = owned
        self.record_result(case, result)
