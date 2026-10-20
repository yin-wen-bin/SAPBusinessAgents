"""Whole-file operation closures, including negative reuse evidence checks."""
import copy
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from sap_business_agents_platform import runtime_validation_qualification as q
from sap_business_agents_platform import workbuddy_qualification as old
from sap_business_agents_platform import workbuddy_validation as bridge
from sap_business_agents_platform.runtime_validation import ValidationCase, ValidationError, digest
from sap_business_agents_platform.runtime_validation_dependencies import archived_scope, operation_equivalence
from sap_business_agents_platform.runtime_validation_store import atomic, capture, artifact, read
from tests.test_runtime_validation import pass_fixture


def scope_fixture(tmp_path, monkeypatch):
    graph = {'platform': ((), ('platform.py',)), 'workbuddy_host': ((), ('transport.py',)), 'author': ((), ('author.py',))}
    monkeypatch.setattr(old, 'COMPONENTS', graph)
    monkeypatch.setattr(old, 'ROOTS', {'author_draft': ('author',)})
    monkeypatch.setitem(bridge.FIXTURES, 'unit', ('unit.py',))
    case = ValidationCase('unit', ('author_draft',), components=('author',), fixture='unit')
    source = tmp_path / 'original'
    package = source / 'src/sap_business_agents_platform'
    package.mkdir(parents=True)
    for file in ('platform.py', 'transport.py', 'author.py', 'runtime_validation.py', 'runtime_validation_checker.py', 'unrelated.py'):
        (package / file).write_bytes(('original ' + file + '\n').encode())
    for file in ('unit.py', 'runtime_validation_cases.py', 'runtime_validation_fixture_policy.py',
            'workbuddy_live_cases/live_guard.py', 'workbuddy_validation_auth.py'):
        path = source / 'scripts' / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('original ' + file).encode())
    dependencies = {name: digest({'requires': [], 'files': {f: hashlib.sha256((package / f).read_bytes()).hexdigest() for f in files}})
        for name, (_, files) in graph.items()}
    dependencies.update(case_fixture=bridge.fixture_digest(case, source / 'scripts'), case_data=bridge.data_digest(case, source),
        case_checker=digest({f: (package / f).read_bytes().hex() for f in ('runtime_validation.py', 'runtime_validation_checker.py')}))
    names = [p.relative_to(source).as_posix() for p in source.rglob('*.py')]
    original = capture(source, names, tmp_path / 'archives')
    execution = tmp_path / 'execution'
    execution.mkdir()
    env, case, bound, first, _ = pass_fixture(execution, case=case, dependencies=dependencies)
    body = {k: v for k, v in q.ledger(env.state())[first].items() if k not in {'id', 'created_at', 'sequence'}}
    body['source_snapshot'] = original
    identifier = q.append(env.value, body)
    startup = {'operation': 'authentication', 'status': 'completed', 'cleanup_complete': True,
        'environment_digest': 'new-env', 'result_metadata': {'authenticated': True},
        **{k: v for k, v in bound['runtime'].items() if k.startswith('service_site') or k == 'authentication_revision'}}
    atomic(env.root / 'jobs' / (digest('auth') + '.json'), startup)
    proof_root = tmp_path / 'receipt'
    def receipt_for(target):
        common = {'passed': True, 'target_binding_digest': digest(bound), 'target_source_digest': target['digest'], 'environment_digest': 'new-env'}
        atomic(proof_root / 'startup.json', {**common, 'check': 'target_startup', 'job_id': 'auth', 'job_digest': digest(startup)})
        for name in ('dependency_regression', 'codex_controls'):
            test = {**common, 'status': 'passed', 'failed': 0, 'tests': ['exact_closure', 'negative_scope'], 'checker_digest': 'tested-checker'}
            atomic(proof_root / (name + '-test.json'), test)
            atomic(proof_root / (name + '.json'), {**common, 'check': name, 'checker_digest': 'tested-checker',
                'test_report': artifact(proof_root, proof_root / (name + '-test.json'))})
        return q.ReuseReceipt(identifier, bound, 'new-env', target, 'identical_operation/1', str(proof_root),
            tuple(artifact(proof_root, proof_root / name) for name in ('startup.json', 'dependency_regression.json', 'codex_controls.json')),
            'scoped-reuse', 'unit-reviewer')
    return env, case, bound, identifier, original, source, names, receipt_for


def test_unrelated_file_does_not_force_business_retest(tmp_path, monkeypatch):
    env, case, bound, key, original, source, names, receipt_for = scope_fixture(tmp_path, monkeypatch)
    (source / 'src/sap_business_agents_platform/unrelated.py').write_text('unrelated fix')
    target = capture(source, names, tmp_path / 'archives')
    receipt = receipt_for(target)
    before = copy.deepcopy(env.state())
    result = q.audit_reuse(env, env.state(), q.ledger(env.state())[key], receipt)
    assert result['status'] == 'auditable'
    assert result['operation_scope']['outside_operation_scope'] == ['src/sap_business_agents_platform/unrelated.py']
    assert env.state() == before
    with pytest.raises(ValidationError, match='not_identical'):
        q.audit_reuse(env, env.state(), q.ledger(env.state())[key], replace(receipt, rule='identical_execution/1'))
    applied = q.apply_reuse(env, receipt, authorized=True)
    assert q.select(env, env.state(), bound, 'new-env')['id'] == applied
    assert q.ledger(env.state())[key] == before['validation_ledger'][key]


@pytest.mark.parametrize('file', ['transport.py', 'author.py', 'runtime_validation_checker.py'])
def test_bound_execution_or_checker_changes_still_require_real_retest(tmp_path, monkeypatch, file):
    env, case, bound, key, original, source, names, receipt_for = scope_fixture(tmp_path, monkeypatch)
    (source / 'src/sap_business_agents_platform' / file).write_text('critical change')
    target = capture(source, names, tmp_path / 'archives')
    with pytest.raises(ValidationError, match='dependencies_mismatch'):
        q.audit_reuse(env, env.state(), q.ledger(env.state())[key], receipt_for(target))


def test_dependency_topology_cannot_be_reconstructed_from_changed_declarations(tmp_path, monkeypatch):
    env, case, bound, key, original, source, names, receipt_for = scope_fixture(tmp_path, monkeypatch)
    monkeypatch.setitem(old.COMPONENTS, 'author', (('workbuddy_host',), ('author.py',)))
    with pytest.raises(ValidationError, match='dependencies_mismatch'):
        archived_scope(case, original, bound['dependencies'])


def test_scoped_reuse_needs_bound_regressions_and_does_not_override_failure(tmp_path, monkeypatch):
    env, case, bound, key, original, source, names, receipt_for = scope_fixture(tmp_path, monkeypatch)
    receipt = receipt_for(original)
    with pytest.raises(ValidationError, match='evidence_missing'):
        q.audit_reuse(env, env.state(), q.ledger(env.state())[key], replace(receipt, evidence=receipt.evidence[:1]))
    q.append(env.value, {'kind': 'failure', 'scope': 'case', 'binding': bound, 'environment_digest': 'new-env'})
    with pytest.raises(ValidationError, match='failure_unresolved'):
        q.audit_reuse(env, env.state(), q.ledger(env.state())[key], receipt)
