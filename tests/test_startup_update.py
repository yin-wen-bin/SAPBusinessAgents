"""Verified startup updates: no real services, SDK/model or SAP calls."""
import copy
import importlib.util
import json
import socket
import struct
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


update = load('verified_update', 'scripts/startup-update.py')
simple = load('verified_start', 'scripts/start-active.py')
controller = load('verified_controller', 'scripts/service-startup.py')


@pytest.mark.parametrize('family,size,port_offset,pid_offset',[(socket.AF_INET,24,8,20),(socket.AF_INET6,56,20,52)])
def test_native_listener_table_uses_exact_port_and_owner(family,size,port_offset,pid_offset):
    raw=bytearray(4+size)
    struct.pack_into('<I',raw,0,1)
    struct.pack_into('<I',raw,4+port_offset,socket.htons(8765))
    struct.pack_into('<I',raw,4+pid_offset,12345)
    assert controller.tcp_listener_rows(raw,family)==[(8765,12345)]
    with pytest.raises(OSError,match='table_invalid'):
        controller.tcp_listener_rows(raw[:-1],family)


@pytest.mark.skipif(controller.os.name!='nt',reason='Windows ownership API')
def test_native_inventory_avoids_redacted_netstat(monkeypatch):
    monkeypatch.setattr(controller,'windows_listeners',lambda:{8765:12345,4321:54321})
    monkeypatch.setattr(controller.subprocess,'run',lambda *a,**k:pytest.fail('Do not replace observed owners with redacted rows'))
    assert controller.listeners()=={8765:12345,4321:54321}


@pytest.fixture
def target(tmp_path, monkeypatch):
    root = tmp_path
    for name, content in {'src/app.py': 'answer=2\n',
        'scripts/startup-update.py': (ROOT/'scripts/startup-update.py').read_text(),
        'scripts/service-startup.py': '# synthetic startup\n',
        'site/src/page.ts': 'export const answer=2\n'}.items():
        path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content, encoding='utf-8')
    api = {'source_digest': 'a'*64, 'archive': str(root/'old'), 'environment': {'digest': 'e'*64},
           'api_port': 8765, 'site_port': 4321}
    site = {'fingerprint': 'b'*64, 'dist_path': str(root/'old-site'), 'mode': 'preview'}
    update.atomic(root/'.local-data/startup-api-current.json', api)
    update.atomic(root/'.local-data/site-builds/current.json', site)
    manifest = {'version': 1, 'files': update.inputs(root), 'dependencies': {'environment': api['environment']}}
    manifest['digest'] = update.digest(manifest)
    events = []
    def activate(args):
        args.verified_update_check()
        handoff = update.read(args.target_record)
        events.append('activate')
        api.update(handoff['target']); site.update(handoff['site'])
        return {'status': 'completed'}
    boot = {'active_records': lambda root: (copy.deepcopy(api), copy.deepcopy(site), {}),
        'settings_for': lambda root: SimpleNamespace(data_root=root/'.local-data'),
        'source_manifest': lambda *args: copy.deepcopy(manifest),
        'site_fingerprint': lambda *args: 'f'*64,
        'freeze': lambda root, manifest: root/'.local-data/startup-releases'/manifest['digest'],
        'control': SimpleNamespace(activity=lambda root: False, file_lock=lambda *args, **kwargs: nullcontext()), 'jobs_pending': lambda root: False,
        'activate_code': activate,
        'request': lambda url: {'fingerprint': site['fingerprint']} if 'sapba-build' in url else {'ok': True, 'source_digest': api['source_digest']},
        'valid_health': lambda value: value.get('ok') is True,
        'valid_binding': lambda value, expected: value.get('source_digest') == expected}
    log = root/'.local-data/startup/updates/proof/tests.log'
    log.parent.mkdir(parents=True); log.write_text('synthetic passing controller evidence')
    proof = {'passed': True, 'evidence': {log.relative_to(root).as_posix(): update.sha(log)}}
    registration = update.register(root, update.inputs(root), proof, boot=boot)
    path = Path(registration['record'])
    def build(*args, **kwargs): events.append('build'); return SimpleNamespace(returncode=0)
    monkeypatch.setattr(update.subprocess, 'run', build)
    return SimpleNamespace(root=root, api=api, site=site, manifest=manifest, events=events, boot=boot,
                           path=path, value=update.read(path), log=log)


def test_verified_update_registers_exact_source_without_switching(target):
    assert update.load_record(target.root, target.path)['manifest']==target.manifest
    assert target.events==[] and target.api['source_digest']=='a'*64


