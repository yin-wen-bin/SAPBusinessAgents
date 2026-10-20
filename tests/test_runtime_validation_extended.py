"""Deterministic validation/protocol tests; never invoke a model or SAP."""
import asyncio
import copy
import importlib.util
import json
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from sap_business_agents_platform import runtime_validation_qualification as q
from sap_business_agents_platform.runtime_validation import Registry, ValidationCase, ValidationError, digest, plan, run
from sap_business_agents_platform.runtime_validation_store import atomic, artifact, capture, read, verify_archive, BatchStore
from tests.test_runtime_validation import pass_fixture, runtime, grant, FakeAdapter, Environment

ROOT = Path(__file__).resolve().parents[1]


def test_freeze_rejects_untyped_defaults_before_authorized_binding_changes():
    from sap_business_agents_platform.models import RuntimeSnapshot
    from sap_business_agents_platform.runtime_validation_freeze import require_typed_snapshot
    raw = {'provider_id':'workbuddy','sdk_id':'codebuddy-agent-sdk',
        'model':'fixture-model','configuration_digest':'fixture-digest'}
    original = dict(raw)
    with pytest.raises(ValidationError, match='runtime_projection_required'):
        require_typed_snapshot(raw)
    assert raw == original
    canonical = RuntimeSnapshot.model_validate(raw).model_dump(mode='json')
    assert canonical['model_catalog_digest'] is None
    require_typed_snapshot(canonical)


def test_sap_fixture_digest_follows_catalog_module_and_requires_definition(tmp_path):
    from sap_business_agents_platform.workbuddy_validation import data_digest
    case = Registry().entries['acceptance.campaign']
    with pytest.raises(ValidationError, match='fixture_agent_not_unique'):
        data_digest(case, tmp_path)
    package = tmp_path / 'agents/Common/mm-po-gr-status'
    package.mkdir(parents=True)
    (package/'agent.json').write_text('{"id":"mm-po-gr-status","version":"1"}')
    first = data_digest(case, tmp_path)
    (package/'agent.json').write_text('{"id":"mm-po-gr-status","version":"2"}')
    assert data_digest(case, tmp_path) != first
    unrelated = tmp_path / 'agents/MM/other-agent'
    unrelated.mkdir(parents=True)
    (unrelated/'agent.json').write_text('{}')
    second = data_digest(case, tmp_path)
    (unrelated/'agent.json').write_text('{"changed":true}')
    assert data_digest(case, tmp_path) == second
    duplicate = tmp_path / 'agents/MM/mm-po-gr-status'
    duplicate.mkdir(parents=True)
    (duplicate/'agent.json').write_text('{}')
    with pytest.raises(ValidationError, match='fixture_agent_not_unique'):
        data_digest(case, tmp_path)


def test_unknown_model_projection_does_not_poison_other_model_identity(monkeypatch):
    from sap_business_agents_platform import workbuddy_validation as bridge
    def bindings(snapshot, release):
        if snapshot['model']=='old-unverified':
            raise ValidationError('validation_runtime_identity_missing')
        return {'valid':snapshot['model']}
    monkeypatch.setattr(bridge,'case_bindings',bindings)
    monkeypatch.setattr(bridge.v2,'projection',lambda *args: {'acceptance_baseline':{'status':'validated'}})
    old=bridge.projection(None,{}, {'environment_digest':'env'}, {'model':'old-unverified'}, ['acceptance_baseline'])
    assert old['acceptance_baseline']['status']=='unverified'
    assert old['acceptance_baseline']['missing_cases']==['acceptance.campaign']
    good=bridge.projection(None,{}, {'environment_digest':'env'}, {'model':'checked-model'}, ['acceptance_baseline'])
    assert good['acceptance_baseline']['status']=='validated'


