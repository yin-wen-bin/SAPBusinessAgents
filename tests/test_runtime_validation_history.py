"""Original evidence and per-case decisions. All calls remain offline/read-only."""
import copy
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from sap_business_agents_platform import runtime_validation_qualification as q
from sap_business_agents_platform.runtime_validation import Registry, ValidationError, digest
from sap_business_agents_platform.runtime_validation_history import (
    dependency_changes, qualification_decisions, recover_source_index,
)
from sap_business_agents_platform.runtime_validation_store import atomic, capture
from tests.test_runtime_validation import pass_fixture
from tests.test_runtime_validation_extended import make_receipt
from tests.test_workbuddy_qualification import manager_fixture, live_pass


def test_dependency_diff_includes_removed_file_edges_and_budget():
    before = {'budget_seconds': 600, 'components': {'x': {'requires': [], 'files': {'a': 'old', 'removed': 'hash'}}}}
    after = {'budget_seconds': 300, 'components': {'x': {'requires': ['y'], 'files': {'a': 'new'}}}}
    changes = dependency_changes(before, after)
    assert {'kind': 'budget_seconds'} in changes
    assert {'kind': 'dependency_edges', 'component': 'x'} in changes
    assert {r['file'] for r in changes if r['kind'] == 'file'} == {'a', 'removed'}


def test_recovery_uses_verified_archive_not_current_matching_file(tmp_path):
    original = tmp_path / 'original'
    original.mkdir()
    (original / 'code.py').write_bytes(b'value = 1\r\n')
    reference = capture(original, ['code.py'], tmp_path / 'archives')
    wanted = {'code.py': hashlib.sha256(b'value = 1\n').hexdigest()}
    result = recover_source_index(original, wanted)
    assert not result['complete'] and result['missing'] == ['code.py']
    result = recover_source_index(original, wanted, archives=[reference])
    assert result['complete'] and not result['qualification_granted'] and not result['working_tree_used']
    (original / 'code.py').write_text('changed working checkout')
    assert recover_source_index(original, wanted, archives=[reference]) == result
    from pathlib import Path
    (Path(reference['directory']) / 'source/code.py').write_text('tampered')
    result = recover_source_index(original, wanted, archives=[reference])
    assert result['missing'] == ['code.py'] and result['rejected_archives']


def test_recovery_rejects_traversal_and_unbound_git_revision(tmp_path):
    with pytest.raises(ValidationError):
        recover_source_index(tmp_path, {'../code.py': 'a' * 64})
    with pytest.raises(ValidationError):
        recover_source_index(tmp_path, {'code.py': 'a' * 64}, git_commit='HEAD')


def test_original_git_content_must_match_recorded_digest(tmp_path, monkeypatch):
    monkeypatch.setattr('subprocess.check_output', lambda *a, **kw: b'old content\n')
    wanted = {'code.py': hashlib.sha256(b'old content\n').hexdigest()}
    assert recover_source_index(tmp_path, wanted, git_commit='b' * 40)['complete']
    wanted['code.py'] = 'c' * 64
    assert not recover_source_index(tmp_path, wanted, git_commit='b' * 40)['complete']


def test_historical_copy_recovers_content_not_case_or_complete_archive(tmp_path):
    directory = tmp_path / '.local-data/workbuddy-authoring/original-workspace/source'
    directory.mkdir(parents=True)
    (directory / 'code.py').write_bytes(b'old content\n')
    wanted = {'code.py': hashlib.sha256(b'old content\n').hexdigest()}
    result = recover_source_index(tmp_path, wanted, historical_sources=[directory])
    assert result['complete'] and not result['qualification_granted']
    assert result['recovered']['code.py']['complete_archive_proven'] is False
    with pytest.raises(ValidationError, match='source_location_invalid'):
        recover_source_index(tmp_path, wanted, historical_sources=[tmp_path])
    (directory / 'code.py').write_text('different content')
    assert not recover_source_index(tmp_path, wanted, historical_sources=[directory])['complete']


