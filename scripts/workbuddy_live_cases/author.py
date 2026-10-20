"""Real pinned SDK operations against explicitly offline inputs; no SAP reads.

One invocation per operation, retained failures, no automatic qualification from
JSON shape alone. Platform execution/compilation/lineage checks must pass.
"""
import asyncio
import copy
import json
import time
import uuid
import sys
from contextlib import contextmanager
from pathlib import Path

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.factory import AgentDraftService
from sap_business_agents_platform.manifests import validate_manifest
from sap_business_agents_platform.models import RunCreate, RunMode, RunResult, RunStatus
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner
from workbuddy_live_cases.live_guard import attach_validation_monitor

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / ".local-data/workbuddy-validation"
RUN_ID = 'remaining-bounded-' + uuid.uuid4().hex
RUN_ROOT = HERE / RUN_ID
REPORT = RUN_ROOT / 'report.json'
PLAN = {'service_name': 'API_PURCHASEORDER_PROCESS_SRV', 'odata_version': '2.0',
        'entity_set': 'A_PurchaseOrder', 'method': 'GET',
        'select_fields': ['PurchaseOrder', 'Supplier', 'CompanyCode'],
        'filters': [{'field': 'PurchaseOrder', 'operator': 'eq', 'value': '4500001466'}],
        'top': 1}


class AuthorRuntime:
    def __init__(self, planner, snapshot):
        self.planner, self.binding = planner, snapshot
        self.calls = 0
    def supports(self, operation):
        return operation == 'author_draft'
    @contextmanager
    def pin_snapshot(self, value):
        assert all(value.get(k) == v for k, v in self.binding.items())
        yield
    async def author_draft(self, **kwargs):
        self.calls += 1
        return await self.planner.author_draft(**kwargs)


def create_source(store, snapshot, fixture_id):
    """A complete synthetic fixture, never a claim of successful SAP evidence."""
    store.create_run(fixture_id, RunCreate(mode=RunMode.free_query,
        query='完整的离线合成样例：仅测试采购订单抬头草稿编写，未访问SAP，不代表真实业务结论。'), runtime=snapshot)
    result = RunResult(run_id=fixture_id, mode=RunMode.free_query, plan=PLAN,
        evidence=[{'evidence_ref': 'offline_fixture_evidence',
            'source': 'complete_synthetic_fixture_not_sap', 'source_complete': True,
            'rows': [], 'row_count': 0}],
        completeness={'source_complete': True, 'business_complete': True,
            'reason': 'Complete synthetic fixture only; SAP not read', 'missing_evidence': []},
        thread_id='platform-offline-context')
    store.update_run(fixture_id, status=RunStatus.completed, plan_json=PLAN,
        result_json=result, thread_id=result.thread_id)
    return result


