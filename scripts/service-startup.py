"""Version-aware local launcher. No SDK/model/SAP calls or qualification writes."""
from __future__ import annotations

import argparse
import concurrent.futures
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
import socket
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("startup_control", ROOT / "src/sap_business_agents_platform/startup_control.py")
control = importlib.util.module_from_spec(spec); spec.loader.exec_module(control)
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# Read-only validators referenced relative to the loaded platform package.
# Business data, mutable catalogs, credentials and SDK installations stay at
# their explicit existing locations; only these reviewed code resources freeze.
SOURCE_RESOURCES = (
    'config/sap-source-semantics.json',
    'config/runtime-evidence-bindings.json',
    'config/agent-presentation-contract.json',
    'config/month-end-closing-profiles.schema.json',
    'agents/FI/month-end-closing/config/month_end_checklist.toml',
    'site/scripts/agent-presentation.mjs',
    'site/scripts/inspect-agent-presentation.mjs',
    'site/scripts/generate-agent-catalog.mjs',
    'site/scripts/validate-agent-package.mjs',
)


def settings_for(root):
    # Importing the package solely for Settings eagerly imports the complete
    # FastAPI app via __init__.py. Load the same pure configuration module
    # directly instead; from_env and all its checks remain unchanged.
    filename = root / 'src/sap_business_agents_platform/config.py'
    if not filename.exists(): filename = ROOT / 'src/sap_business_agents_platform/config.py'
    module_name = '_sapba_startup_settings'
    definition = importlib.util.spec_from_file_location(module_name, filename)
    module = importlib.util.module_from_spec(definition)
    sys.modules[module_name] = module
    definition.loader.exec_module(module)
    return module.Settings.from_env(root)


def walk_files(directory, *, prune_names=(), prune_paths=()):
    """Prune excluded trees before enumeration, not after walking history."""
    excluded = set(prune_paths)
    for parent, folders, names in os.walk(directory, followlinks=False):
        base = Path(parent)
        folders[:] = [name for name in folders if name not in prune_names and base / name not in excluded]
        for name in names:
            yield base / name


def source_manifest(root):
    files = {}
    for directory in ("src", "scripts"):
        for path in walk_files(root / directory, prune_names={'__pycache__'}):
            if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts:
                if path.is_symlink():
                    raise control.StartupBlocked("startup_source_link_rejected")
                files[path.relative_to(root).as_posix()] = path
    for name in ("tests/test_workbuddy_isolation.py", "tests/test_workbuddy_three_layers.py",
                 "tests/workbuddy_integration_fakes.py", "tests/fixtures/workbuddy_protocol_worker.py", "pyproject.toml"):
        if (root / name).exists(): files[name] = root / name
    for name in SOURCE_RESOURCES:
        if (root / name).exists(): files[name] = root / name
    if 'src/sap_business_agents_platform/app.py' in files and not set(SOURCE_RESOURCES) <= files.keys():
        raise control.StartupBlocked('startup_runtime_resource_missing')
    hashes = hash_files(files.values())
    files = {name: hashes[path] for name, path in files.items()}
    dependencies = {}
    for name in ("pyproject.toml", "requirements.lock", "uv.lock", ".venv/pyvenv.cfg"):
        if (root / name).exists(): dependencies[name] = control.sha(root / name)
    for path in (root / ".venv/Lib/site-packages").glob("*.dist-info/*"):
        if path.name in {"METADATA", "RECORD"}:
            dependencies[path.relative_to(root).as_posix()] = control.sha(path)
    dependencies["interpreter"] = {"path": sys.executable, "sha256": control.sha(sys.executable), "version": sys.version,
        "base_sha256": control.sha(getattr(sys, '_base_executable', sys.executable))}
    # Bind launch-time configuration without copying any credential bytes.
    from dataclasses import asdict
    settings = settings_for(root)
    dependencies['startup_settings_sha256'] = control.digest({key: str(value) for key, value in asdict(settings).items()})
    for path in (root / '.env', settings.sap_env_file):
        if path and path.is_file():
            dependencies.setdefault('configuration_sha256', []).append(control.sha(path))
    body = {"version": 1, "files": files, "dependencies": dependencies}
    return {**body, "digest": control.digest(body)}


def manifest_changes(expected, actual):
    """Safe diagnostics: names only, never configuration or credential values."""
    return {section: sorted(name for name in set(expected.get(section, {})) | set(actual.get(section, {}))
                            if expected.get(section, {}).get(name) != actual.get(section, {}).get(name))
            for section in ('files', 'dependencies')}


def hash_files(paths):
    # Hash every byte at each consistency boundary. Bounded I/O concurrency,
    # not timestamps or a persistent content/authorization cache.
    paths = list(dict.fromkeys(paths))
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(1, len(paths)))) as pool:
        return dict(zip(paths, pool.map(control.sha, paths)))


