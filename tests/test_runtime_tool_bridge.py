"""Synthetic Broker transport checks; no model, credentials or SAP."""
import asyncio
import io
import json
import threading
from pathlib import Path
import pytest
from sap_business_agents_platform.runtime_tool_bridge import create, invoke
from sap_business_agents_platform.runtime_harness import _source_io
from tests.test_runtime_report_contract import make_broker


def test_bridge_uses_frozen_contract_and_hides_run_capability_from_model(tmp_path):
    broker,store,run,token=make_broker(tmp_path)
    store.update_harness_state(run,{'terminal_protocol':2})
    path,instructions=create(broker.settings,broker,run,token)
    binding=json.loads(path.read_text(encoding='utf8'))
    assert binding['capability']==token
    assert token not in instructions
    assert 'request_query_clarification' in {t['name'] for t in binding['tools']}
    assert any(t['name']=='sap_final_report_validate' for t in binding['tools'])
    assert 'sap_password' not in binding and 'sap_username' not in binding


def test_bridge_delivers_exact_arguments_and_owned_headers():
    class Response(io.BytesIO):
        pass
    class Opener:
        def open(self,request,timeout):
            assert request.full_url=='http://127.0.0.1:8765/api/internal/harness/tools/request_query_clarification'
            assert json.loads(request.data)=={'arguments':{'question':'Which purchase order?'}}
            assert request.get_header('X-sapba-run')=='owned-run'
            assert request.get_header('X-sapba-capability')=='owned-capability'
            return Response(b'{"ok":true,"control":"waiting_input"}')
    binding={'api_url':'http://127.0.0.1:8765','run_id':'owned-run','capability':'owned-capability',
             'tools':[{'name':'request_query_clarification'}]}
    assert invoke(binding,'request_query_clarification',{'question':'Which purchase order?'},Opener())['control']=='waiting_input'
    with pytest.raises(ValueError,match='bridge_tool_not_registered'):
        invoke(binding,'post_sap_document',{},Opener())
    with pytest.raises(ValueError,match='bridge_target_not_loopback'):
        invoke({**binding,'api_url':'https://foreign.example'},'request_query_clarification',{},Opener())


def test_source_capture_keeps_other_tasks_responsive_and_drains_on_cancel():
    entered=threading.Event();release=threading.Event();finished=threading.Event()
    def capture():
        entered.set();release.wait(5);finished.set()
    async def scenario():
        work=asyncio.create_task(_source_io(capture))
        while not entered.is_set():await asyncio.sleep(.001)
        # This coroutine can run while the real source operation is blocked.
        assert not work.done()
        work.cancel();await asyncio.sleep(.01)
        assert not work.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):await work
        assert finished.is_set()
    asyncio.run(scenario())
