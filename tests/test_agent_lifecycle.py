from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.agent_lifecycle import (
    AgentLifecycleError,
    AgentLifecycleService,
)
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.managed_rules import (
    ManagedRuleError,
    execute_managed_rule,
    source_digest,
    validate_managed_rule,
)
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.models import (
    AgentActivateRequest,
    AgentAuthoringCreate,
    AgentDraftDeleteRequest,
    AgentDraftUpdate,
    AgentPublishRequest,
    AgentVersionDraftRequest,
    RunCreate,
    RunStatus,
)
from sap_business_agents_platform.skills import SkillError
from sap_business_agents_platform.workflows import agent_digest


class _Unused:
    pass


def _service(
    tmp_path: Path, *, skills: object | None = None
) -> tuple[AgentLifecycleService, RunStore, Settings]:
    settings = Settings(
        repository_root=tmp_path,
        data_root=tmp_path / ".local-data",
        draft_root=tmp_path / ".prototype" / "authoring",
    )
    store = RunStore(settings.database_path)
    service = AgentLifecycleService(
        settings,
        store,
        AgentRepository(tmp_path / "agents"),
        _Unused(),
        _Unused(),
        _Unused(),
        skills=skills,
    )
    return service, store, settings


def _write_active_agent(service: AgentLifecycleService, root: Path) -> dict:
    package = service._blank_package(
        "managed-test-agent",
        "Common",
        {"zh": "测试固定Agent", "en": "Test Fixed Agent"},
    )
    package["manifest"]["version"] = "1.0.0"
    package["manifest"]["validation"] = {
        "verdict": "PASS",
        "executable": True,
        "acceptanceMode": "deterministic_runtime",
        "fixedAgentComparison": "MATCH",
        "freeQueryComparison": "NOT_TESTED",
    }
    directory = root / "agents" / "Common" / "managed-test-agent"
    service._write_package(directory, package)
    return package["manifest"]


def test_activation_rejects_drifted_skill_before_git_mutation(tmp_path: Path) -> None:
    class _DriftedSkills:
        def get(self, _skill_id: str) -> dict:
            raise SkillError(
                "Skill package digest does not match the approved package.",
                code="skill_package_digest_mismatch",
            )

    service, _store, _settings = _service(tmp_path, skills=_DriftedSkills())
    manifest = _write_active_agent(service, tmp_path)
    manifest["execution"]["steps"].insert(
        0,
        {
            "id": "read_history",
            "executor": "skill",
            "operation": "execute",
            "skillId": "sap-ar-dunning-history-evidence",
            "readOnly": True,
            "inputMapping": {},
        },
    )
    manifest["workflow"][0]["executionStepIds"].insert(0, "read_history")
    directory = tmp_path / "agents" / "Common" / "managed-test-agent"
    package = service._blank_package(
        "managed-test-agent",
        "Common",
        {"zh": "测试固定Agent", "en": "Test Fixed Agent"},
    )
    package["manifest"] = manifest
    service._write_package(directory, package)

    with pytest.raises(AgentLifecycleError) as error:
        service.activate(
            "managed-test-agent",
            AgentActivateRequest(
                expectedVersion="1.0.0",
                expectedAgentHash=agent_digest(manifest),
                version="1.0.0",
            ),
        )

    assert error.value.code == "skill_package_digest_mismatch"
    assert not (tmp_path / ".git").exists()


def test_blank_agent_creates_immutable_revision_and_requires_live_validation(tmp_path: Path) -> None:
    service, store, _settings = _service(tmp_path)
    draft = asyncio.run(
        service.create(
            AgentAuthoringCreate(
                source="blank", agentId="inventory-review-agent", module="Common"
            )
        )
    )

    assert draft["revision"] == 1
    assert draft["package"]["manifest"]["slug"] == "inventory-review-agent"
    checked = service.validate(draft["draft_id"])
    assert checked["validation"]["verdict"] == "NOT_TESTED"
    assert checked["validation"]["requires_live_validation"] is True
    assert store.get_agent_authoring_revision(draft["draft_id"], 1)["package"] == draft["package"]