async def main():
    manager = WorkBuddyManager(ROOT)
    snapshot = json.loads((HERE / 'runtime-snapshot.json').read_text(encoding='utf-8'))
    attach_validation_monitor(manager, snapshot, RUN_ROOT / 'safe-events.jsonl')
    planner = WorkBuddyPlanner(ROOT, snapshot['model'], supervisor=manager.supervisor,
                              runtime_snapshot=snapshot)
    stages, jobs = [], []
    report = {'run_id': RUN_ID, 'phase': 'running', 'runtime': snapshot,
              'input_kind': 'explicit_offline_fixture', 'sap_calls': 0,
              'stages': stages, 'qualified_operations': []}
    original = manager.supervisor.run
    async def observe(**kwargs):
        assert kwargs['mode'] == 'bounded' and not kwargs.get('tool_handler')
        assert not kwargs['payload'].get('tools')
        job_id = kwargs.get('task_id') or 'wb-bounded-' + uuid.uuid4().hex
        kwargs['task_id'] = job_id
        job = {'task_id': job_id, 'operation': kwargs['operation']}
        jobs.append(job)
        result = await original(**kwargs)
        job.update(actual_model=result.get('actual_model'),
                   model_identity_source=result.get('model_identity_source'))
        assert job['actual_model'] == snapshot['actual_model']
        assert job['model_identity_source'] == 'assistant_message'
        return result
    manager.supervisor.run = observe
    atomic_json(REPORT, report)

    async def stage(operation, execute, verify):
        entry = {'operation': operation, 'phase': 'running'}
        stages.append(entry)
        atomic_json(REPORT, report)
        print(json.dumps({'operation': operation, 'phase': 'starting'}), flush=True)
        started = time.monotonic()
        before_jobs = len(jobs)
        try:
            with manager.supervisor.verification(snapshot, {operation}):
                result = await execute()
            atomic_json(RUN_ROOT / (operation + '.json'),
                        result.model_dump(mode='json') if hasattr(result, 'model_dump') else result)
            proof = verify(result)
            if asyncio.iscoroutine(proof):
                proof = await proof
            owned = jobs[before_jobs:]
            assert owned and all(j['operation'] == operation for j in owned)
            for j in owned:
                record = json.loads((manager.environment.root / 'jobs' / (digest(j['task_id']) + '.json')).read_text(encoding='utf-8'))
                assert record['status'] == 'completed' and record['cleanup_complete'] is True
                assert record['validation_job'] is True
            atomic_json(RUN_ROOT / (operation + '.json'),
                        result.model_dump(mode='json') if hasattr(result, 'model_dump') else result)
            manager.record_operation_validation(snapshot, operation, job_id=owned[-1]['task_id'],
                permission_modes=['bounded'], evidence={'kind': 'live_validation',
                    'platform_contract_passed': True, 'input_kind': 'explicit_offline_fixture',
                    'sap_calls': 0, 'checks': proof, 'owned_jobs': owned})
            report['qualified_operations'].append(operation)
            entry.update(phase='passed', checks=proof, jobs=owned)
            return result
        except Exception as exc:
            import traceback
            entry.update(phase='failed', failure_code=getattr(exc, 'code', type(exc).__name__),
                         cause_code=getattr(exc.__cause__, 'code', type(exc.__cause__).__name__) if exc.__cause__ else None,
                         failure_location=[{'function': f.name, 'line': f.lineno} for f in traceback.extract_tb(exc.__traceback__)[-3:]],
                         jobs=jobs[before_jobs:])
            return None
        finally:
            entry['elapsed_seconds'] = round(time.monotonic() - started, 2)
            atomic_json(REPORT, report)
            print(json.dumps(entry, ensure_ascii=False), flush=True)

    evidence = [{'evidence_ref': 'offline_fixture_evidence', 'source': 'offline_validation_fixture_not_sap',
                 'source_complete': False, 'rows': [], 'row_count': 0,
                 'missing_evidence': ['live_sap_not_read']}]
    settings = Settings(repository_root=ROOT, data_root=RUN_ROOT / 'data', draft_root=RUN_ROOT / 'drafts')
    store = RunStore(settings.database_path)
    fixture_id = 'offline_author_source_' + uuid.uuid4().hex
    result = create_source(store, snapshot, fixture_id)
    runtime = AuthorRuntime(planner, snapshot)
    factory = AgentDraftService(settings, store, runtime)
    async def verify_author(draft):
        assert runtime.calls == 1
        loaded = store.get_draft(draft.draft_id)
        folder = Path(loaded.path)
        manifest = json.loads((folder / 'agent.json').read_text(encoding='utf-8'))
        validate_manifest(manifest)
        assert all(step.get('readOnly', True) for step in manifest['execution']['steps'])
        texts = [(folder / f'content.{lang}.md').read_text(encoding='utf-8') for lang in ('zh', 'en')]
        assert all(text.strip() for text in texts) and texts[0] != texts[1]
        assert 'offline' in texts[1].lower() or 'not' in texts[1].lower()
        retry = await factory.create_from_run(fixture_id, module='MM')
        assert retry.draft_id == draft.draft_id and runtime.calls == 1
        return {'isolated_factory_persistence': True, 'bilingual': True,
                'readonly_manifest': True, 'idempotency': True, 'draft_id': draft.draft_id,
                'source_kind': 'explicit_offline_fixture_not_sap'}
    await stage('author_draft', lambda: factory.create_from_run(fixture_id, module='MM'), verify_author)
    assert manager.snapshot()['enabled'] is False
    report.update(phase='completed', runtime_after=manager.snapshot())
    atomic_json(REPORT, report)
    print(json.dumps({'report': str(REPORT.relative_to(ROOT)), 'qualified_operations': report['qualified_operations'], 'sap_calls': 0}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
