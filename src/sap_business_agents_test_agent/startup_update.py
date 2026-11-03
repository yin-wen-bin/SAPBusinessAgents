"""Publish a startup proposal after verified application; never activate here."""
from __future__ import annotations

import hashlib
import runpy
import uuid
from .common import write_json


def register_application(store, task):
    if (task.get('phase') != 'completed' or task.get('application', {}).get('status') != 'applied'
            or task.get('final_round') != task.get('round') or task.get('regressions')
            or not task.get('verified_patch_digest')
            or task.get('verified_patch_digest') != task.get('final_patch_digest')):
        return {'status': 'blocked', 'code': 'startup_update_verification_incomplete'}
    root = store.repository.resolve()
    if not (root / '.local-data/startup-api-current.json').is_file():
        return {'status': 'not_configured'}
    source = store.task_dir(task['id']) / 'source'
    script = root / 'scripts/startup-update.py'
    candidate_script = source / 'scripts/startup-update.py'
    if (not script.is_file() or not candidate_script.is_file()
            or script.read_bytes() != candidate_script.read_bytes()):
        return {'status': 'blocked', 'code': 'startup_update_verifier_not_in_tested_source'}
    module = runpy.run_path(str(script))
    expected = module['inputs'](source)
    if module['inputs'](root) != expected:
        return {'status': 'blocked', 'code': 'startup_update_workspace_differs_from_tested_source'}
    current = task['results'][str(task['round'])]
    if not current or any(value['status'] != 'passed' for value in current.values()):
        return {'status': 'blocked', 'code': 'startup_update_verification_incomplete'}
    proof = root / '.local-data/startup/updates' / uuid.uuid4().hex / 'auto-test-proof.json'
    write_json(proof, {'task_id': task['id'], 'round': task['round'],
        'source_digest': task['round_source_digest'], 'verified_patch_digest': task['verified_patch_digest'],
        'cases': {name: {'status': value['status'], 'mode': value['mode'],
                         'evidence': value.get('evidence_manifest', [])} for name, value in current.items()},
        'business_acceptance_granted': False})
    return module['register'](root, expected, {'passed': True, 'kind': 'completed_auto_test',
        'task_id': task['id'], 'evidence': {proof.relative_to(root).as_posix(): hashlib.sha256(proof.read_bytes()).hexdigest()}})
