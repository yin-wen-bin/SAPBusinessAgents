from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from sap_business_agents_platform.models import AgentActivateRequest, AgentLifecycleRequest
from sap_business_agents_platform.site_release import SiteReleaseError, SiteReleaseManager
from sap_business_agents_platform.workflows import agent_digest
import importlib.util
_spec=importlib.util.spec_from_file_location("lifecycle_regression_fixtures",Path(__file__).with_name("test_agent_lifecycle.py"))
_fixtures=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_fixtures)
_service,_write_active_agent=_fixtures._service,_fixtures._write_active_agent


def test_switch_passes_the_configured_loopback_port(tmp_path, monkeypatch):
    script=tmp_path/'scripts'/'Switch-SAPBusinessAgentsSite.ps1'
    script.parent.mkdir();script.write_text('# test switch',encoding='utf-8')
    manager=SiteReleaseManager(tmp_path,tmp_path/'.local-data',site_origins=('http://127.0.0.1:64686',))
    captured=[]
    def run(command,**kwargs):
        captured.extend(command)
        Path(command[command.index('-ResultPath')+1]).write_text(json.dumps({'status':'completed'}),encoding='utf-8')
        return subprocess.CompletedProcess(command,0)
    monkeypatch.setattr(subprocess,'run',run)
    assert manager.switch({'dist_path':'test','fingerprint':'f'},operation_id='op',agent_id='a',version='1.0.0',catalog_module='MM')['status']=='completed'
    assert captured[captured.index('-SitePort')+1]=='64686'


@pytest.mark.parametrize('origins',[
    ('https://example.com',),
    ('http://127.0.0.1:4321','http://localhost:5432'),
    ('http://user:secret@127.0.0.1:4321',),
    ('http://127.0.0.1:4321/unexpected',),
])
def test_switch_rejects_external_or_ambiguous_targets(tmp_path,origins):
    manager=SiteReleaseManager(tmp_path,tmp_path/'.local-data',site_origins=origins)
    with pytest.raises(SiteReleaseError):manager._site_port()


def test_two_loopback_names_for_same_port_are_one_target(tmp_path):
    manager=SiteReleaseManager(tmp_path,tmp_path/'.local-data',site_origins=('http://127.0.0.1:4321','http://localhost:4321'))
    assert manager._site_port()==4321


def test_deactivate_then_reactivate_preserves_main_and_history(tmp_path,monkeypatch):
    service,store,settings=_service(tmp_path)
    manifest=_write_active_agent(service,tmp_path)
    directory=tmp_path/'agents'/'Common'/manifest['slug']
    package=service._capture_package(directory)
    history=directory/'versions'/'1.0.0';service._write_package(history,package)
    before={p.relative_to(history).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in history.rglob('*') if p.is_file()}
    (tmp_path/'.gitignore').write_text('.local-data/\n.prototype/\n',encoding='utf-8')
    def git(*args):return subprocess.check_output(['git',*args],cwd=tmp_path,text=True).strip()
    git('init','-b','main');git('config','user.email','test@example.com');git('config','user.name','Test')
    git('add','.');git('commit','-m','Initial')
    monkeypatch.setattr(service._site_release,'prepare',lambda *a,**k:{'fingerprint':'test','dist_path':'test'})
    monkeypatch.setattr(service._site_release,'switch',lambda *a,**k:{'status':'completed'})
    expected={'expectedVersion':'1.0.0','expectedAgentHash':agent_digest(manifest)}
    result=service.deactivate(manifest['slug'],AgentLifecycleRequest(**expected,reason='regression'))
    assert result['state']=='inactive' and result['pushed'] is False
    assert git('branch','--show-current')=='main'
    assert not git('status','--porcelain')
    assert service.agents.lifecycle(manifest['slug'])['state']=='inactive'
    result=service.activate(manifest['slug'],AgentActivateRequest(**expected,version='1.0.0'))
    assert result['state']=='active' and result['site_refresh_status']=='completed'
    assert git('branch','--show-current')=='main'
    assert before=={p.relative_to(history).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in history.rglob('*') if p.is_file()}
