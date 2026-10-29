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
from contextvars import ContextVar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_STARTUP_VERSION = 1
_verification = ContextVar('startup_verification_boundary',default=None)
spec = importlib.util.spec_from_file_location("startup_control", ROOT / "src/sap_business_agents_platform/startup_control.py")
control = importlib.util.module_from_spec(spec)
exec(compile(Path(spec.origin).read_bytes(),spec.origin,'exec'),control.__dict__)
env_spec = importlib.util.spec_from_file_location('startup_environment', ROOT / 'src/sap_business_agents_platform/runtime_environment.py')
environments = importlib.util.module_from_spec(env_spec)
exec(compile(Path(env_spec.origin).read_bytes(),env_spec.origin,'exec'),environments.__dict__)
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def verify_environment(binding):
    """Reuse bytes only in this synchronous preparation/maintenance boundary."""
    scope=_verification.get()
    key=('environment',control.digest(binding))
    if scope is None or key not in scope:
        environments.verify(binding)
        if scope is not None:scope[key]=True
# Read-only validators referenced relative to the loaded platform package.
# Business data, mutable catalogs, credentials and SDK installations stay at
# their explicit existing locations; only these reviewed code resources freeze.
SOURCE_RESOURCES = (
    'scripts/Start-SAPBusinessAgents.ps1',
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
    filename = ROOT / 'src/sap_business_agents_platform/config.py'
    module_name = '_sapba_startup_settings'
    definition = importlib.util.spec_from_file_location(module_name, filename)
    module = importlib.util.module_from_spec(definition)
    sys.modules[module_name] = module
    exec(compile(filename.read_bytes(),str(filename),'exec'),module.__dict__)
    return module.Settings.from_env(root)


def walk_files(directory, *, prune_names=(), prune_paths=()):
    """Prune excluded trees before enumeration, not after walking history."""
    excluded = set(prune_paths)
    for parent, folders, names in os.walk(directory, followlinks=False):
        base = Path(parent)
        folders[:] = [name for name in folders if name not in prune_names and base / name not in excluded]
        for name in names:
            yield base / name


def source_manifest(root, environment=None):
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
    for name in ("pyproject.toml", "requirements.lock", "uv.lock"):
        if (root / name).exists(): dependencies[name] = control.sha(root / name)
    if environment is None:
        current = root/'.local-data/startup-api-current.json'
        if current.exists():
            environment = json.loads(current.read_text(encoding='utf-8-sig')).get('environment')
    if environment:
        verify_environment(environment)
        dependencies['environment'] = environment
    else:
        if (root/'.venv/pyvenv.cfg').exists():dependencies['.venv/pyvenv.cfg']=control.sha(root/'.venv/pyvenv.cfg')
        for path in (root/'.venv/Lib/site-packages').glob('*.dist-info/*'):
            if path.name in {'METADATA','RECORD'}:dependencies[path.relative_to(root).as_posix()]=control.sha(path)
        dependencies['interpreter']={'path':sys.executable,'sha256':control.sha(sys.executable),'version':sys.version,
            'base_sha256':control.sha(getattr(sys,'_base_executable',sys.executable))}
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
        if manifest['digest'] != key:
            raise ValueError('manifest pointer mismatch')
        environment = record.get('environment') or manifest.get('dependencies', {}).get('environment')
        if environment:
            verify_environment(environment)
            bound = manifest.get('dependencies', {}).get('environment')
            if bound is not None and bound != environment:
                raise control.StartupBlocked('startup_environment_binding_mismatch')
            if bound is None and environments.legacy_binding(root, manifest, artifact_proofs=environment.get('artifact_proofs', ())) != environment:
                raise control.StartupBlocked('startup_environment_binding_mismatch')
            # Source-declared dependencies belong to the verified archive, not
            # the dirty checkout. Live configuration must still match exactly.
            matching = all(manifest.get('dependencies', {}).get(k) == requested.get('dependencies', {}).get(k)
                           for k in ('startup_settings_sha256', 'configuration_sha256'))
        else:
            matching = manifest.get('dependencies') == requested.get('dependencies')
        if not matching:
            # A pip update can leave the source archive intact but change the
            # interpreter's actual packages. Never re-label that archive or
            # silently waive its dependency binding just to open the site.
            blocked = control.StartupBlocked('startup_approved_dependencies_changed')
            blocked.reason = 'installed_dependencies_differ_from_approved_release'
            blocked.changes = manifest_changes(manifest, requested)
            raise blocked
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
        native = {name for name in manifest['files'] if Path(name).suffix in {'.mjs','.ps1'}}
        if not native <= set(SOURCE_RESOURCES):
            raise control.StartupBlocked('startup_native_resource_not_reviewed')
        # The existing validation archive deliberately does not accept JS/PS.
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
    entries = list((target/'source').rglob('*'))
    if target.resolve()!=target or any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in [target,target/'source',*entries]):
        raise control.StartupBlocked('startup_archive_link_rejected')
    actual = {p.relative_to(target / "source").as_posix() for p in entries if p.is_file()}
    if actual != set(manifest["files"]) or any(control.sha(target / "source" / name) != value for name, value in manifest["files"].items()):
        raise control.StartupBlocked("startup_archive_changed")


def bytecode_inventory(archive):
    """Inspect untrusted cache bytes, never import/execute or certify them."""
    manifest=json.loads((archive/'manifest.json').read_text(encoding='utf-8-sig'))
    if control.digest({k:v for k,v in manifest.items() if k!='digest'})!=manifest['digest']:
        raise control.StartupBlocked('startup_archive_manifest_changed')
    source=archive/'source';entries=list(source.rglob('*'))
    if archive.resolve()!=archive or any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in [archive,source,*entries]):
        raise control.StartupBlocked('startup_archive_link_rejected')
    actual={p.relative_to(source).as_posix():p for p in entries if p.is_file()}
    if any(name not in actual or control.sha(actual[name])!=expected for name,expected in manifest['files'].items()):
        raise control.StartupBlocked('startup_archive_original_changed')
    caches=[]
    for name in sorted(actual.keys()-manifest['files'].keys()):
        path=actual[name];match=re.fullmatch(r'(.+)\.'+re.escape(sys.implementation.cache_tag)+r'(?:\.opt-[12])?\.pyc',path.name)
        original=path.parent.parent/(match[1]+'.py') if match and path.parent.name=='__pycache__' else None
        raw=path.read_bytes()
        if (not original or original.relative_to(source).as_posix() not in manifest['files']
                or len(raw)<16 or raw[:4]!=importlib.util.MAGIC_NUMBER
                or int.from_bytes(raw[4:8],'little') not in {0,1,3}):
            raise control.StartupBlocked('startup_archive_nonstandard_extra_file')
        info=path.stat()
        caches.append({'path':name,'sha256':__import__('hashlib').sha256(raw).hexdigest(),
                       'size':len(raw),'created_ns':info.st_ctime_ns,'modified_ns':info.st_mtime_ns})
    return manifest,caches


