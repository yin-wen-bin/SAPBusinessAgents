"""One isolated three-stage case, using the existing controller unchanged.

Only platform-admitted SAP reads; no production draft/campaign binding, no
publication. Staging supervision does not enable the public WorkBuddy Runtime.
"""
import asyncio
import json
import time
import uuid
from dataclasses import replace
from pathlib import Path

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.models import RuntimeSnapshot
from sap_business_agents_platform.acceptance import agent_execution_digest, canonical_hash
from sap_business_agents_platform.workflows import agent_digest
from sap_business_agents_platform.agent_acceptance_campaigns import _contract
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / ".local-data/workbuddy-validation"
RUN_ROOT = HERE / ('acceptance-' + uuid.uuid4().hex)
REPORT = RUN_ROOT / 'report.json'
OPERATIONS = {'acceptance_baseline', 'acceptance_free_query'}


async def main():
    from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
    from sap_business_agents_platform.runtime_policy import OPERATIONS as ALL_OPERATIONS
    from sap_business_agents_platform.workbuddy_validation import legacy_fixture_gate
    scoped = legacy_fixture_gate(WorkBuddyManager(ROOT), OPERATIONS)
    admission = None if scoped else WorkBuddyManager(ROOT).snapshot()
    required = set() if scoped else set(ALL_OPERATIONS) - OPERATIONS
    def required_modes(op):
        return {'bounded','trusted_local'} if op == 'review_agent_feedback' else (
            {'trusted_local'} if op == 'workflow_authoring.v2' else {'bounded'})
    missing = sorted(op for op in required if admission['operation_capabilities'][op]['status'] != 'validated'
        or not required_modes(op).issubset(admission['operation_capabilities'][op].get('permission_modes', [])))
    if missing:
        atomic_json(REPORT, {'phase': 'not_run', 'reason': 'prerequisite_failed', 'missing_operations': missing, 'sap_calls': 0, 'campaigns_created': 0})
        print(json.dumps({'phase': 'not_run', 'missing_operations': missing, 'campaigns_created': 0}), flush=True)
        return
    settings = replace(Settings.from_env(ROOT), data_root=RUN_ROOT / 'data', draft_root=RUN_ROOT / 'drafts')
    app = create_app(settings)
    snapshot = RuntimeSnapshot.model_validate(json.loads((HERE / 'runtime-snapshot.json').read_text(encoding='utf-8'))).model_dump(mode='json')
    # Use the actual isolated execution service before any draft/Campaign is
    # created. A frozen request must already contain its authentication binding.
    app.state.runtime_authentication.assert_binding('workbuddy', snapshot)
    await app.state.plugin_manager.start()
    manager = app.state.sdk_manager.workbuddy
    coordinator, store = app.state.coordinator, app.state.store
    jobs_service, lifecycle = app.state.agent_acceptance_campaigns, app.state.agent_lifecycle
    from workbuddy_live_cases.live_guard import attach_validation_monitor
    attach_validation_monitor(manager, snapshot, HERE / 'acceptance-events.jsonl')
    source = app.state.agents.get('mm-po-gr-status')
    source_package = app.state.agents.package('mm-po-gr-status')
    original_digest = agent_execution_digest(source, source_package['rules_source'])
    draft = lifecycle.create_version_draft('mm-po-gr-status', bump='patch',
        expected_version=source['version'], expected_hash=agent_digest(source))
    package, revision = draft['package'], draft['revision']
    contract = _contract(package['manifest'])
    case_value = {'case_id': 'workbuddy-first-case', 'input': {
        'schedule_line_delivery_date': '2022-11-15', 'purchasing_organization': '1010',
        'purchase_order': '4500001466'}, 'sensitive_inputs': {}}
    campaign_id = 'wb_validation_campaign_' + uuid.uuid4().hex[:16]
    store.create_agent_acceptance_campaign(campaign={
        'campaign_id': campaign_id, 'draft_id': draft['draft_id'], 'revision': revision,
        'operation_id': 'wb_validation_operation_' + uuid.uuid4().hex[:16],
        'request_id': uuid.uuid4().hex, 'request_hash': canonical_hash(case_value),
        'agent_id': 'mm-po-gr-status', 'acceptance_mode': 'three_stage',
        'runtime': snapshot, 'digests': {'execution': original_digest},
        'artifact_dir': str(RUN_ROOT / 'artifacts'), 'status': 'running'}, cases=[case_value])
    report = {'phase': 'running', 'campaign_id': campaign_id, 'runtime': snapshot,
        'isolated_data_root': str(RUN_ROOT.relative_to(ROOT)),
        'production_qualification_bound': False, 'qualified_operations': [], 'owned_jobs': []}
    atomic_json(REPORT, report)
    native = manager.supervisor.run
    async def observe(**kwargs):
        assert kwargs['operation'] in OPERATIONS
        assert kwargs.get('mode', 'bounded') == 'bounded' and kwargs.get('tool_handler')
        assert kwargs['snapshot'] == snapshot
        workspace = Path(kwargs['payload']['cwd']).resolve()
        assert workspace.is_relative_to(settings.data_root.resolve())
        assert not (workspace / 'agent-package').exists()
        # The actual worker receives only the frozen public business contract;
        # the fixed package stays in the platform controller for stage three.
        assert source_package['rules_source'] not in json.dumps(kwargs['payload'], ensure_ascii=False)
        item = {'task_id': kwargs['task_id'], 'operation': kwargs['operation'], 'phase': 'running'}
        report['owned_jobs'].append(item)
        atomic_json(REPORT, report)
        print(json.dumps({'phase': 'starting', **item}), flush=True)
        try:
            result = await native(**kwargs)
            assert result.get('actual_model') == snapshot['actual_model']
            assert result.get('model_identity_source') == 'assistant_message'
            item['phase'] = 'completed'
            return result
        except Exception as exc:
            item.update(phase='failed', failure_code=getattr(exc, 'code', type(exc).__name__))
            raise
        finally:
            atomic_json(REPORT, report)
    manager.supervisor.run = observe
    started = time.monotonic()
    try:
        # Scheduler tasks inherit this exact verification scope. All public
        # gates and production Runtime enabled/default selections stay intact.
        with manager.supervisor.verification(snapshot, OPERATIONS):
            await coordinator.start()
            result = await jobs_service._run_case(campaign_id, draft['draft_id'], revision, 0,
                case_value, package, contract, snapshot)
        atomic_json(RUN_ROOT / 'case-result.json', result)
        report['result'] = {'verdict': result['verdict'],
            'failure_category': result.get('failure_category'),
            'source_anchor_verdict': result.get('source_anchors', {}).get('verdict'),
            'validation_issues': result.get('validation_issues', [])}
        assert result['verdict'] == 'PASS'
        assert result['free_query']['comparison']['verdict'] == 'MATCH'
        assert result['fixed_agent']['comparison']['verdict'] == 'MATCH'
        assert result['baseline']['candidate_access'] is False
        assert result['source_anchors']['verdict'] == 'PASS'
        assert agent_execution_digest(app.state.agents.get('mm-po-gr-status'),
            app.state.agents.package('mm-po-gr-status')['rules_source']) == original_digest
        for operation in sorted(OPERATIONS):
            owned = [j for j in report['owned_jobs'] if j['operation'] == operation]
            assert len(owned) == 1
            job = json.loads((manager.environment.root / 'jobs' / (digest(owned[0]['task_id']) + '.json')).read_text(encoding='utf-8'))
            assert job['status'] == 'completed' and job['validation_job'] and job['cleanup_complete']
            manager.record_operation_validation(snapshot, operation, job_id=owned[0]['task_id'],
                permission_modes=['bounded'], evidence={'kind': 'live_validation', 'platform_contract_passed': True,
                    'isolated_three_stage_case': True, 'candidate_access': False,
                    'free_comparison': 'MATCH', 'fixed_comparison': 'MATCH', 'source_anchors': 'PASS'})
            report['qualified_operations'].append(operation)
        report['phase'] = 'passed'
    except Exception as exc:
        import traceback
        report.update(phase='failed', failure_code=getattr(exc, 'code', type(exc).__name__),
            failure_stage=getattr(exc, 'failure_stage', None),
            validation_issues=getattr(exc, 'issues', []),
            diagnostics=getattr(exc, 'diagnostics', {}),
            failure_location=[{'function': f.name, 'line': f.lineno} for f in traceback.extract_tb(exc.__traceback__)[-3:]])
        active = jobs_service.active_runs.get(campaign_id)
        if active:
            report['cleanup_complete'] = await coordinator.cancel_acceptance_run(active, timeout=10)
    finally:
        await coordinator.stop()
        await app.state.plugin_manager.stop()
        report.update(elapsed_seconds=round(time.monotonic() - started, 2), runtime_after=manager.snapshot())
        assert report['runtime_after']['enabled'] is False
        store.update_agent_acceptance_campaign(draft['draft_id'], campaign_id,
            status='passed' if report['phase'] == 'passed' else 'failed', phase='completed', report=report)
        atomic_json(REPORT, report)
        print(json.dumps({key: report.get(key) for key in ('phase', 'failure_code', 'failure_stage',
            'validation_issues', 'qualified_operations', 'elapsed_seconds')}), flush=True)
        print(str(REPORT.relative_to(ROOT)), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
