"""Explicit future-turn selection never rewrites old task/account authority."""
import copy
from types import SimpleNamespace
import pytest
from sap_business_agents_platform.authoring_binding import selection_digest
from sap_business_agents_platform.models import AgentDraftUpdate
from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError
from tests.test_agent_authoring import _service,create

def setup(tmp_path,provider='workbuddy'):
    service,store,_=_service(tmp_path);draft=create(service)
    saved=store.get_agent_authoring_draft(draft['draft_id'])
    saved['metadata']['runtime_snapshot']={'provider_id':'workbuddy','model':'old','authentication_binding_revision':8,'environment_digest':'old-worker'}
    saved['thread_id']='old-thread';store.save_agent_authoring_draft(saved,expected_revision=draft['revision'])
    snapshot={'provider_id':provider,'model':'current','authentication_binding_revision':9,'environment_digest':'current-worker'}
    service.runtime=SimpleNamespace(snapshot=lambda:copy.deepcopy(snapshot))
    return service,store,draft,snapshot

@pytest.mark.parametrize('provider',['codex','workbuddy'])
def test_explicit_binding_is_atomic_revision_and_idempotent(tmp_path,provider):
    service,store,draft,snapshot=setup(tmp_path,provider)
    old=copy.deepcopy(store.list_agent_conversation_turns(draft['draft_id']))
    payload=AgentDraftUpdate(expectedRevision=draft['revision'],runtimeBindingMode='current_default',
        expectedRuntimeDigest=selection_digest(snapshot),requestId='explicit-bind-once')
    result=service.update(draft['draft_id'],payload)
    assert result['revision']==draft['revision']+1
    current=store.get_agent_authoring_draft(draft['draft_id'])
    assert current['metadata']['runtime_snapshot']==snapshot and current['thread_id'] is None
    event=current['metadata']['runtime_binding_history'][-1]
    assert event['previous_snapshot']['authentication_binding_revision']==8
    assert store.list_agent_conversation_turns(draft['draft_id'])[:len(old)]==old
    service.runtime.snapshot=lambda:pytest.fail('retry must not consult a changed default')
    assert service.update(draft['draft_id'],payload)['revision']==result['revision']
    with pytest.raises(AgentLifecycleError,match='Conflicting'):
        service.update(draft['draft_id'],payload.model_copy(update={'expected_runtime_digest':'f'*64}))

def test_ordinary_update_keeps_old_binding(tmp_path):
    service,store,draft,_=setup(tmp_path)
    service.update(draft['draft_id'],AgentDraftUpdate(expectedRevision=draft['revision'],readme='Documentation only'))
    assert store.get_agent_authoring_draft(draft['draft_id'])['metadata']['runtime_snapshot']['model']=='old'

def test_changed_default_or_admission_failure_cannot_mutate(tmp_path):
    service,store,draft,snapshot=setup(tmp_path)
    before=store.get_agent_authoring_draft(draft['draft_id'])
    payload=AgentDraftUpdate(expectedRevision=draft['revision'],runtimeBindingMode='current_default',expectedRuntimeDigest=selection_digest(snapshot),requestId='changed-default')
    calls=[]
    def changed():
        calls.append(1)
        return copy.deepcopy(snapshot) if len(calls)==1 else {**snapshot,'environment_digest':'changed'}
    service.runtime.snapshot=changed
    with pytest.raises(AgentLifecycleError):service.update(draft['draft_id'],payload)
    assert store.get_agent_authoring_draft(draft['draft_id'])==before
    assert store.get_agent_operation(draft['draft_id']) is None

def test_explicit_binding_requires_confirmation_fields():
    with pytest.raises(ValueError):AgentDraftUpdate(expectedRevision=1,runtimeBindingMode='current_default')
