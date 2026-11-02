"""Plain startup never enters deployment or invokes SDK/model/SAP checks."""
from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('simple_startup', ROOT / 'scripts/start-active.py')
simple = importlib.util.module_from_spec(spec)
spec.loader.exec_module(simple)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path
    api = {'archive':str(root/'archive'), 'source_digest':'a'*64,
           'environment':{'digest':'e'*64}, 'pid':10}
    script = root/'archive/source/scripts/service-startup.py'
    site = {'fingerprint':'b'*64, 'dist_path':str(root/'.local-data/site-builds'/('b'*64)/'dist'),
            'pid':20, 'mode':'preview'}
    data = root/'.local-data'
    writes = []

    def atomic(path, value):
        writes.append((str(path), json.loads(json.dumps(value))))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    atomic(data/'startup-api-current.json', api)
    atomic(data/'site-builds/current.json', site)
    atomic(Path(site['dist_path'])/'sapba-build.json', {'fingerprint':site['fingerprint']})
    writes.clear()
    state = SimpleNamespace(api=True, site=True, starts=[], gets=[], locks=[], browser=[], pid=100,
                            health_fault=None, site_fault=None, exited=False)
    identities = {
        10:{'ProcessId':10,'CreationDate':'api-birth','CommandLine':f'python.exe -I -B {script} serve'},
        20:{'ProcessId':20,'CreationDate':'site-birth','CommandLine':f'node.exe astro.mjs preview --outDir {site["dist_path"]}'}}

    def status(url):
        state.gets.append(url)
        if 'sapba-build' in url:
            if not state.site:
                return None
            return {'fingerprint':state.site_fault or site['fingerprint']}
        if not state.api:
            return None
        health = {'ok':True,'startup_binding':{'verified':True,'source_digest':api['source_digest'],
                  'environment_digest':api['environment']['digest'],'pid':10,'boot_id':'boot'}}
        if state.health_fault:
            health['startup_binding'][state.health_fault] = 'wrong'
        return health

    def spawn(argv, cwd, log):
        state.starts.append(argv)
        state.pid += 1
        if argv[0] == 'node.exe':
            state.site = True
            identities[state.pid] = {'ProcessId':state.pid, 'CreationDate':'new-site',
                                    'CommandLine':' '.join(argv)}
        else:
            state.api = True
        return SimpleNamespace(pid=state.pid, poll=lambda:0 if state.exited else None)

    def lock(path, **kwargs):
        state.locks.append(str(path))
        return nullcontext()

    def forbidden(*args, **kwargs):
        pytest.fail('Plain startup must not deploy, enumerate all ports, or audit SDK admission')

    boot = {'control':SimpleNamespace(file_lock=lock, atomic=atomic), 'spawn':spawn,
            'valid_health':lambda health:health.get('ok') is True,
            'valid_binding':lambda health,key:health['startup_binding'].get('source_digest')==key,
            'service_identities':lambda pids,root:{pid:identities[pid] for pid in pids},
            'same_process_identity':lambda saved,current:saved==current,
            'listeners':forbidden, 'ensure':forbidden, 'check_admission':forbidden,
            'source_manifest':forbidden, 'freeze':forbidden}
    monkeypatch.setattr(simple.runpy, 'run_path', lambda path:boot)
    monkeypatch.setattr(simple, 'status', status)
    monkeypatch.setattr(simple, 'port_open', lambda port:False)
    import webbrowser
    monkeypatch.setattr(webbrowser, 'open', lambda url:state.browser.append(url))
    args = SimpleNamespace(root=root,port=8765,site_port=4321,timeout=1,no_browser=True,reuse_only=False)
    return args, api, site, script, state, writes, boot, identities


def start(setup):
    args, _, _, script, *_ = setup
    return simple.simple_start(args, Path('bound-python.exe'), script)


def test_hot_start_is_two_gets_and_no_disk_audit_or_lock(setup):
    assert start(setup) == 0
    _, _, _, _, state, writes, *_ = setup
    assert len(state.gets) == 2
    assert state.starts == state.locks == writes == []


@pytest.mark.parametrize('missing', ['api', 'site', 'both'])
def test_only_missing_services_start_without_activation_journal(setup, missing):
    args, api, site, script, state, writes, *_ = setup
    state.api = missing not in {'api','both'}
    state.site = missing not in {'site','both'}
    assert start(setup) == 0
    assert len(state.starts) == (2 if missing == 'both' else 1)
    assert not (args.root/'.local-data/startup/pending-switch.json').exists()
    assert not (args.root/'.local-data/startup-maintenance.json').exists()
    assert all(Path(path).name in {'startup-api-current.json','current.json'} for path,_ in writes)
    assert all('ensure' not in argv and 'activate-code' not in argv for argv in state.starts)
    if missing != 'site':
        assert state.starts[0][:3] == ['bound-python.exe','-I','-B']
        assert 'serve' in state.starts[0] and api['archive'] in state.starts[0]
    assert not any('install' in arg or 'pip' in arg for argv in state.starts for arg in argv)


@pytest.mark.parametrize('field', ['source_digest','environment_digest'])
def test_wrong_running_binding_never_restarts_or_grants_qualification(setup, field):
    state = setup[4]
    state.health_fault = field
    with pytest.raises(ValueError, match='binding_unconfirmed'):
        start(setup)
    assert state.starts == [] and setup[5] == []


