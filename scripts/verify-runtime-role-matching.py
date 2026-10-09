"""One complete non-SAP role analysis + feedback, in a fresh isolated database.

WorkBuddy qualification requires persisted business checks and owned cleanup.
Codex uses its existing tool-free client for the authorized non-SAP comparison.
This script never changes the default Runtime or enables a Provider.
"""
import argparse
import asyncio
import copy
import json
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.role_matching import RoleMatchingService
from sap_business_agents_platform.runtime_policy import orchestration_digest
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest
from sap_business_agents_platform.workbuddy_diagnostics import safe_progress
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTION = ('采购专员只负责一项业务操作：按计划行交货日期检查采购订单的收货数量、未收货数量和入库状态。'
    '不包含付款、库存分配、邮件或执行SAP。请基于完整目录匹配，所有结论引用此用户描述，避免拆成多项重复操作。')
FEEDBACK = '保持唯一采购订单入库状态检查操作、来源与全部目录覆盖。明确不执行邮件、付款或库存分配，不新增业务操作。'
OPERATIONS = {'analyze_role_matching', 'review_role_matching_feedback'}

class Runtime:
    def __init__(self, provider, binding, planner):
        self.current_provider_id, self.binding, self.planner = provider, binding, planner
    def snapshot(self, *_): return copy.deepcopy(self.binding)
    def supports(self, operation): return operation in OPERATIONS
    @contextmanager
    def pin(self, provider, model=None, effort=None):
        assert provider == self.current_provider_id
        yield
    @contextmanager
    def pin_snapshot(self, binding):
        assert binding == self.binding
        yield
    async def analyze_role_matching(self, **kwargs): return await self.planner.analyze_role_matching(**kwargs)
    async def review_role_matching_feedback(self, **kwargs): return await self.planner.review_role_matching_feedback(**kwargs)

