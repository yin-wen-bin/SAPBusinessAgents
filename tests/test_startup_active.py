"""Offline lifecycle checks: no SDK, authentication, model or SAP invocation."""
import copy
import importlib.util
import json
from pathlib import Path
import py_compile
from types import SimpleNamespace

import pytest

from tests.test_startup_versioning import launcher


@pytest.fixture
def active(tmp_path,monkeypatch):
    control=launcher.control
    binding={'version':1,'path':str(tmp_path/'.venv'),'python':str(tmp_path/'.venv/Scripts/python.exe')}
    binding['digest']=control.digest(binding)
    manifest={'version':1,'files':{},'dependencies':{'environment':binding}}
    manifest['digest']=control.digest(manifest)
    archive=tmp_path/'.local-data/startup-releases'/manifest['digest'];(archive/'source').mkdir(parents=True)
    control.atomic(archive/'manifest.json',manifest)
    api={'archive':str(archive),'source_digest':manifest['digest'],'environment':binding,'pid':100,'api_port':8765,'site_port':4321}
    site={'fingerprint':'f'*64,'dist_path':str(tmp_path/'.local-data/site-builds'/('f'*64)/'dist'),'mode':'preview','pid':200}
    control.atomic(tmp_path/'.local-data/startup-api-current.json',api)
    control.atomic(tmp_path/'.local-data/site-builds/current.json',site)
    health={'ok':True,'loopback_only':True,'sap_read':{'selected_provider':'embedded','data':{'read_only':True}},
            'startup_binding':{'verified':True,'source_digest':manifest['digest'],'environment_digest':binding['digest'],'pid':100,'boot_id':'test-boot'}}
    ports={8765:100,4321:200};calls=[]
    monkeypatch.setattr(launcher,'listeners',lambda:ports)
    monkeypatch.setattr(launcher,'service_identities',lambda pids,root:{pid:{'ProcessId':pid,'CreationDate':str(pid),'CommandLine':str(root)} for pid in pids})
    def request(url,**kw):calls.append(url);return {'fingerprint':site['fingerprint']} if 'sapba-build' in url else health
    monkeypatch.setattr(launcher,'request',request)
    args=SimpleNamespace(root=tmp_path,port=8765,site_port=4321,timeout=90,restart=False,rebuild=False,no_browser=True,dev=False,reuse_only=False,site_only=False)
    return args,api,site,health,ports,calls


def test_hot_open_has_no_checkout_or_authorization_audit(active,monkeypatch):
    args,api,site,_,_,calls=active
    for name in ('source_manifest','freeze','check_admission','jobs_pending'):
        monkeypatch.setattr(launcher,name,lambda *a,**k:pytest.fail('Hot open must not prepare/audit'))
    monkeypatch.setattr(launcher.environments,'verify',lambda *a,**k:pytest.fail('No full env read'))
    monkeypatch.setattr(launcher.control,'activity',lambda *a:pytest.fail('No history read'))
    (args.root/'src').mkdir();(args.root/'src/broken.py').write_text('not valid Python !')
    result=launcher.ensure(args)
    assert result['api_reused'] and result['workspace_checked'] is False
    assert result['current_changes_loaded'] is None and result['source_digest']==api['source_digest']
    assert len(calls)==2 and all('health' in url or 'sapba-build' in url for url in calls)
    assert json.loads((args.root/'.local-data/startup-api-current.json').read_bytes())==api


@pytest.mark.parametrize('field',['environment_digest','boot_id','source_digest','pid'])
def test_hot_open_cannot_misidentify_running_version(active,field):
    args,_,_,health,_,_=active;health['startup_binding'].pop(field)
    with pytest.raises(launcher.control.StartupBlocked,match='running_binding_unconfirmed'):
        launcher.ensure(args)


def test_hot_open_checks_saved_process_creation(active):
    args,api,*_=active
    api['listener_identity']={'ProcessId':100,'CreationDate':'old-birth','CommandLine':str(args.root)}
    launcher.control.atomic(args.root/'.local-data/startup-api-current.json',api)
    with pytest.raises(launcher.control.StartupBlocked,match='process_identity_changed'):launcher.ensure(args)


@pytest.mark.parametrize('missing',[8765,4321])
def test_cold_component_uses_only_bound_pair(active,monkeypatch,missing):
    args,api,site,_,ports,_=active;ports.pop(missing)
    monkeypatch.setattr(launcher,'_ensure',lambda args:{'api':args.frozen_record,'site':args.frozen_site,'active':args.active_start})
    assert launcher.ensure(args)=={'api':api,'site':site,'active':True}


@pytest.mark.parametrize('operation',['restart','site_only'])
def test_explicit_restart_never_selects_checkout(active,monkeypatch,operation):
    args,api,*_=active;setattr(args,operation,True)
    monkeypatch.setattr(launcher,'_ensure',lambda args:args.frozen_record)
    assert launcher.ensure(args)==api


