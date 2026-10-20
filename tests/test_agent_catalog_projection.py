import copy
import json
import threading
import time

import pytest

from sap_business_agents_platform.agent_catalog import AgentCatalogProjection, CatalogReadContext, CatalogSourceChanged, management_catalog
from tests.test_agent_lifecycle import _service, _write_active_agent


def ready(projection, previous=0):
    end=time.monotonic()+10
    while time.monotonic()<end:
        value=projection.get()
        if not value['snapshot']['refreshing'] and (value['snapshot']['id']>previous or value['snapshot']['error_code']):
            return value
        time.sleep(.005)
    raise AssertionError('projection did not finish')


@pytest.fixture
def catalog(tmp_path):
    service,store,settings=_service(tmp_path)
    manifest=_write_active_agent(service,tmp_path)
    return service,store,settings,manifest


def test_legacy_management_unchanged_and_request_reads_are_batched(catalog, monkeypatch):
    service,store,_,manifest=catalog
    expected=service._catalog_from_context()
    reads=[]
    original=store.list_agent_authoring_drafts
    monkeypatch.setattr(store,'list_agent_authoring_drafts',lambda: (reads.append(1),original())[1])
    assert service.catalog()==expected
    assert len(reads)==1
    assert management_catalog(service,agent_id=manifest['slug'])==expected
    with pytest.raises(KeyError): management_catalog(service,agent_id='not-found')


def test_summary_uses_no_database_and_unchanged_content_is_not_revalidated(catalog,monkeypatch):
    service,store,_,_=catalog
    monkeypatch.setattr(store,'list_agent_authoring_drafts',lambda: pytest.fail('summary read drafts'))
    projection=AgentCatalogProjection(service)
    try:
        first=ready(projection)
        assert first['snapshot']['available'] and len(first['items'])==1
        assert 'management' not in first['items'][0]
        projection.get(refresh=True); second=ready(projection,first['snapshot']['id'])
        assert second['snapshot']['timings_ms']['manifest_validations']==0
        assert second['snapshot']['timings_ms']['database_reads']==0
    finally: projection.close()


def test_cold_get_and_refresh_do_not_wait_for_repository_write_lock(catalog):
    service,_,_,_=catalog
    projection=AgentCatalogProjection(service)
    try:
        with service.agents.write_transaction():
            start=time.perf_counter(); value=projection.get()
            assert time.perf_counter()-start<.2
            assert value['snapshot']['refreshing'] and not value['snapshot']['available']
        assert ready(projection)['snapshot']['available']
    finally: projection.close()


def test_failed_refresh_keeps_previous_snapshot_and_requires_explicit_retry(catalog):
    service,_,settings,manifest=catalog
    projection=AgentCatalogProjection(service)
    try:
        first=ready(projection); path=settings.repository_root/'agents/Common'/manifest['slug']/'agent.json'
        original=path.read_bytes();path.write_text('{bad',encoding='utf-8')
        projection.get(refresh=True); failed=ready(projection,first['snapshot']['id'])
        assert failed['items']==first['items']
        assert failed['snapshot']['stale'] and failed['snapshot']['error_code']
        assert not projection.get()['snapshot']['refreshing']
        path.write_bytes(original);projection.get(refresh=True)
        assert not ready(projection,first['snapshot']['id'])['snapshot']['error_code']
    finally: projection.close()


def test_external_same_timestamp_change_is_validated_and_deleted_source_removed(catalog):
    service,_,settings,manifest=catalog
    projection=AgentCatalogProjection(service)
    try:
        first=ready(projection);path=settings.repository_root/'agents/Common'/manifest['slug']/'agent.json'
        changed=copy.deepcopy(manifest);changed['title']['zh']='新的名称'
        path.write_text(json.dumps(changed),encoding='utf-8')
        projection.get(refresh=True);second=ready(projection,first['snapshot']['id'])
        assert second['items'][0]['title']['zh']=='新的名称'
        assert second['snapshot']['timings_ms']['manifest_validations']==1
        path.unlink();projection.get(refresh=True);third=ready(projection,second['snapshot']['id'])
        assert third['snapshot']['available'] and third['items']==[]
    finally: projection.close()