def test_owned_cleanup_handles_exited_parent_with_live_children():
    runner=load('shared_cleanup_test',ROOT/'scripts/validate-ai-runtime.py')
    class Child:
        def poll(self): return 0
    class Job:
        def __init__(self): self.live=True; self.terminated=False
        def empty(self): return not self.live
        def terminate(self): self.live=False; self.terminated=True
    job=Job()
    assert runner.finish_owned_process(job,Child())
    assert job.terminated


def test_safe_execution_diagnostic_omits_exception_values(tmp_path):
    class Broken(FakeAdapter):
        async def execute(self, case, scope): raise KeyError('private-secret')
    case=ValidationCase('a',('author_draft',))
    registry=Registry({'a':case})
    grants={'a':grant(case)}; params={'a':{'po':'123'}}; targets={'a':'isolated'}
    values=asyncio.run(run(plan(registry,['a'],runtime(),grants,params,targets),registry,
        BatchStore(tmp_path),Broken(),grants,params,targets))
    assert values['a']['diagnostics']['exception_type']=='KeyError'
    assert 'private-secret' not in json.dumps(values)


def test_unknown_resolution_requires_live_same_case_proof(tmp_path,monkeypatch):
    env,case,bound,original,_=pass_fixture(tmp_path)
    failure=q.append(env.value,{'kind':'failure','scope':'unknown','failure_code':'validation_execution_error',
        'binding':bound,'environment_digest':'env'})
    passed=q.append(env.value,{'kind':'case_pass','execution_kind':'live','binding':bound,'environment_digest':'env'})
    q.append(env.value,{'kind':'resolution','resolution':'unknown_attempt_superseded_by_live_case',
        'failure_id':failure,'pass_id':passed})
    monkeypatch.setattr(q,'verify_pass',lambda *args: False)
    assert failure in q.blocks(env.state(),bound,'env',environment=env)
    monkeypatch.setattr(q,'verify_pass',lambda *args: True)
    assert failure not in q.blocks(env.state(),bound,'env',environment=env)
    env.value['validation_ledger'][passed]['execution_kind']='reuse'
    monkeypatch.setattr(q,'verify_pass',lambda env, record: record.get('execution_kind')=='live')
    assert failure in q.blocks(env.state(),bound,'env',environment=env)
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_no_whole_registry_fingerprint_when_adding_case():
    registry = Registry()
    old = {c.id: c.digest for c in registry.entries.values()}
    extra = ValidationCase('extra', ('free_query',), version='2')
    next_registry = Registry({**registry.entries, 'extra': extra})
    assert {k: next_registry.entries[k].digest for k in old} == old
    assert len(next_registry.requirements('free_query')) == 3


def test_sample_component_change_does_not_invalidate_role(tmp_path):
    from sap_business_agents_platform import workbuddy_qualification as legacy
    directory = tmp_path / 'components'
    for _, files in legacy.COMPONENTS.values():
        for name in files:
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name, encoding='utf-8')
    first = legacy.dependency_manifests(directory)
    (directory / 'sample_scope_graph.py').write_text('new sampling rules')
    second = legacy.dependency_manifests(directory)
    changed = {op for op in first if first[op]['digest'] != second[op]['digest']}
    assert changed == {'sample_discovery'}
    (directory / 'workbuddy_supervisor.py').write_text('terminal/cleanup change')
    third = legacy.dependency_manifests(directory)
    assert all(second[op]['digest'] != third[op]['digest'] for op in second)


def test_artifact_extra_source_and_secrets_rejected(tmp_path):
    source = tmp_path / 'src'
    source.mkdir()
    (source / 'code.py').write_text('value = 1')
    saved = capture(source, ['code.py'], tmp_path / 'archive')
    (Path(saved['directory']) / 'source/extra.py').write_text('wrong = True')
    with pytest.raises(ValidationError, match='inventory_changed'):
        verify_archive(saved['directory'], saved['digest'])
    atomic(source / 'settings.json', {'password': 'do-not-archive'})
    with pytest.raises(ValidationError, match='sensitive'):
        capture(source, ['settings.json'], tmp_path / 'other')


