"""Synthetic platform receipts only: no SAP, SDK, auth or current-store writes."""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.factory import AgentDraftService, DraftError
from sap_business_agents_platform.harness import _presentation_hash
from sap_business_agents_platform.models import (Completeness, HarnessResult, RunCreate, RunMode,
                                                RunPresentation, RunResult, RunStatus, RuntimeSnapshot)
from sap_business_agents_platform.pending_draft_source import digest
from tests.test_free_query_history import PLAN
from tests.test_platform_runtime import _settings, FakePlanner, FakeEmbeddedProvider


class NoAuthor:
    def supports(self, *a):
        raise AssertionError('Pending scaffold must not call the SDK')
    async def author_draft(self, **kw):
        raise AssertionError('Pending scaffold must not call the model')


def draft_count(store):
    with store._connect() as db:
        return db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0]


def seed_pending(settings, store, *, provider='workbuddy', run_id='synthetic', session_id='session', accept=True,
                 source_complete=True, business_complete=False):
    runtime = RuntimeSnapshot(provider_id=provider, sdk_id=provider, model='synthetic-model', configuration_digest='frozen')
    store.create_run(run_id, RunCreate(mode=RunMode.free_query, query='Synthetic readonly query'), query_origin='user')
    raw = json.dumps({'rows':[{'SyntheticKey':'PRIVATE-ROW-NEVER-COPY'}]}, ensure_ascii=False, sort_keys=True).encode()
    ref = 'ev_' + hashlib.sha256(raw).hexdigest()[:24]
    path = settings.data_root/'harness'/run_id/'evidence'; path.mkdir(parents=True)
    (path/(ref+'.json')).write_bytes(raw)
    (path/(ref+'.meta.json')).write_text(json.dumps({'source_type':'sap_live', 'claim_scope':'customer_business_fact',
                                                  'source_complete':source_complete}))
    store.begin_harness_tool_call(call_id=run_id+'-read', run_id=run_id, tool_name='sap_query_execute', request_hash='read', safe_input={'plan':PLAN})
    store.complete_harness_tool_call(run_id+'-read', status='completed', evidence_ref=ref,
        output={'ok':True,'evidence_ref':ref,'source_type':'sap_live','source_complete':source_complete})
    presentation = RunPresentation(title={'zh':'合成结果','en':'Synthetic result'}, blocks=[
        {'type':'notice','text':{'zh':'业务证据不足','en':'Business evidence incomplete'},'evidence_refs':[ref]}])
    report_hash = _presentation_hash(presentation)
    presentation.validation_ref = 'validation_' + report_hash.removeprefix('sha256:')[:24]
    store.begin_harness_tool_call(call_id=run_id+'-report', run_id=run_id, tool_name='sap_final_report_validate',
        request_hash='report', safe_input={'report':presentation.model_dump(mode='json')})
    store.complete_harness_tool_call(run_id+'-report', status='completed', output={'ok':True,
        'validation_ref':presentation.validation_ref, 'report_hash':report_hash,
        '_validated_report':presentation.model_dump(mode='json')})
    plan={'kind':'sap_business_agents_harness','runtime':'synthetic'}
    result=RunResult(run_id=run_id,mode=RunMode.free_query,plan=plan,query='Synthetic readonly query',
        presentation=presentation,evidence=[{'evidence_ref':ref, 'source_type':'sap_live','source_complete':source_complete}],
        harness=HarnessResult(stop_reason='completed'), completeness=Completeness(source_complete=source_complete,
        business_complete=business_complete,missing_evidence=['source_semantics_unconfirmed','unknown_evidence_reference_rejected']), runtime=runtime)
    store.update_run(run_id,status=RunStatus.inconclusive,plan_json=plan,result_json=result,
                     runtime_json=runtime,thread_id='synthetic-thread',completed_at='2030-01-31T00:00:00+00:00')
    store.append_event(run_id,'harness_completed',{'stop_reason':'completed'})
    store.create_free_query_session(session_id=session_id,run_id=run_id,original_query='Synthetic readonly query',runtime=runtime.model_dump(mode='json'))
    refresh_digest(store, run_id, session_id, accept=accept)
    return ref


def refresh_digest(store, run_id='synthetic', session_id='session', *, accept=True):
    value=digest(store.get_run(run_id).result.model_dump(mode='json'))
    with store._connect() as db:
        db.execute('UPDATE free_query_iterations SET result_digest=? WHERE run_id=?',(value,run_id))
    store.update_free_query_session(session_id,status='satisfied' if accept else 'reviewing',
        accepted_iteration=1 if accept else None,accepted_result_digest=value if accept else None)
    return value


