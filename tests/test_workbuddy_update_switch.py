"""Failure-boundary tests: controlled threads and records, no provider or SAP."""
import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.workbuddy_online_update import UpdateBackend
from sap_business_agents_platform.workbuddy_update_switch import (
    load_anchor, observation, save_immutable, settle, switch,
)


class Owner:
    def __init__(self, root, *, notification_failure=False):
        self.root = root
        self.record = {'operation_id': 'recovery_'+'a'*32, 'source_environment_digest': 'old',
                       'status': 'running', 'cleanup_complete': True}
        self.held_barrier = None
        self.held_guard = SimpleNamespace(released=False)
        self.state = {'environment_digest': 'old'}
        self.account = {'environment': 'old'}
        self.registry = SimpleNamespace(workbuddy=SimpleNamespace(environment=SimpleNamespace(state=lambda:self.state)),
            authentication_service=SimpleNamespace(_account=lambda _:self.account))
        self.notified = threading.Event()
        self.notification_failure = notification_failure
        self.changes = []
        self.lock = threading.RLock()

    def emit(self, **change):
        with self.lock:
            if change.get('status') == 'needs_review':
                self.notified.set()
                if self.notification_failure:
                    self.notification_failure = False
                    raise OSError('synthetic journal unavailable')
            self.record.update(copy.deepcopy(change))
            self.changes.append(copy.deepcopy(change))


class Backend:
    def __init__(self, owner, *, block=None, fail_health=False, fail_rollback=False, fail_leave=False):
        self.owner = owner
        self.record = owner.record
        self.switch_safe = True
        self.started = threading.Event()
        self.release = threading.Event()
        self.active_writer = threading.Event()
        self.events = []
        self.block = block
        self.fail_health = fail_health
        self.fail_rollback = fail_rollback
        self.fail_leave = fail_leave

    def anchor(self, receipt, audit):
        return {'operation_id':self.record['operation_id'], 'source_environment_digest':'old',
                'target_environment_digest':'new', 'backend':{
                    'old_state':{'environment_digest':'old'}, 'old_account':{'environment':'old'},
                    'old_snapshot':{'environment_digest':'old'}, 'old_certificates':[{'id':'source-cert'}],
                    'old_operations':{'free_query':['bounded']}, 'old_certificate':{'id':'source-cert'},
                    'old_check':{'source_job_id':'old-model-check'}},
                'target_account':{'environment':'new'}}

    def enter_switch(self):
        self.switch_safe = False
        self.events.append('barrier_acquired')
        return object()

    def pause(self, stage):
        if self.block == stage:
            self.started.set()
            assert self.release.wait(5), 'test failed to release controlled writer'

    def activate(self, receipt, audit):
        self.active_writer.set()
        try:
            self.pause('before_environment')
            self.owner.state['environment_digest'] = 'new'
            self.events.append('environment_written')
            self.pause('before_account')
            self.owner.account = {'environment':'new'}
            self.events.append('account_written')
            self.pause('after_account')
        finally:
            self.active_writer.clear()
            self.events.append('writer_finished')

    def health(self, receipt, audit):
        self.active_writer.set()
        try:
            self.events.append('health')
            self.pause('during_health')
            if self.fail_health:
                raise ValueError('synthetic health failure')
            self.switch_safe = True
        finally:
            self.active_writer.clear()
            self.events.append('health_writer_finished')

    async def rollback(self, emit):
        assert not self.active_writer.is_set(), 'rollback overlapped active writer'
        self.switch_safe = False
        self.events.append('rollback')
        if self.fail_rollback:
            raise ValueError('synthetic rollback failure')
        self.owner.state['environment_digest'] = 'old'
        self.owner.account = {'environment':'old'}
        self.switch_safe = True

    def cleanup_complete(self):
        return True

    def leave_switch(self, barrier):
        assert not self.active_writer.is_set(), 'released barrier over active writer'
        if self.fail_leave:
            self.events.append('barrier_release_failed')
            raise OSError('synthetic barrier cleanup failure')
        self.events.append('barrier_released')


RECEIPT = {'environment_digest':'new','sdk_version':'0.3.291','cli_version':'2.177.0'}


@pytest.mark.parametrize('boundary',['before_environment','before_account','after_account','during_health'])
@pytest.mark.parametrize('kind',['cancel','timeout'])
def test_cancel_or_timeout_drains_writer_before_rollback(tmp_path, monkeypatch, boundary, kind):
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_job.process_identity',lambda _: 'synthetic-process')
    async def run():
        owner = Owner(tmp_path)
        backend = Backend(owner,block=boundary)
        timeout = asyncio.timeout(None)
        async def operation():
            async with timeout:
                await switch(owner,backend,RECEIPT,None)
        task = asyncio.create_task(operation())
        try:
            assert await asyncio.to_thread(backend.started.wait,3)
            if kind == 'cancel':
                task.cancel()
            else:
                timeout.reschedule(asyncio.get_running_loop().time())
            assert await asyncio.to_thread(owner.notified.wait,3)
            await asyncio.sleep(0)
            assert not task.done()
            assert backend.active_writer.is_set()
            assert 'rollback' not in backend.events and 'barrier_released' not in backend.events
            assert owner.held_barrier is not None
            assert owner.record['status'] == 'needs_review'
            backend.release.set()
            with pytest.raises(asyncio.CancelledError if kind == 'cancel' else TimeoutError):
                await task
            assert owner.state['environment_digest'] == 'old'
            assert backend.events.index('writer_finished') < backend.events.index('rollback') < backend.events.index('barrier_released')
            if boundary == 'during_health':
                assert backend.events.index('health_writer_finished') < backend.events.index('rollback')
            assert owner.record['status'] == 'rolled_back'
            assert owner.held_barrier is None
        finally:
            backend.release.set()
            if not task.done():
                await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize('rollback_fails',[False,True])