def approved_target(root, requested):
    """Restore only the recorded deployed pair, not an arbitrary old build."""
    data = root/'.local-data'
    try:
        record = json.loads((data/'startup-api-current.json').read_text(encoding='utf-8-sig'))
        site = json.loads((data/'site-builds/current.json').read_text(encoding='utf-8-sig'))
        key = record['source_digest']; fingerprint = site['fingerprint']
        if not re.fullmatch('[0-9a-f]{64}', key) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
            raise ValueError('invalid pointer')
        archive = data/'startup-releases'/key
        dist = data/'site-builds'/fingerprint/'dist'
        if Path(record['archive']).resolve() != archive.resolve() or Path(site['dist_path']).resolve() != dist.resolve() or site.get('mode', 'preview') != 'preview':
            raise ValueError('invalid pointer')
        manifest = json.loads((archive/'manifest.json').read_text(encoding='utf-8-sig'))
        verify_archive(archive, manifest)
        if manifest['digest'] != key or manifest.get('dependencies') != requested.get('dependencies'):
            raise ValueError('runtime dependencies changed')
        metadata = json.loads((dist/'sapba-build.json').read_text(encoding='utf-8-sig'))
        if metadata.get('fingerprint') != fingerprint or not (dist/'zh/index.html').is_file() or not (dist/'index.html').is_file():
            raise ValueError('invalid site build')
        return {'archive': archive, 'manifest': manifest, 'site_fingerprint': fingerprint}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise control.StartupBlocked('startup_approved_release_unavailable') from exc


def deferral_key(root, manifest, site_fingerprint):
    path = root/'.local-data/runtimes/workbuddy/state.json'
    return {'requested_source_digest': manifest['digest'], 'requested_site_fingerprint': site_fingerprint,
            'sdk_state_sha256': control.sha(path) if path.exists() else None}


def freeze(root, manifest):
    with control.file_lock(root / '.local-data/startup-releases/release.lock', wait=60):
        return _freeze_owned(root, manifest)


def _freeze_owned(root, manifest):
    target = root / ".local-data/startup-releases" / manifest["digest"]
    if not (target / "manifest.json").exists():
        # Reuse the reviewed archive validator (links, sensitive JSON, concurrent
        # edits and exact inventory); do not invent a weaker snapshot policy.
        sys.path.insert(0, str(ROOT / 'src'))
        from sap_business_agents_platform.runtime_validation_store import capture, relative_file
        native = {name for name in manifest['files'] if Path(name).suffix == '.mjs'}
        if not native <= set(SOURCE_RESOURCES):
            raise control.StartupBlocked('startup_native_resource_not_reviewed')
        # The existing validation archive deliberately does not accept JS.
        # Keep its policy unchanged and bind the exact reviewed native files
        # with the same path, concurrent-content and inventory checks below.
        captured = capture(root, set(manifest['files']) - native, root / '.local-data/startup-source-archives')
        captured_root = Path(captured['directory']) / 'source'
        temporary = target.with_name(target.name + ".tmp-" + uuid.uuid4().hex)
        for name, expected in manifest["files"].items():
            source = root / name
            data = (relative_file(root, name) if name in native else captured_root / name).read_bytes()
            if source.is_symlink(): raise control.StartupBlocked('startup_source_link_rejected')
            if control.sha(source) != expected or __import__('hashlib').sha256(data).hexdigest() != expected:
                raise control.StartupBlocked("startup_source_changed")
            destination = temporary / "source" / name
            destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(data)
        control.atomic(temporary / "manifest.json", manifest)
        verify_archive(temporary, manifest)
        publish_archive(temporary, target)
    verify_archive(target, manifest)
    return target


def publish_archive(temporary, target):
    """Publish once; tolerate brief Windows handles, never overwrite."""
    delays = iter((.1, .2, .4, .8))
    while True:
        if target.exists() or target.is_symlink():
            raise control.StartupBlocked('startup_archive_target_conflict')
        try:
            temporary.rename(target)
            return
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in {5, 32, 33}:
                raise
            delay = next(delays, None)
            if delay is None:
                raise control.StartupBlocked('startup_archive_publish_busy') from exc
            time.sleep(delay)


def verify_archive(target, manifest):
    stored = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    if stored != manifest or control.digest({k: v for k, v in stored.items() if k != "digest"}) != manifest["digest"]:
        raise control.StartupBlocked("startup_archive_manifest_changed")
    actual = {p.relative_to(target / "source").as_posix() for p in (target / "source").rglob("*") if p.is_file()}
    if actual != set(manifest["files"]) or any(control.sha(target / "source" / name) != value for name, value in manifest["files"].items()):
        raise control.StartupBlocked("startup_archive_changed")


def request(url, timeout=2):
    with LOCAL_HTTP.open(url, timeout=max(.05, timeout)) as response:
        return json.loads(response.read())


def listeners():
    result = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/netstat.exe"), "-ano", "-p", "TCP"], capture_output=True, text=True, check=True)
    return {int(parts[1].rsplit(":", 1)[1]): int(parts[-1]) for line in result.stdout.splitlines() if len(parts := line.split()) == 5 and parts[0] == "TCP" and parts[3] == "LISTENING"}


