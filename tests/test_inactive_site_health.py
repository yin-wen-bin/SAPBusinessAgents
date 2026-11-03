import importlib.util
import json
import os
import subprocess
import urllib.error
from pathlib import Path

import pytest

from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError
from sap_business_agents_platform.models import AgentLifecycleRequest
from sap_business_agents_platform.site_release import SiteReleaseError, SiteReleaseManager
from sap_business_agents_platform.workflows import agent_digest


@pytest.mark.parametrize('state,public,marker_state,passes', [
    ('inactive', False, 'inactive', True),
    ('inactive', True, 'inactive', False),
    ('active', True, 'active', True),
    ('active', False, 'active', False),
    ('inactive', False, 'active', False),
])
def test_health_checks_expected_lifecycle_instead_of_skipping_details(tmp_path, monkeypatch, state, public, marker_state, passes):
    import sap_business_agents_platform.site_release as module
    site=tmp_path/'site';site.mkdir();logs=tmp_path/'logs';logs.mkdir()
    manager=SiteReleaseManager(tmp_path,tmp_path/'.local-data')
    requested=[]
    class Process:
        def poll(self):return None
        def terminate(self):pass
        def wait(self,timeout):return 0
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**k:Process())
    clock=iter([0,0,16]);monkeypatch.setattr(module.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    def fetch(url):
        requested.append(url)
        if url.endswith('/sapba-build.json'):
            return json.dumps({'fingerprint':'expected','lifecycle_state':marker_state})
        if '/agents/' in url:
            if not public:raise urllib.error.HTTPError(url,404,'Not found',{},None)
            return 'fixture-agent 1.0.0'
        return 'SAP Business Agents'
    monkeypatch.setattr(manager,'_fetch',fetch)
    kwargs=dict(node='node',astro=Path('astro'),fingerprint='expected',agent_id='fixture-agent',version='1.0.0',catalog_module='MM',expected_state=state)
    if passes:
        manager._verify_candidate(site,tmp_path/'dist',logs,**kwargs)
        assert sum('/agents/' in url for url in requested)==2
    else:
        with pytest.raises(SiteReleaseError) as error:manager._verify_candidate(site,tmp_path/'dist',logs,**kwargs)
        assert error.value.code=='site_candidate_health_failed'


def active_service(tmp_path):
    spec=importlib.util.spec_from_file_location('original_lifecycle_fixtures',Path(os.environ.get('SAPBA_PROPOSAL_BASE', str(Path(__file__).resolve().parents[1])))/'tests/test_agent_lifecycle.py')
    fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
    service,store,settings=fixtures._service(tmp_path);manifest=fixtures._write_active_agent(service,tmp_path)
    (tmp_path/'.gitignore').write_text('.local-data/\n.prototype/\n',encoding='utf-8')
    def git(*args):return subprocess.check_output(['git',*args],cwd=tmp_path,text=True).strip()
    git('init','-b','main');git('config','user.email','test@example.com');git('config','user.name','Test');git('add','.');git('commit','-m','Initial')
    return service,manifest,git


def test_deactivation_passes_inactive_state_to_both_health_gates(tmp_path,monkeypatch):
    service,manifest,git=active_service(tmp_path);states=[]
    def prepare(*args,**kwargs):
        states.append(kwargs['expected_state']);return {'fingerprint':'inactive','dist_path':'test'}
    def switch(*args,**kwargs):
        states.append(kwargs['expected_state']);return {'status':'completed'}
    monkeypatch.setattr(service._site_release,'prepare',prepare);monkeypatch.setattr(service._site_release,'switch',switch)
    result=service.deactivate(manifest['slug'],AgentLifecycleRequest(expectedVersion='1.0.0',expectedAgentHash=agent_digest(manifest)))
    assert states==['inactive','inactive'] and result['state']=='inactive'
    assert git('branch','--show-current')=='main'


def test_health_failure_has_typed_error_and_preserves_active_main(tmp_path,monkeypatch):
    service,manifest,git=active_service(tmp_path);before=git('rev-parse','HEAD')
    def fail(*args,**kwargs):raise SiteReleaseError('Unavailable',code='site_candidate_health_failed',phase='checking_site')
    monkeypatch.setattr(service._site_release,'prepare',fail)
    with pytest.raises(AgentLifecycleError) as error:
        service.deactivate(manifest['slug'],AgentLifecycleRequest(expectedVersion='1.0.0',expectedAgentHash=agent_digest(manifest)))
    assert error.value.code=='site_candidate_health_failed'
    assert git('rev-parse','HEAD')==before and git('branch','--show-current')=='main'
    assert service.agents.lifecycle(manifest['slug'])['state']=='active'