def test_single_refresh_invalidation_wins_and_late_result_cannot_publish(catalog,monkeypatch):
    service,_,_,_=catalog;projection=AgentCatalogProjection(service)
    started=threading.Event();release=threading.Event();calls=[]
    build=projection._build
    def slow():
        calls.append(1);started.set();assert release.wait(5);return build()
    monkeypatch.setattr(projection,'_build',slow)
    try:
        projection.get();assert started.wait(5)
        for _ in range(10): projection.get(refresh=True)
        projection.invalidate();release.set()
        value=ready(projection)
        assert value['snapshot']['id']==1 and len(calls)==2
    finally: release.set();projection.close()


def test_read_context_rejects_external_change_before_publication(catalog):
    service,_,settings,manifest=catalog
    context=CatalogReadContext(service.agents);context.list_all()
    path=settings.repository_root/'agents/Common'/manifest['slug']/'agent.json'
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(CatalogSourceChanged): context.finish()


def test_ttl_and_source_generation_invalidate_without_authorization(catalog):
    service,_,_,_=catalog; clock=[0.0]
    projection=AgentCatalogProjection(service,clock=lambda:clock[0])
    try:
        first=ready(projection);clock[0]=61
        assert projection.get()['snapshot']['refreshing']
        second=ready(projection,first['snapshot']['id'])
        with service.agents.write_transaction(): pass
        assert projection.get()['snapshot']['stale']
        assert ready(projection,second['snapshot']['id'])['snapshot']['available']
    finally: projection.close()


def test_new_source_appears_and_illegal_schema_cannot_replace_valid_snapshot(catalog):
    service,_,settings,manifest=catalog; projection=AgentCatalogProjection(service)
    try:
        first=ready(projection)
        directory=settings.repository_root/'agents/Common/second-agent'
        directory.mkdir(); changed=copy.deepcopy(manifest);changed['slug']='second-agent'
        (directory/'agent.json').write_text(json.dumps(changed),encoding='utf-8')
        projection.get(refresh=True);second=ready(projection,first['snapshot']['id'])
        assert len(second['items'])==2
        changed['execution']['inputSchema']={'type':'invalid-schema-type'}
        (directory/'agent.json').write_text(json.dumps(changed),encoding='utf-8')
        projection.get(refresh=True);failed=ready(projection,second['snapshot']['id'])
        assert failed['items']==second['items'] and failed['snapshot']['error_code']
    finally: projection.close()


def test_api_summary_and_single_management_keep_original_routes_compatible(tmp_path):
    from fastapi.testclient import TestClient
    from tests.test_platform_runtime import create_app, _settings, FakePlanner, FakeEmbeddedProvider
    app=create_app(_settings(tmp_path),planner=FakePlanner(),embedded_provider=FakeEmbeddedProvider())
    with TestClient(app) as client:
        end=time.monotonic()+15
        while True:
            response=client.get('/api/agents/catalog?view=summary&state=all')
            assert response.status_code==200
            summary=response.json()
            if summary['snapshot']['available']: break
            assert time.monotonic()<end
            time.sleep(.01)
        full=client.get('/api/agents/catalog?state=all').json()
        assert isinstance(full,list) and len(full)==len(summary['items'])
        key=full[0]['id']
        detail=client.get(f'/api/agents/{key}?view=management')
        assert detail.status_code==200 and detail.json()==full[0]
        manifest=client.get(f'/api/agents/{key}')
        assert manifest.status_code==200 and manifest.json()['slug']==key
        assert 'management' not in summary['items'][0]
        assert client.get('/api/agents/does-not-exist?view=management').status_code==404