def project_service_command(command, root):
    tokens = [(quoted or bare).replace('\\', '/').casefold() for quoted, bare in re.findall(r'"([^\"]*)"|(\S+)', command or '')]
    prefix = str(root).replace('\\', '/').casefold().rstrip('/') + '/'
    for token in tokens:
        if token.startswith(prefix) and (token.endswith('/astro/bin/astro.mjs') or token.endswith('/service-startup.py') or token.rsplit('/',1)[-1].startswith('start-api') and token.endswith('.py')):
            return True
    return bool(tokens and tokens[0].startswith(prefix) and tokens[0].rsplit('/',1)[-1] in {'python.exe','pythonw.exe'}
                and any(tokens[i] == '-m' and tokens[i+1] == 'sap_business_agents_platform.cli' for i in range(len(tokens)-1)))


def identity(pid, root):
    command = f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'; if($p){{$p|Select-Object ProcessId,CreationDate,CommandLine|ConvertTo-Json -Compress}}"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True, check=True)
    value = json.loads(result.stdout) if result.stdout.strip() else None
    if not value or not project_service_command(value.get('CommandLine'), root):
        raise control.StartupBlocked("startup_foreign_or_unknown_process")
    return value


def stop_owned(saved, root):
    if identity(saved["ProcessId"], root) != saved:
        raise control.StartupBlocked("startup_process_identity_changed")
    subprocess.run(["taskkill.exe", "/PID", str(saved["ProcessId"]), "/T", "/F"], capture_output=True, check=True)


