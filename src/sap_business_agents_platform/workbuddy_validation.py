"""WorkBuddy bridge for shared validation. No SDK import or model discovery."""
from __future__ import annotations

from pathlib import Path
from contextvars import ContextVar

from .runtime_validation import Registry, ValidationError, digest
from . import runtime_validation_qualification as v2

_data_reads = ContextVar('validation_data_reads', default=None)


def case_bindings(snapshot, release, registry=None, *, case_ids=None):
    # One coherent inspection; never retain a filesystem result for the next
    # request/admission. ContextVar keeps concurrent callers independent.
    token = _data_reads.set({'inventories': {}, 'hashes': {}})
    try:
        return _case_bindings(snapshot, release, registry, case_ids=case_ids)
    finally:
        _data_reads.reset(token)


def _case_bindings(snapshot, release, registry=None, *, case_ids=None):
    from . import workbuddy_qualification as legacy
    registry = registry or Registry()
    # The private runner checks a frozen case closure, not every unrelated
    # business fixture. Keep the exact digest algorithm for each selected case
    # and the all-case default used by qualification/status callers.
    if isinstance(case_ids, (str, bytes)):
        raise ValidationError('validation_dependency_invalid')
    wanted = set(registry.entries if case_ids is None else case_ids)
    if wanted - registry.entries.keys():
        raise ValidationError('validation_dependency_invalid')
    manifests = legacy.dependency_manifests()
    execution = legacy.execution_components(release)["digest"]
    results = {}
    for case in registry.entries.values():
        if case.id not in wanted:
            continue
        components = {}
        for operation in case.operations:
            for name, value in manifests[operation]["components"].items():
                files = dict(value["files"])
                # Qualification/report code is a separately versioned checker,
                # not a business algorithm. Execution/auth/transport stay bound.
                for policy in ("workbuddy_qualification.py", "runtime_validation_qualification.py"):
                    files.pop(policy, None)
                components[name] = digest({"requires": value["requires"], "files": files})
        components['case_fixture'] = fixture_digest(case)
        components['case_data'] = data_digest(case)
        from .runtime_validation_checker_release import checker_digest
        components['case_checker'] = checker_digest()
        results[case.id] = v2.binding(snapshot, case, components, execution)
    return results


FIXTURES = {
    'author': ('workbuddy_live_cases/author.py',),
    'agent_explain': ('workbuddy_live_cases/agent_feedback.py',),
    'agent_modify': ('workbuddy_live_cases/agent_feedback.py',),
    'workflows': ('workbuddy_live_cases/workflows.py',),
    'workflow_v2': ('workbuddy_live_cases/workflow_v2.py',),
    'feedback': ('workbuddy_live_cases/free_feedback.py',),
    'roles': ('verify-runtime-role-matching.py',),
    'sample': ('workbuddy_live_cases/sample.py',),
    'acceptance': ('workbuddy_live_cases/acceptance.py', 'run_three_stage_acceptance.py', 'acceptance_source_anchors.py', 'direct_sap_read.py', 'direct_ar_adt_snapshot.py'),
    'limited_sample': ('verify-workbuddy-free-query.py', 'workbuddy_validation_cases.py'),
    'complete_unique_key': ('verify-workbuddy-free-query.py', 'workbuddy_validation_cases.py'),
    'saved_checks': (),
}


def fixture_digest(case, root=None):
    from .runtime_validation_inventory import sha
    root = root or Path(__file__).resolve().parents[2] / 'scripts'
    names = (*FIXTURES[case.fixture], 'runtime_validation_cases.py', 'runtime_validation_fixture_policy.py',
        'workbuddy_live_cases/live_guard.py', 'workbuddy_validation_auth.py')
    return digest({name: sha(root / name) for name in names})