def test_wrong_frontend_or_reused_pid_is_not_accepted(setup):
    state = setup[4]
    state.site_fault = 'different'
    with pytest.raises(ValueError, match='frontend_binding_unconfirmed'):
        start(setup)
    state.site_fault = None
    setup[7][20]['CommandLine'] = 'node.exe unrelated.mjs'
    with pytest.raises(ValueError, match='frontend_process_unconfirmed'):
        start(setup)
    assert state.starts == []


def test_occupied_but_unhealthy_port_is_not_stopped(setup, monkeypatch):
    setup[4].api = False
    monkeypatch.setattr(simple, 'port_open', lambda port:True)
    with pytest.raises(ValueError, match='api_not_ready_no_restart'):
        start(setup)
    assert setup[4].starts == []


@pytest.mark.parametrize('marker', ['startup-maintenance.json','startup/pending-switch.json'])
def test_old_maintenance_does_not_disappear_or_restart_services(setup, marker, capsys):
    args = setup[0]
    path = args.root/'.local-data'/marker
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"existing":"audit"}')
    assert start(setup) == 0  # Open an already healthy page, explicitly warning.
    assert 'still needs recovery' in capsys.readouterr().out
    setup[4].api = False
    with pytest.raises(ValueError, match='switch_recovery_required'):
        start(setup)
    assert path.read_text() == '{"existing":"audit"}'
    assert setup[4].starts == []


def test_competing_double_click_reuses_services_after_lock(setup, monkeypatch):
    state = setup[4]
    state.api = False
    def lock(path, **kwargs):
        state.api = True
        return nullcontext()
    setup[6]['control'].file_lock = lock
    assert start(setup) == 0 and state.starts == []


def test_reuse_only_cannot_start_services(setup):
    setup[0].reuse_only = True
    setup[4].api = False
    with pytest.raises(ValueError, match='reuse_not_proven'):
        start(setup)
    assert setup[4].starts == []


def test_unrelated_record_change_during_start_is_not_overwritten(setup):
    state = setup[4]
    state.api = False
    def lock(path, **kwargs):
        current = setup[0].root/'.local-data/startup-api-current.json'
        changed = json.loads(current.read_bytes())
        changed['source_digest'] = 'c'*64
        current.write_text(json.dumps(changed))
        return nullcontext()
    setup[6]['control'].file_lock = lock
    with pytest.raises(ValueError, match='active_records_changed'):
        start(setup)
    assert state.starts == []


def test_partial_start_failure_does_not_stop_existing_services(setup):
    setup[4].api = False
    setup[4].exited = True
    with pytest.raises(ValueError, match='api_exited'):
        start(setup)
    assert setup[4].site is True
    assert setup[5] == []


def test_frontend_preflight_failure_does_not_start_api(setup):
    setup[4].api = setup[4].site = False
    (Path(setup[2]['dist_path'])/'sapba-build.json').write_text('{"fingerprint":"wrong"}')
    with pytest.raises(ValueError, match='frontend_build_invalid'):
        start(setup)
    assert setup[4].starts == []


def test_slow_start_stops_waiting_without_restart_or_repeated_dispatch(setup, monkeypatch):
    state = setup[4]
    state.api = False
    original_status = simple.status
    monkeypatch.setattr(simple, 'status', lambda url:None if '/api/health' in url else original_status(url))
    clock = [0]
    def sleep(seconds):
        clock[0] += seconds
    monkeypatch.setattr(simple, 'time', SimpleNamespace(monotonic=lambda:clock[0], sleep=sleep, strftime=lambda value:'clock'))
    with pytest.raises(ValueError, match='startup_ready_timeout'):
        start(setup)
    assert len(state.starts) == 1 and clock[0] <= 1.2
    assert setup[5] == []


@pytest.mark.parametrize('value', [[], 'not a health object'])
def test_malformed_health_never_starts_another_service(setup, monkeypatch, value):
    monkeypatch.setattr(simple, 'status', lambda url:value)
    with pytest.raises(ValueError, match='api_response_invalid'):
        start(setup)
    assert setup[4].starts == []


@pytest.mark.parametrize('maintenance', [[], ['--restart'], ['--site-only'], ['--rebuild'], ['--dev']])
def test_plain_main_is_simple_and_explicit_maintenance_keeps_old_entry(tmp_path, monkeypatch, maintenance):
    calls = []
    record = tmp_path / '.local-data/startup-api-current.json'
    record.parent.mkdir(parents=True)
    record.write_text('{}')
    monkeypatch.setattr(simple, 'resolve', lambda root:(Path('python.exe'),Path('frozen-startup.py')))
    monkeypatch.setattr(simple, 'simple_start', lambda *args:calls.append('simple') or 0)
    monkeypatch.setattr(simple.subprocess, 'call', lambda argv,**kwargs:calls.append(argv) or 0)
    assert simple.main(['ensure','--root',str(tmp_path),'--no-browser',*maintenance]) == 0
    if maintenance:
        assert maintenance[0] in calls[0] and 'frozen-startup.py' in calls[0]
    else:
        assert calls == ['simple']


def test_browser_only_opens_after_readiness(setup):
    setup[0].no_browser = False
    assert start(setup) == 0
    assert setup[4].browser == ['http://127.0.0.1:4321/zh/']