def assert_writers(root, server_pids):
    command = "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CreationDate,Name,CommandLine | ConvertTo-Json -Compress"
    rows = json.loads(subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", command], text=True))
    by_id = {row['ProcessId']: row for row in rows}
    allowed = {os.getpid(), *server_pids}
    for pid in list(allowed):
        visited = set()
        while pid in by_id and by_id[pid].get('ParentProcessId') and pid not in {0,4}:
            if pid in visited: raise control.StartupBlocked('startup_process_ancestry_unknown')
            visited.add(pid)
            parent = by_id[pid]['ParentProcessId']
            if parent not in by_id: break
            child_birth = re.search(r'Date\((\d+)', str(by_id[pid].get('CreationDate')))
            parent_birth = re.search(r'Date\((\d+)', str(by_id[parent].get('CreationDate')))
            # A reused parent PID is not an ancestor and cannot exempt a writer.
            if not child_birth or not parent_birth or int(parent_birth[1]) > int(child_birth[1]): break
            pid = parent; allowed.add(pid)
    for row in rows:
        if row.get('ParentProcessId') == os.getpid(): allowed.add(row['ProcessId'])
    unknown = [row['ProcessId'] for row in rows if row['ProcessId'] not in allowed
               and row.get('Name', '').lower() in {'python.exe','pythonw.exe','node.exe','powershell.exe','pwsh.exe'}
               and re.search(re.escape(str(root).replace('\\','/').rstrip('/')) + r"(?=[/\s\"']|$)", (row.get('CommandLine') or '').replace('\\','/'), re.I)]
    if unknown:
        raise control.StartupBlocked('startup_other_writers_active:' + ','.join(map(str, unknown)))


def jobs_pending(root):
    # Reuse the existing strict journal reader; never reconcile or write it.
    sys.path.insert(0, str(root / "src"))
    from sap_business_agents_platform.workbuddy_job_index import enabled, paths
    journal = root / ".local-data/runtimes/workbuddy"
    if enabled(journal):
        value = paths(journal)
        return bool(value)
    # Unknown old journal protocols cannot be treated as empty.
    return any((journal / "jobs").glob("*.json"))


def launch_api(root, archive, port, log):
    args = [sys.executable, "-I", "-B", str(archive / "source/scripts/service-startup.py"), "serve", "--root", str(root), "--port", str(port), "--archive", str(archive)]
    return spawn(args, root, log / "platform")


def spawn(args, cwd, log):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.with_suffix(".stdout.log").open("ab") as stdout, log.with_suffix(".stderr.log").open("ab") as stderr:
        # Local preview must use the same root URL as the frozen local build;
        # Astro otherwise falls back to the public GitHub Pages base path.
        child_environment = {**os.environ, 'PUBLIC_SITE_BASE': '/'}
        return subprocess.Popen(args, cwd=cwd, stdout=stdout, stderr=stderr, env=child_environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def verify_loaded_sources(archive, manifest):
    """Managed packages must come from listed bytes in this exact release."""
    source = (archive / 'source').resolve()
    for name, module in tuple(sys.modules.items()):
        if name == 'sap_business_agents_platform' or name.startswith('sap_business_agents_platform.'):
            parent = source / 'src'
        elif name == 'scripts' or name.startswith('scripts.'):
            parent = source
        else:
            continue
        filename = getattr(module, '__file__', None)
        if filename:
            path = Path(filename).resolve()
            if not path.is_relative_to(parent / name.split('.')[0]):
                raise control.StartupBlocked('startup_import_source_mismatch')
            relative = path.relative_to(source).as_posix()
            if manifest['files'].get(relative) != control.sha(path):
                raise control.StartupBlocked('startup_import_source_mismatch')
        locations = getattr(module, '__path__', None)
        if locations is not None and {Path(location).resolve() for location in locations} != {parent.joinpath(*name.split('.')).resolve()}:
            raise control.StartupBlocked('startup_import_source_mismatch')


def bind_frozen_imports(archive, manifest):
    """Expose only the archived scripts namespace, never the mutable checkout."""
    verify_archive(archive, manifest)
    verify_loaded_sources(archive, manifest)
    source = (archive / 'source').resolve()
    sys.path.insert(0, str(source / 'src'))
    if 'scripts' not in sys.modules:
        specification = importlib.machinery.PathFinder.find_spec('scripts', [str(source)])
        if specification is None:
            raise control.StartupBlocked('startup_campaign_dependency_missing')
        specification.submodule_search_locations = [str(source / 'scripts')]
        # Current scripts is a namespace. If it becomes a regular package,
        # its initializer must also be part of the frozen inventory.
        if specification.origin and manifest['files'].get('scripts/__init__.py') != control.sha(specification.origin):
            raise control.StartupBlocked('startup_import_source_mismatch')
        namespace = importlib.util.module_from_spec(specification)
        sys.modules['scripts'] = namespace
        if specification.loader is not None:
            specification.loader.exec_module(namespace)
        namespace.__path__ = [str(source / 'scripts')]
    # Load the real Campaign handoff before the API can be declared ready.
    # Importing this module does not execute its guarded CLI main function.
    helper = __import__('scripts.run_three_stage_acceptance', fromlist=['*'])
    for name in ('_acceptance_prompt', '_compare', '_normalize_acceptance_projection',
                 '_normalize_run', '_projection_spec', '_validate_projection_matches_visible_report'):
        if not callable(getattr(helper, name, None)):
            raise control.StartupBlocked('startup_campaign_dependency_missing')
    verify_loaded_sources(archive, manifest)


def serve(args):
    manifest = json.loads((args.archive / "manifest.json").read_text())
    bind_frozen_imports(args.archive, manifest)
    from sap_business_agents_platform import startup_control
    from sap_business_agents_platform.cli import configure_internal_api_url
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.app import create_app
    configure_internal_api_url("127.0.0.1", args.port)
    verify_loaded_sources(args.archive, manifest)
    startup_control._binding = {"verified": True, "source_digest": manifest["digest"], "boot_id": uuid.uuid4().hex, "pid": os.getpid(),
        "required_imports": {'scripts.run_three_stage_acceptance': manifest['files']['scripts/run_three_stage_acceptance.py']}}
    import uvicorn
    settings = Settings.from_env(args.root)
    constructed = time.monotonic()
    application = create_app(settings)
    startup_control._binding["phases_ms"] = {"application_construction": round((time.monotonic()-constructed)*1000, 2)}
    startup_control.install_barrier(application, settings)
    # A rollback may lack the additive health field. Only verified bootstrap
    # metadata is added; original business code and checks are unchanged.
    for route in application.routes:
        if getattr(route, "path", None) == "/api/health":
            original = route.dependant.call
            async def health_with_binding():
                return {**(await original()), "startup_binding": startup_control.startup_projection()}
            route.dependant.call = health_with_binding
            break
    uvicorn.run(application, host="127.0.0.1", port=args.port)


def _ensure(args):
    root = args.root.resolve(); data = root / ".local-data"
    activity_root = settings_for(root).data_root
    log = args.attempt
    began = args.started; phases = args.phases
    def phase(name, action):
        args.current_phase = name
        start = time.monotonic()
        try: return action()
        finally:
            phases[name] = round((time.monotonic() - start) * 1000, 2)
            print(f"[SAPBusinessAgents] {name}: {phases[name]} ms", flush=True)
    manifest = phase("source_and_dependency_fingerprint", lambda: source_manifest(root))
    requested_manifest = manifest
    url = f"http://127.0.0.1:{args.port}"
    existing = None
    try: existing = request(url + "/api/health")
    except (OSError, ValueError): pass
    same = bool(existing and valid_health(existing) and valid_binding(existing, manifest['digest']))
    changed = args.restart or not same
    # Always inspect frontend source content; a healthy old page is not current.
    fingerprint = phase("site_fingerprint", lambda: site_fingerprint(root, args.site_port, args.port))
    pointer_path = data / "site-builds/current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8-sig")) if pointer_path.exists() else {}
    requested_fingerprint = fingerprint
    deferred = None; archive = None
    deferred_path = data/'startup/deferred-update.json'
    key = deferral_key(root, manifest, fingerprint)
    # A negative cache only defers an unapproved candidate. It cannot certify
    # any release; cold starts still audit the deployed target under the barrier.
    if not args.dev and deferred_path.exists():
        try:
            saved = json.loads(deferred_path.read_text(encoding='utf-8-sig'))
            if saved.get('key') == key:
                candidate = approved_target(root, requested_manifest)
                if saved.get('approved_source_digest') == candidate['manifest']['digest']:
                    deferred = candidate
        except (OSError, ValueError): pass
    if changed and deferred is None and not args.reuse_only:
        archive = phase('freeze_source', lambda: freeze(root, manifest))
        try:
            phase('admission_preflight', lambda: check_admission(root, archive))
        except control.StartupBlocked as exc:
            if str(exc) != 'workbuddy_sdk_integration_required' or args.dev:
                raise
            deferred = approved_target(root, requested_manifest)
            control.atomic(deferred_path, {'key': key, 'approved_source_digest': deferred['manifest']['digest'],
                'reason': str(exc)})
    if deferred is not None:
        archive = deferred['archive']; manifest = deferred['manifest']; fingerprint = deferred['site_fingerprint']
        same = bool(existing and valid_health(existing) and valid_binding(existing, manifest['digest']))
        changed = args.restart or not same
        print('[SAPBusinessAgents] Workspace update deferred: SDK integration evidence is missing. Using the verified deployed release; pending workspace changes are NOT loaded.', flush=True)
    ports = listeners()
    try: actual_site = request(f"http://127.0.0.1:{args.site_port}/sapba-build.json", timeout=1)
    except (OSError, ValueError): actual_site = {}
    site_changed = args.restart or args.rebuild or pointer.get("fingerprint") != fingerprint or args.site_port not in ports or pointer.get('mode', 'preview') != ('dev' if args.dev else 'preview') or (not args.dev and actual_site.get("fingerprint") != fingerprint)
    if args.reuse_only and (changed or site_changed):
        raise control.StartupBlocked('startup_reuse_not_proven_no_update_performed')
    if not changed and not site_changed:
        phase("ready", lambda: None)
    else:
        if site_changed:
            prepare = ["node.exe", "scripts/generate-agent-catalog.mjs"] if args.dev else ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts/Start-SAPBusinessAgents.ps1"), "-PrepareSiteOnly", "-ApiPort", str(args.port), *(["-RebuildSite"] if args.rebuild else [])]
            if deferred is None:
                phase("prepare_site", lambda: subprocess.run(prepare, cwd=root / 'site' if args.dev else root, check=True))
            phase("site_candidate_ready", lambda: probe_site(root, fingerprint, args, log))
        # Never hold the deployment lock while waiting for business tasks.
        while True:
            if control.activity(activity_root) or jobs_pending(root):
                print("[SAPBusinessAgents] Update pending: waiting for active tasks/cleanup; Ctrl+C leaves tasks running.", flush=True)
                time.sleep(5); continue
            try:
                with control.file_lock(activity_root / "deployment.lock", wait=60), control.file_lock(activity_root / "startup-admission.lock"):
                    if control.activity(activity_root) or jobs_pending(root): continue
                    current_manifest = phase('source_consistency', lambda: source_manifest(root))
                    if current_manifest != requested_manifest:
                        failure = control.StartupBlocked("startup_target_changed_prepare_again")
                        failure.changes = manifest_changes(requested_manifest, current_manifest)
                        raise failure
                    args.current_phase = 'site_consistency'
                    if site_fingerprint(root, args.site_port, args.port) != requested_fingerprint:
                        raise control.StartupBlocked("startup_site_source_changed_prepare_again")
                    current = listeners()
                    if any(current.get(port) != ports.get(port) for port in (args.port, args.site_port)):
                        latest = request(url + '/api/health')
                        served = request(f'http://127.0.0.1:{args.site_port}/sapba-build.json') if not args.dev else {}
                        if valid_health(latest) and valid_binding(latest, manifest['digest']) and served.get('fingerprint') == fingerprint:
                            # A concurrent launcher already completed this exact
                            # update. Never restart the new processes again.
                            changed = False; site_changed = False
                            break
                        raise control.StartupBlocked("startup_listener_changed")
                    owned = {port: identity(current[port], root) for port in (args.port, args.site_port) if port in current}
                    assert_writers(root, list(current.get(port) for port in (args.port, args.site_port) if current.get(port)))
                    if existing and not existing.get("startup_binding", {}).get("verified"):
                        # Legacy writers do not honor the new barrier. Only migrate
                        # them after their owned activity and journals are empty.
                        print("[SAPBusinessAgents] Migrating an idle legacy API with unknown version binding.", flush=True)
                    marker = activity_root / "startup-maintenance.json"
                    if marker.exists(): raise control.StartupBlocked("startup_orphaned_maintenance_requires_review")
                    control.atomic(marker, {"pid": os.getpid(), "source_digest": manifest["digest"]})
                    remove_marker = True
                    try:
                        previous_path = data / 'startup-api-current.json'
                        if not previous_path.exists(): previous_path = data / 'startup-api-legacy-rollback.json'
                        previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
                        process = None
                        if changed:
                            if existing and not previous:
                                raise control.StartupBlocked("startup_rollback_binding_missing")
                            # SDK admission evidence is reviewed before touching the old service.
                            phase("admission_evidence", lambda: check_admission(root, archive))
                        # Recheck after the potentially slow evidence audit too,
                        # especially when migrating a legacy API without this
                        # barrier. Never stop a task accepted during that audit.
                        if control.activity(activity_root) or jobs_pending(root):
                            raise control.StartupBlocked('startup_became_busy_retry')
                        ready_deadline = time.monotonic() + args.timeout
                        site_applied = False
                        api_touched = False
                        try:
                            if changed:
                                remove_marker = False
                                if args.port in owned: stop_owned(owned[args.port], root)
                                api_touched = True
                                process = launch_api(root, archive, args.port, log)
                            if site_changed:
                                remove_marker = False
                                # The API warms concurrently with frontend startup.
                                phase("site_switch", lambda: switch_site(root, fingerprint, args, owned.get(args.site_port), pointer, log))
                                site_applied = True
                            if changed:
                                phase("api_ready", lambda: wait_api(url, manifest["digest"], process, max(.1, ready_deadline-time.monotonic())))
                        except BaseException as primary:
                            rollback_errors = []
                            if site_applied and pointer.get('dist_path'):
                                try:
                                    latest_site = listeners().get(args.site_port)
                                    rollback_args = argparse.Namespace(**{**vars(args), 'dev': pointer.get('mode') == 'dev'})
                                    switch_site(root, pointer['fingerprint'], rollback_args, identity(latest_site, root) if latest_site else None, {}, log / 'rollback')
                                except BaseException as failure: rollback_errors.append('site:' + type(failure).__name__)
                            if api_touched:
                                try:
                                    if process is not None and process.poll() is None:
                                        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=True); process.wait(timeout=10)
                                    if previous:
                                        rollback = launch_api(root, Path(previous["archive"]), args.port, log / "rollback")
                                        wait_api(url, previous["source_digest"], rollback, args.timeout)
                                except BaseException as failure: rollback_errors.append('api:' + type(failure).__name__)
                            if rollback_errors:
                                error = control.StartupBlocked('startup_rollback_incomplete'); error.rollback_errors = rollback_errors
                                raise error from primary
                            remove_marker = True
                            raise
                        if changed:
                            if previous: control.atomic(data / "startup-api-previous.json", previous)
                            control.atomic(data / "startup-api-current.json", {"archive": str(archive), "source_digest": manifest["digest"], "pid": process.pid})
                        remove_marker = True
                    finally:
                        if remove_marker: marker.unlink(missing_ok=True)
                break
            except control.StartupBlocked as exc:
                if str(exc).startswith('startup_other_writers_active:'):
                    print('[SAPBusinessAgents] Waiting for other project processes to exit; no process was stopped. Ctrl+C cancels only this launch.', flush=True)
                elif str(exc) not in {"startup_coordination_busy", 'startup_became_busy_retry'}: raise
                time.sleep(5)
    # The warm path already verified health/build bindings and enumerated the
    # actual listeners. Reuse that one inventory; updates require a fresh one.
    current_ports = listeners() if changed or site_changed else ports
    state = {"status": "completed", "total_duration_ms": round((time.monotonic()-began)*1000, 2), "phases": phases, "source_digest": manifest["digest"], "site_fingerprint": fingerprint, "api_reused": not changed,
             "services": {"api": {"pid": current_ports.get(args.port), "healthy": True, "url": url}, "site": {"pid": current_ports.get(args.site_port), "healthy": True}},
             "site_cache": {"fingerprint": fingerprint, "reused": not site_changed, "build_duration_ms": phases.get("prepare_site", 0)}}
    state.update(update_deferred=deferred is not None, current_changes_loaded=deferred is None,
                 requested_source_digest=requested_manifest['digest'], requested_site_fingerprint=requested_fingerprint)
    if source_manifest(root) != requested_manifest or site_fingerprint(root, args.site_port, args.port) != requested_fingerprint:
        raise control.StartupBlocked("startup_changed_during_launch_not_current")
    control.atomic(log / "result.json", state)
    control.atomic(data / "startup/last-attempt.json", state)
    control.atomic(data / "startup/latest.json", state)
    if deferred is None:
        deferred_path.unlink(missing_ok=True)
    print('[SAPBusinessAgents] Ready; verified deployed release is available. Workspace update remains pending.' if deferred else '[SAPBusinessAgents] Ready; current changes are verified as loaded.', flush=True)
    if not args.no_browser:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{args.site_port}/zh/")
    return state


