from __future__ import annotations

import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.agent_identity import draft_identity_kind, validate_agent_id
from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError
from sap_business_agents_platform.models import AgentAuthoringCreate, AgentDraftUpdate
from sap_business_agents_platform.workflows import agent_digest
from tests.test_agent_lifecycle import _service, _source_draft, _write_active_agent


def _new(service, name="identity-test-agent"):
    return asyncio.run(service.create(AgentAuthoringCreate(source="blank", agentId=name, module="Common")))


def _confirm(service, draft, name=None):
    return service.set_technical_id(draft["draft_id"], SimpleNamespace(
        expected_revision=draft["revision"], agent_id=name or draft["agent_id"],
    ))


@pytest.mark.parametrize("name", ["x", "ab", "A-valid", "two--words", "-bad", "bad-", "con", "nul", "com1", "lpt9", "a" * 81, "x/y", "x_y"])
def test_invalid_names_are_rejected(name):
    with pytest.raises(ValueError, match="agent_technical_id_invalid"):
        validate_agent_id(name)


def test_normalization_and_legacy_identity_are_explicit():
    assert validate_agent_id("  customer-receivables-check  ") == "customer-receivables-check"
    assert draft_identity_kind({"source_type": "clone", "source_version": "1.0.0"}) == "unknown"
    assert draft_identity_kind({"source_type": "clone", "source_version": "1.0.0", "metadata": {"version_origin": {"bump": "minor"}}}) == "version_upgrade"
    assert draft_identity_kind({"source_type": "clone", "source_version": "1.0.0", "metadata": {"origin": {"sourceAgentId": "source-agent"}}}) == "new_agent"


def test_confirmation_is_not_a_content_revision_and_gates_trials(tmp_path):
    service, store, _ = _service(tmp_path)
    draft = _new(service)
    assert draft["technical_identity"] == {"kind": "new_agent", "agent_id": draft["agent_id"], "confirmed": False, "locked": False, "can_rename": True, "blockers": []}
    with pytest.raises(AgentLifecycleError) as error:
        asyncio.run(service.live_validate(draft["draft_id"], input_value={}))
    assert error.value.code == "agent_technical_id_confirmation_required"
    confirmed = _confirm(service, draft)
    assert confirmed["revision"] == 1
    assert confirmed["technical_identity"]["confirmed"]
    assert len(store.list_agent_authoring_revisions(draft["draft_id"])) == 1
    assert confirmed["package"] == draft["package"]


def test_rename_preserves_identity_history_and_invalidates_acceptance(tmp_path):
    service, store, _ = _service(tmp_path)
    draft = _confirm(service, _new(service))
    stored = store.get_agent_authoring_draft(draft["draft_id"])
    stored["validation"] = {"verdict": "PASS", "execution_digest": "same"}
    stored["thread_id"] = "old-sdk-thread"
    stored["metadata"].update(static_checks={"verdict": "PASS"}, trial={"run_id": "old-run"})
    store.save_agent_authoring_draft(stored)
    renamed = _confirm(service, draft, "customer-receivables-check")
    assert renamed["agent_id"] == renamed["package"]["manifest"]["slug"] == "customer-receivables-check"
    assert renamed["path"] == draft["path"]
    assert renamed["draft_id"] == draft["draft_id"]
    assert renamed["revision"] == 2
    assert renamed["thread_id"] is None
    assert renamed["validation"] == {}
    assert renamed["metadata"]["static_checks"] is None and renamed["metadata"]["trial"] is None
    assert renamed["metadata"]["identity_invalidated_validations"][0]["validation"] == stored["validation"]
    assert renamed["metadata"]["identity_invalidated_validations"][0]["applicable_to_current_revision"] is False
    assert renamed["package"]["manifest"]["validation"]["executable"] is False
    assert store.get_agent_authoring_revision(draft["draft_id"], 1)["package"] == draft["package"]
    assert not service._formal_acceptance(renamed, renamed["package"])
    # Unpublished former temporary names are reusable, unlike published tombstones.
    assert _new(service, draft["agent_id"])["agent_id"] == draft["agent_id"]


def test_dedicated_identity_and_undo_cannot_be_bypassed(tmp_path):
    service, _, _ = _service(tmp_path)
    draft = _new(service)
    manifest = copy.deepcopy(draft["package"]["manifest"])
    manifest["slug"] = "other-agent"
    with pytest.raises(AgentLifecycleError) as error:
        service.update(draft["draft_id"], AgentDraftUpdate(expectedRevision=1, manifest=manifest))
    assert error.value.code == "agent_id_immutable"
    renamed = _confirm(service, draft, "other-agent")
    _new(service, draft["agent_id"])
    with pytest.raises(AgentLifecycleError) as error:
        service.undo(draft["draft_id"], expected_revision=2, target_revision=1)
    assert error.value.code == "agent_technical_id_taken"
    assert service.get_draft(draft["draft_id"])["revision"] == renamed["revision"]


def test_clone_is_new_and_upgrade_is_locked(tmp_path):
    service, _, _ = _service(tmp_path)
    source = _write_active_agent(service, tmp_path)
    clone = asyncio.run(service.create(AgentAuthoringCreate(source="clone", sourceAgentId=source["slug"], agentId="business-clone")))
    assert clone["technical_identity"]["kind"] == "new_agent"
    assert service._risk_class(clone, clone["package"]) == "behavior_change"
    clone = _confirm(service, clone, "customer-clone")
    assert clone["agent_id"] == "customer-clone"
    upgrade = service.create_version_draft(source["slug"], bump="patch", expected_version=source["version"], expected_hash=agent_digest(source))
    assert upgrade["technical_identity"]["kind"] == "version_upgrade"
    assert upgrade["technical_identity"]["confirmed"] and upgrade["technical_identity"]["locked"]
    with pytest.raises(AgentLifecycleError, match="technical identity"):
        _confirm(service, upgrade, "renamed-upgrade")


