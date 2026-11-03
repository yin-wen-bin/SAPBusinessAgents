"""One-shot verification and activation of an exact local code update.

No watcher, SDK/model/SAP calls, environment upgrades or business acceptance.
Active API/site pointers remain the deployment authority.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET


VERSION = 'sapba.verified-startup-update/1'
DEFAULT_TESTS = (
    'tests/test_startup_update.py', 'tests/test_startup_simple.py',
    'tests/test_startup_active.py', 'tests/test_startup_versioning.py',
    'tests/test_startup_initialization.py', 'tests/test_startup_archive_publish.py',
    'tests/test_startup_approved_dependencies.py',
    'tests/test_startup_launcher.py', 'tests/test_native_auto_test_startup_update.py',
    'tests/test_native_auto_test_mcp_stream.py',
)
EXCLUDED = {'__pycache__', '.pytest_cache', 'node_modules', 'dist', '.astro', 'generated', '.git'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def safe_location(root, name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or any(part in {'', '.', '..'} for part in name.split('/'))):
        raise ValueError('startup_update_path_invalid')
    root = Path(root).resolve()
    path = root / name
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
            raise ValueError('startup_update_link_rejected')
    if not path.resolve().is_relative_to(root):
        raise ValueError('startup_update_path_invalid')
    if path.is_file() and path.stat().st_nlink > 1:
        raise ValueError('startup_update_file_invalid')
    return path


def safe_file(root, name):
    path = safe_location(root, name)
    if not path.is_file():
        raise ValueError('startup_update_file_invalid')
    return path


def inputs(root):
    """Code bytes only; prune business history, installations and generated output."""
    root = Path(root).resolve()
    names = set()
    for directory in ('src', 'scripts', 'site', 'config'):
        start = root / directory
        if start.is_symlink() or getattr(start, 'is_junction', lambda: False)():
            raise ValueError('startup_update_link_rejected')
        for parent, dirs, files in os.walk(start, followlinks=False):
            dirs[:] = [name for name in dirs if name not in EXCLUDED and not name.startswith('.env')]
            for name in dirs:
                path = Path(parent) / name
                if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                    raise ValueError('startup_update_link_rejected')
            for name in files:
                path = Path(parent) / name
                if (not name.startswith('.env') and name not in {'auth.json', 'credentials.json'}
                        and path.suffix.lower() in {'.py', '.json', '.ps1', '.mjs', '.js', '.ts', '.tsx', '.astro', '.css', '.toml', '.yaml', '.yml'}):
                    names.add(path.relative_to(root).as_posix())
    for name in ('pyproject.toml', 'requirements.lock', 'uv.lock',
                 'start-sap-business-agents.cmd', 'restart-sap-business-agents.cmd',
                 'agents/FI/month-end-closing/config/month_end_checklist.toml',
                 'tests/test_workbuddy_isolation.py', 'tests/test_workbuddy_three_layers.py',
                 'tests/workbuddy_integration_fakes.py', 'tests/fixtures/workbuddy_protocol_worker.py'):
        if (root / name).is_file():
            names.add(name)
    return {name: sha(safe_file(root, name)) for name in sorted(names)}


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def load_record(root, path):
    root = Path(root).resolve()
    path = Path(path)
    relative = path.relative_to(root).as_posix()
    value = read(safe_file(root, relative))
    if (not isinstance(value, dict) or value.get('version') != VERSION or value.get('root') != str(root)
            or not isinstance(value.get('id'), str) or len(value['id']) != 32
            or any(c not in '0123456789abcdef' for c in value['id'])
            or value.get('digest') != digest({k: v for k, v in value.items() if k != 'digest'})):
        raise ValueError('startup_update_record_invalid')
    if not value.get('inputs') or inputs(root) != value['inputs']:
        raise ValueError('startup_update_source_changed_verify_again')
    proof = value.get('verification')
    if (not isinstance(proof, dict) or not isinstance(proof.get('evidence'), dict)
            or not proof['evidence'] or proof.get('passed') is not True):
        raise ValueError('startup_update_verification_missing')
    for name, expected in proof['evidence'].items():
        if not name.startswith('.local-data/startup/updates/') or sha(safe_file(root, name)) != expected:
            raise ValueError('startup_update_verification_changed')
    manifest = value.get('manifest')
    if (not isinstance(manifest, dict) or not isinstance(manifest.get('files'), dict)
            or not manifest['files'] or not isinstance(manifest.get('dependencies'), dict)
            or manifest.get('digest') != digest({k: v for k, v in manifest.items() if k != 'digest'})):
        raise ValueError('startup_update_manifest_invalid')
    if any(value['inputs'].get(name) != expected for name, expected in manifest['files'].items()):
        raise ValueError('startup_update_test_binding_missing')
    return value


def consumed(root, value):
    path = safe_location(Path(root), '.local-data/startup/updates/' + value['id'] + '/activated.json')
    if not path.exists():
        return False
    result = read(safe_file(Path(root), path.relative_to(Path(root)).as_posix()))
    return (result.get('verification_id') == value['id']
            and result.get('record_digest') == value['digest']
            and result.get('status') == 'activated')


def register(root, expected_inputs, verification, *, boot=None):
    """Called by a completed verifier, never by the ordinary startup path."""
    root = Path(root).resolve()
    if inputs(root) != expected_inputs:
        raise ValueError('startup_update_source_changed_verify_again')
    boot = boot or runpy.run_path(str(root / 'scripts/service-startup.py'))
    current, site, _ = boot['active_records'](root)
    manifest = boot['source_manifest'](root, current['environment'])
    if any(expected_inputs.get(name) != expected for name, expected in manifest['files'].items()):
        raise ValueError('startup_update_test_binding_missing')
    fingerprint = boot['site_fingerprint'](root, current.get('site_port', 4321), current.get('api_port', 8765))
    value = {'version': VERSION, 'root': str(root), 'id': uuid.uuid4().hex,
             'base_source_digest': current['source_digest'], 'manifest': manifest,
             'site_fingerprint': fingerprint, 'inputs': expected_inputs, 'verification': verification,
             'already_active': current['source_digest'] == manifest['digest'] and site['fingerprint'] == fingerprint}
    value['digest'] = digest(value)
    path = safe_location(root, '.local-data/startup/verified-update.json')
    # Validation finishes before publishing the small discovery record.
    attempt = safe_location(root, '.local-data/startup/updates/' + value['id'])
    attempt.mkdir(parents=True)
    atomic(attempt / 'verified.json', value)
    load_record(root, attempt / 'verified.json')
    atomic(path, value)
    return {'status': 'verified_pending_start', 'record': str(path),
            'source_digest': manifest['digest'], 'id': value['id']}


def verify(root, test_refs):
    root = Path(root).resolve()
    test_refs = list(dict.fromkeys((*DEFAULT_TESTS, *(test_refs or []))))
    for name in test_refs:
        if not name.startswith('tests/test_') or not name.endswith('.py'):
            raise ValueError('startup_update_test_path_invalid')
        safe_file(root, name)
    before = inputs(root)
    test_files = {p.relative_to(root).as_posix(): sha(p) for p in (root / 'tests').rglob('*.py')
                  if not any(part in EXCLUDED for part in p.parts)}
    directory = safe_location(root, '.local-data/startup/updates/' + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    junit = directory / 'tests.xml'
    log = directory / 'tests.log'
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1', 'PYTHONPATH': str(root / 'src')}
    with log.open('wb') as output:
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-m', 'pytest', '-q', '-o', 'addopts=',
            '--junitxml=' + str(junit), *test_refs], cwd=root, env=environment,
            stdout=output, stderr=subprocess.STDOUT, timeout=1800)
    summary = ET.parse(junit).getroot() if junit.is_file() else None
    suites = list(summary.iter('testsuite')) if summary is not None else []
    counts = {key: sum(int(s.get(key, 0)) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
    if result.returncode or counts['failures'] or counts['errors'] or counts['tests'] <= counts['skipped']:
        raise ValueError('startup_update_regressions_failed: ' + str(log))
    after_tests = {p.relative_to(root).as_posix(): sha(p) for p in (root / 'tests').rglob('*.py')
                   if not any(part in EXCLUDED for part in p.parts)}
    if test_files != after_tests:
        raise ValueError('startup_update_tests_changed_during_verification')
    proof = {'passed': True, 'kind': 'offline_regressions', 'counts': counts, 'test_refs': test_refs,
             'test_files': test_files, 'evidence': {p.relative_to(root).as_posix(): sha(p) for p in (junit, log)}}
    return register(root, before, proof)


def apply(root, path, *, port=8765, site_port=4321, timeout=90, no_browser=False, restart=False, boot=None):
    root = Path(root).resolve()
    value = load_record(root, path)
    boot = boot or runpy.run_path(str(root / 'scripts/service-startup.py'))
    # Serializes launcher invocations only; business operations do not acquire
    # this lock. The deployment lock is still acquired solely by activate_code.
    lock = safe_location(root, '.local-data/startup/updates/' + value['id'] + '/launcher.lock')
    while True:
        if consumed(root, value):
            return {'status': 'already_activated'}
        stack = ExitStack()
        try:
            stack.enter_context(boot['control'].file_lock(lock, wait=0))
        except RuntimeError as error:
            stack.close()
            if str(error) != 'startup_coordination_busy':
                raise
            print('[SAPBusinessAgents] Another launcher is applying this verified update; waiting for its result.', flush=True)
            time.sleep(1)
            continue
        # A failure inside activation must propagate to the controlled rollback;
        # it is never mistaken for failure to acquire this outer launcher lock.
        with stack:
            if consumed(root, value):
                return {'status': 'already_activated'}
            return _apply(root, path, value, port=port, site_port=site_port, timeout=timeout,
                          no_browser=no_browser, restart=restart, boot=boot)


def _apply(root, path, value, *, port, site_port, timeout, no_browser, restart, boot):
    def unchanged():
        if load_record(root, path)['digest'] != value['digest']:
            raise ValueError('startup_update_superseded_start_again')
    unchanged()
    current, site, _ = boot['active_records'](root)
    if current['environment'] != value['manifest']['dependencies'].get('environment'):
        raise ValueError('startup_update_environment_changed_verify_again')
    if current['source_digest'] not in {value['base_source_digest'], value['manifest']['digest']}:
        raise ValueError('startup_update_active_base_changed_verify_again')
    activity_root = boot['settings_for'](root).data_root
    # This loop exists only for the lifetime of the invoked launcher.
    while boot['control'].activity(activity_root) or boot['jobs_pending'](root):
        print('[SAPBusinessAgents] Verified update pending: waiting for tasks/cleanup. Ctrl+C leaves services running.', flush=True)
        time.sleep(5)
    unchanged()
    manifest = boot['source_manifest'](root, current['environment'])
    if manifest != value['manifest']:
        raise ValueError('startup_update_configuration_or_source_changed')
    archive = boot['freeze'](root, manifest)
    print('[SAPBusinessAgents] Preparing the verified API and frontend update...', flush=True)
    subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        str(root / 'scripts/Start-SAPBusinessAgents.ps1'), '-PrepareSiteOnly', '-RebuildSite',
        '-ApiPort', str(port), '-SitePort', str(site_port), '-NoBrowser'], cwd=root, check=True)
    unchanged()
    fingerprint = boot['site_fingerprint'](root, site_port, port)
    if fingerprint != value['site_fingerprint']:
        raise ValueError('startup_update_frontend_changed_verify_again')
    current, site, _ = boot['active_records'](root)
    if current['source_digest'] not in {value['base_source_digest'], manifest['digest']}:
        raise ValueError('startup_update_active_base_changed_verify_again')
    handoff = {'base': current, 'base_site': site,
        'target': {'archive': str(archive), 'source_digest': manifest['digest'],
                   'api_port': port, 'site_port': site_port, 'environment': current['environment']},
        'site': {'fingerprint': fingerprint, 'dist_path': str(root / '.local-data/site-builds' / fingerprint / 'dist'), 'mode': 'preview'},
        'tested_source_digest': manifest['digest']}
    destination = root / '.local-data/startup/updates' / value['id'] / uuid.uuid4().hex
    atomic(destination / 'handoff.json', handoff)
    # The normal controller still owns idle rechecks, locks, process ownership,
    # admission, pointer commit, actual health verification and paired rollback.
    import argparse as _argparse
    args = _argparse.Namespace(root=root, port=port, site_port=site_port, timeout=timeout,
        no_browser=no_browser, restart=restart, target_record=destination/'handoff.json',
        rebuild=False, dev=False, reuse_only=False, site_only=False,
        verified_update_check=unchanged)
    result = boot['activate_code'](args)
    health = boot['request'](f'http://127.0.0.1:{port}/api/health')
    served = boot['request'](f'http://127.0.0.1:{site_port}/sapba-build.json')
    if not boot['valid_health'](health) or not boot['valid_binding'](health, manifest['digest']) or served.get('fingerprint') != fingerprint:
        raise ValueError('startup_update_running_version_mismatch')
    atomic(destination / 'result.json', {'status': 'activated', 'source_digest': manifest['digest'],
        'site_fingerprint': fingerprint, 'verification_id': value['id']})
    atomic(root / '.local-data/startup/updates' / value['id'] / 'activated.json',
        {'status': 'activated', 'verification_id': value['id'], 'record_digest': value['digest'],
         'source_digest': manifest['digest'], 'site_fingerprint': fingerprint})
    print('[SAPBusinessAgents] Verified update activated; API source and frontend version match.', flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('verify', 'apply'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--test', action='append', dest='tests')
    parser.add_argument('--record', type=Path)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--site-port', type=int, default=4321)
    parser.add_argument('--timeout', type=int, default=90)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--restart', action='store_true')
    args = parser.parse_args()
    if args.command == 'verify':
        print(json.dumps(verify(args.root, args.tests)), flush=True)
    else:
        apply(args.root, args.record or args.root/'.local-data/startup/verified-update.json',
              port=args.port, site_port=args.site_port, timeout=args.timeout,
              no_browser=args.no_browser, restart=args.restart)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, ET.ParseError, subprocess.SubprocessError) as error:
        print('[SAPBusinessAgents] Verified update was not completed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
