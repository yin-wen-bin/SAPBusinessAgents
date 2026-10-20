"""Real feedback classification and persisted reinterpretation with offline lineage."""
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
from sap_business_agents_platform.engine import RunCoordinator, _normalize_feedback_decision, _presentation_evidence_refs
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.models import RunCreate, RunMode, RunResult, RunStatus
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / ".local-data/workbuddy-validation"
RUN_ROOT = HERE / ('free-feedback-' + uuid.uuid4().hex)
REPORT = RUN_ROOT / 'report.json'
PRESENTATION = {'schema_version': '1.0', 'title': {'zh': '离线样例', 'en': 'Offline fixture'},
    'validation_ref': 'offline_fixture_validation', 'blocks': [{
        'type': 'table', 'title': {'zh': '样例标记', 'en': 'Fixture marker'}, 'tone': 'neutral',
        'claim_scope': 'diagnostic', 'evidence_refs': ['fixture_ref'],
        'columns': [{'key': 'fixture', 'label': {'zh': '仅离线', 'en': 'Offline only'}, 'format': 'text'}],
        'rows': [{'values': [{'zh': '未读取SAP', 'en': 'SAP not read'}], 'evidence_refs': ['fixture_ref']}],
        'total_rows': 1, 'display_truncated': False, 'source_complete': False}]}


class Runtime:
    def __init__(self, manager, planner, binding):
        self.manager, self.planner, self.binding = manager, planner, binding
    def supports(self, operation):
        return operation in {'review_free_query_feedback', 'revise_free_query_presentation'}
    def snapshot_for_binding(self, binding):
        return self.manager.bound_snapshot(binding)
    @contextmanager
    def pin_snapshot(self, binding):
        assert all(binding.get(k) == v for k, v in self.binding.items())
        yield
    async def revise_free_query_presentation(self, **kwargs):
        return await self.planner.revise_free_query_presentation(**kwargs)


