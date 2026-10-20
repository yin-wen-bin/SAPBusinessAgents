"""Legacy workflow operations: real SDK, platform compiler, isolated saved draft."""
import asyncio
import copy
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.workflow_factory import WorkflowDraftService
from sap_business_agents_platform.workflow_composer import compact_agent_catalog, compile_workflow_proposal
from sap_business_agents_platform.workflow_assistant import fixed_bindings
from sap_business_agents_platform.workflows import validate_workflow, workflow_review_contract
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / ".local-data/workbuddy-validation"
RUN_ROOT = HERE / ('workflows-' + uuid.uuid4().hex)
REPORT = RUN_ROOT / 'report.json'


class Runtime:
    current_provider_id = 'workbuddy'
    def __init__(self, planner, snapshot):
        self.planner, self.binding = planner, snapshot
    def snapshot(self, *_):
        return copy.deepcopy(self.binding)
    def supports(self, operation):
        return operation in {'compose_workflow', 'review_workflow_feedback'}
    def workbuddy_reservation(self):
        return self.planner.workbuddy_reservation()
    @contextmanager
    def pin_snapshot(self, value):
        assert all(value.get(k) == v for k, v in self.binding.items())
        yield
    async def compose_workflow(self, **kwargs):
        return await self.planner.compose_workflow(**kwargs)
    async def review_workflow_feedback(self, **kwargs):
        return await self.planner.review_workflow_feedback(**kwargs)