def test_structured_edit_creates_new_revision_and_diff(tmp_path: Path) -> None:
    service, _store, _settings = _service(tmp_path)
    draft = asyncio.run(
        service.create(
            AgentAuthoringCreate(source="blank", agentId="revision-test-agent", module="Common")
        )
    )
    manifest = draft["package"]["manifest"]
    manifest["summary"]["zh"] = "更新后的说明"
    revised = service.update(
        draft["draft_id"],
        AgentDraftUpdate(
            expectedRevision=1,
            manifest=manifest,
            readme=draft["package"]["readme"],
            rules="",
        ),
    )

    assert revised["revision"] == 2
    assert any(item["path"] == "/manifest/summary/zh" for item in revised["diff"])
    assert service.store.get_agent_authoring_revision(draft["draft_id"], 1)["package"]["manifest"]["summary"]["zh"] != "更新后的说明"


def test_unpublished_draft_delete_removes_authoring_state_and_preserves_validation_run(
    tmp_path: Path,
) -> None:
    service, store, _settings = _service(tmp_path)
    draft = asyncio.run(
        service.create(
            AgentAuthoringCreate(source="blank", agentId="deletable-agent", module="Common")
        )
    )
    run_id = "run_retained_validation"
    store.create_run(
        run_id,
        RunCreate(mode="agent", agentId="deletable-agent", input={}),
    )
    store.update_run(run_id, status=RunStatus.completed)
    store.save_agent_run_snapshot(
        run_id,
        draft["package"]["manifest"],
        draft_id=draft["draft_id"],
        revision=1,
    )
    store.save_agent_validation_attempt(
        draft_id=draft["draft_id"],
        run_id=run_id,
        revision=1,
        report={"status": "completed"},
        report_digest="sha256:" + "0" * 64,
        completed_at=draft["updated_at"],
    )
    draft_row = store.get_agent_authoring_draft(draft["draft_id"])
    draft_row.update(status="needs_review", validation_run_id=run_id)
    store.save_agent_authoring_draft(draft_row)
    draft_path = Path(draft["path"])

    result = service.delete_draft(
        draft["draft_id"],
        AgentDraftDeleteRequest(expectedRevision=1, confirmAgentId="deletable-agent"),
    )

    assert result["deleted"] is True
    assert result["retained_validation_run_ids"] == [run_id]
    assert not draft_path.exists()
    with pytest.raises(KeyError):
        store.get_agent_authoring_draft(draft["draft_id"])
    assert store.get_run(run_id).run_id == run_id
    assert store.get_agent_run_snapshot(run_id)["validation_draft_id"] == draft["draft_id"]
    with store._connect() as connection:
        event = connection.execute(
            "SELECT action, detail_json FROM agent_management_events WHERE event_id = ?",
            (result["audit_event_id"],),
        ).fetchone()
    assert event["action"] == "draft_deleted"
    assert "deletable-agent" not in event["detail_json"]


def test_draft_delete_rejects_confirmation_conflict_and_active_validation(tmp_path: Path) -> None:
    service, store, _settings = _service(tmp_path)
    draft = asyncio.run(
        service.create(
            AgentAuthoringCreate(source="blank", agentId="guarded-agent", module="Common")
        )
    )
    with pytest.raises(AgentLifecycleError) as mismatch:
        service.delete_draft(
            draft["draft_id"],
            AgentDraftDeleteRequest(expectedRevision=1, confirmAgentId="other-agent"),
        )
    assert mismatch.value.code == "agent_draft_delete_confirmation_mismatch"

    with pytest.raises(AgentLifecycleError) as conflict:
        service.delete_draft(
            draft["draft_id"],
            AgentDraftDeleteRequest(expectedRevision=2, confirmAgentId="guarded-agent"),
        )
    assert conflict.value.code == "agent_draft_conflict"

    run_id = "run_active_validation"
    store.create_run(
        run_id,
        RunCreate(mode="agent", agentId="guarded-agent", input={}),
    )
    draft_row = store.get_agent_authoring_draft(draft["draft_id"])
    draft_row.update(status="validating", validation_run_id=run_id)
    store.save_agent_authoring_draft(draft_row)
    with pytest.raises(AgentLifecycleError) as active:
        service.delete_draft(
            draft["draft_id"],
            AgentDraftDeleteRequest(expectedRevision=1, confirmAgentId="guarded-agent"),
        )
    assert active.value.code == "agent_draft_validation_active"
    assert Path(draft["path"]).exists()


