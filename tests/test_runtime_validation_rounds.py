"""Round claims are offline accounting only; no SDK or subprocess calls."""
import copy
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest

from sap_business_agents_platform.runtime_validation import Registry, ValidationError, digest
from sap_business_agents_platform.runtime_validation_rounds import RoundLedger
from sap_business_agents_platform.runtime_validation_store import atomic, read
from tests.test_runtime_validation import grant, runtime


def setup(tmp_path, cases=('agent.create',), limit=20):
    path = tmp_path / 'goal.json'
    atomic(path, {'provider': 'workbuddy', 'model': 'model-a', 'service_site': 'china',
        'environment_digest': 'env', 'max_live_validation_rounds': limit,
        'live_validation_rounds_used': 0, 'round_claims': []})
    binding = {**runtime(), 'environment_digest': 'env'}
    registry = Registry()
    ids = registry.closure(cases)
    business = [registry.entries[k] for k in ids if registry.entries[k].operations]
    manifest = {'runtime': binding, 'selected': list(cases), 'source_snapshot': {'digest': 'source'},
        'authorizations': {c.id: asdict(grant(c, binding)) for c in business},
        'parameters': {c.id: {'po': '123'} for c in business}, 'targets': {c.id: 'isolated' for c in business}}
    manifest['manifest_digest'] = digest(manifest)
    return path, manifest, tmp_path / 'round1'


def done(batch, manifest, *, cleanup=True):
    atomic(batch / 'manifest.json', manifest)
    atomic(batch / 'process.json', {'pid': 123, 'start_id': 'start1', 'source_digest': 'source'})
    atomic(batch / 'process-cleanup.json', {'pid': 123, 'source_digest': 'source', 'cleanup_complete': cleanup})
    atomic(batch / 'report.json', {k: {'status': 'failed'} for k in manifest['parameters']})


def test_atomic_claim_response_loss_and_conflict(tmp_path):
    path, manifest, batch = setup(tmp_path)
    ledger = RoundLedger(path)
    def claim(): return ledger.claim(manifest, batch, request_id='one', repair_basis='initial_missing_cases')
    with ThreadPoolExecutor(4) as pool:
        values = list(pool.map(lambda _: claim(), range(8)))
    assert sum(new for new, _ in values) == 1
    assert read(path)['live_validation_rounds_used'] == 1
    assert {r['number'] for _, r in values} == {1}
    with pytest.raises(ValidationError, match='request_conflict'):
        ledger.claim(manifest, batch, request_id='one', repair_basis='other')
    with pytest.raises(ValidationError, match='reconciliation_required'):
        ledger.claim(manifest, batch, request_id='two', repair_basis='fixed')


def test_twenty_is_hard_limit_failure_still_consumes_round(tmp_path):
    path, manifest, batch = setup(tmp_path, limit=1)
    ledger = RoundLedger(path)
    ledger.claim(manifest, batch, request_id='one', repair_basis='initial_missing_cases')
    done(batch, manifest)
    assert ledger.finish('one')['status'] == 'finished'
    assert ledger.finish('one')['status'] == 'finished'
    with pytest.raises(ValidationError, match='limit_reached'):
        ledger.claim(manifest, tmp_path / 'round2', request_id='two', repair_basis='fixed')
    assert read(path)['live_validation_rounds_used'] == 1


def test_cleanup_unknown_blocks_next_round_and_changed_outcome_rejected(tmp_path):
    path, manifest, batch = setup(tmp_path)
    ledger = RoundLedger(path)
    ledger.claim(manifest, batch, request_id='one', repair_basis='initial_missing_cases')
    done(batch, manifest, cleanup=False)
    with pytest.raises(ValidationError, match='unproven'):
        ledger.finish('one')
    assert read(path)['round_claims'][0]['status'] == 'claimed'
    done(batch, manifest)
    ledger.finish('one')
    atomic(batch / 'report.json', {'agent.create': {'status': 'passed'}})
    with pytest.raises(ValidationError, match='outcome_changed'):
        ledger.finish('one')


def test_campaign_separate_no_scope_expansion_or_model_change(tmp_path):
    path, manifest, batch = setup(tmp_path, cases=('acceptance.campaign', 'agent.create'))
    with pytest.raises(ValidationError, match='separate_round'):
        RoundLedger(path).claim(manifest, batch, request_id='one', repair_basis='initial')
    path, manifest, batch = setup(tmp_path)
    goal = read(path)
    goal['model'] = 'other'
    atomic(path, goal)
    with pytest.raises(ValidationError, match='runtime_changed'):
        RoundLedger(path).claim(manifest, batch, request_id='one', repair_basis='initial')
    assert read(path)['live_validation_rounds_used'] == 0


def test_ledger_count_and_manifest_tampering_rejected(tmp_path):
    path, manifest, batch = setup(tmp_path)
    value = read(path)
    value['live_validation_rounds_used'] = 1
    atomic(path, value)
    with pytest.raises(ValidationError, match='ledger_invalid'):
        RoundLedger(path).claim(manifest, batch, request_id='one', repair_basis='initial')
    manifest['selected'] = ['role.analysis']
    with pytest.raises(ValidationError, match='manifest_changed'):
        RoundLedger(path).claim(manifest, batch, request_id='one', repair_basis='initial')