@pytest.mark.parametrize('provider',['codex','workbuddy'])
@pytest.mark.parametrize('source_complete',[True,False])
def test_confirmed_incomplete_creates_offline_scaffold_without_pass(tmp_path,provider,source_complete):
    settings=_settings(tmp_path); store=RunStore(settings.data_root/'platform.sqlite3')
    ref=seed_pending(settings,store,provider=provider,source_complete=source_complete)
    service=AgentDraftService(settings,store,NoAuthor()); before=store.get_run('synthetic').model_dump(mode='json')
    projection=service.source_projection('synthetic')
    assert projection['can_create_draft'] and projection['draft_creation_mode']=='needs_review'
    draft=asyncio.run(service.create_from_session('session',module='MM'))
    assert draft.status=='needs_review' and not draft.validation['valid']
    provenance=draft.origin['pending_review']
    assert provenance['evidence_refs']==[ref] and not provenance['business_complete']
    assert provenance['source_complete']==source_complete
    assert provenance['qualification']=='NOT_TESTED' and provenance['executable'] is False
    assert {'complete_business_output','implement_deterministic_rules','run_trial','complete_formal_acceptance'} <= set(provenance['todos'])
    assert 'unknown_evidence_reference_rejected' in provenance['evidence_gaps']
    texts='\n'.join(p.read_text(encoding='utf-8') for p in Path(draft.path).rglob('*') if p.is_file())
    assert 'PRIVATE-ROW-NEVER-COPY' not in texts and 'synthetic-thread' not in texts
    manifest=json.loads((Path(draft.path)/'agent.json').read_text(encoding='utf-8'))
    assert manifest['validation']=={'verdict':'NOT_TESTED','executable':False}
    assert manifest['execution']['steps'][0]['request']['plan']==PLAN
    assert store.get_run('synthetic').model_dump(mode='json')==before
    assert asyncio.run(service.create_from_session('session',module='FI')).draft_id==draft.draft_id
    assert draft_count(store)==1


@pytest.mark.parametrize('fault,reason',[
    ('unconfirmed','confirmed_source_required'),('digest','confirmed_source_required'),
    ('cleanup','source_terminal_or_cleanup_unconfirmed'),('handoff','source_terminal_or_cleanup_unconfirmed'),
    ('cancel','source_terminal_or_cleanup_unconfirmed'),('report','source_report_not_verified'),
    ('tamper','source_evidence_digest_mismatch'),('missing','source_evidence_unavailable'),
    ('ownership','invalid_execution_plan'),('write','invalid_execution_plan'),
    ('unknown_reference','source_evidence_reference_invalid'),('lifecycle','source_terminal_or_cleanup_unconfirmed'),
    ('cross_session','invalid_execution_plan'),('running','query_not_successful'),
    ('runtime','source_terminal_or_cleanup_unconfirmed'),('metadata','source_evidence_reference_invalid'),
    ('illegal_filter','invalid_execution_plan'),
])
def test_pending_conversion_rejects_unsafe_source(tmp_path,fault,reason):
    settings=_settings(tmp_path);store=RunStore(settings.data_root/'platform.sqlite3')
    ref=seed_pending(settings,store,accept=fault!='unconfirmed')
    if fault=='digest': store.update_free_query_session('session',accepted_result_digest='changed')
    if fault=='cleanup': store.update_harness_state('synthetic',{'cleanup_incomplete':True})
    if fault=='handoff': store.update_harness_state('synthetic',{'terminal_handoff_failure':{'code':'missing'}})
    if fault=='cancel': store.update_run('synthetic',cancel_requested=True)
    if fault=='runtime': store.update_run('synthetic',runtime_json=None)
    if fault=='metadata':
        path=settings.data_root/'harness/synthetic/evidence'/(ref+'.meta.json')
        meta=json.loads(path.read_bytes());meta['source_complete']=False;path.write_text(json.dumps(meta))
    if fault=='report':
        r=store.get_run('synthetic').result;r.presentation.validation_ref='validation_unknown'
        store.update_run('synthetic',result_json=r);refresh_digest(store)
    if fault=='tamper': (settings.data_root/'harness/synthetic/evidence'/(ref+'.json')).write_text('{}')
    if fault=='missing': (settings.data_root/'harness/synthetic/evidence'/(ref+'.meta.json')).unlink()
    with store._connect() as db:
        if fault=='ownership': db.execute("UPDATE harness_tool_calls SET status='failed' WHERE call_id='synthetic-read'")
        if fault=='write':
            plan={**PLAN,'http_method':'POST'}
            db.execute("UPDATE harness_tool_calls SET safe_input_json=? WHERE call_id='synthetic-read'",(json.dumps({'plan':plan}),))
        if fault=='illegal_filter':
            plan={**PLAN,'filters':[{'field':'CompanyCode','operator':'invented','value':'TEST'}]}
            db.execute("UPDATE harness_tool_calls SET safe_input_json=? WHERE call_id='synthetic-read'",(json.dumps({'plan':plan}),))
        if fault=='lifecycle': db.execute("DELETE FROM events WHERE run_id='synthetic' AND event_type='harness_completed'")
        if fault=='cross_session': db.execute("UPDATE free_query_iterations SET execution_action='reinterpret',source_run_id='foreign' WHERE run_id='synthetic'")
    if fault=='unknown_reference':
        r=store.get_run('synthetic').result;r.presentation.blocks[0].evidence_refs.append('ev_'+'f'*24)
        h=_presentation_hash(r.presentation);r.presentation.validation_ref='validation_'+h.removeprefix('sha256:')[:24]
        store.complete_harness_tool_call('synthetic-report',status='completed',output={'ok':True,'report_hash':h,
            'validation_ref':r.presentation.validation_ref,'_validated_report':r.presentation.model_dump(mode='json')})
        store.update_run('synthetic',result_json=r);refresh_digest(store)
    if fault=='running': store.update_run('synthetic',status=RunStatus.running)
    service=AgentDraftService(settings,store,NoAuthor())
    assert service.source_eligibility('synthetic')==reason
    with pytest.raises(DraftError): asyncio.run(service.create_from_session('session'))
    assert draft_count(store)==0 and not settings.draft_root.exists()


