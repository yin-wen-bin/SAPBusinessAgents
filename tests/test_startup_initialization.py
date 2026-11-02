"""First installation without private state; only owned fake services are used."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_startup_versioning import launcher

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('first_start_bootstrap', ROOT / 'scripts/start-active.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


@pytest.fixture
def first_install(tmp_path, monkeypatch):
    root = tmp_path.resolve(); data = root / '.local-data'
    args = SimpleNamespace(root=root, port=28765, site_port=24321, timeout=90,
                          restart=False, rebuild=False, dev=False, no_browser=True, reuse_only=False)
    binding = {'digest':'e'*64, 'python':str(root / '.venv/Scripts/python.exe')}
    manifest = {'digest':'a'*64, 'files':{}, 'dependencies':{'environment':binding}}
    fingerprint = 'b'*64
    state = SimpleNamespace(ports={}, starts=[], stops=[], phases=[], fail_ready=False, changed=False)
    monkeypatch.setattr(launcher, 'settings_for', lambda root:SimpleNamespace(data_root=data))
    monkeypatch.setattr(launcher, 'initial_preflight', lambda root:binding)
    monkeypatch.setattr(launcher, 'jobs_pending', lambda *a:False)
    monkeypatch.setattr(launcher.control, 'activity', lambda *a:{})
    monkeypatch.setattr(launcher, 'listeners', lambda:dict(state.ports))
    monkeypatch.setattr(launcher, 'port_open', lambda port:False)
    monkeypatch.setattr(launcher, 'assert_writers', lambda *a:None)
    monkeypatch.setattr(launcher, 'site_fingerprint', lambda *a:fingerprint)
    def source(root, environment=None):
        assert environment == binding
        return {**manifest, 'changed':True} if state.changed else manifest
    monkeypatch.setattr(launcher, 'source_manifest', source)
    def freeze(root, value):
        directory = data / 'startup-releases' / value['digest']
        launcher.control.atomic(directory / 'manifest.json', value)
        return directory
    monkeypatch.setattr(launcher, 'freeze', freeze)
    monkeypatch.setattr(launcher, 'check_admission', lambda *a:state.phases.append('admission'))
    def identity(pid, root):
        return {'ProcessId':pid, 'CreationDate':str(pid), 'CommandLine':str(root)}
    monkeypatch.setattr(launcher, 'identity', identity)
    monkeypatch.setattr(launcher, 'listener_belongs_to', lambda pid, saved, root:saved == identity(pid, root))
    def stop(saved, root):
        pid = saved['ProcessId'];state.stops.append(pid)
        state.ports = {port:value for port,value in state.ports.items() if value != pid}
    monkeypatch.setattr(launcher, 'stop_owned', stop)
    class Process:
        def __init__(self, pid):self.pid = pid
        def poll(self):return None if self.pid in state.ports.values() else 0
        def wait(self, timeout):return 0
    def launch(root, archive, port, log):
        state.ports[port] = 101;state.starts.append('api')
        return Process(101)
    monkeypatch.setattr(launcher, 'launch_api', launch)
    def switch(root, key, options, owned, previous, log):
        assert owned is None and not previous
        state.ports[options.site_port] = 202;state.starts.append('site')
        options._site_spawn_callback(Process(202))
        launcher.control.atomic(data / 'site-builds/current.json', {
            'fingerprint':key, 'dist_path':str(data / 'site-builds' / key / 'dist'), 'mode':'preview', 'pid':202})
    monkeypatch.setattr(launcher, 'switch_site', switch)
    def request(url, **kwargs):
        if 'sapba-build' in url:
            if args.site_port not in state.ports:raise OSError('not started')
            return {'fingerprint':fingerprint}
        if args.port not in state.ports:raise OSError('not started')
        return {'ok':True, 'loopback_only':True, 'sap_read':{'selected_provider':'embedded','data':{'read_only':True}},
                'startup_binding':{'verified':True,'source_digest':manifest['digest'],'environment_digest':binding['digest'],'pid':101,'boot_id':'first'}}
    monkeypatch.setattr(launcher, 'request', request)
    def ready(*a):
        if state.fail_ready:raise launcher.control.StartupBlocked('fixture_api_not_ready')
    monkeypatch.setattr(launcher, 'wait_api', ready)
    monkeypatch.setattr(launcher, 'probe_site', lambda *a:state.phases.append('site_probe'))
    monkeypatch.setattr(launcher.subprocess, 'run', lambda argv, **kw:state.phases.append('site_build'))
    return args, state, data, binding


def test_first_install_creates_verified_pair_without_private_history(first_install):
    args, state, data, binding = first_install
    assert not data.exists()
    result = launcher.initialize(args)
    api = json.loads((data / 'startup-api-current.json').read_bytes())
    site = json.loads((data / 'site-builds/current.json').read_bytes())
    assert api['environment'] == binding and api['source_digest'] == result['source_digest']
    assert site['fingerprint'] == result['site_fingerprint']
    assert state.starts == ['api','site'] and state.stops == []
    assert state.phases.index('site_build') < state.phases.index('site_probe')
    assert state.phases.count('admission') == 2
    assert not (data / 'startup-api-previous.json').exists()
    assert not (data / 'startup-maintenance.json').exists()
    assert not (data / 'startup/pending-switch.json').exists()
    assert json.loads((data / 'startup/initialization.json').read_bytes())['status'] == 'completed'


@pytest.mark.parametrize('name', ['startup-api-current.json','startup-api-previous.json',
    'startup-api-legacy-rollback.json','site-builds/current.json','site-builds/previous.json',
    'startup-maintenance.json','startup/pending-switch.json','startup/latest.json'])
def test_missing_current_never_turns_old_deployment_into_first_install(tmp_path, name):
    path = tmp_path / '.local-data' / name
    path.parent.mkdir(parents=True);path.write_bytes(b'original audit bytes')
    with pytest.raises(launcher.control.StartupBlocked, match='existing_state'):
        launcher.control.initial_install_state(tmp_path)
    assert path.read_bytes() == b'original audit bytes'


def test_existing_database_is_not_reinitialized(first_install):
    args, state, data, _ = first_install
    data.mkdir();database = data / 'platform.sqlite3';database.write_bytes(b'private database')
    with pytest.raises(launcher.control.StartupBlocked, match='existing_data'):
        launcher.initialize(args)
    assert database.read_bytes() == b'private database' and state.starts == []


def test_historical_archive_without_first_install_receipt_is_not_adopted(tmp_path):
    archive = tmp_path / '.local-data/startup-releases/old/manifest.json'
    archive.parent.mkdir(parents=True);archive.write_text('{}')
    with pytest.raises(launcher.control.StartupBlocked, match='existing_state'):
        launcher.control.initial_install_state(tmp_path)


def test_preparation_failure_can_retry_without_renewing_an_existing_binding(first_install, monkeypatch):
    args, state, data, _ = first_install
    def denied(*a):raise launcher.control.StartupBlocked('admission_rejected')
    monkeypatch.setattr(launcher, 'check_admission', denied)
    with pytest.raises(launcher.control.StartupBlocked, match='admission_rejected'):
        launcher.initialize(args)
    assert json.loads((data / 'startup/initialization.json').read_bytes())['status'] == 'failed'
    assert state.starts == []
    monkeypatch.setattr(launcher, 'check_admission', lambda *a:None)
    assert launcher.initialize(args)['active_release_ready']


def test_first_api_failure_stops_only_new_processes_and_removes_partial_pointer(first_install):
    args, state, data, _ = first_install;state.fail_ready = True
    with pytest.raises(launcher.control.StartupBlocked, match='fixture_api_not_ready'):
        launcher.initialize(args)
    assert set(state.stops) == {101,202} and not state.ports
    assert not (data / 'startup-api-current.json').exists()
    assert not (data / 'site-builds/current.json').exists()
    assert not (data / 'startup/pending-switch.json').exists()
    assert json.loads((data / 'startup/initialization.json').read_bytes())['status'] == 'failed'


def test_first_commit_uses_owned_health_pid_when_tcp_inventory_omits_listener(first_install, monkeypatch):
    args, state, data, _ = first_install
    monkeypatch.setattr(launcher, 'listeners', lambda:{})
    assert launcher.initialize(args)['services']['api']['pid'] == 101
    assert json.loads((data / 'startup-api-current.json').read_bytes())['listener_identity']['ProcessId'] == 101
    assert not state.stops


def test_first_rollback_stops_recorded_trees_when_tcp_inventory_omits_listeners(first_install, monkeypatch):
    args, state, data, _ = first_install;state.fail_ready = True
    monkeypatch.setattr(launcher, 'listeners', lambda:{})
    with pytest.raises(launcher.control.StartupBlocked, match='fixture_api_not_ready'):
        launcher.initialize(args)
    assert set(state.stops) == {101,202} and not state.ports
    assert not (data / 'site-builds/current.json').exists()


def test_first_commit_rejects_health_pid_outside_the_recorded_tree(first_install, monkeypatch):
    args, state, data, _ = first_install
    request = launcher.request
    def foreign(url, **kwargs):
        value = request(url, **kwargs)
        if 'startup_binding' in value:value['startup_binding']['pid'] = 999
        return value
    monkeypatch.setattr(launcher, 'request', foreign)
    with pytest.raises(launcher.control.StartupBlocked, match='process_unconfirmed'):
        launcher.initialize(args)
    assert set(state.stops) == {101,202} and 999 not in state.stops
    assert not (data / 'startup-api-current.json').exists()


def test_first_record_write_failure_rolls_back_owned_pair(first_install, monkeypatch):
    args, state, data, _ = first_install
    atomic = launcher.control.atomic
    def fail_record(path, value):
        if path == data / 'startup-api-current.json':raise OSError('fixture commit failure')
        return atomic(path, value)
    monkeypatch.setattr(launcher.control, 'atomic', fail_record)
    with pytest.raises(OSError, match='fixture commit failure'):
        launcher.initialize(args)
    assert set(state.stops) == {101,202} and not state.ports
    assert not (data / 'site-builds/current.json').exists()
    assert not (data / 'startup/pending-switch.json').exists()


def test_first_install_rejects_open_port_missing_from_tcp_inventory(first_install, monkeypatch):
    args, state, data, _ = first_install
    monkeypatch.setattr(launcher, 'port_open', lambda port:port == args.port)
    with pytest.raises(launcher.control.StartupBlocked, match='port_in_use'):
        launcher.initialize(args)
    assert not state.starts and not state.stops


def test_occupied_port_is_not_stopped_or_adopted(first_install):
    args, state, data, _ = first_install;state.ports[args.port] = 999
    with pytest.raises(launcher.control.StartupBlocked, match='port_in_use'):
        launcher.initialize(args)
    assert state.ports == {args.port:999} and not state.starts and not state.stops


def test_custom_existing_data_root_is_preserved(first_install, monkeypatch):
    args, state, data, _ = first_install
    private = args.root / 'private-data';private.mkdir();(private / 'platform.sqlite3').write_bytes(b'private')
    monkeypatch.setattr(launcher, 'settings_for', lambda root:SimpleNamespace(data_root=private))
    with pytest.raises(launcher.control.StartupBlocked, match='existing_data'):
        launcher.initialize(args)
    assert not state.starts and (private / 'platform.sqlite3').read_bytes() == b'private'


def test_thin_entry_dispatches_only_missing_state_to_initializer(tmp_path, monkeypatch):
    script = tmp_path / 'src/sap_business_agents_platform/startup_control.py'
    script.parent.mkdir(parents=True);script.write_text((ROOT / 'src/sap_business_agents_platform/startup_control.py').read_text())
    python = tmp_path / '.venv/Scripts/python.exe';python.parent.mkdir(parents=True);python.write_bytes(b'not executed')
    calls = []
    monkeypatch.setattr(bootstrap.subprocess, 'call', lambda command, **kw:calls.append(command) or 0)
    monkeypatch.setattr(bootstrap, 'resolve', lambda *a:pytest.fail('No existing binding to resolve'))
    assert bootstrap.main(['ensure','--root',str(tmp_path),'--no-browser']) == 0
    assert calls[0][:3] == [str(python),'-I','-B']
    assert 'initialize' in calls[0] and '--no-browser' in calls[0]
    assert '--port' in calls[0] and '--site-port' in calls[0]


def test_reuse_only_never_initializes_or_creates_state(tmp_path):
    with pytest.raises(ValueError, match='initialization_required'):
        bootstrap.main(['ensure','--root',str(tmp_path),'--reuse-only'])
    assert not (tmp_path / '.local-data').exists()


def test_existing_invalid_record_still_fails_closed(tmp_path, monkeypatch):
    p = tmp_path / '.local-data/startup-api-current.json';p.parent.mkdir();p.write_text('{broken')
    monkeypatch.setattr(bootstrap, 'initialize_first_install', lambda *a:pytest.fail('Must not replace corrupt state'))
    with pytest.raises(ValueError):bootstrap.main(['ensure','--root',str(tmp_path)])
    assert p.read_text() == '{broken'


def test_preflight_verifies_installed_environment_without_installing(tmp_path, monkeypatch):
    python = launcher.environments.python_path(tmp_path / '.venv');python.parent.mkdir(parents=True);python.write_bytes(b'fixture')
    astro = tmp_path / 'site/node_modules/astro/bin/astro.mjs';astro.parent.mkdir(parents=True);astro.write_text('fixture')
    calls = [];binding = {'digest':'fixture'}
    monkeypatch.setattr(launcher.shutil, 'which', lambda name:name)
    monkeypatch.setattr(launcher.subprocess, 'run', lambda argv, **kw:calls.append(argv) or SimpleNamespace(stdout='v22.19.0'))
    monkeypatch.setattr(launcher.environments, 'capture', lambda *a, **kw:binding)
    monkeypatch.setattr(launcher, 'verify_environment', lambda value:calls.append(value))
    assert launcher.initial_preflight(tmp_path) == binding
    assert calls == [['node.exe','--version'],binding]


def test_explicit_initialize_on_registered_pair_only_reuses_existing_path(first_install, monkeypatch):
    args, state, data, _ = first_install
    launcher.control.atomic(data / 'startup-api-current.json', {'existing':'must validate'})
    monkeypatch.setattr(launcher, 'ensure', lambda options:{'existing':True})
    assert launcher.initialize(args) == {'existing':True}
    assert not state.starts


@pytest.mark.parametrize('port,site_port,timeout', [(0,24321,90),(28765,28765,90),(28765,24321,0)])
def test_invalid_initial_ports_do_not_create_state(first_install, port, site_port, timeout):
    args, state, data, _ = first_install
    args.port, args.site_port, args.timeout = port, site_port, timeout
    with pytest.raises(launcher.control.StartupBlocked, match='arguments_out_of_range'):
        launcher.initialize(args)
    assert not data.exists() and not state.starts


def test_listener_appearing_during_preflight_is_preserved(first_install, monkeypatch):
    args, state, data, binding = first_install
    def preflight(root):
        state.ports[args.site_port] = 999
        return binding
    monkeypatch.setattr(launcher, 'initial_preflight', preflight)
    with pytest.raises(launcher.control.StartupBlocked, match='port_in_use'):
        launcher.initialize(args)
    assert state.ports == {args.site_port:999} and not state.starts and not state.stops


def test_source_changes_before_commit_roll_back_first_pair(first_install, monkeypatch):
    args, state, data, _ = first_install
    monkeypatch.setattr(launcher, 'wait_api', lambda *a:setattr(state, 'changed', True))
    with pytest.raises(launcher.control.StartupBlocked, match='changed_during_launch'):
        launcher.initialize(args)
    assert not state.ports and set(state.stops) == {101,202}
    assert not (data / 'startup-api-current.json').exists()
    assert not (data / 'site-builds/current.json').exists()


def test_failed_first_database_is_retained_and_retry_uses_same_data_root(first_install, monkeypatch):
    args, state, data, _ = first_install
    state.fail_ready = True
    launch = launcher.launch_api
    def api(*a):
        (data / 'platform.sqlite3').write_bytes(b'first-install data, never deleted')
        return launch(*a)
    monkeypatch.setattr(launcher, 'launch_api', api)
    with pytest.raises(launcher.control.StartupBlocked):launcher.initialize(args)
    assert (data / 'platform.sqlite3').read_bytes() == b'first-install data, never deleted'
    state.fail_ready = False
    assert launcher.initialize(args)['active_release_ready']


def test_first_switch_recovery_can_roll_back_without_a_previous_release(first_install, monkeypatch):
    args, state, data, _ = first_install
    state.fail_ready = True
    def unconfirmed(url, key, process, timeout):
        state.ports[args.site_port] = 999
        raise launcher.control.StartupBlocked('fixture_api_not_ready')
    monkeypatch.setattr(launcher, 'wait_api', unconfirmed)
    with pytest.raises(launcher.control.StartupBlocked, match='rollback_incomplete'):
        launcher.initialize(args)
    assert 999 not in state.stops and state.ports[args.site_port] == 999
    assert (data / 'startup/pending-switch.json').exists()
    assert json.loads((data / 'startup/initialization.json').read_bytes())['status'] == 'recovery_required'
    state.ports.clear()  # The unrelated listener exited independently.
    assert launcher.recover_switch(args)['outcome'] == 'rolled_back'
    assert json.loads((data / 'startup/initialization.json').read_bytes())['status'] == 'failed'
    assert not (data / 'site-builds/current.json').exists()
    assert not (data / 'startup/pending-switch.json').exists()


def test_recovery_commits_a_healthy_first_pair(first_install, monkeypatch):
    args, state, data, _ = first_install
    result = launcher.initialize(args)
    api = json.loads((data / 'startup-api-current.json').read_bytes())
    receipt = json.loads((data / 'startup/initialization.json').read_bytes())
    saved = {'initialization':True,'owner_pid':receipt['owner_pid'],'previous':None,'previous_site':{},
             'api_changed':True,'site_changed':True,'target':api,'target_site':result['site_fingerprint'],
             'new_identity':launcher.identity(101,args.root),'site_new_identity':launcher.identity(202,args.root),
             'log':str(data / 'startup/crashed-first-attempt')}
    launcher.control.atomic(data / 'startup/pending-switch.json', saved)
    launcher.control.atomic(data / 'startup-maintenance.json', {'pid':receipt['owner_pid']})
    (data / 'site-builds/current.json').unlink()  # Crashed before the paired pointer was committed.
    monkeypatch.setattr(launcher, 'verify_archive', lambda *a:None)
    monkeypatch.setattr(launcher.environments, 'verify', lambda *a:None)
    assert launcher.recover_switch(args)['outcome'] == 'activated'
    assert json.loads((data / 'site-builds/current.json').read_bytes())['fingerprint'] == result['site_fingerprint']
    assert not state.stops and not (data / 'startup/pending-switch.json').exists()
