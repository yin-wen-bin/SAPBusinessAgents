from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.acceptance import agent_execution_digest
from sap_business_agents_platform.agent_lifecycle import AgentLifecycleService
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.engine import RunCoordinator
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.models import RunCreate, RunMode, RunStatus, utc_now


def setup_trial(tmp_path: Path):
    settings = Settings(repository_root=tmp_path, data_root=tmp_path / "local-data", enforce_agent_acceptance=True)
    store = RunStore(settings.database_path)
    package = AgentLifecycleService._blank_package("trial-recovery", "Common", {"zh": "恢复测试", "en": "Recovery test"})
    run_id, draft_id, revision = "acceptance_persisted_trial", "draft_trial", 1
    draft = {"draft_id": draft_id, "agent_id": "trial-recovery", "source_type": "blank",
             "status": "draft", "revision": revision, "path": str(tmp_path / "draft"),
             "metadata": {}, "validation": {}, "created_at": utc_now(), "updated_at": utc_now()}
    store.save_agent_authoring_draft(draft, package=package)
    operation = store.reserve_agent_operation(draft_id, revision, "trial")
    report = {"type": "trial", "run_id": run_id, "revision": revision,
              "operation_id": operation["operation_id"], "status": "running", "verdict": "pending",
              "execution_digest": agent_execution_digest(package["manifest"], package.get("rules"))}
    store.create_run(run_id, RunCreate(mode=RunMode.agent, agentId="trial-recovery", input={}))
    store.save_agent_run_snapshot(run_id, package["manifest"], rules_source=package.get("rules"), draft_id=draft_id, revision=revision)
    store.save_agent_validation_attempt(draft_id=draft_id, run_id=run_id, revision=revision, report=report, report_digest=None)
    draft.update(status="validating", validation_run_id=run_id, metadata={"trial": report})
    store.save_agent_authoring_draft(draft, expected_revision=revision, operation_id=operation["operation_id"])
    store.update_agent_operation(draft_id, operation["operation_id"], detail={"trial": report})

    def fresh():
        unused = SimpleNamespace()
        return RunCoordinator(settings, store, AgentRepository(tmp_path / "agents"), unused, unused, unused)

    return store, fresh, run_id, draft_id, operation["operation_id"], package


def test_fresh_coordinator_recovers_only_persisted_trial_and_does_not_certify(tmp_path):
    store, fresh, run_id, draft_id, _, package = setup_trial(tmp_path)
    # Mirrors startup: non-replayable authoring work is interrupted, trials remain.
    store.recover_agent_operations()
    coordinator = fresh()
    assert not coordinator._acceptance_runs
    assert coordinator._is_persisted_draft_trial(run_id, store.get_agent_run_snapshot(run_id))
    asyncio.run(coordinator._execute_scheduled_run(run_id))
    run = store.get_run(run_id)
    assert run.status == RunStatus.inconclusive
    assert not run.error
    assert run.result.workflow_output["business_status"] == "inconclusive"
    assert store.get_agent_authoring_revision(draft_id, 1)["package"] == package
    assert store.get_agent_authoring_draft(draft_id)["validation"] == {}
    assert package["manifest"]["validation"]["executable"] is False