@pytest.mark.parametrize('provider',['codex','workbuddy'])
def test_api_accept_create_import_and_downstream_gates(tmp_path,provider):
    settings=_settings(tmp_path);app=create_app(settings,planner=FakePlanner(),embedded_provider=FakeEmbeddedProvider())
    store=app.state.store;app.state.drafts.author=NoAuthor()
    ref=seed_pending(settings,store,provider=provider,accept=False)
    client=TestClient(app)
    get=lambda:client.get('/api/free-query-sessions/session').json()
    assert not get()['can_create_draft']
    expected=store.get_free_query_iteration('session',1)['result_digest']
    response=client.post('/api/free-query-sessions/session/accept',json={'iteration':1,'expectedResultDigest':expected})
    assert response.status_code==200,response.text
    assert get()['draft_creation_mode']=='needs_review'
    assert client.get('/api/free-query-sessions').json()['items'][0]['can_create_draft']
    assert client.get('/api/runs/synthetic').json()['draft_creation_mode']=='needs_review'
    before=store.get_run('synthetic').model_dump(mode='json')
    stale=client.post('/api/free-query-sessions/session/agent-draft',json={'module':'MM','expectedIteration':1,'expectedResultDigest':'stale'})
    assert stale.status_code==409 and draft_count(store)==0
    body={'module':'MM','expectedIteration':1,'expectedResultDigest':expected}
    response=client.post('/api/free-query-sessions/session/agent-draft',json=body)
    assert response.status_code==201,response.text
    data=response.json(); managed=store.get_agent_authoring_draft(data['managed_draft_id'])
    view=client.get('/api/authoring/agents/'+data['managed_draft_id'])
    assert view.status_code==200,view.text
    (tmp_path/'managed-fixture.json').write_text(json.dumps(view.json(),ensure_ascii=False),encoding='utf-8')
    assert managed['status']=='needs_review' and managed['validation']['verdict']=='NOT_TESTED'
    assert managed['validation']['executable'] is False and managed['revision']==1
    assert managed['metadata']['pending_review']['evidence_refs']==[ref]
    assert client.post('/api/free-query-sessions/session/agent-draft',json=body).json()['draft_id']==data['draft_id']
    listing=client.get('/api/authoring/agents?state=unpublished')
    assert listing.status_code==200 and data['managed_draft_id'] in listing.text
    # Existing publication checker must reject the untested scaffold.
    package=app.state.agent_lifecycle._capture_package(Path(managed['path']))
    summary=app.state.agent_lifecycle._validation_summary(managed,package)
    assert summary['publishability']['can_publish'] is False
    assert store.get_run('synthetic').model_dump(mode='json')==before
    # Existing import mapping is a tombstone, never authorization to restore.
    with store._connect() as db:
        db.execute('DELETE FROM agent_authoring_drafts WHERE draft_id=?',(data['managed_draft_id'],))
    from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError
    with pytest.raises(AgentLifecycleError) as error:
        app.state.agent_lifecycle.import_source_draft(data['draft_id'])
    assert error.value.code=='managed_draft_deleted'