async def verify(arguments):
    destination = Path(arguments.data_root).resolve()
    allowed_root = (ROOT / '.local-data/workbuddy-baseline').resolve()
    assert destination.is_relative_to(allowed_root) and destination != allowed_root and not destination.exists(), 'fresh_isolated_directory_required'
    destination.mkdir(parents=True)
    manager = WorkBuddyManager(ROOT)
    assert not manager.snapshot()['enabled'] and not manager.supervisor.reconcile()
    for path in (manager.environment.root / 'jobs').glob('*.json'):
        assert json.loads(path.read_text(encoding='utf-8')).get('status') not in {'running', 'cleanup_pending'}
    jobs = []
    if arguments.provider == 'workbuddy':
        from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner
        binding = manager.runtime_snapshot(require_enabled=False, formal=True)
        assert binding['model'] == binding['actual_model'] == 'hy4-preview'
        planner = WorkBuddyPlanner(ROOT, binding['model'], supervisor=manager.supervisor, runtime_snapshot=binding)
        original = manager.supervisor.run
        async def monitored(**kwargs):
            assert kwargs['snapshot'] == binding and kwargs['operation'] in OPERATIONS
            assert kwargs['mode'] == 'bounded' and not kwargs['payload'].get('tools') and not kwargs.get('tool_handler')
            task = kwargs.get('task_id') or 'role-check-' + uuid.uuid4().hex
            kwargs['task_id'] = task
            jobs.append({'task_id': task, 'operation': kwargs['operation']})
            downstream = kwargs.get('emit')
            def emit(kind, data):
                safe = safe_progress(kind, data)
                if safe:
                    with (destination / 'events.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps({'event': kind, 'operation': kwargs['operation'], **safe}, ensure_ascii=False) + '\n')
                    if kind == 'workbuddy_native_failure': print(json.dumps({'event': kind, **safe}), flush=True)
                if downstream: downstream(kind, data)
            kwargs['emit'] = emit
            result = await original(**kwargs)
            assert result.get('actual_model') == binding['actual_model'] and result.get('model_identity_source') == 'assistant_message'
            return result
        manager.supervisor.run = monitored
    else:
        from sap_business_agents_platform.sdk_manager import SDKManager
        from sap_business_agents_platform.runtime import CodexRuntimeProbe
        from sap_business_agents_platform.codex_planner import CodexPlanner, _agent_authoring_codex
        from sap_business_agents_platform.codex_driver import CodexClient
        settings = Settings.from_env(ROOT)
        registry = SDKManager(ROOT / 'config/sdks.json', ROOT, runtime_probes={'codex': CodexRuntimeProbe()},
            selection_path=settings.sdk_runtime_state_path, legacy_model=settings.codex_model)
        assert registry.default_provider_id == 'codex'
        binding = registry.runtime_snapshot('codex')
        planner = CodexPlanner(ROOT, model=binding['model'], reasoning_effort=binding['reasoning_effort'])
        workspace = destination / 'tool-free-workspace'
        workspace.mkdir()
        planner._driver.client = lambda **_: CodexClient(planner._driver, _agent_authoring_codex(workspace))
    settings = Settings(repository_root=ROOT, data_root=destination / 'data', draft_root=destination / 'drafts')
    store = RunStore(settings.database_path)
    service = RoleMatchingService(settings, store, AgentRepository(ROOT / 'agents'), Runtime(arguments.provider, binding, planner), None)
    report = {'phase': 'running', 'provider': arguments.provider, 'runtime': binding, 'sap_calls': 0,
        'automatic_retry': False, 'operation_digests': {op: orchestration_digest(op) for op in OPERATIONS},
        'stages': [], 'qualified_operations': []}
    report_path = destination / 'report.json'
    atomic_json(report_path, report)

    async def finish(session_id, revision, operation, started):
        # Observation only; each actual model phase retains its 300s deadline.
        async with asyncio.timeout(3600):
            while True:
                current = service.get(session_id)
                if current['status'] in {'completed', 'failed', 'cancelled'}: break
                await asyncio.sleep(.2)
        entry = {'operation': operation, 'status': current['status'], 'session_id': session_id,
                 'elapsed_seconds': round(time.monotonic() - started, 2)}
        report['stages'].append(entry)
        if current['status'] != 'completed':
            entry.update(phase='failed', failure_code=(current.get('error') or {}).get('code', 'role_validation_incomplete'))
            return False
        saved = service.revision(session_id, revision)
        result = saved['result']
        evaluation = result['catalog_evaluation']
        entry.update(catalog_evaluation=evaluation, diagnostics=result.get('runtime_diagnostics', []),
                     workflow_validation_issues=result.get('workflow_validation_issues', []),
                     operation_reconciliation=result.get('operation_reconciliation'))
        complete = (evaluation['agent_catalog_complete'] and evaluation['matching_complete']
            and evaluation['consolidation_complete'] and result['completeness']['workflow_validation_complete']
            and result.get('operation_reconciliation', {}).get('complete') and len(result['operations']) == 1
            and any(m['agent_id'] == 'mm-po-gr-status' and m['coverage'] == 'full' for m in result['agent_matches']))
        if not complete:
            entry.update(phase='failed', failure_code='role_business_validation_incomplete')
            return False
        for collection in ('roles', 'processes', 'operations', 'agent_matches', 'workflow_suggestions', 'agent_gaps'):
            assert all(item.get('evidence_refs') for item in result[collection])
        if revision == 2:
            assert saved['parent_revision'] == 1 and digest(service.revision(session_id, 1)) == report['first_revision_digest']
        assert all(orchestration_digest(op) == value for op, value in report['operation_digests'].items())
        if arguments.provider == 'workbuddy':
            owned = [j for j in jobs if j['operation'] == operation]
            assert owned
            records = [json.loads((manager.environment.root / 'jobs' / (digest(j['task_id']) + '.json')).read_text(encoding='utf-8')) for j in owned]
            assert all(job['cleanup_complete'] and job['validation_job'] for job in records)
            assert records[-1]['status'] == 'completed'
            manager.record_operation_validation(binding, operation, job_id=owned[-1]['task_id'], permission_modes=['bounded'],
                evidence={'kind': 'live_validation', 'platform_contract_passed': True, 'isolated_role_service': True,
                    'full_catalog_coverage': evaluation, 'operation_reconciliation': result['operation_reconciliation'],
                    'document_evidence_validated': True, 'revision': revision, 'sap_calls': 0})
            report['qualified_operations'].append(operation)
        entry['phase'] = 'passed'
        return True
    try:
        with manager.supervisor.verification(binding, OPERATIONS) if arguments.provider == 'workbuddy' else nullcontext():
            await service.start()
            try:
                started = time.monotonic()
                session = await service.create(paths=[], role_description=DESCRIPTION, locale='zh', consent=True)
                passed = await finish(session['session_id'], 1, 'analyze_role_matching', started)
                atomic_json(report_path, report)
                print(json.dumps(report['stages'][-1], ensure_ascii=False), flush=True)
                if passed:
                    report['first_revision_digest'] = digest(service.revision(session['session_id'], 1))
                    started = time.monotonic()
                    await service.feedback(session['session_id'], base_revision=1, message=FEEDBACK,
                        mode='incremental', added_paths=[], added_role_description=None, excluded_document_ids=[])
                    await finish(session['session_id'], 2, 'review_role_matching_feedback', started)
                    print(json.dumps(report['stages'][-1], ensure_ascii=False), flush=True)
                else:
                    report['stages'].append({'operation': 'review_role_matching_feedback', 'phase': 'not_run', 'reason': 'no_valid_initial_revision'})
            finally:
                await service.stop()
    except Exception as error:
        report['failure_code'] = getattr(error, 'code', 'role_verification_failed')
    finally:
        await manager.supervisor.close()
        report.update(phase='passed' if len(report['stages']) == 2 and all(s['phase'] == 'passed' for s in report['stages']) else 'failed',
            cleanup_pending=bool(manager.supervisor.pending()), enabled=manager.snapshot()['enabled'])
        atomic_json(report_path, report)
    print(json.dumps({'report': str(report_path), 'phase': report['phase'], 'cleanup_pending': report['cleanup_pending'], 'sap_calls': 0}), flush=True)
    return int(report['phase'] != 'passed' or report['cleanup_pending'])

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', required=True, choices=['codex', 'workbuddy'])
    parser.add_argument('--data-root', required=True)
    parser.add_argument('--execute', action='store_true')
    arguments = parser.parse_args()
    if not arguments.execute:
        print(json.dumps({'provider': arguments.provider, 'operations': sorted(OPERATIONS), 'sap_calls': 0, 'execute': False}))
    else:
        try:
            result = asyncio.run(verify(arguments))
        except Exception as error:
            code = getattr(error, 'code', 'role_verification_preflight_failed')
            if not isinstance(code, str) or not code.replace('_', '').isalnum() or len(code) > 120:
                code = 'role_verification_preflight_failed'
            directory = Path(arguments.data_root).resolve()
            allowed = (ROOT / '.local-data/workbuddy-baseline').resolve()
            if directory.is_relative_to(allowed) and directory != allowed and directory.is_dir() and not (directory / 'report.json').exists():
                atomic_json(directory / 'report.json', {'phase': 'not_run', 'failure_code': code,
                    'provider': arguments.provider, 'model_jobs': 0, 'sap_calls': 0, 'qualification_granted': False})
            print(json.dumps({'phase': 'not_run', 'failure_code': code, 'provider': arguments.provider}))
            result = 1
        raise SystemExit(result)
