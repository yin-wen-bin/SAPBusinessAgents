"""Owned live WorkBuddy reads via existing platform Broker; no shell SAP access.

Read-only fixture targets are isolated runs. No production drafts, Campaigns,
settings, publication or default-provider selection are changed.
"""
import asyncio
import json
import time
import uuid
import sys
from dataclasses import replace
from pathlib import Path

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.models import RunCreate, RunMode, RunStatus, RuntimeSnapshot
from sap_business_agents_platform.sample_discovery import SampleDiscoveryService
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / ".local-data/workbuddy-validation"
RUN_ROOT = HERE / ('sap-' + uuid.uuid4().hex)
REPORT = RUN_ROOT / 'report.json'


async def main():
    from sap_business_agents_platform.runtime_policy import OPERATIONS
    admission = __import__('sap_business_agents_platform.workbuddy_manager', fromlist=['WorkBuddyManager']).WorkBuddyManager(ROOT).snapshot()
    required = set(OPERATIONS) - {'free_query','sample_discovery','acceptance_baseline','acceptance_free_query'}
    def required_modes(op):
        return {'bounded','trusted_local'} if op == 'review_agent_feedback' else (
            {'trusted_local'} if op == 'workflow_authoring.v2' else {'bounded'})
    missing = sorted(op for op in required if admission['operation_capabilities'][op]['status'] != 'validated'
        or not required_modes(op).issubset(admission['operation_capabilities'][op].get('permission_modes', [])))
    from sap_business_agents_platform.workbuddy_validation import legacy_fixture_gate
    if missing and not legacy_fixture_gate(__import__('sap_business_agents_platform.workbuddy_manager', fromlist=['WorkBuddyManager']).WorkBuddyManager(ROOT), {'sample_discovery'}):
        atomic_json(REPORT, {'phase': 'not_run', 'reason': 'non_sap_prerequisite_failed', 'missing_operations': missing, 'sap_calls': 0})
        print(json.dumps({'phase': 'not_run', 'missing_operations': missing, 'sap_calls': 0}), flush=True)
        return
    settings = replace(Settings.from_env(ROOT), data_root=RUN_ROOT / 'data', draft_root=RUN_ROOT / 'drafts')
    app = create_app(settings)
    # Match application startup: discovered providers are not callable until
    # their existing health/start admission has run. This is isolated state,
    # not a production Runtime enablement or an authorization override.
    await app.state.plugin_manager.start()
    store, broker = app.state.store, app.state.harness_broker
    coordinator = app.state.coordinator
    manager = app.state.sdk_manager.workbuddy
    snapshot = RuntimeSnapshot.model_validate(json.loads((HERE / 'runtime-snapshot.json').read_text(encoding='utf-8'))).model_dump(mode='json')
    from workbuddy_live_cases.live_guard import attach_validation_monitor
    attach_validation_monitor(manager, snapshot, HERE / 'sap-events.jsonl')
    report = {'phase': 'running', 'runtime': snapshot, 'stages': [], 'qualified_operations': [],
              'read_boundary': 'platform_broker_only', 'isolated_data_root': str(RUN_ROOT.relative_to(ROOT))}
    owned, native = [], manager.supervisor.run
    async def observe(**kwargs):
        assert kwargs.get('mode', 'bounded') == 'bounded' and kwargs.get('tool_handler')
        assert kwargs['snapshot'] == snapshot
        owned.append({'task_id': kwargs['task_id'], 'operation': kwargs['operation']})
        if kwargs['operation'] == 'free_query':
            original_handler = kwargs['tool_handler']
            async def guarded_handler(name, arguments):
                if name == 'sap_skill_execute':
                    return {'ok': False, 'code': 'verification_read_scope_rejected'}
                if name == 'sap_query_execute':
                    from sap_business_agents_platform.runtime_query_contract import plans
                    try:
                        steps = plans(arguments.get('plan'))
                        assert len(steps) == 1
                        step = steps[0]
                        assert step['service_name'] == 'API_PURCHASEORDER_PROCESS_SRV'
                        assert step['odata_version'] == '2.0' and step['entity_set'] == 'A_PurchaseOrder'
                        assert step.get('top') == 1 and not step.get('filter_from_previous')
                        assert set(step.get('select_fields') or []) == {'PurchaseOrder','Supplier','CompanyCode'}
                        filters = step.get('filters') or []
                        assert len(filters) == 1 and filters[0]['field'] == 'PurchaseOrder'
                        assert filters[0].get('operator','eq') == 'eq' and filters[0]['value'] == '4500001466'
                    except (AssertionError, ValueError, TypeError, KeyError):
                        return {'ok': False, 'code': 'verification_read_scope_rejected'}
                return await original_handler(name, arguments)
            kwargs['tool_handler'] = guarded_handler
        result = await native(**kwargs)
        assert result.get('actual_model') == snapshot['actual_model']
        assert result.get('model_identity_source') == 'assistant_message'
        return result
    manager.supervisor.run = observe
    atomic_json(REPORT, report)

    def read_audit(run_id):
        calls = store.list_harness_tool_calls(run_id)
        summaries = [{'tool': c['tool_name'], 'status': c['status'],
                      'error_code': (c.get('output') or {}).get('code') or (c.get('output') or {}).get('error_code')}
                     for c in calls]
        external = [c for c in calls if c['tool_name'] in {'sap_query_execute', 'sap_skill_execute'}]
        for c in external:
            args = c.get('safe_input') or {}
            if c['tool_name'] == 'sap_query_execute':
                plan = args.get('plan') or {}
                assert plan.get('method', 'GET') == 'GET'
        return {'tool_calls': summaries, 'external_read_count': len(external)}

    async def stage(operation, execute, verify):
        run_id = 'wb_live_' + uuid.uuid4().hex[:16]
        query = '只读查询采购订单4500001466的抬头，显示采购订单号、公司代码和供应商；明确top=1，不扩展到收货、付款、库存或邮件。'
        store.create_run(run_id, RunCreate(mode=RunMode.free_query, query=query), runtime=snapshot)
        entry = {'operation': operation, 'run_id': run_id, 'phase': 'running'}
        report['stages'].append(entry)
        atomic_json(REPORT, report)
        started, n = time.monotonic(), len(owned)
        print(json.dumps({'operation': operation, 'run_id': run_id, 'phase': 'starting'}), flush=True)
        try:
            with manager.supervisor.verification(snapshot, {operation}):
                result = await execute(run_id)
            proof = verify(run_id, result)
            jobs = owned[n:]
            assert jobs and all(j['operation'] == operation for j in jobs)
            for j in jobs:
                job = json.loads((manager.environment.root / 'jobs' / (digest(j['task_id']) + '.json')).read_text(encoding='utf-8'))
                assert job['status'] == 'completed' and job['cleanup_complete'] and job['validation_job']
            manager.record_operation_validation(snapshot, operation, job_id=jobs[-1]['task_id'], permission_modes=['bounded'],
                evidence={'kind': 'live_validation', 'platform_contract_passed': True, 'checks': proof,
                          'live_readonly_broker': True, 'owned_jobs': jobs})
            report['qualified_operations'].append(operation)
            entry.update(phase='passed', checks=proof, jobs=jobs)
            passed = True
        except Exception as exc:
            import traceback
            entry.update(phase='failed', failure_code=getattr(exc, 'code', type(exc).__name__),
                         validation_issues=(getattr(exc, 'detail', None) or {}).get('validation_issues'),
                         failure_location=[{'function': f.name, 'line': f.lineno} for f in traceback.extract_tb(exc.__traceback__)[-3:]],
                         jobs=owned[n:])
            passed = False
        entry.update(elapsed_seconds=round(time.monotonic() - started, 2), audit=read_audit(run_id))
        atomic_json(REPORT, report)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
        return passed

    async def free(run_id):
        await coordinator._execute_workbuddy_harness(run_id)
        return store.get_run(run_id)
    def verify_free(run_id, record):
        if record.status != RunStatus.completed or not record.result:
            failure = RuntimeError('free_query_platform_not_completed')
            error = record.error or {}
            failure.code = error.get('code', 'free_query_platform_not_completed') if isinstance(error, dict) else 'free_query_platform_not_completed'
            raise failure
        result = record.result
        assert result.presentation and result.presentation.validation_ref
        assert result.completeness.source_complete and result.completeness.business_complete
        calls = store.list_harness_tool_calls(run_id)
        assert any(c['tool_name'] == 'sap_query_execute' and c['status'] == 'completed' for c in calls)
        assert any(c['tool_name'] == 'sap_final_report_validate' and c['status'] == 'completed'
                   and (c.get('output') or {}).get('ok') is True for c in calls)
        assert result.artifacts
        return {'persisted_run_completed': True, 'report_evidence_validated': True,
                'live_get_evidence_present': True, 'source_and_business_complete': True}
    discovery = SampleDiscoveryService(settings, store, broker)
    discovery.workbuddy_manager = manager
    manifest = app.state.agents.get('mm-po-gr-status')
    before = digest(manifest)
    async def sample(run_id):
        return await discovery.discover(run_id, manifest,
            {'schedule_line_delivery_date': '2022-11-15', 'purchasing_organization': '1010', 'purchase_order': '4500001466'},
            revision=1, selected_fields=['plant'])
    def verify_sample(run_id, value):
        atomic_json(RUN_ROOT / 'sample-result.json', value)
        assert value['status'] == 'ready'
        assert value.get('selection_method', 'runtime') in {'runtime', 'deterministic'}
        assert value.get('suggested_inputs', {}).get('plant') and value.get('evidence_refs')
        assert value.get('field_sources', {}).get('plant')
        assert digest(app.state.agents.get('mm-po-gr-status')) == before
        assert read_audit(run_id)['external_read_count'] > 0
        return {'source_cell_and_schema_checked': True, 'real_sample_ready': True,
                'selection_method': value.get('selection_method', 'runtime'),
                'manifest_unchanged': True, 'requires_user_confirmation': True}
    await stage('sample_discovery', sample, verify_sample)
    report.update(phase='completed', runtime_after=manager.snapshot())
    assert report['runtime_after']['enabled'] is False
    atomic_json(REPORT, report)
    print(json.dumps({'report': str(REPORT.relative_to(ROOT)), 'qualified_operations': report['qualified_operations']}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