def ensure(args):
    args.started = time.monotonic(); args.phases = {}; args.current_phase = "preflight"
    args.attempt = args.root / ".local-data/startup" / (time.strftime("%Y%m%dT%H%M%S") + "-versioned-" + str(os.getpid()))
    args.attempt.mkdir(parents=True)
    args.preparation_attempts = []
    try:
        for attempt in range(2):
            try:
                return _ensure(args)
            except control.StartupBlocked as exc:
                # Both checks precede the maintenance marker and any stop/switch.
                # Unwind both locks, then freeze and validate a fresh target.
                if str(exc) not in {'startup_target_changed_prepare_again', 'startup_site_source_changed_prepare_again'}:
                    raise
                args.preparation_attempts.append({'phase':args.current_phase,'error':str(exc),
                    'changes':getattr(exc,'changes',{}),'phases':dict(args.phases)})
                control.atomic(args.attempt/'preparation-attempts.json',args.preparation_attempts)
                if attempt:
                    raise
                print('[SAPBusinessAgents] Files changed while preparing; preparing once more. Existing services remain untouched.',flush=True)
                args.phases = {}
    except BaseException as exc:
        state = {"status": "failed", "phase": args.current_phase, "phases": args.phases,
                 "total_duration_ms": round((time.monotonic()-args.started)*1000, 2),
                 "error": str(exc) if isinstance(exc, control.StartupBlocked) else type(exc).__name__,
                 "rollback_errors": getattr(exc, 'rollback_errors', []),
                 "original_error": type(exc.__cause__).__name__ if exc.__cause__ else None,
                 "changes":getattr(exc,'changes',{}),"preparation_attempts":args.preparation_attempts}
        control.atomic(args.attempt / "result.json", state)
        control.atomic(args.root / ".local-data/startup/last-attempt.json", state)
        raise