def make_receipt(tmp_path):
    env, case, bound, source_id, archive = pass_fixture(tmp_path)
    target = 'new-release'
    job_id = 'target-auth'
    job = {'status': 'completed', 'cleanup_complete': True, 'operation': 'authentication',
        'environment_digest': target, 'result_metadata': {'authenticated': True},
        **{k: v for k, v in bound['runtime'].items() if k.startswith('service_site') or k == 'authentication_revision'}}
    atomic(env.root / 'jobs' / (digest(job_id) + '.json'), job)
    proof_root = tmp_path / 'receipt'
    atomic(proof_root / 'startup.json', {'passed': True, 'check': 'target_startup',
        'target_binding_digest': digest(bound), 'target_source_digest': archive['digest'], 'environment_digest': target,
        'job_id': job_id, 'job_digest': digest(job)})
    receipt = q.ReuseReceipt(source_id, bound, target, archive, 'identical_execution/1', str(proof_root),
        (artifact(proof_root, proof_root / 'startup.json'),), 'request1', 'reviewer1')
    return env, case, bound, source_id, receipt


def test_reuse_is_audited_append_only_idempotent_and_never_chained(tmp_path):
    env, case, bound, source_id, receipt = make_receipt(tmp_path)
    original = copy.deepcopy(env.value['validation_ledger'][source_id])
    assert q.audit_reuse(env, env.state(), original, receipt)['status'] == 'auditable'
    with pytest.raises(ValidationError, match='authorization'):
        q.apply_reuse(env, receipt)
    reused = q.apply_reuse(env, receipt, authorized=True)
    assert q.apply_reuse(env, receipt, authorized=True) == reused
    assert env.value['validation_ledger'][source_id] == original
    assert q.select(env, env.state(), bound, 'new-release')['id'] == reused
    with pytest.raises(ValidationError, match='source_unproven'):
        q.apply_reuse(env, replace(receipt, source_record_id=reused, request_id='chain'), authorized=True)
    with pytest.raises(ValidationError, match='conflict'):
        q.apply_reuse(env, replace(receipt, reviewer='different'), authorized=True)


@pytest.mark.parametrize('change', ['model', 'site', 'schema', 'artifact', 'business_failure', 'startup'])
def test_reuse_refuses_changed_binding_or_unproven_evidence(tmp_path, change):
    env, case, bound, source_id, receipt = make_receipt(tmp_path)
    bound = copy.deepcopy(bound)
    if change == 'model': bound['runtime']['model'] = 'different'
    if change == 'site': bound['runtime']['service_site_revision'] += 1
    if change == 'schema': bound['execution_digest'] = 'changed'
    if change == 'artifact': (Path(receipt.evidence_root) / 'startup.json').write_text('{}')
    if change == 'startup':
        jobpath = env.root / 'jobs' / (digest('target-auth') + '.json')
        job = read(jobpath)
        job['service_site'] = 'international'
        atomic(jobpath, job)
    if change == 'business_failure':
        q.append(env.value, {'kind': 'failure', 'binding': bound, 'environment_digest': 'new-release', 'scope': 'case'})
    with pytest.raises(ValidationError):
        q.audit_reuse(env, env.state(), env.value['validation_ledger'][source_id], replace(receipt, target_binding=bound))


def test_scope_component_environment_and_attempt_are_distinct(tmp_path):
    env, case, bound, record, _ = pass_fixture(tmp_path)
    q.append(env.value, {'kind': 'failure', 'scope': 'component', 'components': {'unrelated': 'x'}})
    assert q.select(env, env.state(), bound, 'env')
    q.append(env.value, {'kind': 'failure', 'scope': 'environment', 'environment_digest': 'other'})
    assert q.select(env, env.state(), bound, 'env')
    q.append(env.value, {'kind': 'failure', 'scope': 'attempt', 'environment_digest': 'env', 'cleanup_pending': True})
    assert not q.select(env, env.state(), bound, 'env')
    assert env.value['validation_ledger'][record]['kind'] == 'case_pass'


