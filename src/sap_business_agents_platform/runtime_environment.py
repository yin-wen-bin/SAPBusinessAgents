"""Explicit Python environment bindings. Stdlib only; never imports an SDK.

Bindings describe installation bytes, not authentication or execution authority.
No persistent environment registry: releases retain their own immutable binding.
"""
from __future__ import annotations

import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile


# Reviewed vendor bytes, not caller-supplied trust. This release has an upstream
# WHEEL/RECORD discrepancy. Every installed artifact member must still match the
# entire hash-verified original wheel; no filename is exempt from validation.
REVIEWED_ARTIFACTS = {
    ('codebuddy-agent-sdk', '0.3.247'):
        '1189d1f06adc3358e62e134bcd704c8ea2089cb271addd90f8f924d1c25fd398',
}


class EnvironmentError(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def package_name(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def python_path(environment):
    return Path(environment) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def payload_files(environment):
    """Include unrecorded import hooks/scripts; RECORD alone misses injected files."""
    files = {}
    for parent, folders, names in os.walk(environment, followlinks=False):
        base = Path(parent)
        for name in folders + names:
            item = base/name
            if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
                raise EnvironmentError('runtime_environment_link_rejected')
        folders[:] = [name for name in folders if name != '__pycache__']
        for name in names:
            path = base/name
            if path.suffix != '.pyc':
                files[path.relative_to(environment).as_posix()] = sha(path)
    return files


def _inventory(directory):
    files, packages = {}, {}
    for info in sorted(directory.glob('*.dist-info')):
        if info.is_symlink() or not info.is_dir():
            raise EnvironmentError('runtime_environment_link_rejected')
        for name in ('METADATA', 'RECORD'):
            path = info / name
            if path.is_symlink() or not path.is_file():
                raise EnvironmentError('runtime_environment_metadata_missing')
            files[path.relative_to(directory).as_posix()] = sha(path)
        metadata = BytesParser().parsebytes((info/'METADATA').read_bytes())
        name = package_name(metadata.get('Name', ''))
        version = metadata.get('Version', '')
        if not name or not version or name in packages:
            raise EnvironmentError('runtime_environment_package_ambiguous')
        packages[name] = version
    if not packages:
        raise EnvironmentError('runtime_environment_empty')
    return files, packages


def _artifact_evidence(binding, proofs, contents):
    """Prove vendor RECORD discrepancies from independently reviewed artifacts.

    Never infer provenance from the installed bytes or accept an arbitrary hash.
    No extraction, SDK import, network access, RECORD rewrite or trust renewal.
    """
    site = Path(binding['site'])
    environment = Path(binding['path'])
    justified = {}
    seen = set()
    for proof in proofs:
        if not isinstance(proof, dict) or set(proof) != {'distribution', 'version', 'path', 'sha256'}:
            raise ValueError('artifact_reference')
        key = (proof['distribution'], proof['version'])
        trusted = REVIEWED_ARTIFACTS.get(key)
        if (not trusted or proof['sha256'] != trusted or key in seen
                or binding['packages'].get(key[0]) != key[1]):
            raise ValueError('artifact_not_reviewed')
        seen.add(key)
        path = Path(proof['path'])
        if not path.is_absolute() or path.resolve() != path or any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
                for p in (path, *path.parents)) or path.stat().st_size > 1024**3:
            raise ValueError('artifact_path')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != trusted:
            raise ValueError('artifact_hash')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            names = [item.filename for item in members]
            if len({name.casefold() for name in names}) != len(names):
                raise ValueError('artifact_duplicate_member')
            infos = {name.split('/')[0] for name in names if name.split('/')[0].endswith('.dist-info')}
            if len(infos) != 1:
                raise ValueError('artifact_distribution')
            info = next(iter(infos))
            metadata = BytesParser().parsebytes(archive.read(info+'/METADATA'))
            if (package_name(metadata.get('Name', '')), metadata.get('Version')) != key:
                raise ValueError('artifact_identity')
            original_rows = list(csv.reader(io.StringIO(archive.read(info+'/RECORD').decode('utf-8'))))
            if any(len(row) != 3 for row in original_rows) or len({row[0].casefold() for row in original_rows}) != len(original_rows):
                raise ValueError('artifact_record')
            rows = {row[0]: row for row in original_rows}
            if set(rows) != set(names):
                raise ValueError('artifact_inventory')
            if sum(item.file_size for item in members) > 1024**3:
                raise ValueError('artifact_size')
            for member in members:
                name = member.filename
                relative = Path(name)
                if (relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name
                        or not (site/relative).resolve().is_relative_to(site.resolve())
                        or (member.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError('artifact_member_path')
                if name == info+'/RECORD':
                    continue  # pip adds installer-owned rows; normal RECORD checks remain below.
                raw = archive.read(member)  # Includes ZIP CRC verification.
                original_hash = hashlib.sha256(raw).hexdigest()
                installed = (site/relative).relative_to(environment).as_posix()
                if contents.get(installed) != original_hash:
                    raise ValueError('artifact_installed_content')
                row = rows[name]
                encoded = 'sha256='+base64.urlsafe_b64encode(bytes.fromhex(original_hash)).decode().rstrip('=')
                if row[1] != encoded or row[2] and row[2] != str(len(raw)):
                    # An installed failure is justified only by the exact same
                    # erroneous vendor row and the hash-proven original bytes.
                    justified[(info, name)] = tuple(row)
    return justified


def prove_official_artifact(binding, artifact):
    """Explicit maintenance preparation; returns evidence, never writes a binding."""
    artifact = Path(artifact).resolve()
    observed = sha(artifact)
    identities = [key for key, value in REVIEWED_ARTIFACTS.items() if value == observed]
    if len(identities) != 1:
        raise EnvironmentError('runtime_artifact_not_reviewed')
    name, version = identities[0]
    proof = {'distribution': name, 'version': version, 'path': str(artifact), 'sha256': observed}
    try:
        _artifact_evidence(binding, [proof], binding['payload_files'])
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        raise EnvironmentError('runtime_artifact_proof_invalid') from exc
    return proof


def capture(environment, *, kind='candidate', run=subprocess.run):
    environment = Path(environment).resolve()
    python = python_path(environment)
    if not python.is_file() or not (environment/'pyvenv.cfg').is_file():
        raise EnvironmentError('runtime_environment_missing')
    # Isolated stdlib inspection only, no package import, login or CLI invocation.
    command = ('import sys,sysconfig,json; print(json.dumps(dict(prefix=sys.prefix,'
               'base_executable=getattr(sys,"_base_executable",sys.executable),'
               'version=sys.version,site=sysconfig.get_path("purelib"))))')
    result = run([str(python), '-I', '-B', '-c', command], capture_output=True,
                 text=True, check=True, timeout=10)
    observed = json.loads(result.stdout)
    site = Path(observed['site']).resolve()
    if Path(observed['prefix']).resolve() != environment or not site.is_relative_to(environment):
        raise EnvironmentError('runtime_environment_prefix_mismatch')
    cfg = (environment/'pyvenv.cfg').read_text(encoding='utf-8').lower()
    if not re.search(r'include-system-site-packages\s*=\s*false', cfg):
        raise EnvironmentError('runtime_environment_system_packages_forbidden')
    files, packages = _inventory(site)
    base = str(Path(observed['base_executable']).resolve())
    value = {'version': 1, 'kind': kind, 'path': str(environment), 'python': str(python),
             'site': str(site), 'python_sha256': sha(python), 'base_python': base,
             'base_sha256': sha(base), 'python_version': observed['version'],
             'cfg_sha256': sha(environment/'pyvenv.cfg'), 'files': files, 'packages': packages,
             'payload_files': payload_files(environment)}
    return {**value, 'digest': digest(value)}


def verify(binding, *, deep=True):
    """Compare existing bytes; never renew a binding after an external change."""
    try:
        body = {k:v for k,v in binding.items() if k != 'digest'}
        if binding['version'] != 1 or digest(body) != binding['digest']:
            raise ValueError('digest')
        environment, site = Path(binding['path']), Path(binding['site'])
        if (not environment.is_absolute() or environment.resolve() != environment
                or not site.resolve().is_relative_to(environment)
                or Path(binding['python']) != python_path(environment)):
            raise ValueError('path')
        if (sha(binding['python']) != binding['python_sha256']
                or sha(binding['base_python']) != binding['base_sha256']
                or sha(environment/'pyvenv.cfg') != binding['cfg_sha256']):
            raise ValueError('interpreter')
        files, packages = _inventory(site)
        if files != binding['files'] or packages != binding['packages']:
            raise ValueError('inventory')
        if deep:
            contents=payload_files(environment)
            if binding.get('payload_files') != contents:
                raise ValueError('payload')
            justified = _artifact_evidence(binding, binding.get('artifact_proofs', []), contents)
            for info in site.glob('*.dist-info'):
                for name, encoded, size in csv.reader(io.StringIO((info/'RECORD').read_text(encoding='utf-8'))):
                    original = site/name
                    path = original.resolve()
                    if not path.is_relative_to(environment) or original.is_symlink():
                        raise ValueError('record_path')
                    if not encoded:
                        if name.endswith(('/RECORD', '.pyc')):
                            continue
                        raise ValueError('unhashed_record')
                    algorithm, value = encoded.split('=', 1)
                    if algorithm != 'sha256' or not path.is_file():
                        raise ValueError('record_hash')
                    actual = base64.urlsafe_b64encode(bytes.fromhex(contents[path.relative_to(environment).as_posix()])).decode().rstrip('=')
                    if actual != value or size and path.stat().st_size != int(size):
                        if justified.get((info.name, name)) != (name, encoded, size):
                            raise ValueError('record_content')
        return binding
    except (OSError, KeyError, ValueError, TypeError, zipfile.BadZipFile) as exc:
        raise EnvironmentError('runtime_environment_changed') from exc


def assert_running(binding):
    verify(binding)
    if Path(sys.executable).resolve() != Path(binding['python']).resolve():
        raise EnvironmentError('runtime_environment_interpreter_mismatch')


def legacy_binding(root, manifest, *, run=subprocess.run, artifact_proofs=()):
    """Register only a legacy installation matching its approved snapshot."""
    root = Path(root).resolve()
    binding = capture(root/'.venv', kind='legacy', run=run)
    if artifact_proofs:
        binding['artifact_proofs'] = list(artifact_proofs)
        binding['digest'] = digest({k:v for k,v in binding.items() if k != 'digest'})
    expected = manifest.get('dependencies', {})
    actual = {'.venv/Lib/site-packages/'+name:value for name,value in binding['files'].items()}
    stored = {k:v for k,v in expected.items() if k.startswith('.venv/Lib/site-packages/')}
    interpreter = expected.get('interpreter', {})
    if (not stored or stored != actual or expected.get('.venv/pyvenv.cfg') != binding['cfg_sha256']
            or interpreter.get('sha256') != binding['python_sha256']
            or interpreter.get('base_sha256') != binding['base_sha256']):
        raise EnvironmentError('runtime_legacy_binding_mismatch')
    return binding