def data_files(case, root=None):
    """Full public input inventory shared by hashing and archived-scope audit."""
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    if not case.operations:
        return []
    reads = _data_reads.get()
    key = (root, bool(case.sap))
    if reads is not None and key in reads['inventories']:
        return list(reads['inventories'][key])
    # Catalog matching and authoring can inspect the entire approved catalog.
    # Query/sample/acceptance fixtures depend on the selected fixed package and
    # connector/normalization contracts, not unrelated authoring documents.
    directory = root / 'agents'
    if case.sap:
        # Catalog reclassification may move the selected package to Common or
        # another module. Never let a missing hard-coded folder silently drop
        # its business definition from the qualification dependency digest.
        matches = [p for p in directory.glob('*/mm-po-gr-status') if (p / 'agent.json').is_file()]
        if len(matches) != 1:
            raise ValidationError('validation_fixture_agent_not_unique')
        directory = matches[0]
    paths = [p for p in directory.rglob('*') if p.is_file() and p.suffix in {'.json', '.py', '.md', '.yaml'} and '__pycache__' not in p.parts]
    paths += [root / 'config' / name for name in ('odata-services.json', 'skills.json', 'business-relationships.json', 'sap-value-normalization.json')]
    result = [p for p in paths if p.exists()]
    if reads is not None:
        reads['inventories'][key] = tuple(result)
    return result


def data_digest(case, root=None):
    """Exact public fixture inputs, not mutable production outputs or secrets."""
    from .runtime_validation_inventory import sha
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    reads = _data_reads.get()
    hashes = reads['hashes'] if reads is not None else {}
    values = {}
    for path in data_files(case, root):
        if path not in hashes:
            hashes[path] = sha(path)
        values[path.relative_to(root).as_posix()] = hashes[path]
    return digest(values)


def projection(environment, state, release, snapshot, operations):
    try:
        bindings = case_bindings(snapshot, release)
    except ValidationError as exc:
        if exc.code != 'validation_runtime_identity_missing':
            raise
        registry = Registry()
        return {operation: {'status':'unverified', 'implemented':True, 'permission_modes':[],
            'required_permission_modes':list(dict.fromkeys(c.mode for c in registry.requirements(operation))),
            'missing_cases':[c.id for c in registry.requirements(operation)], 'validation_origins':{},
            'environment_digest':release['environment_digest'], 'contract_version':'1.0',
            'qualification_version':v2.PROTOCOL, 'reason':exc.code} for operation in operations}
    values = v2.projection(environment, state, release, snapshot, bindings)
    return {op: values[op] for op in operations}


def assert_current(environment, target, snapshot, case):
    state = environment.state()
    release = environment.assert_verification_binding(snapshot, formal=case.layer == "acceptance")
    if not v2.active(state) or case_bindings(snapshot, release)[case.id] != target:
        raise ValidationError("validation_qualification_binding_changed")
    return release


def register(environment, snapshot, case, bound, result, **evidence):
    with environment.maintenance():
        assert_current(environment, bound, snapshot, case)
        return v2.register(environment, case, bound, result, **evidence)


def inspect_saved(environment, snapshot, *, formal=False):
    """Full existing checks, never display caches, login or model probes."""
    from .workbuddy_qualification import environment_check
    release = environment.assert_verification_binding(snapshot, formal=formal)
    receipt = environment_check(environment, environment.state(), release, snapshot)
    if not receipt:
        raise ValidationError("validation_target_startup_unproven")
    return release


def apply_receipt(root, receipt):
    from .workbuddy_manager import WorkBuddyManager
    manager = WorkBuddyManager(root)
    with manager.environment.maintenance():
        if manager._qualification_busy():
            raise ValidationError("validation_writers_not_idle")
        formal = Registry().entries[receipt.target_binding['case_id']].layer == 'acceptance'
        snapshot = manager.runtime_snapshot(receipt.target_binding["runtime"]["model"], require_enabled=False, formal=formal)
        release = inspect_saved(manager.environment, snapshot, formal=formal)
        if release["environment_digest"] != receipt.target_environment or case_bindings(snapshot, release)[receipt.target_binding["case_id"]] != receipt.target_binding:
            raise ValidationError("validation_qualification_binding_changed")
        return v2.apply_reuse(manager.environment, receipt, authorized=True)


def legacy_fixture_gate(manager, operations):
    """Selected operation scope replaces the unrelated all-13 fixture gate.

    Called only by the frozen shared runner. Old direct fixtures retain their
    previous admission until explicitly run through the new scoped entry.
    """
    from .runtime_validation import current_scope
    scope = current_scope()
    if scope is None:
        return False
    if not set(operations).issubset(scope["case"].operations):
        raise ValidationError("validation_native_scope_mismatch")
    inspect_saved(manager.environment, scope["binding"], formal=scope['case'].layer == 'acceptance')
    return True
