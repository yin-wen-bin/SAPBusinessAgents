"""Pinned worker protocol over synthetic SDK messages, never a real model."""
import asyncio
import copy
import json
import sys
from types import SimpleNamespace
import pytest
from sap_business_agents_platform.runtime_harness_contract import terminal_schema
from sap_business_agents_platform.runtime_strict_schema import NativeContract


def test_cached_schema_is_never_shared_with_mutating_callers():
    from sap_business_agents_platform.runtime_report_contract import presentation_schema
    broken=presentation_schema(strict=True,version=2)
    original=copy.deepcopy(broken);broken['properties']['blocks']['items']['properties'].clear()
    assert presentation_schema(strict=True,version=2)==original


def test_original_report_v2_contract_remains_readable_but_unknown_checker_is_rejected():
    from sap_business_agents_platform.runtime_report_contract import freeze,verify,digest,ReportContractError
    from sap_business_agents_platform.runtime_legacy_protocol import REPORT_V2_BINDING
    contract=freeze('run_original','request',{})
    contract['checker_binding']=copy.deepcopy(REPORT_V2_BINDING)
    contract['digest']=digest({k:v for k,v in contract.items() if k!='digest'})
    assert verify(contract)==contract
    contract['checker_binding']['models.py']='0'*64
    contract['digest']=digest({k:v for k,v in contract.items() if k!='digest'})
    with pytest.raises(ReportContractError):verify(contract)


@pytest.mark.parametrize('terminal',[True,False])
def test_v2_bindings_cross_actual_worker_and_native_terminal_gate(tmp_path,monkeypatch,terminal):
    from tests.test_runtime_strict_schema import prepared_report,QUERY
    from tests.test_workbuddy_harness_nullable import envelope
    from sap_business_agents_platform import workbuddy_worker
    from sap_business_agents_platform.runtime_terminal_contract import TerminalChecks
    from sap_business_agents_platform.workbuddy_site import make_configuration,routing
    async def scenario():
        _,_,_,_,accepted=await prepared_report(tmp_path,'workbuddy')
        canonical=envelope()
        for key in ('input_kind','input_field','clarification_question'):canonical.pop(key)
        canonical['presentation']=accepted['validated_report']
        codec=NativeContract(terminal_schema(version=2));wire=codec.encode(canonical);observed=[]
        class ToolUse:
            id,name,input='fixture-call','StructuredOutput',wire
        class Assistant:
            model,content='fixture-model',[ToolUse()]
        class Result:
            is_error,session_id,result=False,'fixture-session',json.dumps(wire)
            structured_output=wire if terminal else None
        class Client:
            def __init__(self,*,options):observed.append(options)
            async def __aenter__(self):return self
            async def __aexit__(self,*_):pass
            async def query(self,prompt):assert prompt==QUERY
            async def receive_messages(self):yield Assistant();yield Result()
        monkeypatch.setitem(sys.modules,'codebuddy_agent_sdk',SimpleNamespace(
            CodeBuddyAgentOptions=lambda **kwargs:SimpleNamespace(**kwargs),CodeBuddySDKClient=Client,
            AssistantMessage=Assistant,ResultMessage=Result,TextBlock=type('Text',(),{}),ToolUseBlock=ToolUse,
            ToolResultBlock=type('ToolResult',(),{}),PermissionResultAllow=SimpleNamespace,
            PermissionResultDeny=SimpleNamespace,HookMatcher=SimpleNamespace))
        site=make_configuration('china',1)
        request={'operation':'free_query','mode':'bounded','cli_path':'fixture-locked-cli',
            'snapshot':{'model':'fixture-model',**site},'service_site_config':routing(site),
            'payload':{'cwd':str(tmp_path),'prompt':QUERY,'output_schema':codec.schema,'system_prompt':codec.instructions()}}
        if not terminal:
            with pytest.raises(ValueError,match='workbuddy_structured_output_missing'):
                await workbuddy_worker.execute(request,lambda *_a,**_k:None)
        else:
            result=await workbuddy_worker.execute(request,lambda *_a,**_k:None)
            checks=TerminalChecks(codec.schema)
            import time
            checks.report_ready(time.monotonic()+90,validated_report=accepted['native_validated_report'])
            checked=checks.check(json.loads(result['text']),submission_id='native',origin='native_terminal',phase='native_terminal')
            assert checked['repair_feedback']['terminal_validated']
            assert codec.read(result['text'])==canonical
        assert json.loads(observed[0].extra_args['json-schema'])==codec.schema
        assert observed[0].tools==observed[0].allowed_tools==['StructuredOutput']
        assert observed[0].setting_sources==[] and observed[0].persist_session is False
    asyncio.run(scenario())
