"""Finite checker extraction equivalence; no PASS waiver or live calls."""
import copy
import hashlib
from pathlib import Path

import pytest

from sap_business_agents_platform import runtime_validation_checker as checker
from sap_business_agents_platform import runtime_validation_checker_release as release
from sap_business_agents_platform import runtime_validation_qualification as q
from sap_business_agents_platform.runtime_validation import digest
from sap_business_agents_platform.runtime_validation_store import atomic, capture, read
from tests.test_runtime_validation import pass_fixture


def test_release_is_pinned_to_full_verifier_and_unchanged_foundation():
    root = Path(checker.__file__).parent
    assert hashlib.sha256((root / 'runtime_validation_checker.py').read_bytes()).hexdigest() == release.EXTRACTED_VERIFIER_SHA256
    assert hashlib.sha256((root / 'runtime_validation.py').read_bytes()).hexdigest() == release.LEGACY_FILES['runtime_validation.py']


def extraction_fixture(tmp_path, monkeypatch):
    root = tmp_path / 'old-source'
    package = root / 'src/sap_business_agents_platform'
    package.mkdir(parents=True)
    foundation = Path(checker.__file__).with_name('runtime_validation.py').read_bytes()
    original_policy = b'original full policy fixture\n'
    (package / 'runtime_validation.py').write_bytes(foundation)
    (package / 'runtime_validation_qualification.py').write_bytes(original_policy)
    archive = capture(root, ['src/sap_business_agents_platform/' + n for n in release.LEGACY_FILES], tmp_path / 'original-archives')
    legacy = digest({'runtime_validation.py': foundation.hex(), 'runtime_validation_qualification.py': original_policy.hex()})
    monkeypatch.setattr(release, 'LEGACY_DIGEST', legacy)
    monkeypatch.setattr(release, 'LEGACY_FILES', {'runtime_validation.py': hashlib.sha256(foundation).hexdigest(),
        'runtime_validation_qualification.py': hashlib.sha256(original_policy).hexdigest()})
    (tmp_path / 'execution').mkdir()
    env, case, bound, first, _ = pass_fixture(tmp_path / 'execution', dependencies={'case_checker': legacy, 'business': 'same'})
    body = {k: v for k, v in q.ledger(env.state())[first].items() if k not in {'id', 'sequence', 'created_at'}}
    body['source_snapshot'] = archive
    identifier = q.append(env.value, body)
    target = copy.deepcopy(bound)
    target['dependencies']['case_checker'] = release.checker_digest()
    return env, case, bound, target, identifier, archive


def test_extraction_retains_original_pass_without_state_writes(tmp_path, monkeypatch):
    env, case, bound, target, identifier, archive = extraction_fixture(tmp_path, monkeypatch)
    before = copy.deepcopy(env.state())
    assert release.compatibility(bound, target, archive)['kind'] == 'checker_extraction/1'
    assert q.select(env, env.state(), target, 'env')['id'] == identifier
    assert env.state() == before
    assert checker.verify_pass(env, before['validation_ledger'][identifier])


@pytest.mark.parametrize('change', ['business', 'model', 'site', 'mode', 'case', 'unknown_checker', 'unreviewed_verifier', 'old_source'])
def test_extraction_cannot_hide_other_changes(tmp_path, monkeypatch, change):
    env, case, bound, target, identifier, archive = extraction_fixture(tmp_path, monkeypatch)
    if change == 'business': target['dependencies']['business'] = 'changed'
    if change == 'model': target['runtime']['model'] = 'changed'
    if change == 'site': target['runtime']['service_site'] = 'international'
    if change == 'mode': target['mode'] = 'trusted_local'
    if change == 'case': target['case_digest'] = 'changed'
    if change == 'unknown_checker': target['dependencies']['case_checker'] = 'unreviewed'
    if change == 'unreviewed_verifier': monkeypatch.setattr(release, 'EXTRACTED_VERIFIER_SHA256', 'changed')
    if change == 'old_source':
        (Path(archive['directory']) / 'source/src/sap_business_agents_platform/runtime_validation_qualification.py').write_bytes(b'tampered')
    assert not release.compatibility(bound, target, archive)
    assert q.select(env, env.state(), target, 'env') is None


@pytest.mark.parametrize('field,value', [('status','cancelled'), ('cleanup_complete',False), ('validation_job',False),
    ('permission_mode','trusted_local'), ('runtime_digest','changed'), ('environment_digest','changed'),
    ('validation_parent_job','changed'), ('operation','other')])
def test_checker_still_rejects_invalid_native_jobs(tmp_path, field, value):
    env, case, bound, identifier, _ = pass_fixture(tmp_path)
    original = q.ledger(env.state())[identifier]
    path = env.root / 'jobs' / (digest(original['owned_jobs'][0]['job_id']) + '.json')
    job = read(path)
    job[field] = value
    atomic(path, job)
    # Re-hash the adversarial fixture so this exercises the field gate, not
    # merely the stale artifact hash gate.
    altered = copy.deepcopy(original)
    altered['owned_jobs'][0]['digest'] = digest(job)
    altered['id'] = digest({k: v for k, v in altered.items() if k != 'id'})
    assert not checker.verify_pass(env, altered)


def test_governance_edits_do_not_change_checker_but_checker_edits_do(monkeypatch):
    before = release.checker_digest()
    read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda p: read_bytes(p) + b'\n# changed policy'
        if p.name == 'runtime_validation_qualification.py' else read_bytes(p))
    assert release.checker_digest() == before
    monkeypatch.setattr(Path, 'read_bytes', lambda p: read_bytes(p) + b'\n# changed evidence checks'
        if p.name == 'runtime_validation_checker.py' else read_bytes(p))
    assert release.checker_digest() != before
