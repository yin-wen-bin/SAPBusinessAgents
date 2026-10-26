"""Native Codex seam with a synthetic stream, no SDK process or SAP."""
import asyncio
import time
from types import SimpleNamespace
import pytest

from tests.test_runtime_report_contract import make_broker
from sap_business_agents_platform.codex_driver import CodexHarnessDriver
from sap_business_agents_platform.runtime_contract import RuntimeRequest
from sap_business_agents_platform.runtime_harness_contract import terminal_schema
from sap_business_agents_platform.runtime_input_control import InputPause, InputControlError, outcome


@pytest.mark.parametrize('cleanup_fails',[False,True])
def test_codex_pause_is_control_event_and_waiting_requires_owned_cleanup(tmp_path,monkeypatch,cleanup_fails):
    broker,store,run_id,token=make_broker(tmp_path)
    state=store.update_harness_state(run_id,{'terminal_protocol':2})
    async def monitor(*_):
        await asyncio.Event().wait()
    class Turn:
        id='synthetic-turn'
        interrupted=False
        async def interrupt(self): self.interrupted=True
        async def stream(self):
            result=await broker.handle(run_id,token,'request_query_clarification',{'question':'Company?'})
            assert result['ok']
            yield SimpleNamespace(method='synthetic/control')
    turn=Turn()
    class Thread:
        id='synthetic-thread'
        async def turn(self,*_,**kw): return turn
    class Client:
        closed=False
        async def __aenter__(self): return self
        async def __aexit__(self,*_):
            self.closed=True
            if cleanup_fails: raise RuntimeError('synthetic cleanup failure')
        async def thread_start(self,**kw): return Thread()
    client=Client()
    import sap_business_agents_platform.harness as harness
    monkeypatch.setattr(harness,'_safe_codex',lambda *_,**kw:client)
    owner=SimpleNamespace(settings=broker.settings,store=store,broker=broker,_active_turns={},_monitor_deadline=monitor)
    context={'run_id':run_id,'capability':token,'workspace':tmp_path,'state':state,
        'cleanup_state':{'complete':False},'thread_id':None,'turn_count':1}
    req=RuntimeRequest('codex','free_query','harness',{'model':'offline','reasoning_effort':'low'},'fixture',
        terminal_schema(version=2),permissions={'full_access':False},deadline=time.monotonic()+30)
    with pytest.raises(InputPause): asyncio.run(CodexHarnessDriver(owner).harness_turn(req,context))
    assert turn.interrupted and client.closed
    assert context['cleanup_state']['complete'] is (not cleanup_fails)
    if cleanup_fails:
        with pytest.raises(InputControlError,match='runtime_cleanup_incomplete'):
            outcome(owner,run_id,context,1,time.monotonic())
    else:
        assert outcome(owner,run_id,context,1,time.monotonic()).status=='waiting_input'


def test_mcp_child_is_bound_to_the_same_source_as_parent(tmp_path):
    from pathlib import Path
    from sap_business_agents_platform.harness import _mcp_overrides
    import sap_business_agents_platform.harness as harness
    broker,store,run_id,token=make_broker(tmp_path)
    overrides=_mcp_overrides(broker.settings,run_id,token,'python')
    expected=str(Path(harness.__file__).resolve().parents[1])
    import json
    for server in ('sap_business_agents','sap_tool_discovery'):
        value=next(line.split('=',1)[1] for line in overrides if line.startswith('mcp_servers.'+server+'.env.PYTHONPATH='))
        assert json.loads(value)==expected