def test_pending_creation_recovers_after_interrupted_session_link(tmp_path):
    settings=_settings(tmp_path);store=RunStore(settings.data_root/'platform.sqlite3')
    seed_pending(settings,store)
    service=AgentDraftService(settings,store,NoAuthor())
    draft=asyncio.run(service.create_from_session('session',module='MM'))
    # Simulate a crash after package persistence but before the session pointer.
    store.update_free_query_session('session',status='satisfied',draft_id=None)
    reopened=AgentDraftService(settings,RunStore(store.path),NoAuthor())
    assert asyncio.run(reopened.create_from_session('session',module='MM')).draft_id==draft.draft_id
    assert draft_count(store)==1


def test_reported_completeness_does_not_erase_recorded_gaps(tmp_path):
    settings=_settings(tmp_path);store=RunStore(settings.data_root/'platform.sqlite3')
    seed_pending(settings,store,business_complete=True)
    store.update_run('synthetic',status=RunStatus.completed)
    service=AgentDraftService(settings,store,NoAuthor())
    assert service.source_projection('synthetic')['draft_creation_mode']=='needs_review'
    draft=asyncio.run(service.create_from_session('session'))
    assert draft.status=='needs_review'
    assert draft.origin['pending_review']['business_complete'] is True  # Original fact, not rewritten.
    assert 'source_semantics_unconfirmed' in draft.origin['pending_review']['evidence_gaps']


def test_original_read_scope_remains_required_and_readonly(tmp_path):
    settings=_settings(tmp_path);store=RunStore(settings.data_root/'platform.sqlite3')
    seed_pending(settings,store)
    scoped={**PLAN,'filters':[{'field':'CompanyCode','operator':'eq','value':'TEST'},
        {'field':'CreationDate','operator':'le','value':'2030-01-01'}]}
    with store._connect() as db:
        db.execute("UPDATE harness_tool_calls SET safe_input_json=? WHERE call_id='synthetic-read'",(json.dumps({'plan':scoped}),))
    service=AgentDraftService(settings,store,NoAuthor())
    draft=asyncio.run(service.create_from_session('session'))
    manifest=json.loads((Path(draft.path)/'agent.json').read_bytes())
    request=manifest['execution']['steps'][0]['request']['plan']
    assert [(f['field'],f['operator']) for f in request['filters']]==[('CompanyCode','eq'),('CreationDate','le')]
    assert request['service_name']==scoped['service_name'] and request['entity_set']==scoped['entity_set']
    assert request['http_method']=='GET' and all(s.get('readOnly',True) for s in manifest['execution']['steps'])
    assert manifest['execution']['inputSchema']['required']


def test_reuse_lineage_resolves_owned_incomplete_source_but_does_not_change_result(tmp_path):
    settings=_settings(tmp_path);store=RunStore(settings.data_root/'platform.sqlite3')
    seed_pending(settings,store,run_id='source',session_id='session')
    seed_pending(settings,store,run_id='accepted',session_id='temporary')
    # Reinterpretation uses source-owned material, never material from a foreign session.
    with store._connect() as db:
        db.execute("DELETE FROM free_query_iterations WHERE session_id='temporary'")
        db.execute("DELETE FROM free_query_sessions WHERE session_id='temporary'")
    store.create_free_query_iteration(session_id='session',iteration=2,run_id='accepted',parent_iteration=1,
        feedback='Explain saved evidence',feedback_type='presentation',execution_action='reinterpret',decision={},source_run_id='source')
    accepted=store.get_run('accepted')
    value=digest(accepted.result.model_dump(mode='json'))
    with store._connect() as db:
        db.execute("UPDATE free_query_iterations SET result_digest=? WHERE run_id='accepted'",(value,))
    store.update_free_query_session('session',status='satisfied',accepted_iteration=2,accepted_result_digest=value)
    service=AgentDraftService(settings,store,NoAuthor())
    draft=asyncio.run(service.create_from_session('session'))
    assert draft.origin['pending_review']['execution_source_run_id']=='source'
    assert draft.origin['pending_review']['source_run_id']=='accepted'
    assert store.get_run('accepted').model_dump(mode='json')==accepted.model_dump(mode='json')