def recover_bytecode(args):
    root=args.root.resolve();api,site,_=active_records(root)
    archive=Path(args.archive or api['archive'])
    if archive!=Path(api['archive']):raise control.StartupBlocked('startup_recovery_not_active_archive')
    data=settings_for(root).data_root;marker=data/'startup-maintenance.json'
    log=root/'.local-data/startup'/('bytecode-recovery-'+uuid.uuid4().hex)
    log.mkdir(parents=True)
    with control.file_lock(data/'deployment.lock',wait=60),control.file_lock(data/'startup-admission.lock'):
        if marker.exists() or (root/'.local-data/startup/pending-switch.json').exists():
            raise control.StartupBlocked('startup_switch_recovery_required')
        if control.activity(data) or jobs_pending(root):raise control.StartupBlocked('startup_recovery_busy')
        ports=listeners();assert_writers(root,[ports[p] for p in (args.port,args.site_port) if p in ports])
        manifest,caches=bytecode_inventory(archive)
        control.atomic(log/'inventory.json',{'archive':str(archive),'source_digest':manifest['digest'],'files':caches})
        control.atomic(marker,{'pid':os.getpid(),'kind':'bytecode_recovery','log':str(log)})
        moved=[]
        try:
            if control.activity(data) or jobs_pending(root):raise control.StartupBlocked('startup_recovery_busy')
            if bytecode_inventory(archive)!=(manifest,caches):raise control.StartupBlocked('startup_recovery_source_changed')
            for item in caches:
                origin=archive/'source'/item['path'];destination=log/'quarantine'/item['path']
                if control.sha(origin)!=item['sha256']:raise control.StartupBlocked('startup_recovery_source_changed')
                destination.parent.mkdir(parents=True,exist_ok=True);origin.replace(destination)
                moved.append(item['path'])
                control.atomic(log/'result.json',{'status':'recovering','moved':moved})
            verify_archive(archive,manifest)
            result={'status':'completed','archive':str(archive),'source_digest':manifest['digest'],
                    'quarantined':len(moved),'manifest_changed':False,'log':str(log)}
            control.atomic(log/'result.json',result)
            print(json.dumps(result),flush=True)
            return result
        except BaseException:
            control.atomic(log/'result.json',{'status':'failed','moved':moved,'trust_granted':False})
            raise
        finally:marker.unlink(missing_ok=True)


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


def listener_belongs_to(pid, saved, root):
    """A venv launcher can own a child listener; require the original creation identity."""
    if not saved or identity(saved['ProcessId'],root)!=saved:return False
    if pid==saved['ProcessId']:return True
    rows=json.loads(subprocess.check_output(['powershell.exe','-NoProfile','-Command',
        'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CreationDate | ConvertTo-Json -Compress'],text=True))
    by_id={v['ProcessId']:v for v in rows};seen=set()
    while pid in by_id and pid not in seen:
        seen.add(pid);child=by_id[pid];parent=by_id.get(child['ParentProcessId'])
        if parent is None:return False
        c=re.search(r'Date\((\d+)',str(child.get('CreationDate')))
        p=re.search(r'Date\((\d+)',str(parent.get('CreationDate')))
        if not c or not p or int(p[1])>int(c[1]):return False
        if parent['ProcessId']==saved['ProcessId']:return True
        pid=parent['ProcessId']
    return False


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


def jobs_pending(root, owner_operation=None):
    # Reuse the existing strict journal reader; never reconcile or write it.
    for provider in ('codex','workbuddy'):
        directory=root/'.local-data/sdk-update-jobs'/provider
        current=directory/'current.json'
        if not current.exists():continue
        try:
            value=json.loads(current.read_bytes());operation=value['operation_id']
            if not re.fullmatch(r'(?:update|recovery)_[a-f0-9]{32}',operation):return True
            record=directory/(operation+'.json')
            if record.exists():value=json.loads(record.read_bytes())
            if value.get('operation_id')!=operation:return True
            if owner_operation==operation:continue
            if (value.get('status') in {'running','cleanup_pending'} or value.get('cleanup_complete') is False
                    or value.get('switch_intent') and value.get('status')=='needs_review'):return True
        except (OSError,ValueError,KeyError,TypeError):return True
    sys.path.insert(0, str(ROOT / "src"))
    from sap_business_agents_platform.workbuddy_job_index import enabled, paths
    journal = root / ".local-data/runtimes/workbuddy"
    if enabled(journal):
        value = paths(journal)
        return bool(value)
    # Unknown old journal protocols cannot be treated as empty.
    return any((journal / "jobs").glob("*.json"))


def release_environment(root, archive):
    manifest = json.loads((archive/'manifest.json').read_text(encoding='utf-8-sig'))
    binding = manifest.get('dependencies', {}).get('environment')
    if binding is None:
        for name in ('startup-api-current.json', 'startup-api-previous.json'):
            path = root/'.local-data'/name
            if path.exists():
                record = json.loads(path.read_text(encoding='utf-8-sig'))
                if record.get('source_digest') == manifest['digest']:
                    binding = record.get('environment')
                    if binding:
                        if environments.legacy_binding(root, manifest, artifact_proofs=binding.get('artifact_proofs', ())) != binding:
                            raise control.StartupBlocked('startup_environment_binding_mismatch')
                        break
    if binding: verify_environment(binding)
    return binding


def launch_api(root, archive, port, log):
    binding = release_environment(root, archive)
    args = [binding['python'] if binding else sys.executable, "-I", "-B", str(archive / "source/scripts/service-startup.py"), "serve", "--root", str(root), "--port", str(port), "--archive", str(archive)]
    return spawn(args, root, log / "platform")


