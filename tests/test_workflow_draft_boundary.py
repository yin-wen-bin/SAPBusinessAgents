"""Persistence boundary regressions; no Runtime or SAP execution."""
from copy import deepcopy
from pathlib import Path

import pytest

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.workflow_factory import WorkflowDraftError, WorkflowDraftService
from tests.test_workflow_factory import _p2p_ap_workflow


def service(tmp_path):
    root = Path(__file__).resolve().parents[1]
    settings = Settings(repository_root=root, data_root=tmp_path/'state', draft_root=tmp_path/'drafts')
    return WorkflowDraftService(settings, RunStore(settings.database_path), AgentRepository(root/'agents'), None, None)


def invalid(kind):
    workflow = _p2p_ap_workflow()
    if kind == 'write': workflow['readOnly'] = False
    elif kind == 'mode': workflow['mode'] = 'uncontrolled'
    elif kind == 'duplicate': workflow['nodes'].append(deepcopy(workflow['nodes'][0]))
    elif kind == 'missing_id': workflow['nodes'][0].pop('id')
    elif kind == 'bad_node': workflow['nodes'] = ['invalid']
    return workflow


@pytest.mark.parametrize('kind', ['write', 'mode', 'duplicate', 'missing_id', 'bad_node'])
def test_invalid_creation_does_not_persist_draft_or_directory(tmp_path, kind):
    author = service(tmp_path)
    with pytest.raises(WorkflowDraftError) as caught:
        author.create({'zh':'测试','en':'Test'}, {}, invalid(kind), 'owned-invalid')
    assert caught.value.code == 'workflow_draft_boundary_invalid'
    assert author.store.list_workflow_drafts() == []
    assert not (author.settings.draft_root/'workflows').exists()


@pytest.mark.parametrize('kind', ['write', 'mode', 'duplicate', 'missing_id', 'bad_node'])
def test_invalid_save_preserves_revision_and_definition(tmp_path, kind):
    author = service(tmp_path)
    draft = author.create({'zh':'测试','en':'Test'}, {}, _p2p_ap_workflow())
    before = deepcopy(draft.workflow)
    with pytest.raises(WorkflowDraftError):
        author.update(draft.draft_id, draft.revision, invalid(kind))
    persisted = author.get(draft.draft_id)
    assert persisted.revision == draft.revision and persisted.workflow == before
    assert len(author.revisions(draft.draft_id)) == 1


def test_blank_and_incomplete_canvas_remain_editable_and_create_retry_is_idempotent(tmp_path):
    author = service(tmp_path)
    blank = author.create({'zh':'空白','en':'Blank'}, {}, None, 'blank-request')
    retry = author.create({'zh':'空白','en':'Blank'}, {}, None, 'blank-request')
    assert retry.draft_id == blank.draft_id and retry.revision == 1
    partial = _p2p_ap_workflow()
    partial['connections'] = []
    saved = author.update(blank.draft_id, 1, partial)
    assert saved.revision == 2 and saved.validation['verdict'] == 'pending'
