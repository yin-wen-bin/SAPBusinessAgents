"""Model identity gates without an SDK install, network, authentication or SAP."""
import asyncio
import sys
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import workbuddy_worker
from sap_business_agents_platform.workbuddy_identity import ROUTING_ALIASES
from sap_business_agents_platform.workbuddy_manager import MODEL_CHECK_SECONDS, WorkBuddyManager
from tests.test_workbuddy_isolation import release, supervisor


def manager_fixture(tmp_path, monkeypatch):
    manager = WorkBuddyManager(tmp_path)
    value = release(manager.environment)
    monkeypatch.setattr(manager.environment, "release", lambda *_: value)
    manager.environment.mutate(lambda state: state.update(
        environment_digest=value["environment_digest"], authenticated=True,
        authentication_environment=value["environment_digest"]))
    return manager, value


def test_bounded_explanation_never_certifies_trusted_revision_or_allows_enable(tmp_path, monkeypatch):
    from sap_business_agents_platform.workbuddy_environment import OPERATIONS
    from sap_business_agents_platform import workbuddy_manager
    manager, value = manager_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(workbuddy_manager.platform, "system", lambda: "Windows")
    manager.environment.mutate(lambda state: state.update(default_model_id="concrete-model", models={
        "concrete-model": {"compatible": True, "environment_digest": value["environment_digest"],
            "check_digest": "verified", "actual_model": "concrete-model",
            "identity_known": True, "model_identity_source": "assistant_message"}}))
    operations = {name: {"status": "validated", "implemented": True,
        "permission_modes": ["trusted_local"] if name == "workflow_authoring.v2" else ["bounded"]}
        for name in OPERATIONS}
    monkeypatch.setattr(manager, "capabilities", lambda *_: operations)
    assert "workbuddy_operation_validation_required" in manager.snapshot()["blockers"]
    with pytest.raises(Exception) as failure:
        manager.set_enabled(True)
    assert failure.value.code == "runtime_not_selectable"
    assert manager.environment.state().get("enabled") is not True
    operations["review_agent_feedback"]["permission_modes"].append("trusted_local")
    assert "workbuddy_operation_validation_required" not in manager.snapshot()["blockers"]


@pytest.mark.parametrize("failure", [None, "workbuddy_deadline_exceeded", "runtime_cleanup_incomplete"])
def test_locked_workspace_cannot_mask_probe_result_or_process_cleanup_failure(tmp_path, monkeypatch, failure):
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    manager, _ = manager_fixture(tmp_path, monkeypatch)
    calls = []
    class LockedWorkspace:
        name = str(tmp_path / "sapba-workbuddy-model-fixture")
        def __init__(self, **kwargs):
            assert kwargs == {"prefix": "sapba-workbuddy-model-"}
        def cleanup(self):
            raise PermissionError("private-user-path-and-sensitive-details")
    monkeypatch.setattr("sap_business_agents_platform.workbuddy_manager.tempfile.TemporaryDirectory", LockedWorkspace)
    async def probe(**kwargs):
        calls.append(kwargs)
        assert kwargs["seconds"] == MODEL_CHECK_SECONDS == 90
        assert kwargs["probe"] is True and "tools" not in kwargs["payload"]
        if failure:
            raise WorkBuddyError(failure)
        return {"text": '{"status":"ok"}', "actual_model": "concrete-model",
                "model_identity_source": "assistant_message"}
    monkeypatch.setattr(manager.supervisor, "run", probe)
    record = asyncio.run(manager.check_model("concrete-model"))["check"]
    assert len(calls) == 1  # Cleanup never retries or falls back to another model.
    assert record["status"] == (failure or "compatible")
    assert record["compatible"] is (failure is None)
    assert record["identity_known"] is (failure is None)
    assert record["warnings"] == [{"code": "workbuddy_model_workspace_cleanup_failed",
                                    "workspace_id": "sapba-workbuddy-model-fixture"}]
    assert "private-user-path" not in str(record)
    assert manager.environment.state()["models"]["concrete-model"] == record


@pytest.mark.parametrize("actual,source,known", [
    *[(alias, "assistant_message", False) for alias in sorted(ROUTING_ALIASES)],
    ("DEFAULT-MODEL", "assistant_message", False),
    ("concrete-model", "assistant_message", True),
    ("concrete-model", "system_metadata", False),
    ("concrete-model", None, False), (None, None, False),
    ([], "assistant_message", False),
])
def test_usable_route_does_not_prove_concrete_identity(tmp_path, monkeypatch, actual, source, known):
    manager, _ = manager_fixture(tmp_path, monkeypatch)
    async def probe(**kwargs):
        assert kwargs["operation"] == "model_check" and kwargs["probe"] is True
        assert "tools" not in kwargs["payload"]
        return {"text": '{"status":"ok"}', "actual_model": actual, "model_identity_source": source}
    monkeypatch.setattr(manager.supervisor, "run", probe)
    result = asyncio.run(manager.check_model("route"))
    assert result["check"]["compatible"] is True
    assert result["check"]["identity_known"] is known
    assert result["item"]["identity_known"] is known
    # Ordinary authoring can use a checked route without claiming its identity.
    binding = manager.runtime_snapshot("route", require_enabled=False)
    assert binding["model_identity_known"] is known
    if not known:
        for get_binding in (lambda: manager.runtime_snapshot("route", require_enabled=False, formal=True),
                            lambda: manager.bound_snapshot(binding, formal=True)):
            with pytest.raises(Exception) as failure:
                get_binding()
            assert failure.value.code == "workbuddy_model_identity_unknown"


