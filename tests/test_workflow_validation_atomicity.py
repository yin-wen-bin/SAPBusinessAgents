"""Rejected validation requests preserve prior state; all fixtures are offline."""
import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.workflow_factory import WorkflowDraftError, WorkflowDraftService
from tests.test_workflow_factory import _p2p_ap_workflow


def test_unknown_expectation_preserves_saved_report_and_acceptance_without_dispatch(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    settings = Settings(repository_root=root, data_root=tmp_path/'state', draft_root=tmp_path/'drafts')
    author = WorkflowDraftService(settings, RunStore(settings.database_path), AgentRepository(root/'agents'), None, None)
    draft = author.create({'zh':'离线夹具','en':'Offline fixture'}, {}, _p2p_ap_workflow())
    # This is a unit fixture, never a qualification receipt or a live result.
    draft.status = 'inconclusive'
    draft.validation_run_id = 'offline-only-run'
    draft.validation = {'valid':True, 'phase':'completed', 'verdict':'inconclusive',
        'validation_report': {'run_id':'offline-only-run','report_digest':'offline-fixture-digest'}}
    author.store.save_workflow_draft(draft)
    before = deepcopy(author.get(draft.draft_id).model_dump(mode='json'))
    def must_not_mutate(*args):
        raise AssertionError('A rejected caller contract must not replace validation state')
    monkeypatch.setattr(author, 'validate_structure', must_not_mutate)
    with pytest.raises(WorkflowDraftError) as rejected:
        asyncio.run(author.validate_live(draft.draft_id, {}, auto_discover=True,
            expectations=[{'output':'unknown_output','operator':'exists'}]))
    assert rejected.value.code == 'workflow_validation_expectation_invalid'
    assert author.get(draft.draft_id).model_dump(mode='json') == before
    assert author.store.list_workflow_conversation_turns(draft.draft_id) == []
