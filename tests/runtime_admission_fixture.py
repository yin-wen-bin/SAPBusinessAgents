"""Isolated full API/coordinator/Harness fixture. Only vendor and SAP bytes are synthetic."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.runtime import RuntimeRouter
from sap_business_agents_platform.runtime_contract import RuntimeResult, RuntimeSession
from sap_business_agents_platform import runtime_harness
from sap_business_agents_platform.runtime_input_control import InputPause
from tests.test_sdk_manager import FakeSDKManager, HealthyEmbeddedProvider
from tests.test_runtime_report_contract import report
from tests.test_report_verification_v2 import table
from tests.test_shared_query_money_chain import row, plan, metric

QUERY='查询供应商17300003截止2022/12/31的未清发票校验'


def fixture_app(data_root, provider='codex'):
    root=Path(__file__).resolve().parents[1]
    settings=Settings(repository_root=root,data_root=Path(data_root),draft_root=Path(data_root)/'drafts',
        skillhub_root=Path(data_root)/'skills',max_free_query_seconds=1800)
    app=create_app(settings,planner=SimpleNamespace(model='offline-model'),
        sdk_manager=FakeSDKManager(),embedded_provider=HealthyEmbeddedProvider())
    owner=app.state.coordinator;store=app.state.store;broker=app.state.harness_broker
    class Manager:
        default_provider_id=provider
        authentication_service=None
        def list(self):return [{'provider_id':provider,'selectable':True,'blockers':[]}]
        def runtime_snapshot(self, selected):
            return {'provider_id':selected,'sdk_id':'synthetic-'+selected,'model':'offline-model',
                'version':'offline','cli_version':'offline','reasoning_effort':'max' if selected=='codex' else None,
                'configuration_digest':'offline','capabilities':[], 'model_check_digest':'offline',
                'runtime_configuration_revision':0}
        def runtime_snapshot_for_model(self, selected, model):
            assert model=='offline-model'
            return self.runtime_snapshot(selected)
    manager=Manager()
    from sap_business_agents_platform.runtime_admission import PreparedRuntime
    manager.workbuddy=SimpleNamespace(environment=object(),
        prepare_admission=lambda request:PreparedRuntime(manager.runtime_snapshot(provider),request.operation,request.permission_mode),
        bound_snapshot=lambda snapshot:manager.runtime_snapshot_for_model(provider,snapshot['model']))
    from sap_business_agents_platform.workbuddy_supervisor import WorkBuddySupervisor
    manager.workbuddy.supervisor=WorkBuddySupervisor(manager.workbuddy.environment)
    # Native process inventory is synthetic; scheduling/capacity remain real.
    manager.workbuddy.supervisor.reconcile=lambda:[]
    owner.planner=RuntimeRouter(manager,{provider:SimpleNamespace()})

    class NativeDriver:
        provider_id=provider
        engineering_copy=False
        def binding(self, run_id, state, model, effort):
            return store.get_run(run_id).runtime.model_dump(mode='json')
        async def cleanup(self, run_id, context):context['cleanup_state']['complete']=True
        async def harness_turn(self, request, context):
            run_id=context['run_id'];context['thread_id']='offline-thread-'+provider
            context['cleanup_state']['complete']=True
            if not store.get_harness_state(run_id).get('input_control'):
                result=await runtime_harness.tool_call(controller,context,'request_query_clarification',
                    {'question':'请确认公司代码 / Please confirm the company code'})
                assert result['ok'];raise InputPause()
            records=[row(CompanyCode='1710',Supplier='17300003',FiscalYear='2022',PostingDate='2022-01-01')]
            frozen_plan=plan()
            frozen_plan['select_fields']=list(dict.fromkeys([*frozen_plan['select_fields'],'Supplier']))
            frozen_plan['filters']=[{'field':'CompanyCode','operator':'eq','value':'1710'},
                {'field':'Supplier','operator':'eq','value':'17300003'},
                {'field':'PostingDate','operator':'le','value':'2022-12-31'}]
            ref=broker._save_evidence(run_id,'sap_live',{'rows':records,'source_complete':True})
            call='offline-source-'+run_id
            store.begin_harness_tool_call(call_id=call,run_id=run_id,tool_name='sap_query_execute',
                request_hash=ref,safe_input={'plan':frozen_plan})
            store.complete_harness_tool_call(call,status='completed',output={'ok':True,'evidence_ref':ref,
                'source_type':'sap_live','source_complete':True,'row_count':1},evidence_ref=ref)
            calc=await runtime_harness.tool_call(controller,context,'sap_money_aggregate',{'evidence_ref':ref})
            assert calc['ok'],calc
            accepted=await runtime_harness.tool_call(controller,context,'sap_final_report_validate',{'report':
                report(schema_version='2.0',blocks=[{'type':'metrics','metrics':[metric(calc)]},table(ref,records)])})
            assert accepted['ok'],accepted
            payload={'status':'inconclusive','intent':'offline fixture','summary':{'zh':'合成证据离线核对完成','en':'Synthetic evidence checked offline'},
                'source_complete':True,'business_complete':False,'missing_evidence':['offline_fixture_not_business_PASS'],
                'evidence_refs':[ref],'executed_plans':[],'presentation':accepted['validated_report']}
            return RuntimeResult(payload,RuntimeSession(provider,context['thread_id']),cleanup_complete=True)
    driver=NativeDriver()
    controller=SimpleNamespace(store=store,broker=broker,settings=settings,manager=manager.workbuddy)
    async def run(run_id,query,thread_id=None,model=None,reasoning_effort=None):
        return await runtime_harness.run(controller,driver,run_id,query,thread_id,model,reasoning_effort)
    controller.run=run
    owner.harness=controller;owner.workbuddy_harness=controller
    app.state.offline_fixture={'provider':provider,'real_model_calls':0,'real_SAP_calls':0}
    return app


if os.environ.get('SAPBA_ADMISSION_FIXTURE_ROOT'):
    location=Path(os.environ['SAPBA_ADMISSION_FIXTURE_ROOT']).resolve()
    assert location.is_relative_to(Path(__file__).resolve().parents[1]/'.local-data/runtime-admission-20261030')
    app=fixture_app(location,os.environ.get('SAPBA_ADMISSION_FIXTURE_PROVIDER','codex'))
