import asyncio
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.factory import AgentDraftService, DraftError
from tests.test_agent_authoring import _service
from tests.test_free_query_history import seed


@pytest.mark.parametrize("failure", ["error", "cancel"])
def test_failed_creation_stays_visible_and_explicit_retry_keeps_one_identity(tmp_path, failure):
    async def scenario():
        management, store, settings = _service(tmp_path)
        seed(store, "creation-source")
        store.update_run("creation-source", thread_id="synthetic-authoring-thread")
        entered, release = asyncio.Event(), asyncio.Event()

        class Author:
            calls = 0

            def supports(self, *_):
                return True

            async def author_draft(self, **_):
                self.calls += 1
                if self.calls == 1:
                    entered.set()
                    await release.wait()
                    raise RuntimeError("private diagnostic must not enter the public projection")
                return {"content_zh": "待业务复核", "content_en": "Business review required", "rule_notes": []}

        author = Author()
        factory = AgentDraftService(settings, store, author)
        management.legacy_factory = factory
        operation = asyncio.create_task(factory.create_from_run("creation-source", module="SD"))
        await asyncio.wait_for(entered.wait(), 5)
        pending = management.list_drafts("unpublished")
        assert len(pending) == 1 and pending[0]["status"] == "creating"
        identity = pending[0]["draft_id"]
        assert pending[0]["management"]["can_delete"] is False
        assert pending[0]["technical_identity"]["confirmed"] is False
        if failure == "cancel":
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        else:
            release.set()
            with pytest.raises(DraftError):
                await operation
        failed = management.list_drafts("unpublished")
        assert len(failed) == 1 and failed[0]["draft_id"] == identity
        assert failed[0]["status"] == "creation_failed"
        assert "private diagnostic" not in str(failed)
        assert failed[0]["validation"]["executable"] is False

        generated = await factory.create_from_run("creation-source", module="SD")
        imported = management.import_source_draft(generated.draft_id)
        final = management.list_drafts("unpublished")
        assert author.calls == 2
        assert len(final) == 1 and final[0]["draft_id"] == identity == imported["managed_draft_id"]
        assert not final[0].get("creation_pending")
        assert final[0]["validation"]["verdict"] != "PASS"
        assert management.import_source_draft(generated.draft_id)["managed_draft_id"] == identity
        management.delete_draft(identity, SimpleNamespace(expected_revision=final[0]["revision"],
            confirm_agent_id=final[0]["agent_id"]))
        assert management.list_drafts("unpublished") == [], "Deleted imports must not reappear as pending creations"

    asyncio.run(scenario())


def test_startup_recovery_exposes_interruption_without_replaying_authoring(tmp_path):
    management, store, _ = _service(tmp_path)
    store.reserve_draft_creation("synthetic-key", "draft_interrupted")
    store.save_draft_creation_progress("synthetic-key", "draft_interrupted", "creating", {
        "source_run_id": "synthetic-source", "source_draft_id": "draft_interrupted",
        "agent_id": "free-query-interrupted", "module": "SD",
        "title": {"zh": "待生成", "en": "Pending"}, "target_version": "0.1.0", "phase": "authoring"})
    store.recover_draft_creations()
    rows = management.list_drafts("unpublished")
    assert len(rows) == 1 and rows[0]["status"] == "creation_failed"
    assert rows[0]["creation"]["error_code"] == "draft_creation_interrupted"
    assert rows[0]["validation"] == {"verdict": "NOT_TESTED", "executable": False}
    assert store.get_draft_import("draft_interrupted") is None