def test_pending_switch_and_missing_pointer_fail_closed(active):
    args,*_=active
    path=args.root/'.local-data/startup/pending-switch.json';launcher.control.atomic(path,{'phase':'starting'})
    with pytest.raises(launcher.control.StartupBlocked,match='recovery_required'):launcher.ensure(args)
    path.unlink();(args.root/'.local-data/startup-api-current.json').unlink()
    with pytest.raises(launcher.control.StartupBlocked,match='binding_unavailable'):launcher.ensure(args)


def test_reuse_only_does_not_start_missing_service(active):
    args,_,_,_,ports,_=active;args.reuse_only=True;ports.clear()
    with pytest.raises(launcher.control.StartupBlocked,match='reuse_not_proven'):launcher.ensure(args)


def test_explicit_code_activation_requires_test_and_base_binding(active,monkeypatch):
    args,api,site,*_=active
    args.target_record=args.root/'handoff.json'
    value={'base':api,'target':api,'site':site,'tested_source_digest':api['source_digest']}
    launcher.control.atomic(args.target_record,value)
    monkeypatch.setattr(launcher,'ensure',lambda args:args.frozen_record)
    assert launcher.activate_code(args)==api
    value['tested_source_digest']='wrong';launcher.control.atomic(args.target_record,value)
    with pytest.raises(launcher.control.StartupBlocked,match='test_binding_missing'):launcher.activate_code(args)
    value['base']={**api,'pid':123};launcher.control.atomic(args.target_record,value)
    with pytest.raises(launcher.control.StartupBlocked,match='base_changed'):launcher.activate_code(args)


def make_cache(tmp_path):
    root=tmp_path/'release';source=root/'source/src';source.mkdir(parents=True)
    original=source/'sample.py';original.write_text('answer=42\n')
    manifest={'version':1,'files':{'src/sample.py':launcher.control.sha(original)},'dependencies':{}}
    manifest['digest']=launcher.control.digest(manifest);launcher.control.atomic(root/'manifest.json',manifest)
    cache=Path(py_compile.compile(str(original),doraise=True))
    return root,manifest,original,cache


def test_bytecode_is_not_silently_ignored(tmp_path):
    archive,manifest,original,cache=make_cache(tmp_path)
    with pytest.raises(launcher.control.StartupBlocked,match='archive_changed'):launcher.verify_archive(archive,manifest)
    _,items=launcher.bytecode_inventory(archive)
    assert len(items)==1 and items[0]['sha256']==launcher.control.sha(cache)
    assert launcher.control.sha(original)==manifest['files']['src/sample.py']


@pytest.mark.parametrize('failure',['source','missing','unknown','bad_bytecode','orphan'])
def test_recovery_inventory_rejects_noncache_changes(tmp_path,failure):
    archive,_,source,cache=make_cache(tmp_path)
    if failure=='source':source.write_text('tampered=1')
    if failure=='missing':source.unlink()
    if failure=='unknown':(source.parent/'unknown.json').write_text('{}')
    if failure=='bad_bytecode':cache.write_bytes(b'bad')
    if failure=='orphan':cache.rename(cache.with_name('unknown.cpython-314.pyc'))
    with pytest.raises(launcher.control.StartupBlocked):launcher.bytecode_inventory(archive)


def test_recovery_quarantines_caches_without_changing_approved_bytes(active,monkeypatch):
    args,api,*_=active;archive=Path(api['archive']);source=archive/'source/src/sample.py'
    source.parent.mkdir(parents=True);source.write_text('answer=42\n')
    manifest=json.loads((archive/'manifest.json').read_bytes());manifest['files']['src/sample.py']=launcher.control.sha(source)
    manifest.pop('digest');manifest['digest']=launcher.control.digest(manifest)
    # Inventory identity remains bound to the current record in the real path.
    launcher.control.atomic(archive/'manifest.json',manifest)
    monkeypatch.setattr(launcher,'active_records',lambda root:(api,{},manifest))
    cache=Path(py_compile.compile(str(source),doraise=True));raw=cache.read_bytes();before=(archive/'manifest.json').read_bytes()
    args.archive=archive
    monkeypatch.setattr(launcher,'settings_for',lambda root:SimpleNamespace(data_root=root/'.local-data'))
    monkeypatch.setattr(launcher.control,'activity',lambda *a:{})
    monkeypatch.setattr(launcher,'jobs_pending',lambda *a:False)
    monkeypatch.setattr(launcher,'assert_writers',lambda *a:None)
    result=launcher.recover_bytecode(args)
    assert result['quarantined']==1 and not cache.exists()
    assert (archive/'manifest.json').read_bytes()==before
    assert next((Path(result['log'])/'quarantine').rglob('*.pyc')).read_bytes()==raw
    launcher.verify_archive(archive,manifest)


def test_bootstrap_has_only_stdlib_imports_and_no_checkout_imports():
    import ast
    file=Path(__file__).parents[1]/'scripts/start-active.py'
    imports={n.module.split('.')[0] for n in ast.walk(ast.parse(file.read_text())) if isinstance(n,ast.ImportFrom)}
    assert imports <= {'__future__','pathlib'}
    source=file.read_text()
    assert "'-I', '-B'" in source and 'site-packages' not in source