@pytest.mark.parametrize('change', ['edit', 'add', 'delete', 'frontend'])
def test_any_code_change_invalidates_proposal_before_activation(target, change):
    path=target.root/('site/src/page.ts' if change=='frontend' else 'src/app.py')
    if change=='delete': path.unlink()
    elif change=='add': (target.root/'src/new.py').write_text('new=1')
    else: path.write_text('changed=1')
    with pytest.raises(ValueError, match='source_changed'):
        update.apply(target.root, target.path, boot=target.boot)
    assert target.events==[]


def test_changed_evidence_is_not_treated_as_verified(target):
    target.log.write_text('different result')
    with pytest.raises(ValueError, match='verification_changed'):
        update.load_record(target.root, target.path)


def test_record_tampering_cannot_retarget_code(target):
    value=update.read(target.path); value['manifest']['digest']='c'*64; update.atomic(target.path,value)
    with pytest.raises(ValueError, match='record_invalid'):
        update.load_record(target.root,target.path)


def test_update_waits_only_in_invoked_process_and_then_builds_switches_and_verifies(target, monkeypatch):
    busy=iter([True,False]); target.boot['control'].activity=lambda root: next(busy)
    monkeypatch.setattr(update.time,'sleep',lambda seconds:target.events.append(('idle_wait',seconds)))
    result=update.apply(target.root,target.path,boot=target.boot,no_browser=True)
    assert result['status']=='completed'
    assert target.events==[('idle_wait',5),'build','activate']
    assert target.api['source_digest']==target.manifest['digest']
    assert target.site['fingerprint']=='f'*64
    assert update.consumed(target.root,target.value)


def test_interrupting_idle_wait_does_not_touch_services(target,monkeypatch):
    target.boot['control'].activity=lambda root:True
    def interrupt(seconds):raise KeyboardInterrupt()
    monkeypatch.setattr(update.time,'sleep',interrupt)
    with pytest.raises(KeyboardInterrupt):update.apply(target.root,target.path,boot=target.boot)
    assert target.events==[] and not update.consumed(target.root,target.value)


def test_idle_gate_uses_existing_configured_data_root(target,monkeypatch):
    configured=target.root/'existing-data';seen=[]
    target.boot['settings_for']=lambda root:SimpleNamespace(data_root=configured)
    def activity(path):
        seen.append(path)
        return len(seen)==1
    target.boot['control'].activity=activity
    monkeypatch.setattr(update.time,'sleep',lambda seconds:None)
    update.apply(target.root,target.path,boot=target.boot)
    assert seen==[configured,configured] and target.events==['build','activate']


@pytest.mark.parametrize('change',['environment','base'])
def test_other_release_or_environment_is_not_overwritten(target,change):
    if change=='environment':target.api['environment']={'digest':'other'}
    else:target.api['source_digest']='c'*64
    with pytest.raises(ValueError,match='changed_verify_again'):
        update.apply(target.root,target.path,boot=target.boot)
    assert target.events==[]


def test_concurrent_edit_during_build_cannot_activate(target,monkeypatch):
    def build(*args,**kwargs):(target.root/'src/app.py').write_text('concurrent=1')
    monkeypatch.setattr(update.subprocess,'run',build)
    with pytest.raises(ValueError,match='source_changed'):
        update.apply(target.root,target.path,boot=target.boot)
    assert 'activate' not in target.events


def test_failed_activation_is_not_acknowledged_as_success(target):
    def fail(args):raise RuntimeError('simulated controlled rollback')
    target.boot['activate_code']=fail
    with pytest.raises(RuntimeError):update.apply(target.root,target.path,boot=target.boot)
    assert not update.consumed(target.root,target.value) and target.api['source_digest']=='a'*64


def test_inner_deployment_busy_does_not_replay_build_or_activation(target):
    def fail(args):
        target.events.append('blocked')
        raise RuntimeError('startup_coordination_busy')
    target.boot['activate_code']=fail
    with pytest.raises(RuntimeError,match='startup_coordination_busy'):
        update.apply(target.root,target.path,boot=target.boot)
    assert target.events==['build','blocked']
    assert not update.consumed(target.root,target.value)


@pytest.mark.parametrize('name',['../outside','/outside','C:/outside','src/../app.py','src\\app.py'])
def test_update_paths_cannot_escape_workspace(target,name):
    with pytest.raises(ValueError,match='path_invalid'):
        update.safe_location(target.root,name)


def test_wrong_actual_health_cannot_mark_update_complete(target):
    target.boot['request']=lambda url:{'ok':True,'source_digest':'wrong','fingerprint':'f'*64}
    with pytest.raises(ValueError,match='running_version_mismatch'):
        update.apply(target.root,target.path,boot=target.boot)
    assert not update.consumed(target.root,target.value)