def test_import_rename_preserves_mapping_and_original_source(tmp_path):
    service, store, settings = _service(tmp_path)
    source = _source_draft(service, store, settings)
    imported = service.import_source_draft(source.draft_id)
    draft = service.get_draft(imported["managed_draft_id"])
    source_hash = draft["metadata"]["source_package_hash"]
    renamed = _confirm(service, draft, "imported-business-agent")
    assert service.import_source_draft(source.draft_id) == imported
    assert renamed["metadata"]["source_package_hash"] == source_hash
    assert service._read_json(settings.draft_root / source.draft_id / "agent.json")["slug"] == "imported-agent"


def test_registry_keeps_published_but_not_draft_tombstones(tmp_path):
    service, store, _ = _service(tmp_path)
    for name, action in [("published-before", "published"), ("deleted-before", "deleted"), ("imported-before", "import_source_draft"), ("draft-before", "draft_deleted")]:
        store.append_agent_management_event(event_id=name, agent_id=name, action=action)
    for name in ("published-before", "deleted-before"):
        with pytest.raises(AgentLifecycleError) as error:
            _new(service, name)
        assert error.value.code == "agent_technical_id_taken"
    _new(service, "imported-before")
    _new(service, "draft-before")


def test_two_drafts_cannot_take_same_id_concurrently(tmp_path):
    service, store, _ = _service(tmp_path)
    left, right = _new(service, "left-agent"), _new(service, "right-agent")
    def attempt(draft):
        try:
            return _confirm(service, draft, "shared-agent")["agent_id"]
        except (AgentLifecycleError, ValueError):
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [left, right]))
    assert sorted(results) == ["conflict", "shared-agent"]
    assert sum(item["agent_id"] == "shared-agent" for item in store.list_agent_authoring_drafts()) == 1


def test_published_inactive_draft_identity_is_locked(tmp_path):
    service, store, _ = _service(tmp_path)
    draft = _confirm(service, _new(service))
    stored = store.get_agent_authoring_draft(draft["draft_id"])
    stored["status"] = "published"
    store.save_agent_authoring_draft(stored)
    assert service.get_draft(draft["draft_id"])["technical_identity"]["locked"]
    with pytest.raises(AgentLifecycleError) as error:
        _confirm(service, draft, "cannot-rename")
    assert error.value.code == "agent_draft_published"


def test_residual_cross_module_directory_blocks_name(tmp_path):
    service, _, _ = _service(tmp_path)
    (tmp_path / "agents" / "SD" / "residual-agent").mkdir(parents=True)
    with pytest.raises(AgentLifecycleError) as error:
        _new(service, "residual-agent")
    assert error.value.code == "agent_technical_id_taken"


def test_check_does_not_reserve_or_change_draft(tmp_path):
    service, store, _ = _service(tmp_path)
    draft = _new(service)
    before = store.get_agent_authoring_draft(draft["draft_id"])
    checked = service.check_technical_id(draft["draft_id"], "  checked-only-name  ")
    assert checked == {"agent_id": "checked-only-name", "available": True, "reserved": False, "revision": 1}
    assert store.get_agent_authoring_draft(draft["draft_id"]) == before
    assert _new(service, "checked-only-name")["agent_id"] == "checked-only-name"


def test_rename_database_failure_restores_package_and_name(tmp_path, monkeypatch):
    service, store, _ = _service(tmp_path)
    draft = _new(service)
    original_save = store.save_agent_authoring_draft
    def fail_save(item, **kwargs):
        if item["revision"] == 2:
            raise OSError("database unavailable")
        return original_save(item, **kwargs)
    monkeypatch.setattr(store, "save_agent_authoring_draft", fail_save)
    with pytest.raises(OSError):
        _confirm(service, draft, "renamed-business-name")
    from pathlib import Path
    assert service._read_json(Path(draft["path"]) / "agent.json")["slug"] == draft["agent_id"]
    assert store.get_agent_authoring_draft(draft["draft_id"])["agent_id"] == draft["agent_id"]
    assert store.get_agent_authoring_draft(draft["draft_id"])["revision"] == 1
    assert not store.agent_identity_taken("renamed-business-name")


def test_undo_rename_creates_revision_and_keeps_old_revisions(tmp_path):
    service, store, _ = _service(tmp_path)
    original = _new(service)
    renamed = _confirm(service, original, "renamed-business-name")
    undone = service.undo(original["draft_id"], expected_revision=2, target_revision=1)
    assert undone["revision"] == 3
    assert undone["agent_id"] == original["agent_id"]
    assert undone["technical_identity"]["confirmed"]
    assert store.get_agent_authoring_revision(original["draft_id"], 2)["package"] == renamed["package"]
    assert undone["metadata"]["identity"]["validation_invalidated_revision"] == 3


def test_legacy_ambiguous_draft_is_readable_but_identity_gated(tmp_path):
    service, store, _ = _service(tmp_path)
    original = _new(service)
    legacy = store.get_agent_authoring_draft(original["draft_id"])
    legacy["source_type"] = "clone"
    legacy["source_version"] = "1.0.0"
    legacy["metadata"] = {}
    store.save_agent_authoring_draft(legacy)
    assert service.get_draft(original["draft_id"])["technical_identity"]["kind"] == "unknown"
    with pytest.raises(AgentLifecycleError) as error:
        _confirm(service, original, "new-business-name")
    assert error.value.code == "agent_identity_unknown"
    with pytest.raises(AgentLifecycleError) as error:
        service.require_technical_identity(original["draft_id"])
    assert error.value.code == "agent_identity_unknown"
