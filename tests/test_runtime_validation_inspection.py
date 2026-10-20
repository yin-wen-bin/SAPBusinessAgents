"""A status summary reuses proof from this inspection, never caller claims."""
from sap_business_agents_platform import runtime_validation_qualification as q
from sap_business_agents_platform import runtime_validation_history as history
from sap_business_agents_platform import workbuddy_validation as bridge
from sap_business_agents_platform.runtime_validation import Registry
from tests.test_runtime_validation import pass_fixture


def setup(monkeypatch, tmp_path):
    env, case, bound, original, _ = pass_fixture(tmp_path)
    registry = Registry({case.id: case})
    monkeypatch.setattr(bridge, 'case_bindings', lambda *a: {case.id: bound})
    seen, real = [], q.verify_pass
    def verify(environment, record):
        seen.append(record['id'])
        return real(environment, record)
    monkeypatch.setattr(q, 'verify_pass', verify)
    return env, case, registry, original, seen


def test_status_and_explicit_history_both_use_full_current_proof_once(tmp_path, monkeypatch):
    env, case, registry, original, seen = setup(monkeypatch, tmp_path)
    for include_previous in (False, True):
        seen.clear()
        value = history.qualification_decisions(env, env.state(), {'environment_digest': 'env'}, {},
            registry=registry, include_previous=include_previous)
        assert value['cases'][0]['current_record_id'] == original
        assert value['cases'][0]['action'] == 'retain_current'
        assert all(op['status'] == 'validated' for op in value['operations'].values())
        assert seen == [original]
        assert value['source_audit'] == ('all_previous' if include_previous else 'current_and_missing')


def test_next_inspection_rechecks_proof_and_rejects_changed_evidence(tmp_path, monkeypatch):
    env, case, registry, original, seen = setup(monkeypatch, tmp_path)
    history.qualification_decisions(env, env.state(), {'environment_digest': 'env'}, {},
        registry=registry, include_previous=False)
    monkeypatch.setattr(q, 'verify_pass', lambda *_: False)
    value = history.qualification_decisions(env, env.state(), {'environment_digest': 'env'}, {},
        registry=registry, include_previous=False)
    assert value['cases'][0]['current_record_id'] is None
    assert value['cases'][0]['action'] == 'evidence_unresolved'
    assert all(op['status'] == 'unverified' for op in value['operations'].values())