def valid_health(value):
    return bool(value.get("ok") and value.get("loopback_only") and value.get("sap_read", {}).get("selected_provider") == "embedded" and value.get("sap_read", {}).get("data", {}).get("read_only") is True)


def valid_binding(value, expected):
    binding = value.get('startup_binding') or {}
    return bool(isinstance(binding, dict) and binding.get('verified') is True and binding.get('source_digest') == expected
                and type(binding.get('pid')) is int and binding['pid'] > 0 and isinstance(binding.get('boot_id'), str) and binding['boot_id'])


def assert_site_metadata(port, expected, *, dev=False):
    if dev: return
    try: value = request(f'http://127.0.0.1:{port}/sapba-build.json', 1)
    except (OSError, ValueError) as exc:
        raise control.StartupBlocked('startup_site_build_binding_invalid') from exc
    if not isinstance(value, dict) or value.get('fingerprint') != expected:
        raise control.StartupBlocked('startup_site_build_binding_invalid')


def site_fingerprint(root, site_port, api_port):
    files = []
    for directory in ("site/src", "site/scripts"):
        files.extend(walk_files(root / directory, prune_paths={root / 'site/src/generated'}))
    for directory, names in (("agents", {"agent.json", "publication.json", "README.md"}), ("workflows", {"workflow.json", "publication.json", "README.md"})):
        files.extend(p for p in walk_files(root / directory, prune_names={'versions'}) if p.name in names)
    for name in ("site/astro.config.mjs", "site/package.json", "site/package-lock.json", "config/agent-authoring-standard.json", "config/agent-presentation-contract.json"):
        if (root / name).exists(): files.append(root / name)
    node = subprocess.check_output(["node.exe", "--version"], text=True).strip()
    # Match the existing Windows launcher material, including CRLF separators.
    ordered = sorted(set(files), key=lambda p: str(p).casefold())
    hashes = hash_files(ordered)
    material = "".join(f"{p.relative_to(root).as_posix()}|{hashes[p]}\r\n" for p in ordered)
    material += f"node|{node}\r\nsite_base|/\r\napi_url|http://127.0.0.1:{api_port}\r\n"
    return __import__('hashlib').sha256(material.encode()).hexdigest()


