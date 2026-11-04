"""Maintenance contract and real stdio connections, without SAP/model calls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

import pytest

from tests.test_startup_versioning import launcher, orchestrator

c = launcher.control
ROOT = Path(__file__).resolve().parents[1]
COMPONENT = 'sap_business_agents_test_agent.mcp'
BRIDGE = ROOT / 'src/sap_business_agents_test_agent/mcp.py'


def participant(root):
    return c.MCPParticipant(root, COMPONENT, BRIDGE)


def directory(root):
    return root / '.local-data/startup/mcp'


def until(check, timeout=5):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, 'bounded wait timed out'
        time.sleep(.02)


def row(pid):
    return {'ProcessId': pid, 'ParentProcessId': 0, 'CreationDate': '/Date(1000)/',
        'Name': 'python.exe', 'CommandLine': f'python -m {COMPONENT} --repository {ROOT}'}


def test_drain_closes_admissions_and_never_holds_deployment_lock(tmp_path):
    accepted, release, switched = threading.Event(), threading.Event(), threading.Event()
    failures = []
    with participant(tmp_path) as p:
        def call():
            with p.operation():
                accepted.set()
                assert release.wait(5)
        def maintain():
            try:
                with c.mcp_maintenance(tmp_path, timeout=5):
                    assert release.is_set()
                    switched.set()
            except BaseException as error: failures.append(error)
        worker = threading.Thread(target=call)
        controller = threading.Thread(target=maintain)
        worker.start(); assert accepted.wait(5)
        controller.start()
        try:
            until(lambda: (directory(tmp_path) / 'maintenance.json').exists())
            assert not switched.is_set()
            with c.file_lock(tmp_path / '.local-data/deployment.lock'): pass
            with pytest.raises(c.StartupBlocked, match='mcp_maintenance_pending'):
                with p.operation(): pytest.fail('new call accepted during drain')
        finally:
            release.set(); worker.join(5); controller.join(5)
        assert not failures and switched.is_set()
        with p.operation(): pass
    assert not (directory(tmp_path) / 'maintenance.json').exists()


def test_live_nonce_is_required_and_source_bound(tmp_path):
    with participant(tmp_path) as p:
        assert c.mcp_quiescent_pids(tmp_path, [row(os.getpid())], ROOT) == set()
        with c.mcp_maintenance(tmp_path):
            assert c.mcp_quiescent_pids(tmp_path, [row(os.getpid())], ROOT) == {os.getpid()}
            original = p.record.copy()
            p.record = {**p.record, 'proof': {**p.record['proof'], 'src/sap_business_agents_platform/startup_control.py': '0'*64}}
            c._mcp_write(p.path, p.record)
            assert c.mcp_quiescent_pids(tmp_path, [row(os.getpid())], ROOT) == set()
            p.record = original; c._mcp_write(p.path, original)


@pytest.mark.parametrize('field,value', [('protocol','old/0'), ('root','elsewhere'),
    ('pid', 999999), ('birth','reused-pid'), ('component','unknown.module'), ('session','../escape')])
def test_tampered_registration_never_exempts_writer(tmp_path, field, value):
    with participant(tmp_path) as p, c.mcp_maintenance(tmp_path):
        c._mcp_write(p.path, {**p.record, field:value})
        assert c.mcp_quiescent_pids(tmp_path, [row(os.getpid())], ROOT) == set()
        c._mcp_write(p.path, p.record)


@pytest.mark.parametrize('field,value', [('CommandLine','python unrelated.py'), ('Name','node.exe')])
def test_command_and_native_process_identity_must_agree(tmp_path, field, value):
    with participant(tmp_path), c.mcp_maintenance(tmp_path):
        assert c.mcp_quiescent_pids(tmp_path, [{**row(os.getpid()), field:value}], ROOT) == set()


def test_stale_ack_is_not_live_and_failure_keeps_writer_guard(tmp_path):
    with participant(tmp_path) as p:
        p.stop.set(); p.thread.join(2)
        c._mcp_write(directory(tmp_path) / (p.session + '.ack.json'), {**p.record, 'challenge':'stale'})
        with c.mcp_maintenance(tmp_path):
            assert c.mcp_quiescent_pids(tmp_path, [row(os.getpid())], ROOT) == set()


def test_timeout_releases_pause_without_cancelling_call(tmp_path):
    with participant(tmp_path) as p, p.operation():
        with pytest.raises(c.StartupBlocked, match='mcp_drain_timeout'):
            with c.mcp_maintenance(tmp_path, timeout=.05): pytest.fail('active operation lost')
        assert not (directory(tmp_path) / 'maintenance.json').exists()
    with participant(tmp_path) as p, p.operation(): pass


def test_cancel_can_drain_calls_but_cannot_race_exclusive_switch(tmp_path):
    with participant(tmp_path) as p, p.operation():
        c._mcp_write(directory(tmp_path) / 'maintenance.json', {'pause':True})
        with p.operation(cancel=True): pass
        with pytest.raises(c.StartupBlocked, match='maintenance_pending'):
            with p.operation(): pytest.fail('new work accepted')
        (directory(tmp_path) / 'maintenance.json').unlink()
    with participant(tmp_path) as p, c.mcp_maintenance(tmp_path):
        with pytest.raises(c.StartupBlocked, match='coordination_busy'):
            with p.operation(cancel=True): pytest.fail('cancel raced exclusive switch')


def test_concurrent_launcher_cannot_take_or_clear_other_owner(tmp_path):
    with c.mcp_maintenance(tmp_path):
        marker = (directory(tmp_path) / 'maintenance.json').read_bytes()
        with pytest.raises(c.StartupBlocked, match='coordination_busy'):
            with c.mcp_maintenance(tmp_path): pytest.fail('two owners')
        assert (directory(tmp_path) / 'maintenance.json').read_bytes() == marker


@pytest.mark.parametrize('unresolved', [False, True])
def test_interrupt_preserves_unresolved_service_transaction(tmp_path, unresolved):
    with pytest.raises(KeyboardInterrupt):
        with c.mcp_maintenance(tmp_path):
            if unresolved: c.atomic(tmp_path / '.local-data/startup-maintenance.json', {'pid':os.getpid()})
            raise KeyboardInterrupt()
    assert (directory(tmp_path) / 'maintenance.json').exists() is unresolved


@pytest.mark.parametrize('journal', [None, 'startup-maintenance.json', 'startup/pending-switch.json'])
def test_orphan_pause_recovery_requires_resolved_original_transaction(tmp_path, journal):
    d = c._mcp_directory(tmp_path)
    c._mcp_write(d / 'maintenance.json', {'protocol': c.MCP_PROTOCOL, 'root':str(tmp_path.resolve()),
        'pid':os.getpid(), 'birth':'old-reused-identity', 'challenge':'old'})
    if journal:
        c.atomic(tmp_path / '.local-data' / journal, {'pid':123})
        with pytest.raises(c.StartupBlocked, match='switch_recovery_required'):
            with c.mcp_maintenance(tmp_path): pytest.fail('unresolved journal ignored')
        assert (d / 'maintenance.json').exists()
    else:
        with c.mcp_maintenance(tmp_path): pass
        assert not (d / 'maintenance.json').exists()


def test_live_owner_record_is_never_stolen(tmp_path):
    d = c._mcp_directory(tmp_path)
    c._mcp_write(d / 'maintenance.json', {'protocol': c.MCP_PROTOCOL, 'root':str(tmp_path.resolve()),
        'pid':os.getpid(), 'birth':c.process_stamp(os.getpid()), 'challenge':'old'})
    with pytest.raises(c.StartupBlocked, match='owner_alive'):
        with c.mcp_maintenance(tmp_path): pytest.fail('live owner stolen')


@pytest.mark.parametrize('rollback_fails', [False, True])
def test_failed_switch_reopens_mcp_only_after_successful_rollback(orchestrator, monkeypatch, rollback_fails):
    args,state,_,data = orchestrator
    def ready(url,digest,process,timeout):
        if digest=='target-source' or rollback_fails: raise c.StartupBlocked('injected_health_failure')
    monkeypatch.setattr(launcher, 'wait_api', ready)
    with pytest.raises(c.StartupBlocked): launcher.ensure(args)
    assert (directory(args.root) / 'maintenance.json').exists() is rollback_fails
    assert (data / 'startup-maintenance.json').exists() is rollback_fails


class Stream:
    def __init__(self, tmp_path, module=COMPONENT, program=None):
        self.root = tmp_path / 'repository'; self.root.mkdir()
        (self.root / 'src').mkdir()
        (self.root / 'src/value.py').write_text('value=1\n')
        subprocess.run(['git', 'init', '--template=', str(self.root)], check=True,
            capture_output=True, stdin=subprocess.DEVNULL)
        self.messages = queue.Queue()
        self.errors = (tmp_path / 'stderr.log').open('wb')
        env = {**os.environ, 'PYTHONPATH':str(ROOT / 'src'), 'PYTHONUTF8':'1',
            'PYTHONDONTWRITEBYTECODE':'1', 'SAPBA_STARTUP_REPOSITORY':str(self.root)}
        args = ['--repository',str(self.root)] if module==COMPONENT else ['--mode','authoring']
        command = [sys.executable, '-B', '-c', program, str(self.root)] if program else [sys.executable, '-B', '-m', module, *args]
        self.process = subprocess.Popen(command,
            cwd=tmp_path, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.reader = threading.Thread(target=self.receive, daemon=True); self.reader.start()
    def receive(self):
        for line in self.process.stdout: self.messages.put(json.loads(line))
    def request(self, identifier, method, params=None):
        self.submit(identifier, method, params)
        response = self.messages.get(timeout=20)
        assert response['id']==identifier and 'error' not in response, response
        assert self.process.poll() is None
        return response['result']
    def submit(self, identifier, method, params=None):
        request = {'jsonrpc':'2.0', 'id':identifier, 'method':method}
        if params is not None: request['params'] = params
        self.process.stdin.write((json.dumps(request)+'\n').encode()); self.process.stdin.flush()
    def tool(self, identifier, name='test_inventory', arguments=None):
        return self.request(identifier, 'tools/call', {'name':name, 'arguments':arguments or {}})
    def close(self):
        self.process.stdin.close()
        try: self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait(timeout=5)
        self.reader.join(2); self.errors.close()


@pytest.mark.parametrize('module', [COMPONENT, 'sap_business_agents_platform.mcp_server'])
def test_actual_mcp_transport_survives_pause_and_resumes(tmp_path, module):
    stream = Stream(tmp_path, module)
    try:
        stream.request(1, 'initialize')
        before = stream.request(2, 'tools/list')
        pid = stream.process.pid
        # Windows venv executable may be a parent shim. Handshake the registered
        # interpreter PID, never infer child ownership from a reused parent PID.
        until(lambda: any(directory(stream.root).glob('[0-9]*.json')))
        registered = json.loads(next(directory(stream.root).glob('[0-9]*.json')).read_bytes())
        own_row = {**row(registered['pid']), 'CommandLine':f'python -m {module} --repository {stream.root}'}
        with c.mcp_maintenance(stream.root):
            assert c.mcp_quiescent_pids(stream.root, [own_row], ROOT) == {registered['pid']}
            stream.request(3, 'ping')
            assert stream.request(4, 'tools/list') == before
            name = 'test_prepare' if module==COMPONENT else before['tools'][0]['name']
            result = stream.tool(5, name)
            assert result['isError'] and result['structuredContent']['accepted'] is False
            assert result['structuredContent']['retryable'] is True
            assert not (stream.root / '.local-data/auto-test/state.sqlite3').exists()
        assert stream.process.pid == pid and stream.process.poll() is None
        stream.request(6, 'ping')
        if module==COMPONENT: assert not stream.tool(7)['isError']
        else:
            result = stream.tool(7, name)
            assert result['structuredContent']['code'] == 'harness_broker_unconfigured'
        assert stream.request(8, 'tools/list') == before
    finally: stream.close()
    assert not list(directory(stream.root).glob('[0-9]*.json'))


def test_real_participant_passes_writer_guard_but_unknown_process_does_not(tmp_path, monkeypatch):
    stream = Stream(tmp_path)
    try:
        stream.request(1, 'initialize')
        registered = json.loads(next(directory(stream.root).glob('[0-9]*.json')).read_bytes())
        rows = [{**row(registered['pid']), 'CommandLine':f'python -m {COMPONENT} --repository {stream.root}'}]
        monkeypatch.setattr(launcher.subprocess, 'check_output', lambda *a,**k:json.dumps(rows))
        with c.mcp_maintenance(stream.root):
            launcher.assert_writers(stream.root, [])
            rows.append({'ProcessId':999999, 'ParentProcessId':0, 'Name':'python.exe',
                'CreationDate':'/Date(0)/', 'CommandLine':f'python {stream.root}/unknown.py'})
            with pytest.raises(c.StartupBlocked, match='other_writers_active:999999'):
                launcher.assert_writers(stream.root, [])
    finally: stream.close()


def test_queued_calls_are_admitted_before_dispatch_and_new_calls_reject_immediately(tmp_path):
    program = '''
import sys,time,uuid
from pathlib import Path
import sap_business_agents_test_agent.mcp as m
root=Path(sys.argv[1]); original=m.invoke
def invoke(store,name,args):
    if name=='test_status' and args.get('task_id')=='hold':
        (root/('entered-'+uuid.uuid4().hex)).touch()
        while not (root/'release').exists(): time.sleep(.02)
        return {'completed':True}
    return original(store,name,args)
m.invoke=invoke
sys.argv=['mcp','--repository',str(root)]; m.main()
'''
    stream = Stream(tmp_path, program=program)
    done = threading.Event()
    failures = []
    def maintain():
        try:
            with c.mcp_maintenance(stream.root, timeout=10):
                assert (stream.root/'release').exists()
        except BaseException as error: failures.append(error)
        finally: done.set()
    controller = threading.Thread(target=maintain)
    try:
        stream.request(1, 'initialize')
        for i in range(4): stream.submit(10+i, 'tools/call', {'name':'test_status','arguments':{'task_id':'hold'}})
        until(lambda: len(list(stream.root.glob('entered-*')))==4)
        controller.start()
        until(lambda: (directory(stream.root)/'maintenance.json').exists())
        stream.submit(20, 'tools/call', {'name':'test_prepare','arguments':{}})
        rejected = stream.messages.get(timeout=2)
        assert rejected['id']==20 and rejected['result']['structuredContent']['accepted'] is False
        assert not done.is_set()
    finally:
        (stream.root/'release').touch()
        if controller.ident: controller.join(12)
        stream.close()
    assert not failures and done.is_set()


def test_cancel_transport_remains_available_while_all_tool_workers_drain(tmp_path):
    program = '''
import sys,time,uuid
from pathlib import Path
import sap_business_agents_test_agent.mcp as m
root=Path(sys.argv[1]); original=m.invoke
def invoke(store,name,args):
    if name=='test_status':
        (root/('entered-'+uuid.uuid4().hex)).touch()
        while not (root/'release').exists(): time.sleep(.02)
        return {'completed':True}
    if name=='test_cancel':
        (root/'release').touch()
        return {'cancelled':True}
    return original(store,name,args)
m.invoke=invoke
sys.argv=['mcp','--repository',str(root)]; m.main()
'''
    stream = Stream(tmp_path, program=program)
    finished = threading.Event()
    errors = []
    def maintain():
        try:
            with c.mcp_maintenance(stream.root, timeout=10): pass
        except BaseException as error: errors.append(error)
        finally: finished.set()
    controller = threading.Thread(target=maintain)
    try:
        stream.request(1, 'initialize')
        for i in range(4): stream.submit(10+i, 'tools/call', {'name':'test_status','arguments':{'task_id':'hold'}})
        until(lambda: len(list(stream.root.glob('entered-*')))==4)
        controller.start()
        until(lambda: (directory(stream.root)/'maintenance.json').exists())
        stream.submit(20, 'tools/call', {'name':'test_cancel','arguments':{'task_id':'hold'}})
        messages = [stream.messages.get(timeout=5) for _ in range(5)]
        cancelled = next(item for item in messages if item['id']==20)
        assert cancelled['result']['structuredContent']['cancelled'] is True
        assert finished.wait(5) and not errors
    finally:
        (stream.root/'release').touch()
        if controller.ident: controller.join(12)
        stream.close()