def spawn(args, cwd, log):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.with_suffix(".stdout.log").open("ab") as stdout, log.with_suffix(".stderr.log").open("ab") as stderr:
        # Local preview must use the same root URL as the frozen local build;
        # Astro otherwise falls back to the public GitHub Pages base path.
        child_environment = {**os.environ, 'PUBLIC_SITE_BASE': '/', 'PYTHONDONTWRITEBYTECODE': '1'}
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
    environment = release_environment(args.root, args.archive)
    # release_environment just verified these bytes in this same synchronous
    # boundary. Retain the interpreter check without hashing every package twice.
    if environment and Path(sys.executable).resolve()!=Path(environment['python']).resolve():
        raise environments.EnvironmentError('runtime_environment_interpreter_mismatch')
    bind_frozen_imports(args.archive, manifest)
    from sap_business_agents_platform import startup_control
    from sap_business_agents_platform.cli import configure_internal_api_url
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.app import create_app
    configure_internal_api_url("127.0.0.1", args.port)
    verify_loaded_sources(args.archive, manifest)
    startup_control._binding = {"verified": True, "source_digest": manifest["digest"], "boot_id": uuid.uuid4().hex, "pid": os.getpid(), "api_port": args.port,
        "required_imports": {'scripts.run_three_stage_acceptance': manifest['files']['scripts/run_three_stage_acceptance.py']}}
    if environment:
        startup_control._binding['environment_digest'] = environment['digest']
        startup_control._binding['python'] = environment['python']
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
    token=_verification.set({})
    try:return _ensure_core(args)
    finally:_verification.reset(token)