def check_admission(root, archive):
    # Run the target implementation against existing evidence, without probing,
    # login, reconciliation or applying any receipt. A failure keeps the old API.
    # This audits SDK integration only. An expired login/model check must not
    # prevent opening the application to repair it. Actual execution keeps its
    # complete, independent authentication/model/permission gates.
    command = ("import sys,runpy,json;from pathlib import Path;a=Path(sys.argv[1]);"
               "boot=runpy.run_path(str(a/'source/scripts/service-startup.py'));"
               "boot['bind_frozen_imports'](a,json.loads((a/'manifest.json').read_text()));"
               "from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager;"
               "m=WorkBuddyManager(Path(sys.argv[2]));s=m.environment.state();"
               "c=m.integration(s,m.environment.release()) if s.get('enabled') else {'status':'disabled'};"
               "print(json.dumps({'status':c.get('status'),'reason':c.get('reason')}));"
               "sys.exit(3 if s.get('enabled') and c.get('status')!='certified' else 0)")
    result = subprocess.run([sys.executable, "-I", "-B", "-c", command, str(archive), str(root)],
                            capture_output=True, text=True, timeout=90)
    if result.returncode:
        try: reason = json.loads(result.stdout.strip().splitlines()[-1]).get('reason')
        except (ValueError, IndexError): reason = None
        if result.returncode == 3 and reason == 'workbuddy_sdk_integration_required':
            raise control.StartupBlocked(reason)
        raise control.StartupBlocked('startup_admission_check_failed')


