"""Finite, reviewed checker extraction compatibility, never a hash waiver.

Only the known original full-file pair and the exact extracted verifier release
are compatible. Original PASS, frozen source and every business dependency are
still checked. Unrecognized checker changes fail closed.
"""
import copy
import hashlib
from pathlib import Path

from .runtime_validation import ValidationError, digest
from .runtime_validation_store import verify_archive

LEGACY_DIGEST = '4feedf6c2aecee319a754c971324e9311d518ce2b81e579f812d2c49d3d2e486'
LEGACY_FILES = {
    'runtime_validation.py': '81e4b2bc9e97515f76795f4eb050cb652fad3ff45a4c9a02c7a5152ced7f6708',
    'runtime_validation_qualification.py': '11d80304d41459298b097609dbd51d9c004da3370ef939bd445babdb30f11923',
}
EXTRACTED_VERIFIER_SHA256 = 'e297d19cfb93bd1e0455c0623202025c8bb11c9cac0887e8314c3c6269dafbb0'


def checker_digest():
    root = Path(__file__).parent
    return digest({name: (root / name).read_bytes().hex()
        for name in ('runtime_validation.py', 'runtime_validation_checker.py')})


def compatibility(before, after, source_snapshot):
    """Return evidence metadata only for the exact, unchanged checker extraction."""
    if before == after:
        return {'kind': 'identical_binding'}
    old, new = copy.deepcopy(before), copy.deepcopy(after)
    original = old.get('dependencies', {}).pop('case_checker', None)
    current = new.get('dependencies', {}).pop('case_checker', None)
    if old != new or original != LEGACY_DIGEST or current != checker_digest():
        return None
    root = Path(__file__).parent
    if (hashlib.sha256((root / 'runtime_validation_checker.py').read_bytes()).hexdigest() != EXTRACTED_VERIFIER_SHA256
            or hashlib.sha256((root / 'runtime_validation.py').read_bytes()).hexdigest() != LEGACY_FILES['runtime_validation.py']):
        return None
    try:
        archive = verify_archive(source_snapshot['directory'], source_snapshot['digest'])
        prefix = 'src/sap_business_agents_platform/'
        if any(archive['files'].get(prefix + name) != expected for name, expected in LEGACY_FILES.items()):
            return None
    except (ValidationError, OSError, KeyError, ValueError, TypeError):
        return None
    return {'kind': 'checker_extraction/1', 'source_checker_digest': original,
        'target_checker_digest': current, 'source_archive_digest': source_snapshot['digest']}
