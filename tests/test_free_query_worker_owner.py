"""Real scheduler + planner + capacity gate, with native dispatch fully mocked."""
import asyncio,json
import time
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from sap_business_agents_platform.workbuddy_environment import WorkBuddyEnvironment
from sap_business_agents_platform.workbuddy_supervisor import WorkBuddySupervisor
from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner
from sap_business_agents_platform.models import RunStatus
from tests.test_free_query_recovery import seed,payload


@pytest.mark.parametrize('foreign_owner',[False,True])
def test_recovery_scheduler_and_worker_keep_the_same_explicit_owner(tmp_path,foreign_owner):
    owner=seed(tmp_path,'workbuddy');calls=[]
    supervisor=WorkBuddySupervisor(WorkBuddyEnvironment(tmp_path))
    report=owner.store.get_run('saved').result.presentation.model_dump(mode='json')
    async def native_boundary(**kwargs):
        # Preserve the actual ownership/concurrency check; replace only process I/O.
        async with supervisor._capacity(False,kwargs['task_id'],kwargs['operation']):
            calls.append(kwargs)
            return {'text':json.dumps({'summary':{'zh':'合成结果','en':'Synthetic'},'presentation':report}),
                'session_id':'fake-owned-native','output_format':'native_json_schema','actual_model':'synthetic'}
    supervisor.run=native_boundary
    runtime=owner.store.get_free_query_session('session')['runtime']
    planner=WorkBuddyPlanner(tmp_path,'synthetic',supervisor=supervisor,runtime_snapshot=runtime)
    owner.workbuddy_harness=SimpleNamespace(manager=SimpleNamespace(supervisor=supervisor))
    owner.planner=SimpleNamespace(revise_free_query_presentation=planner.revise_free_query_presentation,
        pin_snapshot=lambda binding:nullcontext())
    async def execute(run_id):
        await owner._execute_free_query_reinterpret(run_id,owner.store.get_free_query_iteration_by_run(run_id))
    owner._execute=execute
    async def scenario():
        response=await owner.submit_free_query_feedback('session',payload(owner))
        token=planner._feedback_operation.set('foreign-feedback' if foreign_owner else None)
        try:await owner._execute_scheduled_run(response['run_id'])
        finally:planner._feedback_operation.reset(token)
        return owner.store.get_run(response['run_id'])
    run=asyncio.run(scenario())
    if foreign_owner:
        assert run.status==RunStatus.failed and not calls
        assert run.error['detail']['cause_code']=='workbuddy_reservation_owner_mismatch'
        return
    assert run.status==RunStatus.inconclusive,run.error
    assert len(calls)==1 and calls[0]['task_id']==run.run_id
    assert calls[0]['operation']=='revise_free_query_presentation'
    assert not calls[0]['payload'].get('tools') and calls[0]['tool_handler'] is None
    assert owner.store.get_run('stopped').status==RunStatus.cancelled


def test_a_different_execution_owner_cannot_reuse_the_reserved_slot(tmp_path):
    from sap_business_agents_platform.runtime_contract import execution_owner,current_execution_owner
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    supervisor=WorkBuddySupervisor(WorkBuddyEnvironment(tmp_path))
    async def scenario():
        async with supervisor.reserve(task_id='actual-query',parallel=True):
            with execution_owner('another-query'):
                with pytest.raises(WorkBuddyError,match='workbuddy_reservation_owner_mismatch'):
                    async with supervisor._capacity(False,current_execution_owner(),'revise_free_query_presentation'):
                        pytest.fail('foreign owner reached native dispatch')
            assert current_execution_owner() is None
    asyncio.run(scenario())


def test_copied_execution_context_expires_with_its_parent():
    from sap_business_agents_platform.runtime_contract import execution_owner,current_execution_owner,RuntimeContractError
    async def scenario():
        release=asyncio.Event()
        async def child():
            await release.wait()
            return current_execution_owner()
        with execution_owner('original-query'):
            task=asyncio.create_task(child())
        release.set()
        with pytest.raises(RuntimeContractError,match='runtime_execution_owner_expired'):
            await task
        assert current_execution_owner() is None
    asyncio.run(scenario())


def test_parallel_execution_owners_do_not_cross_or_change_capacity(tmp_path):
    from sap_business_agents_platform.runtime_contract import execution_owner,current_execution_owner
    supervisor=WorkBuddySupervisor(WorkBuddyEnvironment(tmp_path))
    async def one(name):
        async with supervisor.reserve(task_id=name,parallel=True):
            with execution_owner(name):
                await asyncio.sleep(0)
                async with supervisor._capacity(False,current_execution_owner(),'revise_free_query_presentation'):
                    await asyncio.sleep(0)
                    return current_execution_owner()
    async def scenario():
        assert await asyncio.gather(*(one('query-'+str(i)) for i in range(10)))==['query-'+str(i) for i in range(10)]
    asyncio.run(scenario())


def test_explicit_feedback_owner_cannot_override_the_query_owner(tmp_path):
    from sap_business_agents_platform.runtime_contract import execution_owner,deadline_scope
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    from unittest.mock import AsyncMock
    supervisor=SimpleNamespace(run=AsyncMock())
    planner=WorkBuddyPlanner(tmp_path,'synthetic',supervisor=supervisor,
        runtime_snapshot={'provider_id':'workbuddy','model':'synthetic'})
    async def scenario():
        token=planner._feedback_operation.set('unrelated-feedback')
        try:
            with deadline_scope(time.monotonic()+5),execution_owner('query-owner'):
                with pytest.raises(WorkBuddyError,match='workbuddy_reservation_owner_mismatch'):
                    await planner._query('synthetic',thread_id=None,system_prompt=None)
        finally:planner._feedback_operation.reset(token)
    asyncio.run(scenario())
    supervisor.run.assert_not_awaited()


def test_codex_scheduler_does_not_add_workbuddy_execution_ownership(tmp_path):
    from sap_business_agents_platform.runtime_contract import current_execution_owner
    owner=seed(tmp_path,'codex');seen=[]
    async def execute(run_id):
        seen.append(current_execution_owner())
        owner.store.update_run(run_id,status=RunStatus.inconclusive)
    owner._execute=execute
    asyncio.run(owner._execute_scheduled_run('saved'))
    assert seen==[None]