def wait_api(url, fingerprint, process, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None: raise control.StartupBlocked("startup_api_exited")
        try:
            value = request(url + "/api/health", min(1, deadline-time.monotonic()))
            if valid_health(value) and valid_binding(value, fingerprint):
                return
        except (OSError, ValueError): pass
        time.sleep(.1)
    raise control.StartupBlocked("startup_api_ready_timeout")


def switch_site(root, fingerprint, args, owned, previous, log):
    dist = root / ".local-data/site-builds" / fingerprint / "dist"
    if not args.dev and not (dist / "sapba-build.json").exists(): raise control.StartupBlocked("startup_site_build_missing")
    cli = root / "site/node_modules/astro/bin/astro.mjs"
    def start(path, dev=None):
        use_dev = args.dev if dev is None else dev
        return spawn(["node.exe", str(cli), 'dev' if use_dev else "preview", "--host", "127.0.0.1", "--port", str(args.site_port), *([] if use_dev else ["--outDir", str(path)]), "--ignore-lock"], root / "site", log / "site")
    def wait(process, expected, use_dev):
        deadline = time.monotonic()+args.timeout
        while time.monotonic()<deadline:
            if process.poll() is not None: raise control.StartupBlocked("startup_site_exited")
            try:
                with LOCAL_HTTP.open(f"http://127.0.0.1:{args.site_port}/zh/", timeout=min(1, max(.05,deadline-time.monotonic()))) as response:
                    if b"SAP Business Agents" in response.read():
                        assert_site_metadata(args.site_port, expected, dev=use_dev); return
            except OSError: pass
            time.sleep(.1)
        raise control.StartupBlocked("startup_site_ready_timeout")
    if owned: stop_owned(owned, root)
    process = None
    try:
        process = start(dist); wait(process, fingerprint, args.dev)
        if previous: control.atomic(root / ".local-data/site-builds/previous.json", previous)
        control.atomic(root / ".local-data/site-builds/current.json", {"fingerprint": fingerprint, "dist_path": str(dist), "pid": process.pid, "mode": 'dev' if args.dev else 'preview'})
    except BaseException:
        if process is not None and process.poll() is None:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=True); process.wait(timeout=10)
        if previous.get("dist_path"):
            previous_dev = previous.get('mode') == 'dev'
            restored = start(previous["dist_path"], previous_dev)
            wait(restored, previous['fingerprint'], previous_dev)
            control.atomic(root / '.local-data/site-builds/current.json', {**previous, 'pid': restored.pid})
        raise


def probe_site(root, fingerprint, args, log):
    """Serve and inspect the immutable candidate BEFORE stopping the old UI."""
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0)); port = available.getsockname()[1]
    candidate_args = argparse.Namespace(**{**vars(args), "site_port": port})
    # Candidate pointer updates are prohibited: use a private process only.
    dist = root / ".local-data/site-builds" / fingerprint / "dist"
    process = spawn(["node.exe", str(root / "site/node_modules/astro/bin/astro.mjs"), 'dev' if args.dev else "preview", "--host", "127.0.0.1", "--port", str(port), *([] if args.dev else ["--outDir", str(dist)]), "--ignore-lock"], root / "site", log / "site-candidate")
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None: raise control.StartupBlocked("startup_site_candidate_exited")
            try:
                with LOCAL_HTTP.open(f"http://127.0.0.1:{port}/zh/", timeout=min(1, max(.05, deadline-time.monotonic()))) as response:
                    if b"SAP Business Agents" in response.read():
                        assert_site_metadata(port, fingerprint, dev=args.dev); return
            except OSError: pass
            time.sleep(.1)
        raise control.StartupBlocked("startup_site_candidate_timeout")
    finally:
        if process.poll() is None:
            # Only this invocation's private candidate and descendants.
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=True)
            process.wait(timeout=10)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("ensure", "serve", "fingerprint"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--site-port", type=int, default=4321)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--reuse-only", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "serve": serve(arguments)
        elif arguments.command == "fingerprint": print(site_fingerprint(arguments.root, arguments.site_port, arguments.port))
        else: ensure(arguments)
    except control.StartupBlocked as exc:
        print(f'[SAPBusinessAgents] Startup blocked: {exc}',file=sys.stderr)
        if str(exc) in {'startup_target_changed_prepare_again','startup_site_source_changed_prepare_again'}:
            print('[SAPBusinessAgents] Files are still changing. No service was switched. Finish saving in other windows, then run the launcher again.',file=sys.stderr)
        if str(exc) == 'startup_archive_publish_busy':
            print('[SAPBusinessAgents] Windows denied publishing frozen source after bounded retries. The staged archive was retained; no service was switched. Close tools holding startup-releases files and retry. Do not disable security software or delete running releases.',file=sys.stderr)
        if str(exc) == 'startup_archive_target_conflict':
            print('[SAPBusinessAgents] A conflicting source archive exists. It was not overwritten; no service was switched.',file=sys.stderr)
        if getattr(arguments,'attempt',None):
            print(f'[SAPBusinessAgents] Details: {arguments.attempt / "result.json"}',file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
