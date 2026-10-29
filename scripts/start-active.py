"""Standard-library bootstrap: open the activated release, never deploy a checkout."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    began = time.monotonic()
    if not argv or argv[0] != 'ensure' or '--root' not in argv:
        raise ValueError('startup_bootstrap_arguments_invalid')
    root = Path(argv[argv.index('--root') + 1]).resolve()
    python, script = resolve(root)
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    return subprocess.call([str(python), '-I', '-B', str(script), *argv,
                            '--started', str(began)], env=environment)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('[SAPBusinessAgents] Activated release could not be opened: ' +
              (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        print('[SAPBusinessAgents] No checkout update was attempted. Use explicit activation/recovery; '
              'do not edit version records or SDK files.', file=sys.stderr)
        raise SystemExit(1)
