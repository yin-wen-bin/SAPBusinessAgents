"""Simple local startup: reuse healthy services or start the activated pair."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import socket
import subprocess
import sys
import time
import urllib.request


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def resolve(root):
    """Check the small bootstrap trust boundary, not the installed package tree."""
    record = read(root / '.local-data/startup-api-current.json')
    key = record['source_digest']
    if not re.fullmatch('[0-9a-f]{64}', key):
        raise ValueError('startup_active_binding_invalid')
    archive = root / '.local-data/startup-releases' / key
    if Path(record['archive']) != archive or archive.resolve() != archive:
        raise ValueError('startup_active_binding_invalid')
    manifest = read(archive / 'manifest.json')
    if manifest['digest'] != key or digest({k: v for k, v in manifest.items() if k != 'digest'}) != key:
        raise ValueError('startup_active_binding_invalid')
    binding = record.get('environment')
    if not binding or digest({k: v for k, v in binding.items() if k != 'digest'}) != binding['digest']:
        raise ValueError('startup_environment_binding_missing')
    if manifest['dependencies'].get('environment', binding) != binding:
        raise ValueError('startup_environment_binding_mismatch')
    python = Path(binding['python'])
    if python.resolve() != python or not python.is_relative_to(Path(binding['path'])):
        raise ValueError('startup_environment_binding_invalid')
    if hashlib.sha256(python.read_bytes()).hexdigest() != binding['python_sha256']:
        raise ValueError('runtime_interpreter_changed')
    # No imports from the mutable checkout. Only these bootstrap modules execute
    # before ensure has selected the hot or fully verified cold path.
    for name in ('scripts/service-startup.py', 'src/sap_business_agents_platform/startup_control.py',
                 'src/sap_business_agents_platform/runtime_environment.py'):
        file = archive / 'source' / name
        if file.resolve() != file or hashlib.sha256(file.read_bytes()).hexdigest() != manifest['files'].get(name):
            raise ValueError('startup_bootstrap_changed')
    script = archive / 'source/scripts/service-startup.py'
    if b'ACTIVE_STARTUP_VERSION = 1' not in script.read_bytes():
        raise ValueError('startup_active_launcher_upgrade_required')
    return python, script


def status(url):
    # Local services must not be reached through an HTTP proxy.
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=2) as response:
            return json.loads(response.read())
    except OSError:
        return None


def port_open(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.5):
            return True
    except OSError:
        return False


def simple_start(args, python, script):
    """No candidate, installation audit, admission review or activation journal."""
    root = args.root.resolve()
    data = root / '.local-data'
    api = read(data / 'startup-api-current.json')
    site = read(data / 'site-builds/current.json')
    fingerprint = site['fingerprint']
    dist = data / 'site-builds' / fingerprint / 'dist'
    if (not re.fullmatch('[0-9a-f]{64}', fingerprint)
            or Path(site['dist_path']) != dist or dist.resolve() != dist
            or site.get('mode', 'preview') != 'preview'):
        raise ValueError('startup_frontend_binding_invalid')
    boot = runpy.run_path(str(script))  # Only the three hash-checked stdlib modules.
    api_url = f'http://127.0.0.1:{args.port}/api/health'
    site_url = f'http://127.0.0.1:{args.site_port}/sapba-build.json'
    observed = {}

    def inspect():
        health, page = status(api_url), status(site_url)
        api_ok = health is not None
        site_ok = page is not None
        if api_ok:
            if not isinstance(health, dict):
                raise ValueError('startup_api_response_invalid')
            binding = health.get('startup_binding', {})
            if (not boot['valid_health'](health)
                    or not boot['valid_binding'](health, api['source_digest'])
                    or binding.get('environment_digest') != api['environment']['digest']):
                raise ValueError('startup_running_binding_unconfirmed_no_restart_performed')
            identity = boot['service_identities']([binding['pid']], root)[binding['pid']]
            if str(script).replace('\\', '/').casefold() not in identity['CommandLine'].replace('\\', '/').casefold():
                raise ValueError('startup_running_source_unconfirmed')
            observed['api'] = identity
        if site_ok:
            if not isinstance(page, dict) or page.get('fingerprint') != fingerprint:
                raise ValueError('startup_frontend_binding_unconfirmed_no_restart_performed')
            identity = boot['service_identities']([site['pid']], root)[site['pid']]
            command = identity['CommandLine'].replace('\\', '/').casefold()
            if (dist.as_posix().casefold() not in command or ' preview ' not in command
                    or site.get('listener_identity') and not boot['same_process_identity'](site['listener_identity'], identity)):
                raise ValueError('startup_frontend_process_unconfirmed')
            observed['site'] = identity
        return api_ok, site_ok

    # Healthy services need no deployment lock, history read or full disk audit.
    api_ok, site_ok = inspect()
    if not (api_ok and site_ok):
        if args.reuse_only:
            raise ValueError('startup_reuse_not_proven_no_update_performed')
        with boot['control'].file_lock(data / 'deployment.lock', wait=10):
            if ((data / 'startup-maintenance.json').exists()
                    or (data / 'startup/pending-switch.json').exists()):
                raise ValueError('startup_switch_recovery_required')
            # Another double-click may have completed startup while we waited.
            if read(data / 'startup-api-current.json') != api or read(data / 'site-builds/current.json') != site:
                raise ValueError('startup_active_records_changed_open_again')
            api_ok, site_ok = inspect()
            log = data / 'startup' / (time.strftime('%Y%m%dT%H%M%S') + '-simple-' + str(os.getpid()))
            started = {}
            for available, port, name in ((api_ok,args.port,'api'), (site_ok,args.site_port,'frontend')):
                if not available and port_open(port):
                    raise ValueError(f'startup_{name}_not_ready_no_restart_performed')
            if not site_ok and read(dist / 'sapba-build.json').get('fingerprint') != fingerprint:
                raise ValueError('startup_frontend_build_invalid')
            if not api_ok:
                print('[SAPBusinessAgents] Starting the activated API...', flush=True)
                started['api'] = boot['spawn']([str(python), '-I', '-B', str(script), 'serve',
                    '--root', str(root), '--port', str(args.port), '--archive', api['archive']], root, log / 'platform')
            if not site_ok:
                print('[SAPBusinessAgents] Starting the saved Web UI...', flush=True)
                started['site'] = boot['spawn'](['node.exe', str(root / 'site/node_modules/astro/bin/astro.mjs'),
                    'preview', '--host', '127.0.0.1', '--port', str(args.site_port), '--outDir', str(dist),
                    '--ignore-lock'], root / 'site', log / 'site')
                site['pid'] = started['site'].pid
                site['listener_identity'] = boot['service_identities']([site['pid']], root)[site['pid']]
            deadline = time.monotonic() + args.timeout
            while True:
                for component, process in started.items():
                    if process.poll() is not None:
                        raise ValueError(f'startup_{component}_exited; logs: {log}')
                api_ok, site_ok = inspect()
                if api_ok and site_ok:
                    break
                if time.monotonic() >= deadline:
                    raise ValueError(f'startup_ready_timeout; existing services were not stopped; logs: {log}')
                time.sleep(.2)
            # Only refresh process receipts for the same already-activated pair.
            # This is not source/environment activation or SDK certification.
            if 'api' in started:
                api['pid'] = started['api'].pid
                api['listener_identity'] = observed['api']
                boot['control'].atomic(data / 'startup-api-current.json', api)
            if 'site' in started:
                boot['control'].atomic(data / 'site-builds/current.json', site)
    pending = (data / 'startup-maintenance.json').exists() or (data / 'startup/pending-switch.json').exists()
    if pending:
        print('[SAPBusinessAgents] Services are available; an earlier maintenance operation still needs recovery. No maintenance record was removed.', flush=True)
    else:
        print('[SAPBusinessAgents] Ready. Existing services were reused or missing services started. No update was performed.', flush=True)
    if not args.no_browser:
        import webbrowser
        webbrowser.open(f'http://127.0.0.1:{args.site_port}/zh/')
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['ensure'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--site-port', type=int, default=4321)
    parser.add_argument('--timeout', type=int, default=90)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--reuse-only', action='store_true')
    args, maintenance = parser.parse_known_args(argv)
    if not (1 <= args.port <= 65535 and 1 <= args.site_port <= 65535 and 1 <= args.timeout <= 300):
        raise ValueError('startup_arguments_out_of_range')
    python, script = resolve(args.root.resolve())
    if not maintenance:
        return simple_start(args, python, script)
    if any(value not in {'--restart', '--site-only', '--rebuild', '--dev'} for value in maintenance):
        raise ValueError('startup_bootstrap_arguments_invalid')
    # Explicit maintenance keeps its existing controlled implementation.
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    return subprocess.call([str(python), '-I', '-B', str(script), *argv], env=environment)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print('[SAPBusinessAgents] Activated release could not be opened: ' +
              (str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__), file=sys.stderr)
        print('[SAPBusinessAgents] No update, installation or automatic restart was attempted. '
              'Do not edit version records or SDK files.', file=sys.stderr)
        raise SystemExit(1)