def test_unpublished_draft_listing_is_localized_and_excludes_published(tmp_path: Path) -> None:
    service, store, _settings = _service(tmp_path)
    draft = asyncio.run(
        service.create(
            AgentAuthoringCreate(source="blank", agentId="listed-agent", module="FI")
        )
    )
    listed = service.list_drafts("unpublished")
    assert listed[0]["agent_id"] == "listed-agent"
    assert listed[0]["module"] == "FI"
    assert listed[0]["title"]["zh"] == "新固定Agent"
    assert listed[0]["management"]["can_delete"] is True

    draft_row = store.get_agent_authoring_draft(draft["draft_id"])
    draft_row["status"] = "published"
    store.save_agent_authoring_draft(draft_row)
    assert service.list_drafts("unpublished") == []
    assert service.list_drafts("all")[0]["status"] == "published"

    with pytest.raises(AgentLifecycleError) as published:
        service.delete_draft(
            draft["draft_id"],
            AgentDraftDeleteRequest(expectedRevision=1, confirmAgentId="listed-agent"),
        )
    assert published.value.code == "agent_draft_published"


def test_draft_delete_rejects_path_outside_authoring_root(tmp_path: Path) -> None:
    service, store, _settings = _service(tmp_path)
    draft = asyncio.run(
        service.create(
            AgentAuthoringCreate(source="blank", agentId="path-guard-agent", module="Common")
        )
    )
    draft_row = store.get_agent_authoring_draft(draft["draft_id"])
    draft_row["path"] = str(tmp_path / "outside")
    store.save_agent_authoring_draft(draft_row)

    with pytest.raises(AgentLifecycleError) as invalid:
        service.delete_draft(
            draft["draft_id"],
            AgentDraftDeleteRequest(expectedRevision=1, confirmAgentId="path-guard-agent"),
        )
    assert invalid.value.code == "agent_draft_path_invalid"
    assert Path(draft["path"]).exists()