@pytest.mark.parametrize("change", [
    "no_attempt", "not_trial", "attempt_revision", "report_revision", "report_run_id",
    "completed_attempt", "digest", "draft_revision", "draft_run", "draft_status",
    "metadata", "operation_kind", "operation_terminal", "operation_detail",
    "snapshot_manifest", "snapshot_rules", "snapshot_digest", "snapshot_owner",
])
def test_fresh_coordinator_rejects_forged_stale_or_orphan_trial(tmp_path, change):
    store, fresh, run_id, draft_id, operation_id, _ = setup_trial(tmp_path)
    snapshot = store.get_agent_run_snapshot(run_id)
    attempt = store.get_agent_validation_attempt(draft_id, run_id)
    draft = store.get_agent_authoring_draft(draft_id)
    if change == "no_attempt":
        with store._connect() as connection:
            connection.execute("DELETE FROM agent_validation_attempts WHERE run_id = ?", (run_id,))
    elif change.startswith("snapshot_"):
        if change == "snapshot_manifest":
            snapshot["manifest"]["execution"]["outputMapping"]["source_complete"] = True
        elif change == "snapshot_rules":
            snapshot["rules_source"] = "def evaluate(inputs): return {}"
        elif change == "snapshot_digest":
            snapshot["agent_digest"] = "sha256:forged"
        else:
            snapshot["validation_draft_id"] = "other-draft"
    elif change.startswith("draft_") or change == "metadata":
        if change == "draft_revision":
            draft["revision"] = 2
        elif change == "draft_run":
            draft["validation_run_id"] = "other-run"
        elif change == "draft_status":
            draft["status"] = "published"
        else:
            draft["metadata"] = {}
        store.save_agent_authoring_draft(draft, expected_revision=1, operation_id=operation_id)
    elif change.startswith("operation_"):
        if change == "operation_kind":
            with store._connect() as connection:
                connection.execute("UPDATE agent_draft_operations SET kind = 'sample_discovery' WHERE operation_id = ?", (operation_id,))
        elif change == "operation_terminal":
            store.finish_agent_operation(draft_id, operation_id)
        else:
            store.update_agent_operation(draft_id, operation_id, detail={})
    else:
        report = copy.deepcopy(attempt["report"])
        if change == "not_trial":
            report["type"] = "acceptance"
        elif change == "report_revision":
            report["revision"] = 2
        elif change == "report_run_id":
            report["run_id"] = "other-run"
        elif change == "digest":
            report["execution_digest"] = "sha256:forged"
        store.save_agent_validation_attempt(draft_id=draft_id, run_id=run_id,
            revision=2 if change == "attempt_revision" else 1, report=report,
            report_digest=None, completed_at=utc_now() if change == "completed_attempt" else None)
    assert not fresh()._is_persisted_draft_trial(run_id, snapshot)


def test_acceptance_named_snapshot_alone_cannot_bypass_normal_execution_gate(tmp_path):
    store, fresh, run_id, _, _, package = setup_trial(tmp_path)
    orphan_run = "acceptance_looks_trusted"
    store.create_run(orphan_run, RunCreate(mode=RunMode.agent, agentId="trial-recovery", input={}))
    store.save_agent_run_snapshot(orphan_run, package["manifest"], draft_id="draft_trial", revision=1)
    coordinator = fresh()
    asyncio.run(coordinator._execute_scheduled_run(orphan_run))
    assert store.get_run(orphan_run).status == RunStatus.failed
    assert store.get_run(orphan_run).error["code"] == "agent_live_validation_required"
    assert store.get_run(run_id).status == RunStatus.queued


def test_startup_interrupts_trial_reserved_before_run_owner_persisted(tmp_path):
    store, _, _, draft_id, operation_id, _ = setup_trial(tmp_path)
    # Simulate the crash window immediately after reservation: no run, attempt,
    # or validating draft owner was persisted.  The reservation must not remain
    # active and the draft must be editable again.
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM agent_validation_attempts WHERE draft_id = ?",
            (draft_id,),
        )
        connection.execute(
            """UPDATE agent_authoring_drafts SET status = 'draft', validation_run_id = NULL,
            metadata_json = '{}' WHERE draft_id = ?""",
            (draft_id,),
        )
        connection.execute(
            "UPDATE agent_draft_operations SET detail_json = '{}' WHERE operation_id = ?",
            (operation_id,),
        )
    store.recover_agent_operations()
    assert store.get_agent_operation(draft_id) is None
    operation = store.get_agent_operation_by_id(draft_id, operation_id)
    assert operation["status"] == "interrupted"
    assert store.get_agent_authoring_draft(draft_id)["status"] == "draft"


def test_startup_interrupts_trial_with_missing_current_draft_owner(tmp_path):
    store, _, run_id, draft_id, operation_id, _ = setup_trial(tmp_path)
    with store._connect() as connection:
        connection.execute(
            "UPDATE agent_authoring_drafts SET status = 'draft', validation_run_id = NULL WHERE draft_id = ?",
            (draft_id,),
        )
    store.recover_agent_operations()
    assert store.get_agent_operation(draft_id) is None
    assert store.get_agent_operation_by_id(draft_id, operation_id)["status"] == "interrupted"
    assert store.get_run(run_id).status == RunStatus.failed