def selected(monkeypatch, case, bound):
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_validation.case_bindings', lambda *a: {case.id: bound})
    return Registry({case.id: case})


def test_current_pass_retained_and_invalid_origin_not_counted(tmp_path, monkeypatch):
    env, case, bound, original, _ = pass_fixture(tmp_path)
    registry = selected(monkeypatch, case, bound)
    before = copy.deepcopy(env.state())
    result = qualification_decisions(env, env.state(), {'environment_digest': 'env'}, {}, registry=registry)
    assert result['cases'][0]['action'] == 'retain_current'
    assert result['cases'][0]['current_record_id'] == original
    assert env.state() == before
    monkeypatch.setattr(q, 'verify_pass', lambda *a: False)
    result = qualification_decisions(env, env.state(), {'environment_digest': 'env'}, {}, registry=registry)
    assert result['cases'][0]['action'] == 'evidence_unresolved'
    assert not result['historical_operations']


def test_actual_verified_receipt_not_constant_empty_list(tmp_path, monkeypatch):
    env, case, bound, original, receipt = make_receipt(tmp_path)
    registry = selected(monkeypatch, case, bound)
    target = {'environment_digest': receipt.target_environment}
    result = qualification_decisions(env, env.state(), target, {}, registry=registry)
    assert result['cases'][0]['action'] == 'evidence_unresolved'
    assert result['reuse_candidates'][0]['status'] == 'bound_receipt_required'
    result = qualification_decisions(env, env.state(), target, {}, registry=registry, receipts=[receipt])
    assert result['cases'][0]['action'] == 'reuse_ready'
    assert len(result['reuse_candidates']) == 1
    assert not any(r['kind'] == 'case_reuse' for r in q.ledger(env.state()).values())
    wrong = replace(receipt, target_binding={**bound, 'runtime': {**bound['runtime'], 'model': 'other'}})
    assert not any(r['status'] == 'reuse_ready' for r in
        qualification_decisions(env, env.state(), target, {}, registry=registry, receipts=[wrong])['reuse_candidates'])


def test_legacy_mode_model_and_dependency_decision(tmp_path, monkeypatch):
    from sap_business_agents_platform import workbuddy_qualification as old
    manager, release = manager_fixture(tmp_path)
    snapshot, record, _ = live_pass(manager, release, operation='review_agent_feedback')
    original_registry = Registry()
    ids = [c.id for c in original_registry.requirements('review_agent_feedback')]
    registry = Registry({key: original_registry.entries[key] for key in original_registry.closure(ids)})
    bindings = {case.id: {'runtime': {}, 'dependencies': {}} for case in registry.entries.values()}
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_validation.case_bindings', lambda *a: bindings)
    result = qualification_decisions(manager.environment, manager.environment.state(), release, snapshot, registry=registry)
    assert result['cases'][0]['historical_pass']
    assert result['cases'][0]['action'] == 'evidence_unresolved'
    assert not result['cases'][1]['historical_pass']  # bounded never covers trusted_local
    old_manifests = old.dependency_manifests
    def changed():
        values = copy.deepcopy(old_manifests())
        values['review_agent_feedback']['components']['workbuddy_host']['files']['workbuddy_supervisor.py'] = 'new'
        return values
    monkeypatch.setattr(old, 'dependency_manifests', changed)
    result = qualification_decisions(manager.environment, manager.environment.state(), release, snapshot, registry=registry)
    assert result['cases'][0]['action'] == 'live_retest_required'
    other = {**snapshot, 'model': 'hy4-preview', 'actual_model': 'hy4-preview'}
    result = qualification_decisions(manager.environment, manager.environment.state(), release, other, registry=registry)
    assert not result['historical_operations']
    assert manager.environment.state()['qualification_records'][record['id']] == record


