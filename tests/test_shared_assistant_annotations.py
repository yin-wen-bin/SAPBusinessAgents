"""Synthetic runtimes only: comments are context, never authorization or workflow fields."""
import asyncio
from copy import deepcopy
import hashlib
import json

import pytest
from pydantic import ValidationError

from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError
from sap_business_agents_platform.models import AgentFeedbackRequest, WorkflowAssistantReference
from sap_business_agents_platform.workflow_assistant import fingerprint, state
from sap_business_agents_platform.workflow_factory import WorkflowDraftError
from tests.test_agent_authoring import _service, create, Runtime, send_and_wait
from tests.test_workflow_assistant import setup, payload, finished


@pytest.mark.parametrize('kind', ['readme', 'rules'])
def test_agent_fixed_content_annotations_are_revision_bound(tmp_path, kind):
    service, _, _ = _service(tmp_path)
    draft = create(service)
    request = AgentFeedbackRequest(baseTurn=len(draft['conversation']), baseRevision=1,
        feedback='Explain this content', requestId='content', intent='explain',
        annotations=[dict(kind=kind, path=f'/{kind}', comment='Explain without edits', revision=1)])
    context = service._feedback_context(draft['draft_id'], 1, request)
    assert context['annotations'][0]['kind'] == kind
    assert context['annotations'][0]['comment'] == 'Explain without edits'
    for changes in ({'path': '/manifest/slug'}, {'revision': 2}, {'sourceId': 'another-draft'}):
        bad = request.model_copy(deep=True)
        for key, value in changes.items():
            setattr(bad.annotations[0], {'sourceId': 'source_id'}.get(key, key), value)
        with pytest.raises(AgentLifecycleError):
            service._feedback_context(draft['draft_id'], 1, bad)


def test_two_agent_annotations_apply_one_revision_without_reading_sap(tmp_path):
    service, _, _ = _service(tmp_path)
    runtime = service.runtime = Runtime('revise_agent', mutate=lambda package: package['manifest']['title'].update(zh='新名称', en='New name'))
    draft = create(service)
    request = AgentFeedbackRequest(baseTurn=len(draft['conversation']), baseRevision=1,
        feedback='Apply the annotated changes', requestId='batch', intent='revise', annotations=[
            dict(kind='manifest_field', path='/manifest/title/zh', comment='修改中文名称', revision=1),
            dict(kind='manifest_field', path='/manifest/title/en', comment='Update the English name', revision=1)])
    _, result = asyncio.run(send_and_wait(service, draft, request))
    assert result['revision'] == 2
    assert len(runtime.calls) == 1
    assert len(runtime.calls[0]['feedback_context']['annotations']) == 2
    assert result['package']['manifest']['slug'] == draft['agent_id']
    assert 'annotations' not in result['package']['manifest']


def test_reference_additions_preserve_exact_old_fingerprint():
    old = payload(references=[dict(kind='workflow_field', draftId='draft', revision=1, path='/title')])
    legacy = deepcopy(old)
    for reference in legacy['references']:
        for field in ('comment', 'objectId', 'objectKind'):
            reference.pop(field)
    expected = {k: v for k, v in legacy.items() if k != 'allowedChangePaths' or v is not None}
    assert fingerprint(old) == hashlib.sha256(json.dumps(expected, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    changed = deepcopy(old)
    changed['references'][0]['comment'] = 'Change the title'
    assert fingerprint(changed) != fingerprint(old)


@pytest.mark.parametrize('changes', [dict(comment=' '), dict(comment='x' * 2001), dict(objectId='node'), dict(objectKind='node'), dict(selector='button')])
def test_workflow_reference_invalid_metadata_rejected(changes):
    with pytest.raises(ValidationError):
        WorkflowAssistantReference(kind='workflow_field', draftId='draft', revision=1, path='/title', **changes)


def test_workflow_batch_keeps_comments_outside_definition_and_scope_opt_in(tmp_path):
    service, author, draft = setup(tmp_path)
    author.action = 'revise'
    author.mutate = lambda workflow: workflow['title'].update(zh='批量标注', en='Batch comments')
    original = deepcopy(draft.workflow)
    references = [dict(kind='workflow_field', draftId=draft.draft_id, revision=1,
        path=f'/title/{language}', comment=f'Update {language} title') for language in ('zh', 'en')]
    request = payload(intent='revise', references=references)
    async def check():
        service.assistant.submit(draft.draft_id, request)
        service.assistant.submit(draft.draft_id, request)
        await finished(service.assistant)
    asyncio.run(check())
    result = service.get(draft.draft_id)
    assert result.revision == 2
    assert len(author.calls) == 1
    assert [ref['comment'] for ref in author.calls[0]['references']] == ['Update zh title', 'Update en title']
    assert author.calls[0]['allowed_paths'] is None
    assert result.workflow['nodes'] == original['nodes']
    assert 'references' not in result.workflow and 'comment' not in result.workflow
    assert state(result)['rounds'][0]['status'] == 'completed'
    changed = deepcopy(request)
    changed['references'][0]['comment'] = 'Different request'
    with pytest.raises(WorkflowDraftError) as error:
        service.assistant.submit(draft.draft_id, changed)
    assert error.value.code == 'workflow_request_conflict'


@pytest.mark.parametrize('change', ['wrong_object', 'wrong_draft', 'stale_revision', 'duplicate'])
def test_workflow_reference_cannot_follow_another_node(tmp_path, change):
    service, author, draft = setup(tmp_path)
    reference = dict(kind='workflow_field', draftId=draft.draft_id, revision=1,
        path='/nodes/0', objectKind='node', objectId=draft.workflow['nodes'][0]['id'], comment='Explain the node')
    if change == 'wrong_object': reference['objectId'] = 'not-this-node'
    if change == 'wrong_draft': reference['draftId'] = 'other'
    if change == 'stale_revision': reference['revision'] = 2
    refs = [reference, deepcopy(reference)] if change == 'duplicate' else [reference]
    async def check():
        with pytest.raises(WorkflowDraftError):
            service.assistant.submit(draft.draft_id, payload(references=refs))
    asyncio.run(check())
    assert not author.calls
    assert service.get(draft.draft_id).revision == 1


def test_workflow_connection_identity_preserves_the_pair(tmp_path):
    service, author, draft = setup(tmp_path)
    from sap_business_agents_platform.workflow_assistant import checked_reference_identity
    row = draft.workflow['connections'][0]
    source, target = row['from'], row['to']
    start = f"{source['nodeId']}:{source['port']}" if source['scope'] == 'node_output' else f"{source['scope']}:{source.get('port', 'value')}"
    reference = dict(path='/connections/0', objectKind='connection', objectId=f"{start}->{target['nodeId']}:{target['port']}")
    checked_reference_identity(draft.workflow, reference)
    reference['objectId'] += '-wrong-target'
    with pytest.raises(WorkflowDraftError):
        checked_reference_identity(draft.workflow, reference)


@pytest.mark.parametrize('pointer', ['', '/', '/items'])
def test_iteration_connection_identity_uses_the_exact_saved_pointer(pointer):
    from sap_business_agents_platform.workflow_assistant import checked_reference_identity
    workflow = {'connections': [dict(from_='unused')]}
    workflow['connections'] = [{'from': {'scope': 'iteration_item', 'pointer': pointer}, 'to': {'nodeId': 'a', 'port': 'b'}}]
    checked_reference_identity(workflow, dict(path='/connections/0', objectKind='connection', objectId=f'iteration:{pointer}->a:b'))