def test_draft_delete_restores_package_when_database_delete_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _store, _settings = _service(tmp_path)
    draft = asyncio.run(
        service.create(
            AgentAuthoringCreate(source="blank", agentId="restore-delete", module="Common")
        )
    )
    draft_path = Path(draft["path"])
    original_files = {
        item.relative_to(draft_path).as_posix(): item.read_bytes()
        for item in draft_path.rglob("*")
        if item.is_file()
    }

    def fail_delete(*args, **kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(service.store, "delete_agent_authoring_draft", fail_delete)
    with pytest.raises(RuntimeError, match="simulated database failure"):
        service.delete_draft(
            draft["draft_id"],
            AgentDraftDeleteRequest(
                expectedRevision=draft["revision"], confirmAgentId="restore-delete"
            ),
        )

    assert service.get_draft(draft["draft_id"])["agent_id"] == "restore-delete"
    assert {
        item.relative_to(draft_path).as_posix(): item.read_bytes()
        for item in draft_path.rglob("*")
        if item.is_file()
    } == original_files


def test_managed_rule_is_scanned_and_runs_in_isolated_process() -> None:
    source = "from decimal import Decimal\n\ndef evaluate(inputs):\n    return {'total': str(Decimal(inputs['left']) + Decimal(inputs['right']))}\n"
    digest = source_digest(source)
    assert validate_managed_rule(source, expected_digest=digest)["ok"] is True
    assert execute_managed_rule(source, {"left": "1.20", "right": "2.30"}, expected_digest=digest) == {"total": "3.50"}

    with pytest.raises(ManagedRuleError):
        validate_managed_rule("def evaluate(inputs):\n    return open('secret').read()\n")


def test_managed_rule_preserves_unicode_across_isolated_process() -> None:
    source = "def evaluate(inputs):\n    return {'text': '中文验收'}\n"

    assert execute_managed_rule(
        source, {}, expected_digest=source_digest(source)
    ) == {"text": "中文验收"}


def test_managed_rule_reuses_identical_report_without_losing_rows_or_raising_limit():
    source = "def evaluate(inputs):\n    report = {'records': [{'text': 'a' * 1200} for i in range(1000)]}\n    return {'business_report': report, 'workflow_output': {'business_report': report}}\n"
    result = execute_managed_rule(source, {}, expected_digest=source_digest(source))
    assert len(result['business_report']['records']) == 1000
    assert result['business_report'] == result['workflow_output']['business_report']
    huge = "def evaluate(inputs):\n    return {'text': 'a' * 2100000}\n"
    with pytest.raises(ManagedRuleError, match='2 MB'):
        execute_managed_rule(huge, {}, expected_digest=source_digest(huge))


def test_managed_rule_exception_never_leaks_evidence_values():
    source = "def evaluate(inputs):\n    return inputs['private-reference-12345']\n"
    with pytest.raises(ManagedRuleError) as error:
        execute_managed_rule(source, {}, expected_digest=source_digest(source))
    assert 'private-reference-12345' not in str(error.value)


def test_inactive_agent_is_hidden_from_business_catalog_but_available_to_management(tmp_path: Path) -> None:
    service, _store, _settings = _service(tmp_path)
    manifest = _write_active_agent(service, tmp_path)
    directory = tmp_path / "agents" / "Common" / "managed-test-agent"
    service._write_json(
        directory / "publication.json",
        {
            "schemaVersion": 1,
            "agent_id": "managed-test-agent",
            "lifecycle_state": "inactive",
            "state": "inactive",
            "active_version": "1.0.0",
            "latest_version": "1.0.0",
            "active_digest": agent_digest(manifest),
        },
    )

    assert service.agents.list() == []
    assert service.catalog("inactive")[0]["id"] == "managed-test-agent"


def test_metadata_only_version_reuses_pass_acceptance_and_publishes_local_commit(tmp_path: Path) -> None:
    service, _store, _settings = _service(tmp_path)
    current = _write_active_agent(service, tmp_path)
    (tmp_path / ".gitignore").write_text(".local-data/\n.prototype/\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=tmp_path, check=True, capture_output=True)

    draft = service.create_version_draft(
        "managed-test-agent",
        bump="patch",
        expected_version="1.0.0",
        expected_hash=agent_digest(current),
    )
    package = draft["package"]
    package["manifest"]["summary"]["en"] = "Updated documentation only."
    revised = service.update(
        draft["draft_id"],
        AgentDraftUpdate(
            expectedRevision=1,
            manifest=package["manifest"],
            readme=package["readme"],
            rules=package.get("rules") or "",
        ),
    )
    validated = service.validate(draft["draft_id"])
    assert validated["risk_class"] == "metadata_only"
    assert validated["validation"]["verdict"] == "PASS"

    result = service.publish(
        draft["draft_id"],
        AgentPublishRequest(
            expectedRevision=revised["revision"],
            targetVersion="1.0.1",
            activate=True,
        ),
    )
    assert result["active"] is True
    assert len(result["commit_sha"]) == 40
    assert service.agents.get("managed-test-agent")["version"] == "1.0.1"
    assert (tmp_path / "agents" / "Common" / "managed-test-agent" / "versions" / "1.0.0" / "agent.json").is_file()
    assert subprocess.run(["git", "status", "--porcelain"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout == ""


def test_permanent_delete_is_blocked_while_agent_is_active(tmp_path: Path) -> None:
    service, _store, _settings = _service(tmp_path)
    _write_active_agent(service, tmp_path)
    assert "agent_must_be_inactive" in service._delete_blockers("managed-test-agent")