@pytest.mark.parametrize('maintenance',[[],['--restart']])
def test_start_and_restart_automatically_apply_one_verified_proposal(target,monkeypatch,maintenance):
    calls=[]
    monkeypatch.setattr(simple,'resolve',lambda root:(Path('bound-python.exe'),Path('frozen.py')))
    monkeypatch.setattr(simple.subprocess,'call',lambda argv,**kwargs:calls.append(argv) or 0)
    monkeypatch.setattr(simple,'simple_start',lambda *args:pytest.fail('Must finish the update before opening'))
    assert simple.main(['ensure','--root',str(target.root),'--no-browser',*maintenance])==0
    assert len(calls)==1 and 'apply' in calls[0] and '--no-browser' in calls[0]
    assert ('--restart' in calls[0])==bool(maintenance)


def test_completed_update_is_not_reapplied_after_normal_agent_publication(target,monkeypatch):
    update.apply(target.root,target.path,boot=target.boot)
    target.site['fingerprint']='d'*64
    update.atomic(target.root/'.local-data/site-builds/current.json',target.site)
    calls=[]
    monkeypatch.setattr(simple,'resolve',lambda root:(Path('bound-python.exe'),Path('frozen.py')))
    monkeypatch.setattr(simple,'simple_start',lambda *args:calls.append('fast_start') or 0)
    monkeypatch.setattr(simple.subprocess,'call',lambda *args,**kwargs:pytest.fail('Do not downgrade/reapply'))
    assert simple.main(['ensure','--root',str(target.root)])==0 and calls==['fast_start']


def test_frontend_only_verified_update_also_enters_activation(target,monkeypatch):
    target.api['source_digest']=target.manifest['digest']
    update.atomic(target.root/'.local-data/startup-api-current.json',target.api)
    calls=[]
    monkeypatch.setattr(simple,'resolve',lambda root:(Path('python.exe'),Path('frozen.py')))
    monkeypatch.setattr(simple.subprocess,'call',lambda argv,**kwargs:calls.append(argv) or 0)
    assert simple.main(['ensure','--root',str(target.root)])==0
    assert len(calls)==1 and 'apply' in calls[0]


def test_another_launcher_completion_is_reused_without_build_or_switch(target,monkeypatch):
    def competing(*args,**kwargs):
        update.atomic(target.root/'.local-data/startup/updates'/target.value['id']/'activated.json',
            {'verification_id':target.value['id'],'record_digest':target.value['digest'],'status':'activated'})
        raise RuntimeError('startup_coordination_busy')
    target.boot['control'].file_lock=competing
    monkeypatch.setattr(update.time,'sleep',lambda seconds:None)
    assert update.apply(target.root,target.path,boot=target.boot)['status']=='already_activated'
    assert target.events==[]


def test_reuse_only_never_applies_queued_update(target,monkeypatch):
    monkeypatch.setattr(simple,'resolve',lambda root:(Path('python.exe'),Path('frozen.py')))
    monkeypatch.setattr(simple,'simple_start',lambda *args:0)
    monkeypatch.setattr(simple.subprocess,'call',lambda *args,**kwargs:pytest.fail('Read-only entry cannot activate'))
    assert simple.main(['ensure','--root',str(target.root),'--reuse-only'])==0


def test_updater_edit_before_start_is_rejected_without_executing_it(target,monkeypatch):
    (target.root/'scripts/startup-update.py').write_text('raise AssertionError("unverified code")')
    monkeypatch.setattr(simple,'resolve',lambda root:(Path('python.exe'),Path('frozen.py')))
    with pytest.raises(ValueError,match='verifier_changed'):
        simple.main(['ensure','--root',str(target.root)])


def test_verification_uses_real_pytest_result_and_never_registers_failure(tmp_path,monkeypatch):
    # A tiny real child process catches zero/failed-test certification mistakes.
    (tmp_path/'tests').mkdir()
    test=tmp_path/'tests/test_example.py';test.write_text('def test_example(): assert True\n')
    registrations=[]
    monkeypatch.setattr(update,'DEFAULT_TESTS',())
    monkeypatch.setattr(update,'register',lambda root,expected,proof:registrations.append(proof) or {'status':'verified'})
    assert update.verify(tmp_path,['tests/test_example.py'])=={'status':'verified'}
    assert registrations[0]['counts']['tests']==1
    test.write_text('def test_example(): assert False\n')
    with pytest.raises(ValueError,match='regressions_failed'):
        update.verify(tmp_path,['tests/test_example.py'])
    assert len(registrations)==1