def test_qualification_response_loss_does_not_overwrite_or_redispatch(tmp_path):
    class Adapter(FakeAdapter):
        def record_result(self, *args): raise ValidationError('registration_response_lost')
        def recover_registration(self, *args): self.recovered = True
    case = ValidationCase('one', ('author_draft',))
    registry, adapter, store = Registry({'one': case}), Adapter(), BatchStore(tmp_path)
    grants, params, targets = {'one': grant(case)}, {'one': {'po': '123'}}, {'one': 'isolated'}
    value = plan(registry, ['one'], runtime(), grants, params, targets)
    first = asyncio.run(run(value, registry, store, adapter, grants, params, targets))
    assert first['one']['status'] == 'passed' and first['one']['qualification_error'] == 'registration_response_lost'
    asyncio.run(run(value, registry, store, adapter, grants, params, targets))
    assert adapter.calls == ['one'] and adapter.recovered


def test_real_state_protocol_fence_and_legacy_writer_block(tmp_path):
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyEnvironment
    from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
    env = WorkBuddyEnvironment(tmp_path)
    q.activate(env, lambda: {'all_writers_checked': True, 'old_writers': [], 'active_jobs': [], 'cleanup_pending': [],
        'state_digest': digest(env.state()), 'process_inventory_digest': 'complete-inventory', 'checked_at': time.time()})
    state = env.state()
    assert q.active(state) and any(r['kind'] == 'protocol_fence' for r in state['qualification_records'].values())
    # An unmodified protocol-1 reader's exact accepted-kind set rejects this.
    assert any(r['kind'] not in {'live_pass', 'failure', 'reuse'} for r in state['qualification_records'].values())
    manager = WorkBuddyManager(tmp_path)
    with pytest.raises(Exception, match='validation_use_shared_runner'):
        manager.apply_qualification_reuse('model-a', 'old')
    with pytest.raises(Exception, match='validation_use_shared_runner'):
        manager.migrate_qualification_history()


