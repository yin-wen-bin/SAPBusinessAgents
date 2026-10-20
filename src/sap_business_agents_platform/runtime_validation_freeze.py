"""Read-only preparation of a scoped frozen manifest; no execution admission."""
from pathlib import Path

from .runtime_validation import Registry, ValidationError, digest
from .runtime_validation_inventory import sha
from .runtime_validation_store import capture, read, atomic


def _saved_authentication(root):
    """Load existing authentication only; never probe or reconcile a live login.

    This private WorkBuddy validation path does not change Codex admission.
    Reject active operations before loading the normal service, whose startup
    recovery could otherwise write to the shared authentication state.
    """
    from types import SimpleNamespace
    from .config import Settings
    from .runtime_authentication import ACTIVE, RuntimeAuthenticationService
    from .runtime_authentication_adapters import OfficialAuthenticationAdapter
    from .workbuddy_environment import WorkBuddyEnvironment
    path = Settings.from_env(Path(root)).data_root / 'sdk-runtimes/authentication.json'
    saved = read(path)
    if any(o.get('status') in ACTIVE for o in saved.get('operations', {}).values()):
        raise ValidationError('validation_authentication_operation_active')
    environment = WorkBuddyEnvironment(root)
    adapter = OfficialAuthenticationAdapter(Path(root), 'workbuddy', SimpleNamespace(environment=environment))
    service = RuntimeAuthenticationService(path.parent, {'workbuddy': adapter})
    account = service.state.get('accounts', {}).get('workbuddy', {})
    if (service.invalid_state or account.get('status') != 'authenticated'
            or account.get('authenticated') is not True or account.get('invalidation_pending')):
        raise ValidationError('validation_authentication_proof_required')
    return service, account, environment.state()


def _check_authentication_revision(snapshot, account, state):
    revision = snapshot.get('authentication_revision')
    if (not isinstance(revision, int) or isinstance(revision, bool)
            or revision != account['revision'] or revision != state.get('authentication_revision')):
        raise ValidationError('validation_authentication_revision_changed')


def bind_verified_snapshot(root, snapshot, *, harness=False):
    """Explicitly bind a NEW request before creating its authorization digest."""
    from .models import RuntimeSnapshot
    if snapshot.get('provider_id') != 'workbuddy':
        raise ValidationError('validation_provider_live_adapter_unavailable')
    service, account, state = _saved_authentication(root)
    _check_authentication_revision(snapshot, account, state)
    # Refuse stale supplied bindings/sites; never replace them with defaults.
    provisional = dict(snapshot)
    if provisional.get('authentication_binding_revision') is None:
        provisional['authentication_binding_revision'] = account['revision']
    service.assert_binding('workbuddy', provisional)
    if harness:
        from .runtime_policy import ORCHESTRATION_VERSION
        fields = {'execution_flow': 'harness', 'orchestration_version': ORCHESTRATION_VERSION}
        if any(provisional.get(k) not in {None, value} for k, value in fields.items()):
            raise ValidationError('validation_runtime_flow_binding_changed')
        provisional.update(fields)
    bound = RuntimeSnapshot.model_validate(service.bind('workbuddy', provisional)).model_dump(mode='json')
    service.assert_binding('workbuddy', bound)
    return bound


def require_harness_binding(snapshot):
    """Scheduler annotations must already be present in a frozen Harness request."""
    from .runtime_policy import ORCHESTRATION_VERSION
    if snapshot.get('execution_flow') != 'harness' or snapshot.get('orchestration_version') != ORCHESTRATION_VERSION:
        raise ValidationError('validation_runtime_flow_binding_required')


def require_authentication_binding(root, snapshot):
    """Strict frozen-request preflight, using the execution service's assertion.

    Missing fields remain errors. This function neither binds nor repairs an
    existing request and does not perform a login/model/SAP call.
    """
    if snapshot.get('provider_id') != 'workbuddy':
        raise ValidationError('validation_provider_live_adapter_unavailable')
    service, account, state = _saved_authentication(root)
    _check_authentication_revision(snapshot, account, state)
    service.assert_binding('workbuddy', snapshot)