def test_python_mcp_children_explicitly_disable_bytecode():
    root=Path(__file__).parents[1]/'src/sap_business_agents_platform'
    for name in ('codex_planner.py','harness.py'):
        source=(root/name).read_text()
        assert "['-B', '-m', 'sap_business_agents_platform.mcp_server'" in source or '["-B", "-m", "sap_business_agents_platform.mcp_server"' in source


def test_generated_authoring_child_cannot_write_cache(tmp_path):
    import os,subprocess,sys
    from dataclasses import dataclass
    from sap_business_agents_platform.codex_planner import _with_authoring_mcp
    @dataclass
    class Config:
        launch_args_override:tuple=('app-server',)
    client=SimpleNamespace(_client=SimpleNamespace(_sync=SimpleNamespace(config=Config())))
    _with_authoring_mcp(client,{'internal_api_url':'http://127.0.0.1:8765','operation_id':'offline','capability':'synthetic'})
    arg=next(v for v in client._client._sync.config.launch_args_override if '.args=' in v)
    argv=json.loads(arg.split('=',1)[1])
    package=tmp_path/'sap_business_agents_platform';package.mkdir()
    (package/'__init__.py').write_text('')
    (package/'mcp_server.py').write_text('print("offline-child-imported")')
    env={**os.environ,'PYTHONPATH':str(tmp_path),'PYTHONDONTWRITEBYTECODE':'0'}
    result=subprocess.run([sys.executable,*argv],env=env,cwd=tmp_path,capture_output=True,text=True,check=True,timeout=10)
    assert 'offline-child-imported' in result.stdout
    assert not list(package.rglob('*.pyc'))


def test_verification_reuse_is_local_and_boundary_scoped(monkeypatch):
    calls=[];monkeypatch.setattr(launcher.environments,'verify',lambda value:calls.append(copy.deepcopy(value)))
    binding={'digest':'synthetic'}
    launcher.verify_environment(binding);launcher.verify_environment(binding)
    assert len(calls)==2  # Outside a boundary there is no reusable authority.
    token=launcher._verification.set({})
    try:
        launcher.verify_environment(binding);launcher.verify_environment(binding)
        assert len(calls)==3
        launcher._verification.set({})
        launcher.verify_environment(binding)
        assert len(calls)==4
    finally:launcher._verification.reset(token)


def test_plain_bootstrap_resolves_only_hash_checked_active_code(active):
    args,api,_,_,_,_=active;archive=Path(api['archive']);source=archive/'source'
    module_spec=importlib.util.spec_from_file_location('active_bootstrap',Path(__file__).parents[1]/'scripts/start-active.py')
    bootstrap=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(bootstrap)
    # The resolver uses no imports, package scanning or environment capture.
    for name in ('scripts/service-startup.py','src/sap_business_agents_platform/startup_control.py','src/sap_business_agents_platform/runtime_environment.py'):
        file=source/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('ACTIVE_STARTUP_VERSION = 1\n')
    python=Path(api['environment']['python']);python.parent.mkdir(parents=True);python.write_bytes(b'fixture interpreter, never executed')
    env=api['environment'];env.pop('digest');env['python_sha256']=launcher.control.sha(python);env['digest']=launcher.control.digest(env)
    manifest={'version':1,'files':{p.relative_to(source).as_posix():launcher.control.sha(p) for p in source.rglob('*') if p.is_file()},'dependencies':{'environment':env}}
    manifest['digest']=launcher.control.digest(manifest)
    target=args.root/'.local-data/startup-releases'/manifest['digest'];archive.rename(target)
    api.update(archive=str(target),source_digest=manifest['digest'])
    launcher.control.atomic(target/'manifest.json',manifest);launcher.control.atomic(args.root/'.local-data/startup-api-current.json',api)
    assert bootstrap.resolve(args.root)==(python,target/'source/scripts/service-startup.py')
    (target/'source/scripts/service-startup.py').write_text('tampered')
    with pytest.raises(ValueError,match='bootstrap_changed'):bootstrap.resolve(args.root)


@pytest.mark.parametrize('fault',['birth','command','pid',None])
def test_native_and_cim_identity_require_same_process(fault):
    saved={'ProcessId':123,'CommandLine':'owned','CreationDate':'1970-01-01T00:00:01.23Z'}
    native={'ProcessId':123,'CommandLine':'owned','CreationDate':'/Date(1230)/'}
    if fault=='birth':native['CreationDate']='/Date(1231)/'
    if fault=='command':native['CommandLine']='foreign'
    if fault=='pid':native['ProcessId']=124
    assert launcher.same_process_identity(saved,native)==(fault is None)


def test_native_process_query_matches_actual_cim_identity(tmp_path):
    import os,subprocess,sys
    if os.name!='nt':pytest.skip('Windows process identity')
    script=tmp_path/'start-api-identity.py';script.write_text('import time;time.sleep(30)')
    child=subprocess.Popen([sys.executable,'-I','-B',str(script)])
    try:
        native=launcher.native_process_identity(child.pid)
        cim=launcher.identity(child.pid,tmp_path)
        assert launcher.same_process_identity(native,cim)
    finally:
        child.terminate();child.wait(timeout=10)
