"""Campaign admission through shared source handling with synthetic execution.

The vendor seam and deterministic candidate calculation are fixtures. Source
schema/GET validation, frozen auth, scheduling, reporting and comparison are
production code. All evidence and synthetic verdicts remain in tmp_path.
"""
import asyncio
import copy
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sap_business_agents_platform.acceptance import agent_execution_digest, canonical_hash
from sap_business_agents_platform.acceptance_contract import compile_contract, contract_issues
from sap_business_agents_platform.acceptance_runtime import AgentAcceptanceCampaignRequest, acceptance_runtime_digest
from sap_business_agents_platform.agent_acceptance_campaigns import AgentAcceptanceJobs
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.engine import RunCoordinator
from sap_business_agents_platform.harness import CodexHarnessController, HarnessToolBroker
from sap_business_agents_platform.models import RunCreate, RunMode
from sap_business_agents_platform.runtime import RuntimeRouter
from sap_business_agents_platform.runtime_authentication import RuntimeAuthenticationService
from sap_business_agents_platform.runtime_contract import RuntimeResult, RuntimeSession, RuntimeContractError
from sap_business_agents_platform.workbuddy_harness import WorkBuddyHarnessController
from sap_business_agents_platform.workbuddy_supervisor import WorkBuddySupervisor
from sap_business_agents_platform.workbuddy_site import make_configuration
from tests.test_agent_acceptance_campaigns import _Lifecycle, _seed_runnable
from tests.test_harness import FakeSkills
from tests.test_source_semantics import definition, gl_plan
from tests.test_source_semantics_flow import provider_with_memory_http

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('provider', ['codex', 'workbuddy'])
@pytest.mark.parametrize('failure', [None, 'grain', 'coverage'])
def test_campaign_admission_to_true_source_guard_and_comparison(tmp_path, monkeypatch, provider, failure):
    from sap_business_agents_platform.codex_driver import CodexHarnessDriver
    from sap_business_agents_platform.workbuddy_driver import WorkBuddyHarnessDriver
    settings = Settings(repository_root=ROOT, data_root=tmp_path/'state')
    store = RunStore(settings.database_path)
    site = make_configuration('china',1) if provider == 'workbuddy' else {}
    auth = RuntimeAuthenticationService(settings.data_root/'sdk-runtimes', {provider:SimpleNamespace(context=lambda:site)})
    auth.state['accounts'][provider] = {'revision':9,'status':'authenticated','authenticated':True,**site}
    snapshot = auth.bind(provider, {'provider_id':provider,'sdk_id':'offline-only','model':'offline-model',
        'actual_model':'offline-model','configuration_digest':'offline','execution_flow':'harness',
        'model_identity_known':True, 'model_identity_source':'assistant_message',
        'environment_digest':'offline-environment','sdk_fingerprint':'offline-sdk',
        'reasoning_effort':'high' if provider=='codex' else None,**site})
    class Environment:
        root = tmp_path/'worker-not-started'
        def assert_operation(self, bound, operation, **kwargs): auth.assert_binding(provider,bound)
    environment = Environment()
    supervisor = WorkBuddySupervisor(environment)
    manager = SimpleNamespace(default_provider_id=provider,configuration_revision=1,authentication_service=auth,
        runtime_snapshot=lambda:copy.deepcopy(snapshot),runtime_snapshot_for_model=lambda *a:copy.deepcopy(snapshot))
    manager.workbuddy = SimpleNamespace(environment=environment,supervisor=supervisor,
        bound_snapshot=lambda value,**k:copy.deepcopy(value),runtime_snapshot=lambda *a,**k:copy.deepcopy(snapshot))
    @contextmanager
    def events(sink): yield
    router = RuntimeRouter(manager,{provider:SimpleNamespace(bind_events=events)})
    sap, http_trace = provider_with_memory_http(tmp_path)
    broker = HarnessToolBroker(settings,store,sap,FakeSkills())
    codex = CodexHarnessController(settings,store,broker)
    wb = WorkBuddyHarnessController(settings,store,broker,manager.workbuddy)
    codex.authentication_service = wb.authentication_service = auth
    coordinator = RunCoordinator(settings,store,SimpleNamespace(),sap,FakeSkills(),router,harness=codex)
    coordinator.workbuddy_harness = wb
    draft = _seed_runnable(store,tmp_path)
    package = store.get_agent_authoring_revision(draft['draft_id'],4)['package']
    manifest = package['manifest']
    acceptance = manifest['execution']['acceptance']
    acceptance['sourceSemantics'] = definition(categories=['posted_normal','noted_item'] if failure=='coverage' else ['posted_normal'])
    acceptance['facts'].append('record_kind')
    acceptance['factDefinitions']['record_kind'] = 'Economic category proved by source evidence.'
    item_schema = manifest['execution']['outputSchema']['properties']['records']['items']
    item_schema['properties']['record_kind'] = {'type':'string','enum':['posted_normal','noted_item'], 'title':{'zh':'类型','en':'Kind'}}
    item_schema['required'].append('record_kind')
    assert not contract_issues(manifest)
    trial = store.get_run('trial-ready').result.model_dump(mode='json')
    trial['workflow_output']['records'][0]['record_kind'] = 'posted_normal'
    trial['workflow_output']['business_report']['records'][0]['record_kind'] = 'posted_normal'
    store.update_run('trial-ready',result_json=trial)
    draft['revision'] = 5
    for key in ('trial','effective_trial'):
        draft['metadata'][key].update(revision=5, execution_digest=agent_execution_digest(manifest),
            acceptance_contract_digest=canonical_hash(acceptance))
    store.save_agent_authoring_draft(draft,package=package)
    operations = []
    captured = []
    text = lambda value:{'zh':str(value),'en':str(value)}
    async def native(driver,request,context):
        operations.append(request.operation)
        if request.operation == 'acceptance_baseline':
            assert request.context == {} and not (Path(request.cwd)/'agent-package').exists()
        rid,cap = context['run_id'],context['capability']
        schema = await broker.handle(rid,cap,'sap_schema_get',{'service_name':'API_GLACCOUNTLINEITEM','odata_version':'2.0','entity_sets':['GLAccountLineItem']})
        assert schema['ok']
        candidate = gl_plan()
        if failure == 'grain': candidate['select_fields'] = ['CompanyCode','AmountInCompanyCodeCurrency']
        v = await broker.handle(rid,cap,'sap_query_validate',{'plan':candidate})
        if not v['ok']:
            error = RuntimeContractError('source_query_contract_rejected')
            error.detail = {'validation_issues':v['validation_issues']}
            raise error
        read = await broker.handle(rid,cap,'sap_query_execute',{'plan':candidate})
        assert read['ok']
        captured.append(read['source_semantics'])
        refs = [read['evidence_ref']]
        record = {'company_code':'1710','business_status':'normal','item_count':2,'record_kind':'posted_normal','evidence_refs':refs}
        projection = {'records':[record],'metrics':{'count':1},'business_status':'normal',
            'source_complete':True,'evidence_complete':True,'business_complete':True,'evidence_gap_codes':[],'evidence_refs':refs}
        columns = list(record.keys()-{'evidence_refs'})
        report = {'title':text('Offline only'),'blocks':[
            {'type':'table','columns':[{'key':k,'label':text(k)} for k in columns],
             'rows':[{'values':[text(record[k]) for k in columns],'evidence_refs':refs}],
             'claim_scope':'customer_business_fact','evidence_refs':refs},
            {'type':'metrics','metrics':[{'id':k,'label':text(k),'value':text(v),'evidence_refs':refs} for k,v in
                [('count',1),('business_status','normal'),('source_complete','true'),('evidence_complete','true'),('business_complete','true')]],
             'claim_scope':'customer_business_fact','evidence_refs':refs}]}
        checked = await broker.handle(rid,cap,'sap_final_report_validate',{'report':report,'acceptance_projection':projection})
        if not checked['ok']:
            error = RuntimeContractError('acceptance_report_validation_failed')
            error.detail = {'validation_issues':checked['validation_issues']}
            raise error
        context['thread_id'] = 'offline-thread'
        return RuntimeResult({'status':'completed','intent':'read','clarification_question':'','input_kind':None,'input_field':None,
            'summary':text('Offline only'),'source_complete':True,'business_complete':True,'missing_evidence':[],
            'evidence_refs':refs,'executed_plans':[],'presentation':checked['validated_report'],'acceptance_projection':projection},
            RuntimeSession(provider,'offline-thread'),cleanup_complete=True)
    monkeypatch.setattr(CodexHarnessDriver,'harness_turn',native)
    monkeypatch.setattr(WorkBuddyHarnessDriver,'harness_turn',native)
    async def fixed_fixture(manifest, inputs, **kwargs):
        # Candidate calculation is synthetic. Read source again through the
        # production Provider; never reuse baseline data as the candidate answer.
        raw = await sap.execute_plan(gl_plan(),'offline fixed fixture')
        value = copy.deepcopy(trial)
        value['evidence'] = [{'payload':raw}]
        store.create_run('fixed-offline',RunCreate(mode=RunMode.agent,agentId='acceptance-test',input=inputs))
        store.update_run('fixed-offline',status='completed',result_json={**value,'run_id':'fixed-offline'})
        return 'fixed-offline'
    monkeypatch.setattr(coordinator,'submit_agent_snapshot',fixed_fixture)
    jobs = AgentAcceptanceJobs(settings,store,_Lifecycle(),coordinator,manager)
    real_popen = subprocess.Popen
    def local_git_only(args, **kwargs):
        command = str(args[0]).replace('\\','/').split('/')[-1].lower()
        assert command in {'git','git.exe'}, 'SDK process forbidden'
        assert not {'push','pull','fetch','clone','reset','clean'}.intersection(str(a) for a in args[1:])
        if {'commit','add','init','config'}.intersection(str(a) for a in args[1:]):
            assert Path(kwargs.get('cwd') or '.').resolve().is_relative_to(tmp_path.resolve())
        return real_popen(args, **kwargs)
    async def scenario():
        with patch('socket.socket.connect',side_effect=AssertionError('live network forbidden')), patch('subprocess.Popen',side_effect=local_git_only):
            await coordinator.start()
            try:
                payload = AgentAcceptanceCampaignRequest(expectedRevision=5,requestId='one-only',
                    expectedRuntimeDigest=acceptance_runtime_digest(snapshot),cases=[{'caseId':'one','input':{'company_code':'1710'}}])
                first = jobs.start(draft['draft_id'],payload)
                await jobs.tasks[first['campaign_id']]
                value = jobs.get(draft['draft_id'],first['campaign_id'])
                assert jobs.start(draft['draft_id'],payload)['campaign_id'] == first['campaign_id']
                assert auth.leases.get(provider,0) == 0 and supervisor.reconcile() == []
                return value
            finally:
                await coordinator.stop()
    campaign = asyncio.run(scenario())
    report = campaign['report']
    if failure:
        assert report['verdict'] == 'NOT_TESTED' and report['executable'] is False, report
        assert report['failure_stage'] == 'baseline' and report['failure_category'] == 'evidence', report
        assert operations == ['acceptance_baseline']
        if failure=='grain': assert 'data' not in http_trace
    else:
        assert report['verdict'] == 'PASS', report
        assert operations == ['acceptance_baseline','acceptance_free_query']
        assert report['cases'][0]['fixed_agent']['comparison']['verdict'] == 'MATCH'
        assert len(captured) == 2
    # These are synthetic isolated test records, never production business PASS.
    assert store.path.is_relative_to(tmp_path) if hasattr(store,'path') else settings.data_root.is_relative_to(tmp_path)