def _ensure_core(args):
    root = args.root.resolve(); data = root / ".local-data"
    activity_root = data
    log = args.attempt
    began = args.started; phases = args.phases
    def phase(name, action):
        args.current_phase = name
        start = time.monotonic()
        try: return action()
        finally:
            phases[name] = round((time.monotonic() - start) * 1000, 2)
            print(f"[SAPBusinessAgents] {name}: {phases[name]} ms", flush=True)
    frozen = getattr(args, 'frozen_record', None)
    def pending_jobs():
        return jobs_pending(root,getattr(args,'operation',None)) if frozen else jobs_pending(root)
    def target_manifest():
        if not frozen: return source_manifest(root)
        archive = Path(frozen['archive'])
        value = json.loads((archive/'manifest.json').read_bytes())
        if archive.resolve() != root/'.local-data/startup-releases'/value['digest'] or value['digest'] != frozen['source_digest']:
            raise control.StartupBlocked('startup_target_binding_invalid')
        verify_archive(archive, value)
        if value['dependencies'].get('environment') != frozen['environment']:
            raise control.StartupBlocked('startup_environment_binding_mismatch')
        verify_environment(frozen['environment'])
        if getattr(args,'active_start',False) or getattr(args,'code_activation',False):
            from dataclasses import asdict
            settings = settings_for(root)
            live_settings = control.digest({key:str(item) for key,item in asdict(settings).items()})
            hashes = [control.sha(path) for path in (root/'.env',settings.sap_env_file) if path and path.is_file()]
            if (value['dependencies'].get('startup_settings_sha256') != live_settings
                    or value['dependencies'].get('configuration_sha256',[]) != hashes):
                raise control.StartupBlocked('startup_approved_configuration_changed')
        if getattr(args, 'check_operation', None): args.check_operation()
        return value
    def target_site():
        if not frozen: return site_fingerprint(root, args.site_port, args.port)
        site = json.loads((data/'site-builds/current.json').read_text(encoding='utf-8-sig'))
        if site['fingerprint'] != getattr(args,'base_site',args.frozen_site)['fingerprint']:
            raise control.StartupBlocked('startup_site_binding_changed')
        return args.frozen_site['fingerprint']
    pending_path = data/'startup/pending-switch.json'
    if pending_path.exists():
        raise control.StartupBlocked('startup_switch_recovery_required')
    manifest = phase("source_and_dependency_fingerprint", target_manifest)
    activity_root = settings_for(root).data_root
    requested_manifest = manifest
    url = f"http://127.0.0.1:{args.port}"
    existing = None
    try: existing = request(url + "/api/health")
    except (OSError, ValueError): pass
    same = bool(existing and valid_health(existing) and valid_binding(existing, manifest['digest']))
    changed = args.restart or not same
    # Always inspect frontend source content; a healthy old page is not current.
    fingerprint = phase("site_fingerprint", target_site)
    pointer_path = data / "site-builds/current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8-sig")) if pointer_path.exists() else {}
    requested_fingerprint = fingerprint
    deferred = None; archive = None
    deferred_path = data/'startup/deferred-update.json'
    key = deferral_key(root, manifest, fingerprint)
    # A negative cache only defers an unapproved candidate. It cannot certify
    # any release; cold starts still audit the deployed target under the barrier.
    if not frozen and not args.dev and deferred_path.exists():
        try:
            saved = json.loads(deferred_path.read_text(encoding='utf-8-sig'))
            if saved.get('key') == key:
                candidate = approved_target(root, requested_manifest)
                if saved.get('approved_source_digest') == candidate['manifest']['digest']:
                    deferred = candidate
        except (OSError, ValueError, control.StartupBlocked): pass
    if changed and deferred is None and not args.reuse_only:
        archive = Path(frozen['archive']) if frozen else phase('freeze_source', lambda: freeze(root, manifest))
        try:
            phase('admission_preflight', lambda: check_admission(root, archive))
        except control.StartupBlocked as exc:
            if str(exc) != 'workbuddy_sdk_integration_required' or args.dev or frozen:
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
    site_changed = args.restart or getattr(args,'site_only',False) or args.rebuild or pointer.get("fingerprint") != fingerprint or args.site_port not in ports or pointer.get('mode', 'preview') != ('dev' if args.dev else 'preview') or (not args.dev and actual_site.get("fingerprint") != fingerprint)
    if args.reuse_only and (changed or site_changed):
        raise control.StartupBlocked('startup_reuse_not_proven_no_update_performed')
    if not changed and not site_changed:
        phase("ready", lambda: None)
    else:
        if site_changed:
            prepare = ["node.exe", "scripts/generate-agent-catalog.mjs"] if args.dev else ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts/Start-SAPBusinessAgents.ps1"), "-PrepareSiteOnly", "-ApiPort", str(args.port), *(["-RebuildSite"] if args.rebuild else [])]
            if deferred is None and not frozen:
                phase("prepare_site", lambda: subprocess.run(prepare, cwd=root / 'site' if args.dev else root, check=True))
            phase("site_candidate_ready", lambda: probe_site(root, fingerprint, args, log))
        # Never hold the deployment lock while waiting for business tasks.
        while True:
            if control.activity(activity_root) or pending_jobs():
                print("[SAPBusinessAgents] Update pending: waiting for active tasks/cleanup; Ctrl+C leaves tasks running.", flush=True)
                time.sleep(5); continue
            try:
                with control.file_lock(activity_root / "deployment.lock", wait=60), control.file_lock(activity_root / "startup-admission.lock"):
                    _verification.set({})  # A new lock boundary must reread actual bytes.
                    if control.activity(activity_root) or pending_jobs(): continue
                    current_manifest = phase('source_consistency', target_manifest)
                    if current_manifest != requested_manifest:
                        failure = control.StartupBlocked("startup_target_changed_prepare_again")
                        failure.changes = manifest_changes(requested_manifest, current_manifest)
                        raise failure
                    args.current_phase = 'site_consistency'
                    if target_site() != requested_fingerprint:
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
                        environment = manifest.get('dependencies', {}).get('environment')
                        if changed and environment is None and (root/'.venv/pyvenv.cfg').exists():
                            environment = environments.legacy_binding(root, manifest)
                        if previous and not previous.get('environment') and (root/'.venv/pyvenv.cfg').exists():
                            old_manifest = json.loads((Path(previous['archive'])/'manifest.json').read_bytes())
                            previous = {**previous, 'environment': environments.legacy_binding(root, old_manifest,
                                artifact_proofs=(environment or {}).get('artifact_proofs', ()))}
                        if previous and previous.get('environment'):
                            old_archive=Path(previous['archive'])
                            verify_archive(old_archive,json.loads((old_archive/'manifest.json').read_bytes()))
                            verify_environment(previous['environment'])
                        # Recheck after the potentially slow evidence audit too,
                        # especially when migrating a legacy API without this
                        # barrier. Never stop a task accepted during that audit.
                        if control.activity(activity_root) or pending_jobs():
                            raise control.StartupBlocked('startup_became_busy_retry')
                        if frozen: target_manifest()  # Account/config can change during evidence audit.
                        ready_deadline = time.monotonic() + args.timeout
                        site_applied = False
                        api_touched = False
                        switch = {'version':1,'phase':'prepared','owner_pid':os.getpid(),
                                  'operation_id':getattr(args,'operation',None),'api_changed':changed,
                                  'site_changed':site_changed,'previous':previous,'previous_site':pointer,
                                  'target':{'archive':str(archive),'source_digest':manifest['digest'],'api_port':args.port,'site_port':args.site_port,
                                            **({'environment':environment} if environment else {})},
                                  'target_site':fingerprint,'owned':{str(k):v for k,v in owned.items()},
                                  'log':str(log)}
                        if not changed:switch['target']=previous
                        control.atomic(pending_path, switch)
                        try:
                            if getattr(args,'on_switch',None):args.on_switch()
                            if changed:
                                remove_marker = False
                                switch['phase']='stopping'; control.atomic(pending_path,switch)
                                if args.port in owned: stop_owned(owned[args.port], root)
                                api_touched = True
                                switch['phase']='starting'; control.atomic(pending_path,switch)
                                process = launch_api(root, archive, args.port, log)
                                switch.update(phase='health',new_pid=process.pid)
                                # Creation identity is recorded before health polling.
                                switch['new_identity']=identity(process.pid,root)
                                control.atomic(pending_path,switch)
                            if site_changed:
                                remove_marker = False
                                # The API warms concurrently with frontend startup.
                                def site_spawned(site_process):
                                    switch['site_new_identity']=identity(site_process.pid,root)
                                    control.atomic(pending_path,switch)
                                args._site_spawn_callback=site_spawned
                                try:
                                    phase("site_switch", lambda: switch_site(root, fingerprint, args, owned.get(args.site_port), pointer, log))
                                finally:
                                    del args._site_spawn_callback
                                site_applied = True
                            if changed:
                                phase("api_ready", lambda: wait_api(url, manifest["digest"], process, max(.1, ready_deadline-time.monotonic())))
                                if environment:
                                    health = request(url+'/api/health')
                                    if health.get('startup_binding',{}).get('environment_digest')!=environment['digest']:
                                        raise control.StartupBlocked('startup_environment_health_mismatch')
                            if frozen:
                                _verification.set({})  # New process and commit boundary.
                                phase('final_consistency',target_manifest)
                                if json.loads((data/'site-builds/current.json').read_bytes())['fingerprint']!=fingerprint:
                                    raise control.StartupBlocked('startup_site_binding_changed')
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
                                        stop_owned(switch['new_identity'],root);process.wait(timeout=10)
                                    if previous:
                                        # Rollback resolves its own interpreter, not the target's.
                                        control.atomic(data/'startup-api-previous.json',previous)
                                        rollback = launch_api(root, Path(previous["archive"]), args.port, log / "rollback")
                                        wait_api(url, previous["source_digest"], rollback, args.timeout)
                                        control.atomic(data/'startup-api-current.json',{**previous,'pid':rollback.pid})
                                except BaseException as failure: rollback_errors.append('api:' + type(failure).__name__)
                            if rollback_errors:
                                error = control.StartupBlocked('startup_rollback_incomplete'); error.rollback_errors = rollback_errors
                                raise error from primary
                            remove_marker = True
                            pending_path.unlink(missing_ok=True)
                            raise
                        if changed:
                            if previous: control.atomic(data / "startup-api-previous.json", previous)
                            record = {"archive": str(archive), "source_digest": manifest["digest"], "pid": process.pid, "api_port":args.port,"site_port":args.site_port,
                                      'listener_identity':identity(listeners()[args.port],root)}
                            if environment: record['environment'] = environment
                            control.atomic(data / "startup-api-current.json", record)
                        switch['phase']='committed'; control.atomic(pending_path,switch)
                        pending_path.unlink(missing_ok=True)
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
    # New process/commit is a fresh consistency boundary. Do not reuse a
    # pre-maintenance verification across it, or exclude it from elapsed time.
    if not frozen and target_manifest() != requested_manifest:
        raise control.StartupBlocked("startup_changed_during_launch_not_current")
    if frozen and getattr(args,'code_activation',False):
        if json.loads((data/'site-builds/current.json').read_bytes())['fingerprint'] != fingerprint:
            raise control.StartupBlocked('startup_site_binding_changed')
    elif not frozen and target_site() != requested_fingerprint:
        raise control.StartupBlocked('startup_site_binding_changed')
    state['startup_mode']='active' if getattr(args,'active_start',False) else 'activation'
    state['workspace_checked']=not bool(frozen)
    if getattr(args,'active_start',False):state['current_changes_loaded']=None
    state['active_release_ready']=True
    state['total_duration_ms']=round((time.monotonic()-began)*1000,2)
    control.atomic(log / "result.json", state)
    control.atomic(data / "startup/last-attempt.json", state)
    control.atomic(data / "startup/latest.json", state)
    if deferred is None:
        deferred_path.unlink(missing_ok=True)
    print('[SAPBusinessAgents] Ready; activated release is available. Checkout changes were not applied.' if getattr(args,'active_start',False) else '[SAPBusinessAgents] Ready; reviewed target is verified as activated.', flush=True)
    if not args.no_browser:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{args.site_port}/zh/")
    return state


