"""Synthetic authoring regressions; no SAP, model or current draft mutations."""
import asyncio
import copy
from types import SimpleNamespace

from sap_business_agents_platform.authoring_checks import candidate_business_output_issues
from sap_business_agents_platform.authoring_harness import check_candidate
from sap_business_agents_platform.factory import AgentDraftService, _manifest_from_run
from tests.test_agent_authoring import _service, create
from tests.test_free_query_history import PLAN, seed


def incomplete_package(slug='author-test', module='SD'):
    manifest = _manifest_from_run(slug, 'Synthetic purchase order review', copy.deepcopy(PLAN), '')
    manifest['module'] = module
    return {'manifest': manifest, 'readme': 'Synthetic review notes', 'rules': None, 'files': {}}


def test_metadata_revision_keeps_existing_business_gap_without_blocking_save():
    original = incomplete_package()
    assert {x['code'] for x in candidate_business_output_issues(original['manifest'])} == {'agent_business_output_contract_missing'}
    proposed = copy.deepcopy(original)
    proposed['manifest']['title'] = {'zh': '采购订单概要核查', 'en': 'Purchase order header review'}
    proposed['manifest']['summary'] = {'zh': '核对采购订单基础资料。', 'en': 'Review purchase order header data.'}
    result = check_candidate(proposed, original)
    assert result.status == 'passed', result.issues
    assert proposed['manifest']['execution'] == original['manifest']['execution']
    assert candidate_business_output_issues(proposed['manifest']), 'The business gap must remain; metadata cannot grant readiness'
    assert proposed['manifest']['validation'] == original['manifest']['validation']


def test_behavior_change_still_requires_complete_business_contract():
    original = incomplete_package()
    proposed = copy.deepcopy(original)
    proposed['manifest']['execution']['steps'][0]['request']['plan']['top'] = 2
    result = check_candidate(proposed, original)
    assert result.status == 'repairable'
    assert 'agent_business_output_contract_missing' in {x['code'] for x in result.issues}


def test_runtime_metadata_save_preserves_identity_and_unresolved_readiness(tmp_path):
    service, store, _ = _service(tmp_path)
    draft = create(service)
    package = incomplete_package(draft['agent_id'], 'SD')
    first = service._reserve_operation(draft['draft_id'], draft['revision'], 'manual_edit')
    try:
        saved = service._apply_package(draft['draft_id'], draft['revision'], package,
            kind='manual_edit', operation_id=first['operation_id'])
    finally:
        service._finish_operation(draft['draft_id'], first['operation_id'])
    before = store.get_agent_authoring_revision(saved['draft_id'], saved['revision'])['package']
    proposed = copy.deepcopy(before)
    proposed['manifest']['title'] = {'zh': '订单基础资料核查', 'en': 'Order header review'}
    operation = service._reserve_operation(saved['draft_id'], saved['revision'], 'feedback')
    try:
        updated = service._apply_package(saved['draft_id'], saved['revision'], proposed,
            kind='feedback', operation_id=operation['operation_id'], safe_runtime=True)
    finally:
        service._finish_operation(saved['draft_id'], operation['operation_id'])
    assert updated['revision'] == saved['revision'] + 1
    actual = store.get_agent_authoring_revision(updated['draft_id'], updated['revision'])['package']
    assert actual['manifest']['execution'] == before['manifest']['execution']
    assert actual['manifest']['slug'] == before['manifest']['slug']
    assert candidate_business_output_issues(actual['manifest'])
    assert updated.get('validation', {}).get('verdict') != 'PASS'


def test_conversion_is_visible_while_model_runs_and_becomes_one_managed_draft(tmp_path):
    async def scenario():
        management, store, settings = _service(tmp_path)
        seed(store, 'pending-source')
        store.update_run('pending-source', thread_id='synthetic-source-thread')
        entered, release = asyncio.Event(), asyncio.Event()
        class Author:
            calls = 0
            def supports(self, *_): return True
            async def author_draft(self, **_):
                self.calls += 1
                entered.set()
                await release.wait()
                return {'content_zh': '待完善的合成测试草稿', 'content_en': 'Synthetic draft requiring review', 'rule_notes': []}
        author = Author()
        factory = AgentDraftService(settings, store, author)
        management.legacy_factory = factory
        task = asyncio.create_task(factory.create_from_run('pending-source', module='SD'))
        await asyncio.wait_for(entered.wait(), 5)
        try:
            pending = [x for x in management.list_drafts('unpublished')
                       if (x.get('metadata') or {}).get('source_run_id') == 'pending-source']
            assert len(pending) == 1, 'The management list must expose one pending conversion before model completion'
            assert pending[0]['status'] == 'creating'
            assert pending[0].get('validation', {}).get('verdict') != 'PASS'
            pending_id = pending[0]['draft_id']
        finally:
            release.set()
            generated = await task
        imported = management.import_source_draft(generated.draft_id)
        final = [x for x in management.list_drafts('unpublished')
                 if (x.get('metadata') or {}).get('source_run_id') == 'pending-source']
        assert len(final) == 1 and final[0]['draft_id'] == imported['managed_draft_id'] == pending_id
        assert final[0]['status'] == 'needs_review'
        assert author.calls == 1
        assert management.import_source_draft(generated.draft_id)['managed_draft_id'] == pending_id
    asyncio.run(scenario())