def test_health_failure_rolls_back_or_keeps_barrier(tmp_path,monkeypatch,rollback_fails):
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_job.process_identity',lambda _: 'synthetic-process')
    async def run():
        owner=Owner(tmp_path)
        backend=Backend(owner,fail_health=True,fail_rollback=rollback_fails)
        with pytest.raises(ValueError):
            await switch(owner,backend,RECEIPT,None)
        if rollback_fails:
            assert owner.held_barrier is not None
            assert 'barrier_released' not in backend.events
            assert owner.state['environment_digest']=='new'
        else:
            assert owner.held_barrier is None
            assert owner.state['environment_digest']=='old'
            assert owner.account=={'environment':'old'}
            assert owner.record['status']=='rolled_back'
    asyncio.run(run())


@pytest.mark.parametrize('fail_health',[False,True])
def test_barrier_release_failure_never_publishes_completed_or_releases_guard(tmp_path,monkeypatch,fail_health):
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_job.process_identity',lambda _: 'synthetic-process')
    async def run():
        owner=Owner(tmp_path)
        guard=owner.held_guard
        backend=Backend(owner,fail_health=fail_health,fail_leave=True)
        with pytest.raises(OSError,match='barrier cleanup failure'):
            await switch(owner,backend,RECEIPT,None)
        assert all(change.get('status')!='completed' for change in owner.changes)
        assert owner.record['status']=='cleanup_pending'
        assert owner.record['cleanup_complete'] is False
        assert owner.record['error']=={'code':'runtime_cleanup_incomplete','phase':'cleanup'}
        assert backend.switch_safe is False
        assert owner.held_barrier is not None
        assert owner.held_guard is guard and guard.released is False
        assert 'barrier_release_failed' in backend.events
        assert 'barrier_released' not in backend.events
    asyncio.run(run())


def test_completed_is_published_only_after_barrier_release(tmp_path,monkeypatch):
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_job.process_identity',lambda _: 'synthetic-process')
    async def run():
        owner=Owner(tmp_path)
        backend=Backend(owner)
        original_emit=owner.emit
        def observed_emit(**change):
            if change.get('status')=='completed':
                assert 'barrier_released' in backend.events
                assert owner.held_barrier is None
            original_emit(**change)
        owner.emit=observed_emit
        await switch(owner,backend,RECEIPT,None)
        assert owner.record['status']=='completed'
    asyncio.run(run())


def test_restart_between_environment_and_account_uses_saved_source(tmp_path,monkeypatch):
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_job.process_identity',lambda _:None)
    owner=Owner(tmp_path)
    backend=Backend(owner)
    anchor=backend.anchor(RECEIPT,None)
    ref=save_immutable(tmp_path/owner.record['operation_id']/'rollback-anchor.json',anchor)
    owner.record['rollback_anchor']=ref
    owner.record['switch_writer']={'pid':1234,'identity':'dead-writer','state':'started'}
    owner.state['environment_digest']='new'  # persisted environment, old account
    record_before=copy.deepcopy(owner.record)
    files_before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result=observation(owner.registry,tmp_path,owner.record)
    assert result=={'environment':'target','account':'source','writer_may_be_alive':False,'requires_explicit_review':True}
    restored=UpdateBackend(owner.registry,owner.record,anchor=load_anchor(tmp_path,owner.record))
    assert restored.old_state['environment_digest']=='old'
    assert restored.old_snapshot['environment_digest']=='old'
    assert restored.old_check['source_job_id']=='old-model-check'
    assert owner.record==record_before
    assert {p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}==files_before


def test_anchor_tamper_is_not_adopted_as_current_source(tmp_path):
    owner=Owner(tmp_path)
    anchor=Backend(owner).anchor(RECEIPT,None)
    path=tmp_path/owner.record['operation_id']/'rollback-anchor.json'
    owner.record['rollback_anchor']=save_immutable(path,anchor)
    anchor['source_environment_digest']='new'
    path.write_text(json.dumps(anchor))
    result=observation(owner.registry,tmp_path,owner.record)
    assert result['environment']=='unconfirmed'
    assert result['error']=='workbuddy_update_anchor_invalid'
    assert owner.state['environment_digest']=='old'


def test_cancellation_notification_failure_still_drains_writer(tmp_path,monkeypatch):
    """A failed diagnostic write cannot abandon a live committing thread."""
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_job.process_identity',lambda _: 'synthetic-process')
    async def run():
        owner=Owner(tmp_path,notification_failure=True)
        backend=Backend(owner,block='before_environment')
        task=asyncio.create_task(switch(owner,backend,RECEIPT,None))
        try:
            assert await asyncio.to_thread(backend.started.wait,3)
            task.cancel()
            assert await asyncio.to_thread(owner.notified.wait,3)
            # Let the cancellation handler and its finally blocks run while
            # the actual writer remains blocked by our event.
            for _ in range(10):
                await asyncio.sleep(0)
            assert not task.done(), 'journal failure abandoned an active writer'
            assert 'rollback' not in backend.events and 'barrier_released' not in backend.events
            backend.release.set()
            result=await asyncio.gather(task,return_exceptions=True)
            assert isinstance(result[0],BaseException)
            assert not backend.active_writer.is_set()
            assert backend.events.index('writer_finished') < backend.events.index('rollback')
        finally:
            backend.release.set()
            await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run())