async def main():
    manager = WorkBuddyManager(ROOT)
    snapshot = json.loads((HERE / 'runtime-snapshot.json').read_text(encoding='utf-8'))
    from workbuddy_live_cases.live_guard import attach_validation_monitor
    attach_validation_monitor(manager, snapshot, HERE / 'free-feedback-events.jsonl')
    planner = WorkBuddyPlanner(ROOT, snapshot['model'], supervisor=manager.supervisor, runtime_snapshot=snapshot)
    jobs = []
    original = manager.supervisor.run
    async def observe(**kwargs):
        task = kwargs.get('task_id') or 'wb-feedback-' + uuid.uuid4().hex
        kwargs['task_id'] = task
        jobs.append({'task_id': task, 'operation': kwargs['operation']})
        assert kwargs['mode'] == 'bounded' and not kwargs.get('tool_handler') and not kwargs['payload'].get('tools')
        result = await original(**kwargs)
        atomic_json(RUN_ROOT / (task + '-response.json'), result)
        assert result.get('actual_model') == snapshot['actual_model']
        return result
    manager.supervisor.run = observe
    report = {'phase': 'running', 'runtime': snapshot, 'input_kind': 'explicit_offline_fixture',
              'sap_calls': 0, 'stages': [], 'qualified_operations': []}
    atomic_json(REPORT, report)
    async def stage(operation, execute, verify):
        from runtime_validation_cases import selected
        if not selected(operation):
            return None
        n, started = len(jobs), time.monotonic()
        entry = {'operation': operation, 'phase': 'running'}
        report['stages'].append(entry)
        print(json.dumps({'operation': operation, 'phase': 'starting'}), flush=True)
        atomic_json(REPORT, report)
        try:
            with manager.supervisor.verification(snapshot, {operation}):
                result = await execute()
            proof = verify(result)
            owned = jobs[n:]
            assert owned and all(j['operation'] == operation for j in owned)
            for j in owned:
                job = json.loads((manager.environment.root / 'jobs' / (digest(j['task_id']) + '.json')).read_text(encoding='utf-8'))
                assert job['status'] == 'completed' and job['cleanup_complete'] and job['validation_job']
            manager.record_operation_validation(snapshot, operation, job_id=owned[-1]['task_id'],
                permission_modes=['bounded'], evidence={'kind': 'live_validation', 'platform_contract_passed': True,
                    'input_kind': 'explicit_offline_fixture', 'sap_calls': 0, 'checks': proof, 'owned_jobs': owned})
            report['qualified_operations'].append(operation)
            entry.update(phase='passed', checks=proof, jobs=owned)
        except Exception as exc:
            import traceback
            entry.update(phase='failed', failure_code=getattr(exc, 'code', type(exc).__name__), jobs=jobs[n:],
                failure_location=[{'function': f.name, 'line': f.lineno} for f in traceback.extract_tb(exc.__traceback__)[-3:]])
        entry['elapsed_seconds'] = round(time.monotonic() - started, 2)
        atomic_json(REPORT, report)
        print(json.dumps(entry, ensure_ascii=False), flush=True)

    cases = [('presentation', '仅把结果改为更清楚的双语表格，不改变查询范围和事实。', 'reinterpret'),
             ('scope_or_filter', '把采购订单筛选改为4500000114，重新取证，不沿用旧事实。', 'requery'),
             ('new_intent', '这是一个完全不同的新问题：改为查生产订单，不保留原采购范围，建立新的查询会话。', 'start_new_session'),
             ('unclear', '请帮我修改一下，但我尚未确定要改查询范围还是展示方式，请先问清楚。', 'clarify')]
    async def classify():
        answers = []
        for kind, message, action in cases:
            raw = await planner.review_free_query_feedback(thread_id='platform-fixture-context', original_query='采购订单只读查询',
                previous_query='采购订单4500001466只读查询', previous_plan={'method': 'GET'},
                previous_presentation=PRESENTATION, previous_summary={'zh': '离线样例', 'en': 'Offline fixture'},
                available_evidence_refs=['fixture_ref'], completeness={'source_complete': False, 'business_complete': False},
                deterministic_rule_results=[], feedback=message, feedback_type_hint=kind, supplemental_input=None)
            checked = _normalize_feedback_decision(raw, kind, available_evidence_refs={'fixture_ref'})
            assert checked['action'] == action and checked['feedback_type'] == kind
            assert all(set(e['evidence_refs']) <= {'fixture_ref'} for e in checked['candidate_expectations'])
            if action == 'clarify':
                assert checked['clarification_question']
            answers.append(checked)
        return answers
    await stage('review_free_query_feedback', classify,
        lambda answers: {'four_feedback_paths': [a['action'] for a in answers], 'evidence_reference_checked': True})

    if '--classify-only' in sys.argv:
        report.update(phase='completed', runtime_after=manager.snapshot())
        atomic_json(REPORT, report)
        return

    settings = Settings(repository_root=ROOT, data_root=RUN_ROOT / 'data', draft_root=RUN_ROOT / 'drafts')
    store = RunStore(settings.database_path)
    source, target = 'offline_source_' + uuid.uuid4().hex, 'offline_reinterpret_' + uuid.uuid4().hex
    for run_id in (source, target):
        store.create_run(run_id, RunCreate(mode=RunMode.free_query, query='仅离线样例展示，不访问SAP'), runtime=snapshot)
    result = RunResult(run_id=source, mode=RunMode.free_query, plan={'fixture': True},
        evidence=[{'evidence_ref': 'fixture_ref', 'source': 'explicit_offline_fixture', 'source_complete': False}],
        presentation=PRESENTATION, completeness={'source_complete': False, 'business_complete': False,
            'reason': 'Offline fixture only', 'missing_evidence': ['live_sap_not_read']},
        summary={'zh': 'SAP未读取', 'en': 'SAP not read'}, thread_id='platform-fixture-context')
    store.update_run(source, status=RunStatus.inconclusive, plan_json=result.plan, result_json=result, thread_id=result.thread_id)
    store.save_free_query_evidence_links(target, source, {'alias_fixture': 'fixture_ref'})
    # No SAP provider/Skill is supplied: reinterpretation must never touch either.
    coordinator = RunCoordinator(settings, store, AgentRepository(ROOT / 'agents'), None, None,
                                 Runtime(manager, planner, snapshot))
    async def reinterpret():
        await coordinator._execute_free_query_reinterpret(target, {'source_run_id': source,
            'feedback': '仅将报告标题改为“离线验证结果” / “Offline Validation Result”。保持行值、证据、列键、完整性与限制不变，不调用SAP。'})
        return store.get_run(target)
    def verify_reinterpret(saved):
        assert saved.status == RunStatus.inconclusive and saved.result
        revised = saved.result
        assert revised.completeness == result.completeness and revised.rule_results == result.rule_results
        assert not revised.tool_calls and revised.evidence[0]['source_run_id'] == source
        assert _presentation_evidence_refs(revised.presentation.model_dump(mode='json')) == {'alias_fixture'}
        assert revised.presentation.title.model_dump() == {'zh': '离线验证结果', 'en': 'Offline Validation Result'}
        block = revised.presentation.blocks[0]
        assert block.type == 'table' and len(block.rows) == 1
        assert block.rows[0].values[0].model_dump() == PRESENTATION['blocks'][0]['rows'][0]['values'][0]
        assert block.columns[0].key == 'fixture' and block.source_complete is False
        assert revised.artifacts
        assert store.get_run(source).result == result
        return {'isolated_reinterpretation_persisted': True, 'zero_sap_path': True,
                'completeness_and_lineage_preserved': True, 'display_values_unchanged': True,
                'source_run_immutable': True}
    await stage('revise_free_query_presentation', reinterpret, verify_reinterpret)
    report.update(phase='completed', runtime_after=manager.snapshot())
    assert report['runtime_after']['enabled'] is False
    atomic_json(REPORT, report)
    print(json.dumps({'report': str(REPORT.relative_to(ROOT)), 'qualified_operations': report['qualified_operations'],
                      'sap_calls': 0}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
