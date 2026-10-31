"""Real gates and coordinator methods; only native SDK/auth/SAP adapters are synthetic."""
import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.runtime import RuntimeRouter, RuntimeUnavailableError
from sap_business_agents_platform.runtime_admission import tracing, measure, current_trace, RuntimeAdmissionRequest, prepare
from sap_business_agents_platform.sdk_manager import SDKManager, SDKManagerError
from tests.test_sdk_manager import _write_runtime_registry, FakeAdapter, FakeRuntimeProbe


def manager(tmp_path):
    registry=tmp_path/'sdks.json';_write_runtime_registry(registry)
    value=SDKManager(registry,tmp_path,adapters={'python':FakeAdapter()},
        runtime_probes={'codex':FakeRuntimeProbe()},selection_path=tmp_path/'selection.json')
    asyncio.run(value.check_provider('codex'))
    asyncio.run(value.refresh_models('codex'))
    asyncio.run(value.set_default_model('codex','test-model'))
    return value


def test_modern_codex_preparation_reads_status_once_with_same_frozen_result(tmp_path,monkeypatch):
    value=manager(tmp_path);router=RuntimeRouter(value,{'codex':object()})
    counts=[];original=value._snapshot
    def snapshot(*args,**kwargs):
        counts.append(args[0].provider_id)
        return original(*args,**kwargs)
    monkeypatch.setattr(value,'_snapshot',snapshot)
    before=(tmp_path/'selection.json').read_bytes()
    with tracing('submission') as trace:
        result=asyncio.run(prepare(router,RuntimeAdmissionRequest(operation='free_query')))
    assert counts==['codex']
    assert result['model']=='test-model' and result['reasoning_effort']=='medium'
    assert (tmp_path/'selection.json').read_bytes()==before
    assert trace.counts['status']==1 and trace.counts['consistency']==1
    assert 'admission;dur=' in trace.header()


@pytest.mark.parametrize('change',['disabled','model','installed'])
def test_new_gate_preserves_existing_rejections(tmp_path,change):
    value=manager(tmp_path);router=RuntimeRouter(value,{'codex':object()})
    if change=='disabled':value.set_enabled('codex',False)
    if change=='model':value._runtime_config['model_checks'].clear()
    if change=='installed':value.adapters['python'].installed=None
    with pytest.raises(RuntimeUnavailableError) as error:
        router.snapshot()
    assert error.value.code==('runtime_default_not_configured' if change=='disabled' else 'runtime_not_selectable')


def test_end_fence_rejects_install_change_without_retry(tmp_path,monkeypatch):
    value=manager(tmp_path);original=value.runtime_snapshot;calls=[]
    def changed(provider):
        calls.append(provider);result=original(provider)
        value.adapters['python'].installed='9.9.9'
        return result
    monkeypatch.setattr(value,'runtime_snapshot',changed)
    with pytest.raises(SDKManagerError,match='changed during admission'):
        value.prepare_runtime_snapshot('codex')
    assert calls==['codex']


def test_profile_is_diagnostic_only_and_does_not_reuse_authority_across_thread_or_queue(tmp_path):
    value=manager(tmp_path);router=RuntimeRouter(value,{'codex':object()})
    with tracing('submission') as first:
        asyncio.run(prepare(router,RuntimeAdmissionRequest(operation='free_query')))
    value.set_enabled('codex',False)
    with tracing('dispatch') as second:
        with pytest.raises(RuntimeUnavailableError):
            asyncio.run(prepare(router,RuntimeAdmissionRequest(operation='free_query')))
    assert first.trace_id!=second.trace_id
    assert first.closed and second.closed and current_trace() is None
    assert 'test-model' not in json.dumps(first.payload())


def test_closed_profile_cannot_be_changed_by_late_background_work():
    with tracing('submission') as trace:
        with measure('status'):pass
    original=copy.deepcopy(trace.payload()['counts'])
    thread=threading.Thread(target=lambda:trace.add('status',100));thread.start();thread.join()
    assert trace.counts==original


def test_coordinator_forwards_timing_without_copying_child_budget_to_workflow_parent(tmp_path):
    from sap_business_agents_platform.database import RunStore
    from sap_business_agents_platform.engine import RunCoordinator
    from sap_business_agents_platform.models import RunCreate
    store=RunStore(tmp_path/'data.sqlite3')
    store.create_run('parent',RunCreate(mode='free_query',query='fixture'))
    store.set_progress('parent',hard_limit_seconds=3600,elapsed_seconds=400)
    store.create_run('child',RunCreate(mode='free_query',query='fixture'),parent_run_id='parent',node_id='n1')
    owner=RunCoordinator.__new__(RunCoordinator);owner.store=store
    owner._set_progress('child',phase='preparing',state='waiting_input',elapsed_seconds=48,
        hard_limit_seconds=1800,deadline_phase='querying',extension_count=0)
    child=store.get_run('child').progress;parent=store.get_run('parent').progress
    assert child.elapsed_seconds==48 and child.hard_limit_seconds==1800
    assert child.next_deadline_at is None
    assert parent.elapsed_seconds==400 and parent.hard_limit_seconds==3600
