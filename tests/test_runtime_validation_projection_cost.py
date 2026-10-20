"""Filter unrelated failures, not the proof needed to resolve relevant ones."""
from copy import deepcopy

import pytest

from sap_business_agents_platform import runtime_validation_qualification as q
from tests.test_runtime_validation_failure_resolution import fixture, failure_body, register_retest
from sap_business_agents_platform import workbuddy_validation as bridge
from sap_business_agents_platform import runtime_validation_inventory as inventory
from sap_business_agents_platform.runtime_validation import Registry


def test_unrelated_case_does_not_open_resolution_proof(tmp_path, monkeypatch):
    env, case, bound, old_id, _ = fixture(tmp_path)
    q.append(env.value, failure_body(bound))
    register_retest(env, case, env.state()['validation_ledger'][old_id])
    other = deepcopy(bound)
    other['case_id'] = 'unrelated.case'
    monkeypatch.setattr(q, 'verify_pass', lambda *_: pytest.fail('unrelated live proof opened'))
    assert q.blocks(env.state(), other, 'env', environment=env) == []


@pytest.mark.parametrize('scope', ['environment', 'component', 'attempt', 'unknown'])
def test_relevant_shared_or_unknown_failure_remains_blocking(tmp_path, monkeypatch, scope):
    env, _, bound, _, _ = fixture(tmp_path)
    failure = failure_body(bound, scope=scope, components={'business': 'same'}, cleanup_pending=True)
    failed = q.append(env.value, failure)
    other = deepcopy(bound)
    if scope != 'unknown':
        other['case_id'] = 'unrelated.case'
    monkeypatch.setattr(q, 'verify_pass', lambda *_: pytest.fail('no resolution exists'))
    assert q.blocks(env.state(), other, 'env', environment=env) == [failed]


def test_relevant_resolution_still_checks_actual_evidence_each_call(tmp_path, monkeypatch):
    env, case, bound, old_id, _ = fixture(tmp_path)
    failed = q.append(env.value, failure_body(bound))
    passed = register_retest(env, case, env.state()['validation_ledger'][old_id])
    seen = []
    real = q.verify_pass
    def verify(environment, record):
        seen.append(record['id'])
        return real(environment, record)
    monkeypatch.setattr(q, 'verify_pass', verify)
    assert not q.blocks(env.state(), bound, 'env', environment=env)
    assert seen == [passed]
    monkeypatch.setattr(q, 'verify_pass', lambda *_: False)
    assert q.blocks(env.state(), bound, 'env', environment=env) == [failed]


def test_older_case_failure_needs_no_unrelated_resolution_io(tmp_path, monkeypatch):
    env, case, bound, old_id, _ = fixture(tmp_path)
    failed = q.append(env.value, failure_body(bound, scope='case'))
    monkeypatch.setattr(q, 'verify_pass', lambda *_: pytest.fail('older case resolution opened'))
    assert q.blocks(env.state(), bound, 'env', after=env.value['validation_ledger'][failed]['sequence'] + 1,
        environment=env) == []
    assert q.blocks(env.state(), bound, 'env', environment=env) == [failed]


def test_data_reads_shared_only_inside_one_inspection(tmp_path, monkeypatch):
    root = tmp_path
    package = root / 'agents/MM/mm-po-gr-status'
    package.mkdir(parents=True)
    source = package / 'agent.json'
    source.write_text('{}', encoding='utf-8')
    registry = Registry()
    first, second = registry.entries['agent.create'], registry.entries['role.analysis']
    actual, seen = inventory.sha, []
    def sha(path):
        seen.append(path)
        return actual(path)
    monkeypatch.setattr(inventory, 'sha', sha)
    token = bridge._data_reads.set({'inventories': {}, 'hashes': {}})
    try:
        before = bridge.data_digest(first, root)
        assert bridge.data_digest(second, root) == before
        assert seen == [source]
    finally:
        bridge._data_reads.reset(token)
    source.write_text('{"changed":true}', encoding='utf-8')
    assert bridge.data_digest(first, root) != before
    assert seen == [source, source]


def test_binding_inspection_resets_scope_even_on_error(monkeypatch):
    seen = []
    def inner(*args, **kwargs):
        seen.append(bridge._data_reads.get())
        raise ValueError('diagnostic')
    monkeypatch.setattr(bridge, '_case_bindings', inner)
    for _ in range(2):
        with pytest.raises(ValueError, match='diagnostic'):
            bridge.case_bindings({}, {})
        assert bridge._data_reads.get() is None
    assert seen[0] is not seen[1]