def active_records(root):
    """Small, non-authorizing projection of the existing two active pointers."""
    try:
        data = root / '.local-data'
        api = json.loads((data/'startup-api-current.json').read_text(encoding='utf-8-sig'))
        site = json.loads((data/'site-builds/current.json').read_text(encoding='utf-8-sig'))
        key, fingerprint = api['source_digest'], site['fingerprint']
        if not all(re.fullmatch('[0-9a-f]{64}', value) for value in (key, fingerprint)):
            raise ValueError('digest')
        archive = root/'.local-data/startup-releases'/key
        dist = root/'.local-data/site-builds'/fingerprint/'dist'
        if (Path(api['archive']) != archive or archive.resolve() != archive
                or Path(site['dist_path']) != dist or dist.resolve() != dist
                or site.get('mode', 'preview') != 'preview'):
            raise ValueError('path')
        manifest = json.loads((archive/'manifest.json').read_text(encoding='utf-8-sig'))
        if manifest['digest'] != key or control.digest({k:v for k,v in manifest.items() if k!='digest'}) != key:
            raise ValueError('manifest')
        binding = api.get('environment')
        if (not binding or binding['digest'] != control.digest({k:v for k,v in binding.items() if k!='digest'})
                or manifest['dependencies'].get('environment', binding) != binding):
            raise ValueError('environment')
        return api, site, manifest
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise control.StartupBlocked('startup_active_binding_unavailable') from exc


def service_identities(pids, root):
    """One bounded inventory for hot-open ownership, not a history/writer scan."""
    if os.name=='nt':
        try:
            found={pid:native_process_identity(pid) for pid in set(pids)}
            if any(not project_service_command(v['CommandLine'],root) for v in found.values()):
                raise control.StartupBlocked('startup_foreign_or_unknown_process')
            return found
        except (OSError,ValueError):
            pass  # Unsupported/access-denied native queries never waive ownership.
    clause = ' OR '.join('ProcessId='+str(int(pid)) for pid in set(pids))
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
        "$p=@(Get-CimInstance Win32_Process -Filter '"+clause+"');"
        "$p | Select-Object ProcessId,CreationDate,CommandLine | ConvertTo-Json -Compress"],
        capture_output=True, text=True, check=True, timeout=5)
    values = json.loads(result.stdout) if result.stdout.strip() else []
    values = values if isinstance(values, list) else [values]
    found = {v['ProcessId']:v for v in values}
    if set(found) != set(pids) or any(not v.get('CreationDate') or not project_service_command(v.get('CommandLine'), root) for v in values):
        raise control.StartupBlocked('startup_foreign_or_unknown_process')
    return found


