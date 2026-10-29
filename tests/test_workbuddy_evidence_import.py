"""Terminal evidence import uses synthetic bytes, never an SDK/model/SAP."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import workbuddy_job_index as index
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError, atomic_json, digest
from sap_business_agents_platform.workbuddy_online_evidence import copy_evidence, copy_job
from sap_business_agents_platform.sdk_manager import SDKManagerError


TASK = 'model_check_synthetic_import'
NAME = digest(TASK) + '.json'


def raw_record(**changes):
    record = {'task_id': TASK, 'operation': 'model_check', 'status': 'completed',
              'cleanup_complete': True, 'note': '\u4e2d\u6587'}
    record.update(changes)
    # Deliberately differs from atomic_json's whitespace/key/Unicode encoding.
    return (json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n').encode()


def indexed(root, *, entries=None, dirty=None):
    root.mkdir(parents=True, exist_ok=True)
    value = {'protocol': index.PROTOCOL, 'revision': 7, 'entries': entries or {}, 'dirty': dirty}
    (root / 'unfinished-jobs.json').write_text(json.dumps({**value, 'sha256': digest(value)}))
    (root / 'job-index-protocol.json').write_text(json.dumps({'protocol': index.PROTOCOL}))


def source(root, raw, *, use_index=False):
    (root / 'jobs').mkdir(parents=True)
    (root / 'jobs' / NAME).write_bytes(raw)
    if use_index:
        indexed(root)
    return SimpleNamespace(root=root)


def do_import(root, raw=None, **kwargs):
    raw = raw_record() if raw is None else raw
    return index.import_completed_job(root, raw, TASK, hashlib.sha256(raw).hexdigest(), **kwargs)


@pytest.mark.parametrize('source_indexed', [False, True])
@pytest.mark.parametrize('destination_indexed', [False, True])
def test_exact_byte_copy_works_across_all_index_modes(tmp_path, source_indexed, destination_indexed):
    raw = raw_record()
    src = source(tmp_path / 'source', raw, use_index=source_indexed)
    dst = SimpleNamespace(root=tmp_path / 'target')
    if destination_indexed:
        indexed(dst.root)
    ref = {'path': 'jobs/' + NAME, 'sha256': hashlib.sha256(raw).hexdigest()}
    check = {'source_job_id': TASK, 'source_job_digest': digest(json.loads(raw))}
    cert = {'evidence': {'transport_origin': ref, 'repeated': ref}}
    copy_evidence(src, dst, cert, check)
    copy_evidence(src, dst, cert, check)
    assert (dst.root / ref['path']).read_bytes() == raw
    assert hashlib.sha256((dst.root / ref['path']).read_bytes()).hexdigest() == ref['sha256']
    if destination_indexed:
        state = index._load(dst.root)
        assert state['entries'] == {} and state['dirty'] is None and state['revision'] == 8
        assert index.paths(dst.root) == []
    else:
        assert not index.enabled(dst.root)


def test_import_does_not_relax_ordinary_update_of_missing_job(tmp_path):
    indexed(tmp_path)
    with pytest.raises(WorkBuddyError, match='workbuddy_job_index_recovery_required'):
        atomic_json(tmp_path / 'jobs' / NAME, json.loads(raw_record()))
    assert do_import(tmp_path) == 'created'


def test_concurrent_identical_import_creates_once(tmp_path):
    indexed(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: do_import(tmp_path), range(8)))
    assert results.count('created') == 1
    assert results.count('already_present') == 7
    assert index._load(tmp_path)['revision'] == 8


@pytest.mark.parametrize('change', [
    {'task_id': 'another-task'}, {'operation': 'free_query'}, {'status': 'running'},
    {'status': 'failed'}, {'cleanup_complete': False}, {'cleanup_complete': 1},
])
def test_non_model_or_unfinished_records_are_never_imported(tmp_path, change):
    indexed(tmp_path)
    before = (tmp_path / 'unfinished-jobs.json').read_bytes()
    with pytest.raises(WorkBuddyError, match='workbuddy_job_import_invalid'):
        do_import(tmp_path, raw_record(**change))
    assert (tmp_path / 'unfinished-jobs.json').read_bytes() == before
    assert not (tmp_path / 'jobs' / NAME).exists()


def test_bad_hash_and_json_are_rejected(tmp_path):
    with pytest.raises(WorkBuddyError, match='workbuddy_job_import_invalid'):
        index.import_completed_job(tmp_path, raw_record(), TASK, '0' * 64)
    with pytest.raises(WorkBuddyError, match='workbuddy_job_import_invalid'):
        do_import(tmp_path, b'not JSON')


@pytest.mark.parametrize('prior', [raw_record(note='conflicting proof'), raw_record(status='running', cleanup_complete=False)])
def test_existing_conflicting_or_active_record_is_immutable(tmp_path, prior):
    indexed(tmp_path)
    (tmp_path / 'jobs').mkdir()
    (tmp_path / 'jobs' / NAME).write_bytes(prior)
    before = (tmp_path / 'unfinished-jobs.json').read_bytes()
    with pytest.raises(WorkBuddyError, match='workbuddy_job_import_conflict'):
        do_import(tmp_path)
    assert (tmp_path / 'jobs' / NAME).read_bytes() == prior
    assert (tmp_path / 'unfinished-jobs.json').read_bytes() == before


def test_matching_bytes_do_not_override_unfinished_index_entry(tmp_path):
    raw = raw_record()
    entry = {'status': 'running', 'record_digest': digest(json.loads(raw))}
    indexed(tmp_path, entries={NAME: entry})
    (tmp_path / 'jobs').mkdir()
    (tmp_path / 'jobs' / NAME).write_bytes(raw)
    with pytest.raises(WorkBuddyError, match='workbuddy_job_import_conflict'):
        do_import(tmp_path)
    assert index._load(tmp_path)['entries'] == {NAME: entry}


def test_other_active_entries_are_preserved(tmp_path):
    other = {'task_id': 'other', 'status': 'running', 'cleanup_complete': False}
    other_name = digest('other') + '.json'
    indexed(tmp_path, entries={other_name: index._entry(other)})
    (tmp_path / 'jobs').mkdir()
    (tmp_path / 'jobs' / other_name).write_text(json.dumps(other))
    do_import(tmp_path)
    assert index.paths(tmp_path) == [(tmp_path / 'jobs' / other_name, other)]


@pytest.mark.parametrize('where', ['before_dirty', 'after_dirty', 'after_job'])
def test_interrupted_transaction_never_auto_repairs_or_claims_success(tmp_path, monkeypatch, where):
    indexed(tmp_path)
    original_store = index._store
    original_publish = index._atomic_bytes_owned
    calls = 0
    def store(root, value, **kwargs):
        nonlocal calls
        calls += 1
        if (where == 'before_dirty' and calls == 1) or (where == 'after_job' and calls == 2):
            raise OSError('synthetic crash')
        return original_store(root, value, **kwargs)
    def publish(*args, **kwargs):
        if where == 'after_dirty':
            raise OSError('synthetic crash')
        return original_publish(*args, **kwargs)
    monkeypatch.setattr(index, '_store', store)
    monkeypatch.setattr(index, '_atomic_bytes_owned', publish)
    with pytest.raises(OSError, match='synthetic crash'):
        do_import(tmp_path)
    monkeypatch.setattr(index, '_store', original_store)
    monkeypatch.setattr(index, '_atomic_bytes_owned', original_publish)
    if where == 'before_dirty':
        assert index._load(tmp_path)['dirty'] is None
        assert do_import(tmp_path) == 'created'
    else:
        assert json.loads((tmp_path / 'unfinished-jobs.json').read_bytes())['dirty'] == NAME
        with pytest.raises(WorkBuddyError, match='workbuddy_job_index_recovery_required'):
            do_import(tmp_path)
        assert (tmp_path / 'jobs' / NAME).exists() is (where == 'after_job')


@pytest.mark.parametrize('location', ['source', 'target'])
def test_dirty_index_blocks_source_and_target(tmp_path, location):
    src = source(tmp_path / 'source', raw_record(), use_index=True)
    dst = SimpleNamespace(root=tmp_path / 'target')
    indexed(dst.root)
    indexed(src.root if location == 'source' else dst.root, dirty=NAME)
    with pytest.raises(WorkBuddyError, match='workbuddy_job_index_recovery_required'):
        copy_job(src, dst, {'source_job_id': TASK})
    assert not (dst.root / 'jobs' / NAME).exists()


@pytest.mark.parametrize('field,value', [('path', 'jobs/' + digest('other') + '.json'), ('path', '../jobs/' + NAME), ('sha256', '0' * 64)])
def test_certificate_reference_must_match_actual_job(tmp_path, field, value):
    src = source(tmp_path / 'source', raw_record())
    dst = SimpleNamespace(root=tmp_path / 'target')
    ref = {'path': 'jobs/' + NAME, 'sha256': hashlib.sha256(raw_record()).hexdigest()}
    ref[field] = value
    with pytest.raises(SDKManagerError):
        copy_evidence(src, dst, {'evidence': {'transport_origin': ref}}, {'source_job_id': TASK})
    assert not (dst.root / 'jobs' / NAME).exists()


def test_source_semantic_digest_is_checked(tmp_path):
    src = source(tmp_path / 'source', raw_record())
    with pytest.raises(SDKManagerError) as caught:
        copy_job(src, SimpleNamespace(root=tmp_path / 'target'), {'source_job_id': TASK, 'source_job_digest': '0' * 64})
    assert caught.value.code == 'workbuddy_update_evidence_required'


@pytest.mark.parametrize('location', ['source', 'target'])
def test_jobs_directory_link_cannot_escape_environment(tmp_path, location):
    outside = tmp_path / 'outside'
    outside.mkdir()
    root = tmp_path / location
    root.mkdir()
    link = root / 'jobs'
    if os.name == 'nt':
        result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        if location == 'target':
            with pytest.raises(WorkBuddyError, match='workbuddy_job_import_invalid'):
                do_import(root)
        else:
            (outside / NAME).write_bytes(raw_record())
            with pytest.raises(WorkBuddyError, match='workbuddy_job_import_invalid'):
                copy_job(SimpleNamespace(root=root), SimpleNamespace(root=tmp_path / 'target'), {'source_job_id': TASK})
    finally:
        # Remove only the junction/symlink itself, never its target.
        if os.name == 'nt':
            os.rmdir(link)
        else:
            link.unlink()


def test_expired_budget_does_not_begin_transaction(tmp_path):
    indexed(tmp_path)
    before = (tmp_path / 'unfinished-jobs.json').read_bytes()
    with pytest.raises(TimeoutError):
        do_import(tmp_path, deadline=time.monotonic() - 1)
    assert (tmp_path / 'unfinished-jobs.json').read_bytes() == before


def test_atomic_byte_publish_retries_only_sharing_violations(tmp_path, monkeypatch):
    indexed(tmp_path)
    original = index.os.replace
    failures = []
    def replace(src, dst):
        if Path(dst).name == NAME and not failures:
            failures.append(True)
            error = OSError('sharing violation')
            error.winerror = 32
            raise error
        return original(src, dst)
    monkeypatch.setattr(index.os, 'replace', replace)
    assert do_import(tmp_path) == 'created'
    assert failures == [True]
    assert (tmp_path / 'jobs' / NAME).read_bytes() == raw_record()


def test_certificate_namespaces_operation_and_infrastructure_and_is_immutable(tmp_path, monkeypatch):
    from sap_business_agents_platform import workbuddy_online_evidence as evidence
    src = source(tmp_path / 'source', raw_record())
    release = {'environment_digest': 'a' * 64, 'worker': 'worker.py', 'files': {'worker.py': 'b' * 64}}
    src.state = lambda: {'default_model_id': 'm', 'models': {'m': {'source_job_id': TASK}}}
    src.release = lambda: release
    target = {'infrastructure': {'host': 'version-one'}}
    monkeypatch.setattr(evidence.integration, 'binding', lambda _: target)
    monkeypatch.setattr(evidence.integration, 'audit', lambda env, rel, proof: proof)
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_sdk_target_audit.target_model_origin', lambda *_: True)
    report = tmp_path / 'checks.xml'
    report.write_bytes(b'<testsuites/>')
    support = {'id': 'support', 'operations': {'free_query': ['bounded']}}
    session = SimpleNamespace(environment=src, evidence_id='first')
    first = evidence.certificate(session, support, report)
    assert evidence.certificate(session, support, report) == first
    session.evidence_id = 'second'
    second = evidence.certificate(session, support, report)
    assert first['core']['authorization']['path'] != second['core']['authorization']['path']
    report.write_bytes(b'<testsuites changed="true"/>')
    with pytest.raises(SDKManagerError) as caught:
        evidence.certificate(session, support, report)
    assert caught.value.code == 'sdk_update_binding_changed'
    target['infrastructure']['host'] = 'version-two'
    third = evidence.certificate(session, support, report)
    assert third['core']['authorization']['path'] != second['core']['authorization']['path']