def require_typed_snapshot(value):
    """Callers freeze the same projection consumed by persisted runtime runs.

    Never silently add defaults after the authorization fingerprint is issued.
    """
    from .models import RuntimeSnapshot
    try:
        normalized = RuntimeSnapshot.model_validate(value).model_dump(mode='json')
    except ValueError:
        raise ValidationError('validation_runtime_projection_required') from None
    if normalized != value:
        raise ValidationError('validation_runtime_projection_required')


def source_files(root, registry, selected):
    from .workbuddy_validation import FIXTURES
    root = Path(root)
    # Python imports are dynamic. Archive the package for reliable imports;
    # qualification still uses the narrower explicit operation dependency map.
    # Unrelated source bytes never enter an operation's dependency digest.
    files = {p.relative_to(root).as_posix() for p in (root / 'src').rglob('*')
        if p.is_file() and p.suffix in {'.py', '.json', '.md', '.txt'} and '__pycache__' not in p.parts}
    helpers = ('validate-ai-runtime.py', 'runtime_validation_cases.py', 'runtime_validation_fixture_policy.py',
        'runtime_validation_delegate.py',
        'verify-workbuddy-operations.py', 'workbuddy_validation_auth.py', 'workbuddy_validation_diagnostics.py',
        'workbuddy_validation_cases.py', 'workbuddy_live_cases/live_guard.py')
    files.update('scripts/' + f for f in helpers)
    for key in registry.closure(selected):
        files.update('scripts/' + f for f in FIXTURES[registry.entries[key].fixture])
    # Status projection may inspect other case digests, without executing them.
    files.update('scripts/' + f for values in FIXTURES.values() for f in values)
    # Public fixed definitions/ports are business inputs, not production answers.
    for directory in ('agents', 'config'):
        for p in (root / directory).rglob('*'):
            if p.is_file() and p.suffix in {'.py', '.json', '.md', '.yaml', '.yml'}:
                files.add(p.relative_to(root).as_posix())
    return sorted(files)


def prepare(root, batch, manifest):
    from .workbuddy_environment import WorkBuddyEnvironment
    from .workbuddy_validation import case_bindings
    root, batch = Path(root), Path(batch)
    registry = Registry()
    if manifest['runtime'].get('provider_id') != 'workbuddy':
        raise ValidationError('validation_provider_live_adapter_unavailable')
    require_typed_snapshot(manifest['runtime'])
    require_authentication_binding(root, manifest['runtime'])
    selected = registry.closure(manifest['selected'])
    if any(registry.entries[k].layer == 'acceptance' or registry.entries[k].fixture in {
            'limited_sample', 'complete_unique_key'} for k in selected):
        require_harness_binding(manifest['runtime'])
    environment = WorkBuddyEnvironment(root)
    release = environment.release(manifest['runtime']['environment_digest'])
    bindings = case_bindings(manifest['runtime'], release, registry, case_ids=selected)
    frozen = {**manifest, 'case_bindings': {k: bindings[k] for k in selected}}
    controls = ['pyproject.toml', 'config/sdks.json', '.local-data/sdk-runtimes/default.json',
        '.local-data/sdk-runtimes/authentication.json']
    frozen['controls'] = {p: sha(root / p) for p in controls if (root / p).exists()}
    account = read(root / '.local-data/sdk-runtimes/authentication.json')['accounts']['workbuddy']
    frozen['authentication_account_digest'] = digest(account)
    frozen['source_files'] = source_files(root, registry, selected)
    frozen['source_snapshot'] = capture(root, frozen['source_files'], batch.parent / 'archives')
    frozen['manifest_digest'] = digest(frozen)
    atomic(batch / 'manifest.json', frozen)
    return frozen


def verify_manifest(manifest):
    if manifest.get('manifest_digest') != digest({k: v for k, v in manifest.items() if k != 'manifest_digest'}):
        raise ValidationError('validation_manifest_changed')