def test_history_recovers_freeze_binding_and_string_job_references(tmp_path, monkeypatch):
    from sap_business_agents_platform import runtime_validation_report as report
    from tests.test_runtime_validation import runtime
    monkeypatch.setattr(report, 'inventory', lambda root: {'records': []})
    snapshot = runtime()
    frozen = tmp_path / '.local-data/workbuddy-validation/shared-scope/freeze.json'
    atomic(frozen, {'bindings': {'workbuddy': snapshot}})
    report_path = frozen.parent / 'workbuddy-report.json'
    atomic(report_path, {'jobs': ['sample-job'], 'phase': 'passed'})
    base = tmp_path / '.local-data/runtimes/workbuddy'
    atomic(base / 'state.json', {'capabilities': {'old:sample_discovery:bounded': {
        'status': 'validated', 'job_id': 'sample-job', 'permission_modes': ['bounded']}}})
    atomic(base / 'jobs' / (digest('sample-job') + '.json'), {
        'runtime_digest': digest(snapshot), 'operation': 'sample_discovery', 'cleanup_complete': True})
    value = report.audit_history(tmp_path, 'model-a')
    row = next(c for c in value['case_matrix'] if c['case_id'] == 'sample.scope')
    assert row['historical_operation_pass']
    evidence = row['legacy_evidence'][0]
    assert evidence['model'] == 'model-a' and evidence['runtime_binding_recovered']
    assert evidence['candidate_reports'][0]['job_ids'] == ['sample-job']
    value = report.audit_history(tmp_path, 'other-model')
    assert not value['historical_operations']


def test_unknown_model_is_not_attributed_to_selected_model(tmp_path, monkeypatch):
    from sap_business_agents_platform import runtime_validation_report as report
    monkeypatch.setattr(report, 'inventory', lambda root: {'records': []})
    atomic(tmp_path / '.local-data/runtimes/workbuddy/state.json', {'capabilities': {
        'old:free_query:bounded': {'status': 'validated', 'job_id': 'no-snapshot', 'permission_modes': ['bounded']}}})
    value = report.audit_history(tmp_path, 'selected')
    assert value['historical_operations'] == []
    assert value['unattributed_legacy'][0]['reason'] == 'original_runtime_model_unknown'


def test_audit_projection_edits_do_not_change_acceptance_execution_binding(monkeypatch):
    from sap_business_agents_platform import workbuddy_validation as bridge
    from tests.test_runtime_validation import runtime
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_qualification.execution_components',
        lambda release: {'digest': 'frozen-worker'})
    original = bridge.case_bindings(runtime(), {}, case_ids=['acceptance.campaign'])
    read_bytes = Path.read_bytes
    audit_files = {'workbuddy_qualification.py', 'runtime_validation_history.py', 'runtime_validation_report.py',
        'runtime_validation_rounds.py'}
    monkeypatch.setattr(Path, 'read_bytes', lambda p: read_bytes(p) + b'\n# audit projection edit'
        if p.name in audit_files else read_bytes(p))
    assert bridge.case_bindings(runtime(), {}, case_ids=['acceptance.campaign']) == original
    monkeypatch.setattr(Path, 'read_bytes', lambda p: read_bytes(p) + b'\n# actual transport edit'
        if p.name == 'workbuddy_supervisor.py' else read_bytes(p))
    assert bridge.case_bindings(runtime(), {}, case_ids=['acceptance.campaign']) != original


def test_unchecked_default_model_does_not_break_status_or_borrow_other_model(monkeypatch):
    def no_identity(*args):
        raise ValidationError('validation_runtime_identity_missing')
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_validation.case_bindings', no_identity)
    value = qualification_decisions(None, {}, {'environment_digest': 'env'}, {'model': 'unchecked'})
    assert len(value['cases']) == 16
    assert all(c['action'] == 'evidence_unresolved' and c['current_record_id'] is None for c in value['cases'])
    assert value['historical_operations'] == [] and value['reuse_candidates'] == []