@pytest.mark.parametrize("actual,source,code", [
    ("default-model", "assistant_message", "workbuddy_model_identity_unknown"),
    ("concrete-model", None, "workbuddy_model_identity_unknown"),
    ("other-model", "assistant_message", "workbuddy_model_alias_not_allowed"),
])
def test_historical_true_flags_do_not_bypass_formal_gate_or_rewrite_history(tmp_path, monkeypatch, actual, source, code):
    manager, value = manager_fixture(tmp_path, monkeypatch)
    check = {"compatible": True, "environment_digest": value["environment_digest"],
             "identity_known": True, "actual_model": actual, "model_identity_source": source,
             "check_digest": "saved-original-check"}
    manager.environment.mutate(lambda state: state.update(models={"concrete-model": check}))
    saved = manager.environment.state_path.read_bytes()
    binding = manager.runtime_snapshot("concrete-model", require_enabled=False)
    for get_binding in (lambda: manager.runtime_snapshot("concrete-model", require_enabled=False, formal=True),
                        lambda: manager.bound_snapshot(binding, formal=True)):
        with pytest.raises(Exception) as failure:
            get_binding()
        assert failure.value.code == code
    manager.models()
    assert manager.environment.state_path.read_bytes() == saved


def test_verified_concrete_model_passes_formal_binding(tmp_path, monkeypatch):
    manager, value = manager_fixture(tmp_path, monkeypatch)
    manager.environment.mutate(lambda state: state.update(models={"concrete-model": {
        "compatible": True, "environment_digest": value["environment_digest"],
        "identity_known": True, "actual_model": "concrete-model",
        "model_identity_source": "assistant_message", "check_digest": "verified-check"}}))
    binding = manager.runtime_snapshot("concrete-model", require_enabled=False, formal=True)
    assert manager.bound_snapshot(binding, formal=True) == binding
    from sap_business_agents_platform.models import RuntimeSnapshot
    persisted = RuntimeSnapshot.model_validate(binding).model_dump(mode="json")
    assert persisted["model_identity_source"] == "assistant_message"
    assert manager.bound_snapshot(persisted, formal=True)["model_identity_known"] is True


@pytest.mark.parametrize("model,source", [("default-model", "assistant_message"), ("concrete-model", None)])
def test_baseline_artifact_cannot_self_certify_model_identity(model, source):
    from sap_business_agents_platform.acceptance import validate_direct_baseline, canonical_hash
    normalized = {"records": [], "metrics": {}, "source_complete": True, "evidence_complete": True,
                  "business_complete": True, "business_status": "normal", "limitations": [], "evidence_gap_codes": []}
    artifact = {"schema_version": "workbuddy-baseline/1", "runtime": "workbuddy_sdk_direct_sap",
                "candidate_access": False, "used_sap_business_agents": False,
                "runtime_snapshot": {"provider_id": "workbuddy", "model": model, "actual_model": model,
                    "model_identity_known": True, "model_identity_source": source, "reasoning_effort": None,
                    "environment_digest": "a" * 64, "configuration_digest": "frozen", "sdk_fingerprint": "fingerprint"},
                "sources": [{"read_only": True, "evidence_ref": "fixture:evidence", "http_method": "GET"}],
                "normalized_result": normalized, "result_hash": canonical_hash(normalized)}
    with pytest.raises(ValueError, match="concrete Runtime binding"):
        validate_direct_baseline(artifact)


def test_acceptance_worker_cannot_use_forged_legacy_alias_flag(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    binding.update(model="default-model", actual_model="fixture-model", model_identity_known=True)
    with pytest.raises(Exception) as failure:
        asyncio.run(owner.run(task_id="alias", snapshot=binding, operation="acceptance_baseline", payload={}, seconds=5))
    assert failure.value.code == "workbuddy_model_identity_unknown"
    assert not owner.active and not owner.pending()


@pytest.mark.parametrize("assistant_models,actual", [
    ([], None), (["concrete-model"], "concrete-model"),
    (["concrete-model", "concrete-model"], "concrete-model"),
    (["concrete-model", "other-model"], None),
])
def test_worker_uses_assistant_identity_not_requested_route_or_system_metadata(monkeypatch, assistant_models, actual):
    class Assistant:
        def __init__(self, model):
            self.model, self.content = model, []
    class Result:
        is_error, structured_output, result, session_id = False, None, '{"status":"ok"}', "fixture"
    messages = [SimpleNamespace(data={"model": "default-model"}),
                *[Assistant(model) for model in assistant_models],
                SimpleNamespace(data={"model": "fast-model"}), Result()]
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["options"].tools == []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def query(self, prompt):
            assert prompt == "probe"
        async def receive_response(self):
            for message in messages:
                yield message
    monkeypatch.setitem(sys.modules, "codebuddy_agent_sdk", SimpleNamespace(
        CodeBuddyAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs), CodeBuddySDKClient=Client,
        AssistantMessage=Assistant, ResultMessage=Result, TextBlock=type("Text", (), {}),
        PermissionResultAllow=SimpleNamespace, PermissionResultDeny=SimpleNamespace,
        ToolUseBlock=type("ToolUse", (), {}), ToolResultBlock=type("ToolResult", (), {})))
    result = asyncio.run(workbuddy_worker.execute({"operation": "model_check", "mode": "bounded",
        "cli_path": "fixture-cli", "snapshot": {"model": "default-model"},
        "payload": {"cwd": "fixture", "prompt": "probe"}}, lambda *_args, **_kwargs: None))
    assert result["actual_model"] == actual
    assert result["model_identity_source"] == ("assistant_message" if actual else None)