def test_cli_plan_acceptance_expands_only_saved_checks_and_calls_no_runtime(monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        module = load('validation_cli_test', ROOT / 'scripts/validate-ai-runtime.py')
        from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
        monkeypatch.setattr(WorkBuddyManager, '__init__', lambda *a, **k: pytest.fail('manager should not load'))
        assert module.main(['plan', '--case', 'acceptance.campaign']) == 0
        value = json.loads(capsys.readouterr().out)
        assert [v['case_id'] for v in value['cases']] == ['environment.current', 'infrastructure.current', 'acceptance.campaign']
        assert value['cases'][-1]['reason'] == 'validation_not_authorized'
    finally:
        sys.path.pop(0)


def test_fixture_policy_rejects_parameter_scope_changes():
    module = load('fixture_policy_test', ROOT / 'scripts/runtime_validation_fixture_policy.py')
    case = Registry().entries['sample.scope']
    value = module.parameters(case)
    module.validate(case, value)
    value.pop('purchasing_organization')
    with pytest.raises(ValidationError, match='parameters_changed'):
        module.validate(case, value)


def test_frozen_process_checks_imports_in_new_interpreter(tmp_path):
    from sap_business_agents_platform.runtime_validation_freeze import source_files
    files = source_files(ROOT, Registry(), ['query.feedback'])
    assert 'src/sap_business_agents_platform/workbuddy_contracts.json' in files
    assert 'scripts/run_three_stage_acceptance.py' in files
    assert 'scripts/acceptance_source_anchors.py' in files
    assert 'scripts/direct_sap_read.py' in files
    assert 'scripts/direct_ar_adt_snapshot.py' in files
    assert not any('.local-data/' in p or p.endswith('.env') for p in files)
    # Package __init__ imports the application. Use the actual complete dynamic
    # import closure rather than an unrealistically empty package stub.
    names = [p for p in files if p.startswith('src/') or p in {'scripts/run_three_stage_acceptance.py','scripts/acceptance_source_anchors.py','scripts/direct_sap_read.py','scripts/direct_ar_adt_snapshot.py'}]
    archive = capture(ROOT, names, tmp_path / 'archives')
    boot = "import sys,json,os,faulthandler;faulthandler.dump_traceback_later(20);sys.path[:0]=[sys.argv[1],os.path.dirname(sys.argv[1])];from scripts.run_three_stage_acceptance import _acceptance_prompt,_compare,_normalize_run;from scripts.acceptance_source_anchors import capture;from sap_business_agents_platform.runtime_validation_store import process_binding;print(json.dumps(process_binding(json.loads(sys.argv[2]))))"
    run_ = subprocess.run([sys.executable, '-I', '-B', '-c', boot, str(Path(archive['directory']) / 'source/src'), json.dumps(archive)],
        # Import-only process: allow Windows cold-file scanning. This is not a
        # model/stage budget and does not change any Runtime timeout policy.
        capture_output=True, text=True, timeout=60)
    assert run_.returncode == 0, run_.stderr
    assert json.loads(run_.stdout)['source_digest'] == archive['digest']


def test_history_index_handles_array_reports_and_does_not_invent_operations(tmp_path, monkeypatch):
    from sap_business_agents_platform import runtime_validation_report as reports
    atomic(tmp_path / '.local-data/runtimes/workbuddy/state.json', {'capabilities': {}})
    atomic(tmp_path / '.local-data/workbuddy-validation/batch/report.json', [{'status': 'failed'}])
    monkeypatch.setattr(reports, 'inventory', lambda root: {'records': []})
    value = reports.audit_history(tmp_path)
    assert value['historical_operations'] == []
    assert value['planned_operations'] == 15 and value['planned_business_cases'] == 16
    assert all(c['audit'] == 'no_original_pass' for c in value['case_matrix'])


def test_finite_nonexecution_carry_requires_real_bound_test_report(tmp_path):
    env, case, bound, source_id, receipt = make_receipt(tmp_path)
    source = tmp_path / 'newsource'
    (source / 'docs').mkdir(parents=True)
    (source / 'code.py').write_text('code')
    (source / 'docs/runtime-validation.md').write_text('reviewed documentation')
    archive = capture(source, ['code.py', 'docs/runtime-validation.md'], tmp_path / 'next-archives')
    root = Path(receipt.evidence_root)
    startup = read(root / 'startup.json')
    startup['target_source_digest'] = archive['digest']
    atomic(root / 'startup.json', startup)
    refs = [artifact(root, root / 'startup.json')]
    for name in ('projection_regression', 'codex_controls'):
        test = root / (name + '-test.json')
        atomic(test, {'status': 'passed', 'failed': 0, 'tests': ['offline-proof'],
            'target_binding_digest': digest(bound), 'checker_digest': 'fixture-checker'})
        proof = root / (name + '.json')
        atomic(proof, {'check': name, 'passed': True, 'target_binding_digest': digest(bound),
            'target_source_digest': archive['digest'], 'environment_digest': 'new-release',
            'test_report': artifact(root, test), 'checker_digest': 'fixture-checker'})
        refs.append(artifact(root, proof))
    receipt = replace(receipt, rule='non_execution_projection/1', target_snapshot=archive, evidence=tuple(refs))
    assert q.audit_reuse(env, env.state(), env.value['validation_ledger'][source_id], receipt)['changed_files'] == ['docs/runtime-validation.md']
    atomic(root / 'codex_controls-test.json', {'status': 'failed'})
    with pytest.raises(ValidationError):
        q.audit_reuse(env, env.state(), env.value['validation_ledger'][source_id], receipt)


def test_cross_process_claim_uses_one_preallocated_job(tmp_path):
    case = ValidationCase('once', ('author_draft',))
    request = {'case': asdict(case), 'runtime': runtime(), 'grant': asdict(grant(case))}
    code = "import json,sys;from sap_business_agents_platform.runtime_validation import ValidationCase,Authorization;from sap_business_agents_platform.runtime_validation_store import BatchStore;r=json.loads(sys.argv[2]);new,job=BatchStore(sys.argv[1]).claim(ValidationCase(**r['case']),r['runtime'],Authorization(**r['grant']),'isolated',{'po':'123'});print(json.dumps({'new':new,'job':job['job_id']}))"
    command = [sys.executable, '-B', '-c', code, str(tmp_path / 'batch'), json.dumps(request)]
    processes = [subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    values = []
    for p in processes:
        output, errors = p.communicate(timeout=20)
        assert p.returncode == 0, errors
        values.append(json.loads(output))
    assert sum(v['new'] for v in values) == 1
    assert len({v['job'] for v in values}) == 1


def test_stale_writer_clearance_cannot_activate(tmp_path):
    env = Environment(tmp_path)
    base = {'all_writers_checked': True, 'old_writers': [], 'active_jobs': [], 'cleanup_pending': [],
        'state_digest': digest(env.state()), 'process_inventory_digest': 'proof', 'checked_at': time.time()}
    for update in ({'old_writers': ['old-api']}, {'checked_at': time.time()-31}, {'state_digest': 'stale'}):
        with pytest.raises(ValidationError, match='clearance'):
            q.activate(env, lambda: {**base, **update})
        assert not env.value


def test_formal_identity_gate_only_applies_to_acceptance(monkeypatch):
    from sap_business_agents_platform.workbuddy_validation import inspect_saved
    from sap_business_agents_platform import workbuddy_qualification
    class SavedEnvironment:
        def __init__(self): self.flags = []
        def state(self): return {}
        def assert_verification_binding(self, snapshot, *, formal):
            self.flags.append(formal)
            return {'environment_digest': 'fixture'}
    monkeypatch.setattr(workbuddy_qualification, 'environment_check', lambda *a: {'owned_check': True})
    env = SavedEnvironment()
    inspect_saved(env, runtime())
    inspect_saved(env, runtime(), formal=True)
    assert env.flags == [False, True]


@pytest.mark.parametrize('case_id,formal', [('query.feedback', False), ('acceptance.campaign', True)])
def test_receipt_uses_case_identity_policy(tmp_path, monkeypatch, case_id, formal):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from sap_business_agents_platform import workbuddy_validation as bridge, workbuddy_manager
    flags = []
    bound = {'runtime': {'model': 'fixture-model'}, 'case_id': case_id}
    class Manager:
        def __init__(self, root):
            self.environment = SimpleNamespace(maintenance=nullcontext)
        def _qualification_busy(self): return False
        def runtime_snapshot(self, model, *, require_enabled, formal):
            assert model == 'fixture-model' and not require_enabled
            flags.append(formal)
            return {'model': model}
    def inspect(environment, snapshot, *, formal):
        flags.append(formal)
        return {'environment_digest': 'fixture-environment'}
    monkeypatch.setattr(workbuddy_manager, 'WorkBuddyManager', Manager)
    monkeypatch.setattr(bridge, 'inspect_saved', inspect)
    monkeypatch.setattr(bridge, 'case_bindings', lambda *a: {case_id: bound})
    monkeypatch.setattr(bridge.v2, 'apply_reuse', lambda *a, **kw: 'mock-record-only')
    receipt = SimpleNamespace(target_binding=bound, target_environment='fixture-environment')
    assert bridge.apply_receipt(tmp_path, receipt) == 'mock-record-only'
    assert flags == [formal, formal]