def native_process_identity(pid):
    """Read command + creation time from Windows; no shell or mutable cache."""
    import ctypes
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]
    kernel.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)]
    kernel.GetProcessTimes.restype=w.BOOL
    handle=kernel.OpenProcess(0x1000,False,int(pid))
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    try:
        creation,exit_time,user,system=(w.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle,*map(ctypes.byref,(creation,exit_time,user,system))):
            raise ctypes.WinError(ctypes.get_last_error())
        query=ctypes.WinDLL('ntdll').NtQueryInformationProcess
        query.argtypes=[w.HANDLE,w.ULONG,ctypes.c_void_p,w.ULONG,ctypes.POINTER(w.ULONG)]
        query.restype=w.LONG
        needed=w.ULONG()
        query(handle,60,None,0,ctypes.byref(needed))
        if not 16<=needed.value<=262144:raise ValueError('process_command_unavailable')
        buffer=ctypes.create_string_buffer(needed.value)
        if query(handle,60,buffer,len(buffer),ctypes.byref(needed))!=0:
            raise ValueError('process_command_unavailable')
        class UnicodeString(ctypes.Structure):
            _fields_=[('Length',w.USHORT),('MaximumLength',w.USHORT),('Buffer',ctypes.c_void_p)]
        text=ctypes.cast(buffer,ctypes.POINTER(UnicodeString)).contents
        start=ctypes.addressof(buffer)
        if (text.Length%2 or not text.Buffer or text.Length>text.MaximumLength
                or text.Buffer<start or text.Buffer+text.Length>start+len(buffer)):
            raise ValueError('process_command_unavailable')
        command=ctypes.wstring_at(text.Buffer,text.Length//2)
        ticks=(creation.dwHighDateTime<<32)|creation.dwLowDateTime
        return {'ProcessId':int(pid),'CreationDate':'/Date('+str((ticks-116444736000000000)//10000)+')/',
                'CommandLine':command}
    finally:kernel.CloseHandle(handle)


def same_process_identity(saved, observed):
    if saved==observed:return True
    if saved.get('ProcessId')!=observed.get('ProcessId') or saved.get('CommandLine')!=observed.get('CommandLine'):
        return False
    def birth(value):
        match=re.fullmatch(r'/Date\((\d+)\)/',str(value))
        if match:return int(match[1])
        from datetime import datetime,timezone
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError('process_creation_unknown')
        delta=parsed-datetime(1970,1,1,tzinfo=timezone.utc)
        return (delta.days*86400+delta.seconds)*1000+delta.microseconds//1000
    try:return birth(saved.get('CreationDate'))==birth(observed.get('CreationDate'))
    except (ValueError,TypeError,AttributeError):return False


def ensure_active(args):
    began = time.monotonic()
    api, site, manifest = active_records(args.root)
    args.phases['active_records'] = round((time.monotonic()-began)*1000, 2)
    root = args.root; data = root/'.local-data'
    if (data/'startup/pending-switch.json').exists() or (data/'startup-maintenance.json').exists():
        raise control.StartupBlocked('startup_switch_recovery_required')
    args.current_phase = 'active_services'
    began = time.monotonic()
    ports = listeners()
    identities = service_identities([ports[p] for p in (args.port,args.site_port) if p in ports], root) if any(p in ports for p in (args.port,args.site_port)) else {}
    health = None; served = None
    if args.port in ports:
        try: health = request(f'http://127.0.0.1:{args.port}/api/health', timeout=2)
        except (OSError,ValueError): pass
    if args.site_port in ports:
        try: served = request(f'http://127.0.0.1:{args.site_port}/sapba-build.json', timeout=2)
        except (OSError,ValueError): pass
    api_ok = bool(health and valid_health(health) and valid_binding(health,api['source_digest'])
                  and health['startup_binding'].get('environment_digest')==api['environment']['digest']
                  and health['startup_binding'].get('pid')==ports.get(args.port))
    site_ok = bool(served and served.get('fingerprint')==site['fingerprint'])
    if args.port in ports and not api_ok and not args.restart:
        raise control.StartupBlocked('startup_running_binding_unconfirmed_use_explicit_restart')
    if args.site_port in ports and not site_ok and not (args.restart or getattr(args,'site_only',False)):
        raise control.StartupBlocked('startup_frontend_binding_unconfirmed_use_explicit_restart')
    # Saved creation identities are checked when present; legacy records are
    # never silently renewed and still require command ownership + boot binding.
    for port, record in ((args.port,api),(args.site_port,site)):
        saved = record.get('listener_identity')
        if saved and port in ports and not same_process_identity(saved,identities[ports[port]]):
            raise control.StartupBlocked('startup_process_identity_changed')
    args.phases['active_services'] = round((time.monotonic()-began)*1000, 2)
    if api_ok and site_ok and not args.restart and not getattr(args,'site_only',False):
        state = {'status':'completed','startup_mode':'active','active_release_ready':True,
                 'workspace_checked':False,'current_changes_loaded':None,'api_reused':True,
                 'source_digest':api['source_digest'],'site_fingerprint':site['fingerprint'],
                 'phases':args.phases,'services':{'api':{'pid':ports[args.port],'healthy':True},
                                               'site':{'pid':ports[args.site_port],'healthy':True}}}
        return finish_attempt(args,state)
    if args.reuse_only:
        raise control.StartupBlocked('startup_reuse_not_proven_no_update_performed')
    if getattr(args,'site_only',False) and not api_ok:
        raise control.StartupBlocked('startup_site_only_requires_running_active_api')
    args.frozen_record = api; args.frozen_site = site; args.active_start = True
    return _ensure(args)


def finish_attempt(args, state):
    state['total_duration_ms'] = round((time.monotonic()-args.started)*1000,2)
    control.atomic(args.attempt/'result.json',state)
    control.atomic(args.root/'.local-data/startup/last-attempt.json',state)
    control.atomic(args.root/'.local-data/startup/latest.json',state)
    if not args.no_browser:
        import webbrowser
        webbrowser.open(f'http://127.0.0.1:{args.site_port}/zh/')
    state['total_duration_ms'] = round((time.monotonic()-args.started)*1000,2)
    control.atomic(args.attempt/'result.json',state)
    control.atomic(args.root/'.local-data/startup/last-attempt.json',state)
    control.atomic(args.root/'.local-data/startup/latest.json',state)
    print('[SAPBusinessAgents] Ready; activated release is available. Checkout changes were not applied.',flush=True)
    return state


def ensure(args):
    args.started = getattr(args,'bootstrap_started',None) or time.monotonic(); args.bootstrap_started=None
    args.phases = {}; args.current_phase = "preflight"
    args.attempt = args.root / ".local-data/startup" / (time.strftime("%Y%m%dT%H%M%S") + "-versioned-" + str(os.getpid())+'-'+uuid.uuid4().hex[:8])
    args.attempt.mkdir(parents=True)
    args.preparation_attempts = []
    try:
        for attempt in range(2):
            try:
                if getattr(args,'frozen_record',None) or getattr(args,'activation',False):
                    return _ensure(args)
                return ensure_active(args)
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
                 "error": str(exc) if isinstance(exc, (control.StartupBlocked,environments.EnvironmentError)) else type(exc).__name__,
                 "rollback_errors": getattr(exc, 'rollback_errors', []),
                 "original_error": type(exc.__cause__).__name__ if exc.__cause__ else None,
                 "reason": getattr(exc, 'reason', None),
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
    binding = release_environment(root, archive)
    result = subprocess.run([binding['python'] if binding else sys.executable, "-I", "-B", "-c", command, str(archive), str(root)],
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
        process = start(dist)
        if getattr(args,'_site_spawn_callback',None):args._site_spawn_callback(process)
        wait(process, fingerprint, args.dev)
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


def verify_activation_configuration(root, record):
    configuration=record['configuration']
    for filename,expected in configuration['files'].items():
        file=Path(filename)
        if (control.sha(file) if file.exists() else None)!=expected:
            raise control.StartupBlocked('sdk_update_binding_changed')
    authpath=settings_for(root).data_root/'sdk-runtimes/authentication.json'
    auth=json.loads(authpath.read_bytes()) if authpath.exists() else {'accounts':{}}
    keys=('revision','binding_revision','status','authenticated','identity_digest','authentication_source','site_digest')
    accounts={k:{n:v.get(n) for n in keys} for k,v in auth['accounts'].items()}
    if accounts!=configuration['accounts']:raise control.StartupBlocked('sdk_update_binding_changed')


def recover_switch(args):
    """Explicit recovery of the original transaction; never reinstall/requery."""
    root=args.root.resolve();data=root/'.local-data';activity_root=settings_for(root).data_root
    path=data/'startup/pending-switch.json'
    with control.file_lock(activity_root/'deployment.lock'), control.file_lock(activity_root/'startup-admission.lock'):
        if not path.exists():return {'status':'nothing_pending'}
        saved=json.loads(path.read_bytes())
        operation=saved.get('operation_id');record=None
        if operation:
            if not re.fullmatch(r'update_[a-f0-9]{32}',operation):
                raise control.StartupBlocked('sdk_update_request_invalid')
            record_path=data/'sdk-update-jobs/codex'/(operation+'.json')
            record=json.loads(record_path.read_bytes())
            if record.get('target')!=saved['target'] or record.get('base',{}).get('source_digest')!=(saved.get('previous') or {}).get('source_digest'):
                raise control.StartupBlocked('sdk_update_binding_changed')
            verify_activation_configuration(root,record)
        if control.activity(activity_root) or jobs_pending(root,saved.get('operation_id')):
            raise control.StartupBlocked('startup_recovery_busy')
        # If the owner still exists, this is not a crashed switch.
        processes=listeners()
        server_pids=[processes[p] for p in (args.port,args.site_port) if p in processes]
        assert_writers(root,server_pids)
        marker=activity_root/'startup-maintenance.json'
        if marker.exists():
            value=json.loads(marker.read_bytes())
            if value.get('pid')!=saved['owner_pid']:
                raise control.StartupBlocked('startup_recovery_owner_mismatch')
        current=processes.get(args.port)
        health={}
        if current:
            try:health=request(f'http://127.0.0.1:{args.port}/api/health')
            except (OSError,ValueError):pass
        target=saved['target'];previous=saved['previous']
        healthy_target=(not saved['api_changed'] or valid_health(health) and valid_binding(health,target['source_digest'])
                        and health.get('startup_binding',{}).get('environment_digest')==target.get('environment',{}).get('digest'))
        if healthy_target and (not saved['site_changed'] or request(f'http://127.0.0.1:{args.site_port}/sapba-build.json').get('fingerprint')==saved['target_site']):
            if saved['api_changed'] and not listener_belongs_to(current,saved.get('new_identity'),root):
                raise control.StartupBlocked('startup_recovery_process_unconfirmed')
            if saved['site_changed'] and not listener_belongs_to(processes.get(args.site_port),saved.get('site_new_identity'),root):
                raise control.StartupBlocked('startup_recovery_process_unconfirmed')
            if saved['api_changed']:
                verify_archive(Path(target['archive']),json.loads((Path(target['archive'])/'manifest.json').read_bytes()))
                if target.get('environment'):environments.verify(target['environment'])
                if previous:control.atomic(data/'startup-api-previous.json',previous)
                control.atomic(data/'startup-api-current.json',{**target,'pid':current})
            outcome='activated'
        else:
            if saved['api_changed']:
                if not previous:raise control.StartupBlocked('startup_rollback_binding_missing')
                if current:
                    owned=saved.get('new_identity')
                    # A crash before a PID receipt cannot authorize stopping an
                    # arbitrary listener, even when its health looks plausible.
                    old=saved.get('owned',{}).get(str(args.port))
                    if valid_health(health) and valid_binding(health,previous['source_digest']):
                        if not listener_belongs_to(current,old,root):
                            raise control.StartupBlocked('startup_recovery_process_unconfirmed')
                    else:
                        if not listener_belongs_to(current,owned,root):raise control.StartupBlocked('startup_recovery_process_unconfirmed')
                        stop_owned(owned,root);current=None
                if not current:
                    control.atomic(data/'startup-api-previous.json',previous)
                    process=launch_api(root,Path(previous['archive']),args.port,Path(saved['log'])/'recovery')
                    wait_api(f'http://127.0.0.1:{args.port}',previous['source_digest'],process,args.timeout)
                    control.atomic(data/'startup-api-current.json',{**previous,'pid':process.pid})
                else:
                    control.atomic(data/'startup-api-current.json',{**previous,'pid':current})
            if saved['site_changed']:
                prior=saved['previous_site'];pid=listeners().get(args.site_port)
                if not prior.get('dist_path'):raise control.StartupBlocked('startup_rollback_binding_missing')
                owned=saved.get('site_new_identity') or saved.get('owned',{}).get(str(args.site_port))
                if pid and not listener_belongs_to(pid,owned,root):
                    raise control.StartupBlocked('startup_recovery_process_unconfirmed')
                rollback_args=argparse.Namespace(**{**vars(args),'dev':prior.get('mode')=='dev'})
                switch_site(root,prior['fingerprint'],rollback_args,owned if pid else None,{},Path(saved['log'])/'recovery')
            outcome='rolled_back'
        if record is not None:
            record.update(status='completed' if outcome=='activated' else 'failed',phase=outcome,outcome=outcome,cleanup_complete=True)
            if outcome=='rolled_back':record['error']={'code':'sdk_activation_rolled_back','phase':'recovery'}
            control.atomic(record_path,record)
        control.atomic(Path(saved['log'])/'recovery.json',{'outcome':outcome,'operation_id':saved.get('operation_id')})
        path.unlink();marker.unlink(missing_ok=True)
        return {'status':'completed','outcome':outcome}


def activate_sdk(args):
    """Gated independent executor. Called only by an explicitly accepted update."""
    if not re.fullmatch(r'update_[a-f0-9]{32}',args.operation or ''):
        raise control.StartupBlocked('sdk_update_request_invalid')
    root=args.root.resolve();directory=root/'.local-data/sdk-update-jobs/codex';area=directory/args.operation
    path=directory/(args.operation+'.json')
    record=json.loads(path.read_bytes());ticket=json.loads((area/'handoff.json').read_bytes())
    if (record['operation_id']!=args.operation or ticket.get('digest')!=control.digest({k:v for k,v in ticket.items() if k!='digest'})
            or any(ticket.get(k)!=record.get(k) for k in ('base','target','configuration'))):
        raise control.StartupBlocked('sdk_activation_handoff_unconfirmed')
    manifest=json.loads((Path(record['base']['archive'])/'manifest.json').read_bytes())
    bind_frozen_imports(Path(record['base']['archive']),manifest)
    from sap_business_agents_platform.sdk_activation_handoff import inventory, verify_executor
    identity_record=verify_executor(inventory(pids=[os.getpid(),ticket['api']['pid']]),os.getpid(),ticket['api']['pid'],ticket['api'],ticket['command'])
    control.atomic(area/'executor-ready.json',{'ticket_digest':ticket['digest'],'identity':identity_record})
    while time.time()<ticket['expires_at']:
        accepted_path=area/'executor-accepted.json'
        if accepted_path.exists() and json.loads(accepted_path.read_bytes()).get('ticket_digest')==ticket['digest']:break
        time.sleep(.1)
    if time.time()>=ticket['expires_at'] or not (area/'executor-accepted.json').exists():return
    accepted=json.loads((area/'executor-accepted.json').read_bytes())
    if accepted!={'ticket_digest':ticket['digest'],'identity':identity_record}:
        raise control.StartupBlocked('sdk_activation_handoff_unconfirmed')
    args.frozen_record=record['target']
    args.frozen_site=json.loads((root/'.local-data/site-builds/current.json').read_text(encoding='utf-8-sig'))
    def check_operation():
        verify_activation_configuration(root,record)
        current=json.loads((root/'.local-data/startup-api-current.json').read_text(encoding='utf-8-sig'))
        if current.get('source_digest') not in {record['base']['source_digest'],record['target']['source_digest']}:
            raise control.StartupBlocked('sdk_update_binding_changed')
        environments.verify(record['base']['environment'])
    args.check_operation=check_operation
    def on_switch():
        record.update(status='running',phase='switching',cleanup_complete=False)
        control.atomic(path,record)
    args.on_switch=on_switch
    try:
        check_operation()
        result=ensure(args)
        health=request(f'http://127.0.0.1:{args.port}/api/health')
        if (not valid_health(health) or not valid_binding(health,record['target']['source_digest'])
                or health.get('startup_binding',{}).get('environment_digest')!=record['target']['environment']['digest']):
            raise control.StartupBlocked('startup_environment_health_mismatch')
        record.update(status='completed',phase='activated',outcome='activated',cleanup_complete=True,
                      observed_versions={'sdk':record['target_version'],'environment':record['target']['environment']['digest']})
    except BaseException as exc:
        record.update(status='needs_review',phase='switching',error={'code':str(exc) if isinstance(exc,control.StartupBlocked) else 'sdk_activation_failed','phase':'switching'})
    record['updated_at']=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
    control.atomic(path,record)


def activate_code(args):
    """Explicit tested-code handoff; receipts identify targets, never certify them."""
    if not args.target_record:raise control.StartupBlocked('startup_activation_target_missing')
    handoff=json.loads(args.target_record.read_text(encoding='utf-8-sig'))
    current,site,_=active_records(args.root)
    if handoff['base']!=current or handoff.get('base_site',site)!=site:
        raise control.StartupBlocked('startup_activation_base_changed')
    target=handoff['target']
    if target.get('environment')!=current['environment']:
        raise control.StartupBlocked('startup_code_activation_cannot_change_environment')
    if handoff.get('tested_source_digest')!=target['source_digest']:
        raise control.StartupBlocked('startup_activation_test_binding_missing')
    args.frozen_record=target;args.frozen_site=handoff.get('site',site)
    fingerprint=args.frozen_site['fingerprint']
    if (not re.fullmatch('[0-9a-f]{64}',fingerprint)
            or Path(args.frozen_site['dist_path'])!=args.root/'.local-data/site-builds'/fingerprint/'dist'
            or args.frozen_site.get('mode','preview')!='preview'):
        raise control.StartupBlocked('startup_site_binding_invalid')
    args.base_site=site;args.code_activation=True
    def unchanged():
        actual=json.loads((args.root/'.local-data/startup-api-current.json').read_bytes())
        if actual['source_digest'] not in {current['source_digest'],target['source_digest']}:
            raise control.StartupBlocked('startup_activation_base_changed')
    args.check_operation=unchanged
    return ensure(args)


def development_site(args):
    """Explicit frontend development, never a production pointer update."""
    api,site,_=active_records(args.root)
    if args.site_port==api.get('site_port',4321) or args.restart or args.rebuild:
        raise control.StartupBlocked('startup_dev_requires_separate_site_port_no_restart')
    health=request(f'http://127.0.0.1:{args.port}/api/health')
    if not valid_health(health) or not valid_binding(health,api['source_digest']):
        raise control.StartupBlocked('startup_dev_requires_running_active_api')
    if args.site_port in listeners():raise control.StartupBlocked('startup_dev_port_in_use')
    log=args.root/'.local-data/startup'/('dev-'+uuid.uuid4().hex)
    process=spawn(['node.exe',str(args.root/'site/node_modules/astro/bin/astro.mjs'),'dev',
                   '--host','127.0.0.1','--port',str(args.site_port),'--ignore-lock'],args.root/'site',log/'site')
    print('[SAPBusinessAgents] Development frontend PID '+str(process.pid)+'; activated pointers unchanged.',flush=True)
    return process.pid


def activate_frontend(args):
    """An explicit rebuild is an update, not an ordinary opening of the app."""
    current,site,_=active_records(args.root)
    if args.restart or getattr(args,'site_only',False):
        raise control.StartupBlocked('startup_frontend_activation_arguments_invalid')
    subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                    str(args.root/'scripts/Start-SAPBusinessAgents.ps1'),'-PrepareSiteOnly',
                    '-RebuildSite','-ApiPort',str(args.port),'-SitePort',str(args.site_port)],check=True)
    fingerprint=site_fingerprint(args.root,args.site_port,args.port)
    args.frozen_record=current
    args.frozen_site={'fingerprint':fingerprint,'dist_path':str(args.root/'.local-data/site-builds'/fingerprint/'dist'),'mode':'preview'}
    args.base_site=site;args.code_activation=True;args.rebuild=False
    if active_records(args.root)[:2]!=(current,site):
        raise control.StartupBlocked('startup_activation_base_changed')
    return ensure(args)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("ensure", "serve", "fingerprint", "activate-code", "activate-sdk", "recover-bytecode", "recover-switch"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--site-port", type=int, default=4321)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--archive", type=Path)
    parser.add_argument('--target-record',type=Path)
    parser.add_argument('--started',dest='bootstrap_started',type=float,help=argparse.SUPPRESS)
    parser.add_argument('--site-only',action='store_true')
    parser.add_argument("--operation", help=argparse.SUPPRESS)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--reuse-only", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "serve": serve(arguments)
        elif arguments.command == 'activate-code': activate_code(arguments)
        elif arguments.command == 'recover-bytecode': recover_bytecode(arguments)
        elif arguments.command == 'activate-sdk': activate_sdk(arguments)
        elif arguments.command == 'recover-switch': recover_switch(arguments)
        elif arguments.command == "fingerprint": print(site_fingerprint(arguments.root, arguments.site_port, arguments.port))
        elif arguments.dev: development_site(arguments)
        elif arguments.rebuild: activate_frontend(arguments)
        else: ensure(arguments)
    except (control.StartupBlocked,environments.EnvironmentError) as exc:
        print(f'[SAPBusinessAgents] Startup blocked: {exc}',file=sys.stderr)
        if isinstance(exc,environments.EnvironmentError):
            print('[SAPBusinessAgents] The registered Python environment no longer matches its verified binding. Keep the existing service and environment; review the external change. No new binding was granted.',file=sys.stderr)
        if str(exc) in {'startup_target_changed_prepare_again','startup_site_source_changed_prepare_again'}:
            print('[SAPBusinessAgents] Files are still changing. No service was switched. Finish saving in other windows, then run the launcher again.',file=sys.stderr)
        if str(exc) == 'startup_archive_publish_busy':
            print('[SAPBusinessAgents] Windows denied publishing frozen source after bounded retries. The staged archive was retained; no service was switched. Close tools holding startup-releases files and retry. Do not disable security software or delete running releases.',file=sys.stderr)
        if str(exc) == 'startup_archive_target_conflict':
            print('[SAPBusinessAgents] A conflicting source archive exists. It was not overwritten; no service was switched.',file=sys.stderr)
        if str(exc) == 'startup_approved_dependencies_changed':
            print('[SAPBusinessAgents] Installed Python dependencies differ from the approved release (for example, after an SDK update). The old dependency binding cannot be reused. Validate and activate a release against the installed packages; do not edit stored digests or downgrade automatically. No service was switched.',file=sys.stderr)
            print('[SAPBusinessAgents] Changed dependency records: '+', '.join(getattr(exc,'changes',{}).get('dependencies',[])),file=sys.stderr)
        if getattr(arguments,'attempt',None):
            print(f'[SAPBusinessAgents] Details: {arguments.attempt / "result.json"}',file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
