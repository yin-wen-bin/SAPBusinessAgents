"""No SDK/model/SAP: bounded initialize readiness and exact cache attestation."""
import asyncio,copy,importlib.util,json
from pathlib import Path
import pytest
from tests.test_workbuddy_auth_hardening import auth_service
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('settling_login',ROOT/'scripts/login-workbuddy-runtime.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
ACCOUNT={'userId':'fixture-user','token':'do-not-export'}
STAMP={'context_digest':'same-context','source_digest':'same-source','cache_digest':'first'}
class Transport:
    def __init__(self,values):self.values=iter(values);self.queue=asyncio.Queue();self.writes=[];self.closed=False
    async def connect(self):pass
    async def close(self):self.closed=True
    async def write(self,line):
        message=json.loads(line);self.writes.append(message)
        self.queue.put_nowait({'type':'control_response','response':{'subtype':'success',
            'request_id':message['request_id'],'response':next(self.values)}})
    async def read_messages(self):
        while True:yield await self.queue.get()
async def immediate(_delay):pass
def run(transport,monkeypatch,observation=lambda:dict(STAMP)):
    monkeypatch.setattr(helper.asyncio,'sleep',immediate)
    monkeypatch.setattr(helper.webbrowser,'open',lambda *_:pytest.fail('OAuth must not open'))
    return asyncio.run(helper.run_authentication(lambda:transport,check_only=True,method_id='external',
        no_browser=True,environment='internal',emit=lambda *_:None,settle_account=True,observation=observation))
def test_delayed_complete_account_uses_same_cli_and_never_oauth(monkeypatch):
    transport=Transport([{'account':None},{'account':ACCOUNT}])
    result=run(transport,monkeypatch)
    assert result['authenticated'] and result['authentication_observation']==STAMP
    assert len(transport.writes)==2 and transport.closed
    assert all(m['type']=='control_request' and m['request']['subtype']=='initialize'
        and m['request']['hasPrompt'] is False and m['request']['environment']=='internal' for m in transport.writes)
    assert 'do-not-export' not in json.dumps(result)
def test_persistent_absence_is_unconfirmed_not_success(monkeypatch):
    transport=Transport([{}]*4)
    with pytest.raises(helper.LoginError,match='workbuddy_login_required') as failure:run(transport,monkeypatch)
    assert failure.value.authentication_evidence=='initialize_account_absent'
    assert len(transport.writes)==4 and transport.closed
    assert all(m['request']['subtype']=='initialize' for m in transport.writes)
def test_account_read_during_cache_mutation_requires_another_stable_read(monkeypatch):
    observations=iter([STAMP,{**STAMP,'cache_digest':'renewed'},{**STAMP,'cache_digest':'renewed'},{**STAMP,'cache_digest':'renewed'}])
    transport=Transport([{'account':ACCOUNT}]*2)
    result=run(transport,monkeypatch,lambda:next(observations))
    assert result['authentication_observation']['cache_digest']=='renewed' and len(transport.writes)==2
def test_profile_context_change_immediately_blocks(monkeypatch):
    observations=iter([STAMP,{**STAMP,'context_digest':'another-profile'}])
    transport=Transport([{'account':ACCOUNT}])
    with pytest.raises(helper.LoginError,match='runtime_authentication_context_changed'):
        run(transport,monkeypatch,lambda:next(observations))
    assert len(transport.writes)==1 and transport.closed
def test_no_stable_cache_never_confirms_account(monkeypatch):
    count=iter(range(8));transport=Transport([{'account':ACCOUNT}]*4)
    with pytest.raises(helper.LoginError,match='runtime_authentication_context_changed'):
        run(transport,monkeypatch,lambda:{**STAMP,'cache_digest':str(next(count))})
    assert transport.closed
def test_owned_stable_proof_can_confirm_same_account_renewal(tmp_path):
    service,adapter,_=auth_service(tmp_path)
    asyncio.run(service.check('workbuddy'));original=copy.deepcopy(service._account('workbuddy'))
    check=adapter.check
    async def renewing(**kwargs):
        result=await check(**kwargs);adapter.stamp['cache_digest']='renewed'
        return {**result,'authentication_observation':dict(adapter.stamp)}
    adapter.check=renewing
    view=asyncio.run(service.check('workbuddy'))
    assert view['authenticated'] is True and service._account('workbuddy')['revision']==original['revision']
    assert service._account('workbuddy')['confirmed_binding']==original['confirmed_binding']
def test_stale_proof_cannot_bless_later_cache_update(tmp_path):
    service,adapter,_=auth_service(tmp_path)
    asyncio.run(service.check('workbuddy'));old=copy.deepcopy(service._account('workbuddy'))
    check=adapter.check
    async def racing(**kwargs):
        result=await check(**kwargs);proof=dict(adapter.stamp);adapter.stamp['cache_digest']='concurrent-change'
        return {**result,'authentication_observation':proof}
    adapter.check=racing
    view=asyncio.run(service.check('workbuddy'))
    assert view['authenticated'] is None and view['failure_code']=='runtime_authentication_context_changed'
    assert service._account('workbuddy')['confirmed_binding']==old['confirmed_binding']
def test_absent_account_reason_reaches_gate_without_generic_substitution(tmp_path):
    service,adapter,_=auth_service(tmp_path)
    async def absent(**kwargs):return {'authenticated':False,'authentication_evidence':'initialize_account_absent'}
    adapter.check=absent
    asyncio.run(service.check('workbuddy'))
    with pytest.raises(Exception) as blocked:service.assert_binding('workbuddy',service.bind('workbuddy',{}))
    assert blocked.value.code=='workbuddy_authentication_confirmation_required'