async def main():
    manager = WorkBuddyManager(ROOT)
    snapshot = json.loads((HERE / 'runtime-snapshot.json').read_text(encoding='utf-8'))
    from workbuddy_live_cases.live_guard import attach_validation_monitor
    attach_validation_monitor(manager, snapshot, HERE / 'workflows-events.jsonl')
    planner = WorkBuddyPlanner(ROOT, snapshot['model'], supervisor=manager.supervisor, runtime_snapshot=snapshot)
    agents = AgentRepository(ROOT / 'agents')
    catalog = compact_agent_catalog(agents)
    settings = Settings(repository_root=ROOT, data_root=RUN_ROOT / 'data', draft_root=RUN_ROOT / 'drafts')
    store = RunStore(settings.database_path)
    service = WorkflowDraftService(settings, store, agents, None, None, Runtime(planner, snapshot))
    report = {'phase': 'running', 'runtime': snapshot, 'sap_calls': 0, 'stages': [], 'qualified_operations': []}
    jobs = []
    original = manager.supervisor.run
    async def observe(**kwargs):
        task = kwargs.get('task_id') or 'wb-workflow-' + uuid.uuid4().hex
        kwargs['task_id'] = task
        jobs.append({'task_id': task, 'operation': kwargs['operation']})
        assert kwargs['mode'] == 'bounded' and not kwargs.get('tool_handler') and not kwargs['payload'].get('tools')
        result = await original(**kwargs)
        # These inputs contain repository definitions only, never private SAP rows.
        atomic_json(RUN_ROOT / (task + '-response.json'), result)
        assert result.get('actual_model') == snapshot['actual_model']
        assert result.get('model_identity_source') == 'assistant_message'
        return result
    manager.supervisor.run = observe

    async def stage(operation, execute, verify):
        from runtime_validation_cases import selected, active_adapter
        if not selected(operation):
            path = RUN_ROOT / (operation + '-precursor.json')
            if active_adapter() and path.exists():
                value = json.loads(path.read_text(encoding='utf-8'))
                return service.get(value['draft_id']) if operation == 'compose_workflow' else value
            return None
        entry = {'operation': operation, 'phase': 'running'}
        report['stages'].append(entry)
        atomic_json(REPORT, report)
        started, n = time.monotonic(), len(jobs)
        print(json.dumps({'operation': operation, 'phase': 'starting'}), flush=True)
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
                    'sap_calls': 0, 'checks': proof, 'owned_jobs': owned})
            report['qualified_operations'].append(operation)
            entry.update(phase='passed', checks=proof, jobs=owned)
            if active_adapter() and operation in {'compose_workflow', 'review_workflow'}:
                atomic_json(RUN_ROOT / (operation + '-precursor.json'),
                    {'draft_id': result.draft_id} if operation == 'compose_workflow' else result)
            return result
        except Exception as exc:
            import traceback
            entry.update(phase='failed', failure_code=getattr(exc, 'code', type(exc).__name__),
                failure_location=[{'function': f.name, 'line': f.lineno} for f in traceback.extract_tb(exc.__traceback__)[-3:]],
                jobs=jobs[n:])
        finally:
            entry['elapsed_seconds'] = round(time.monotonic() - started, 2)
            atomic_json(REPORT, report)
            print(json.dumps(entry, ensure_ascii=False), flush=True)

    async def compose():
        draft = service.start_composition('只创建一个采购订单入库状态检查工作流，使用已批准的mm-po-gr-status。'
            '对外暴露该Agent已声明的查询条件与业务结果，无邮件、付款或其他Agent，不运行SAP。', 'zh')
        tasks = list(service._tasks)
        assert tasks
        await asyncio.gather(*tasks)
        return service.get(draft.draft_id)
    def verify_composition(draft):
        assert not draft.composition.get('error') and not draft.composition.get('gaps')
        validate_workflow(draft.workflow, agents, require_pins=True)
        assert len(draft.workflow['nodes']) == 1
        assert draft.workflow['nodes'][0]['agentId'] == 'mm-po-gr-status'
        assert store.list_workflow_conversation_turns(draft.draft_id)[-1]['status'] == 'completed'
        return {'isolated_compiler_and_persistence': True, 'fixed_bindings': fixed_bindings(draft.workflow),
                'draft_id': draft.draft_id, 'no_integrations': True}
    draft = await stage('compose_workflow', compose, verify_composition)

    # Independent valid platform-compiled specimen means a composition failure
    # does not prevent checking review/repair of existing saved workflows.
    specimen, composition = compile_workflow_proposal(workflow_id='wb-legacy-validation',
        requirement='采购订单入库状态检查', locale='zh', agents=agents, catalog=catalog,
        proposal={'title': {'zh': '隔离入库检查', 'en': 'Isolated receipt check'},
                  'summary': {'zh': '离线编写验证', 'en': 'Offline authoring validation'},
                  'stages': [{'id': 'receipt', 'agent_id': 'mm-po-gr-status', 'confidence': 'high',
                    'capability': {'zh': '入库状态', 'en': 'Receipt status'}, 'bindings': [],
                    'requested_outputs': ['business_status', 'business_report', 'records']} ]})
    assert not composition['gaps']
    validate_workflow(specimen, agents, require_pins=True)
    pinned = [{'agent_id': 'mm-po-gr-status', 'input_schema': agents.get('mm-po-gr-status')['execution']['inputSchema'],
               'output_schema': agents.get('mm-po-gr-status')['execution']['outputSchema']}]
    contract = workflow_review_contract(specimen, agents)
    def verify_review(result):
        assert result['verdict'] == 'pass' and not any(i['severity'] == 'error' for i in result['issues'])
        assert all(result['summary'].get(lang, '').strip() for lang in ('zh', 'en'))
        validate_workflow(specimen, agents, require_pins=True)
        return {'canonical_workflow_valid': True, 'review_contract_checked': True, 'bilingual': True}
    reviewed = await stage('review_workflow', lambda: planner.review_workflow(workflow=specimen, agent_contracts=pinned,
        validation_input={'schedule_line_delivery_date': '2022-11-15', 'purchasing_organization': '1010', 'purchase_order': '4500001466'},
        review_contract=contract), verify_review)
    damaged = copy.deepcopy(specimen)
    expected = copy.deepcopy(specimen['connections'])
    damaged['connections'][0]['to']['port'] = 'undeclared_input'
    def verify_repair(result):
        candidate = copy.deepcopy(damaged)
        candidate['connections'] = result['connections']
        validate_workflow(candidate, agents, require_pins=True)
        assert candidate['connections'] == expected
        assert fixed_bindings(candidate) == fixed_bindings(specimen)
        assert candidate == specimen
        return {'only_connections_changed': True, 'declared_ports_restored': True, 'fixed_bindings_unchanged': True}
    from runtime_validation_cases import selected
    if reviewed and selected('repair_workflow'):
        await stage('repair_workflow', lambda: planner.repair_workflow(workflow=damaged, agent_contracts=pinned,
            error={'code': 'workflow_connection_input_missing', 'message': 'connection references undeclared_input; restore declared input ports only'},
            thread_id=reviewed['thread_id']), verify_repair)
    elif selected('repair_workflow'):
        report['stages'].append({'operation': 'repair_workflow', 'phase': 'not_run', 'reason': 'review_failed'})

    if not selected('review_workflow_feedback'):
        report.update(phase='completed', runtime_after=manager.snapshot())
        atomic_json(REPORT, report)
        return
    if draft is None:
        report['stages'].append({'operation': 'review_workflow_feedback', 'phase': 'not_run', 'reason': 'composition_failed'})
        report.update(phase='completed', runtime_after=manager.snapshot())
        atomic_json(REPORT, report)
        return
    saved = draft
    before_bindings = fixed_bindings(saved.workflow)
    async def feedback():
        current = service.get(saved.draft_id)
        turns = store.list_workflow_conversation_turns(saved.draft_id)
        service.submit_feedback(saved.draft_id, base_turn=turns[-1]['turn'] if turns else 1,
            base_revision=current.revision, feedback='只把双语标题改为“隔离采购订单检查” / “Isolated Purchase Order Check”。'
            '保留唯一Agent、固定版本、Schema、绑定与业务范围，不执行SAP。',
            feedback_type_hint='presentation', locale='zh', validation_run_id=None)
        await asyncio.gather(*list(service._tasks))
        return service.get(saved.draft_id)
    def verify_feedback(after):
        turn = store.list_workflow_conversation_turns(after.draft_id)[-1]
        assert turn['status'] == 'completed' and turn['action'] == 'revise_workflow'
        validate_workflow(after.workflow, agents, require_pins=True)
        assert fixed_bindings(after.workflow) == before_bindings
        assert after.workflow['title'] == {'zh': '隔离采购订单检查', 'en': 'Isolated Purchase Order Check'}
        return {'isolated_feedback_persisted': True, 'fixed_bindings_unchanged': True,
                'turn': turn['turn'], 'revision': after.revision}
    await stage('review_workflow_feedback', feedback, verify_feedback)
    report.update(phase='completed', runtime_after=manager.snapshot())
    assert report['runtime_after']['enabled'] is False
    atomic_json(REPORT, report)
    print(json.dumps({'report': str(REPORT.relative_to(ROOT)),
                      'qualified_operations': report['qualified_operations'], 'sap_calls': 0}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
