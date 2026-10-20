"""Offline only: owned fake authentication, projections and immutable receipt audit."""
import asyncio
import copy
import hashlib
import json

import pytest

from tests.test_workbuddy_auth_hardening import auth_service as original_auth_service
from tests.test_workbuddy_auth_recovery import make_receipt
from sap_business_agents_platform.model_compatibility_view import projection, workbuddy_projection
from sap_business_agents_platform.workbuddy_auth_reuse_audit import propose
from sap_business_agents_platform.workbuddy_auth_recovery import applicable_receipt
from sap_business_agents_platform.workbuddy_environment import digest, file_hash


def auth_service(tmp_path):
    service, adapter, changed = original_auth_service(tmp_path)
    adapter.stamp["source_digest"] = "owned-authentication-protocol"
    return service, adapter, changed


def test_same_account_login_keeps_revision_and_codex_keeps_legacy_invalidation(tmp_path):
    async def run():
        service, adapter, changed = auth_service(tmp_path)
        await service.check("workbuddy")
        snapshot = service.bind("workbuddy", {})
        op = await service.start("workbuddy", "login", "same")
        await service.tasks[op["operation_id"]]
        assert service.operation("workbuddy", op["operation_id"])["status"] == "completed"
        assert service.bind("workbuddy", {}) == snapshot
        assert changed == [("workbuddy", False), ("workbuddy", False)]
        await service.check("codex")
        before = service.bind("codex", {})
        op = await service.start("codex", "login", "codex")
        await service.tasks[op["operation_id"]]
        assert service.bind("codex", {})["authentication_binding_revision"] == before["authentication_binding_revision"] + 1
        assert adapter.calls == ["check", "check"]  # Fake official auth only, no models.
    asyncio.run(run())


@pytest.mark.parametrize("case", ["identity", "missing_identity", "source", "expired", "pending",
                                 "switch_account", "logout_event", "concurrent_revision", "environment", "protocol", "missing_observation"])
def test_login_cannot_reuse_across_revocation_or_unknown_identity(tmp_path, case):
    async def run():
        service, adapter, _ = auth_service(tmp_path)
        await service.check("workbuddy")
        before = service.bind("workbuddy", {})
        account = service.state["accounts"]["workbuddy"]
        if case == "identity": adapter.identity = "different-account"
        if case == "missing_identity": adapter.identity = None
        if case == "expired": account.update(authenticated=None, status="expired", failure_code="workbuddy_authentication_required")
        if case == "pending": account["invalidation_pending"] = True
        if case == "environment": adapter.stamp["context_digest"] = "changed-execution-environment"
        if case == "protocol": adapter.stamp["source_digest"] = "changed-auth-protocol"
        if case == "missing_observation": adapter.stamp.pop("source_digest")
        async def execute(action, event, **kwargs):
            result = await adapter.check(**kwargs)
            if case == "source": result["source"] = "external_configuration"
            if case == "logout_event": event("signed_out", {})
            if case == "concurrent_revision": account["revision"] += 1
            return result
        adapter.execute = execute
        op = await service.start("workbuddy", "switch_account" if case == "switch_account" else "login",
                                 case, confirmed=True)
        await service.tasks[op["operation_id"]]
        assert service.bind("workbuddy", {})["authentication_binding_revision"] > before["authentication_binding_revision"]
    asyncio.run(run())


def test_late_site_change_does_not_accept_old_login(tmp_path):
    from sap_business_agents_platform.workbuddy_site import make_configuration
    async def run():
        service, adapter, _ = auth_service(tmp_path)
        await service.check("workbuddy")
        before = copy.deepcopy(service.state["accounts"]["workbuddy"])
        async def execute(action, event, **kwargs):
            result = await adapter.check(**kwargs)
            adapter.site = make_configuration("international", 2)
            return result
        adapter.execute = execute
        op = await service.start("workbuddy", "login", "late-site")
        await service.tasks[op["operation_id"]]
        result = service.operation("workbuddy", op["operation_id"])
        assert result["status"] == "failed"
        assert service.state["accounts"]["workbuddy"] == before
    asyncio.run(run())


@pytest.mark.parametrize("proof", [False, True])
def test_changed_cache_requires_owned_confirmation_to_retain_revision(tmp_path, proof):
    async def run():
        service, adapter, _ = auth_service(tmp_path)
        await service.check("workbuddy")
        before = service.bind("workbuddy", {})
        async def execute(action, event, **kwargs):
            result = await adapter.check(**kwargs)
            adapter.stamp["cache_digest"] = "renewed"
            if proof: result["authentication_observation"] = dict(adapter.stamp)
            return result
        adapter.execute = execute
        op = await service.start("workbuddy", "login", "renewed")
        await service.tasks[op["operation_id"]]
        assert (service.bind("workbuddy", {}) == before) is proof
    asyncio.run(run())


def test_cleanup_unconfirmed_never_accepts_login(tmp_path):
    async def run():
        service, adapter, changed = auth_service(tmp_path)
        await service.check("workbuddy")
        adapter.cleanup_complete = False
        op = await service.start("workbuddy", "login", "cleanup")
        await service.tasks[op["operation_id"]]
        result = service.operation("workbuddy", op["operation_id"])
        assert result["status"] == "cleanup_pending"
        assert service.view("workbuddy")["authenticated"] is not True
    asyncio.run(run())


def audit_fixture(tmp_path):
    env, state, release, check, receipt = make_receipt(tmp_path)
    state["authentication_revision"] = 9
    state["authentication_environment"] = state["environment_digest"]
    site = {k: state[k] for k in ("service_site", "service_site_revision", "service_site_digest")}
    auth = {"accounts": {"workbuddy": {"status": "authenticated", "authenticated": True,
            "revision": 9, "checked_at": "2026-10-20T09:18:30+08:00",
            "authentication_observation": {"context_digest": "context", "source_digest": "source"},
            "confirmed_binding": {"identity_digest": state["authentication_identity_digest"],
                "authenticated": True, "source": "official_cli", **site}}},
            "operations": {"login": {"operation_id": "login", "provider_id": "workbuddy",
                "action": "login", "status": "completed", "cleanup_complete": True, "service_context": site,
                "created_at": "2026-10-20T09:17:54+08:00", "completed_at": "2026-10-20T09:18:14+08:00"}}}
    return env, state, check, receipt, auth


def test_audit_six_eight_nine_appends_only_in_isolated_copy(tmp_path):
    env, state, check, old, auth = audit_fixture(tmp_path)
    before = copy.deepcopy(state)
    result = propose(env, state, auth, original_identity=old["original_identity"],
                     original_check=old["model_checks"][check["model_id"]])
    assert result["decision"] == "proposal_only"
    receipt = result["receipt_fields"]
    assert receipt["source_revision"] == 6 and receipt["target_revision"] == 9
    path = env.root / "new-current.json"
    path.write_text(json.dumps(result["current_identity_artifact"]), encoding="utf-8")
    receipt["current_identity"] = {"path": path.name, "sha256": file_hash(path)}
    receipt["id"] = digest(receipt)
    isolated = copy.deepcopy(state)
    isolated["authentication_recoveries"][receipt["id"]] = receipt
    assert applicable_receipt(env, isolated, model=check["model_id"], source_revision=6,
                              check_digest=check["check_digest"]) == receipt
    assert state == before and isolated["model_check_history"] == state["model_check_history"]
    assert isolated["authentication_recoveries"][old["id"]] == old


@pytest.mark.parametrize("case", ["missing", "modified", "identity", "model", "protocol", "site",
                                 "cleanup", "revision", "expired"])
def test_audit_rejects_missing_modified_or_cross_binding_evidence(tmp_path, case):
    env, state, check, receipt, auth = audit_fixture(tmp_path)
    reference = receipt["model_checks"][check["model_id"]]
    if case == "missing": (env.root / reference["path"]).unlink()
    if case == "modified": (env.root / reference["path"]).write_text("{}")
    if case in {"model", "protocol"}:
        check["model_id" if case == "model" else "model_protocol_digest"] = "different"
        check["check_digest"] = digest({k: v for k, v in check.items() if k != "check_digest"})
        (env.root / reference["path"]).write_text(json.dumps(check))
        reference["sha256"] = file_hash(env.root / reference["path"])
    if case == "identity": state["authentication_identity_digest"] = "different"
    if case == "site": state["service_site_digest"] = "different"
    if case == "revision": auth["accounts"]["workbuddy"]["revision"] = 10
    if case == "expired": auth["accounts"]["workbuddy"]["authenticated"] = None
    if case == "cleanup": auth["operations"]["login"]["cleanup_complete"] = False
    result = propose(env, state, auth, original_identity=receipt["original_identity"], original_check=reference)
    assert result["decision"] == "blocked" and "receipt_fields" not in result


def test_presentation_never_grants_qualification(tmp_path):
    _, state, check, _, _ = audit_fixture(tmp_path)
    item = {"model_id": check["model_id"], "check": state["models"][check["model_id"]], "check_stale": True}
    before = copy.deepcopy(state)
    assert workbuddy_projection(state, item, ready=True)["status"] == "recovery_pending"
    assert workbuddy_projection(state, item, ready=False)["status"] == "blocked"
    state["authenticated"] = None
    assert workbuddy_projection(state, item, ready=True)["status"] == "authentication_required"
    state["authenticated"] = True
    assert state == before
    assert projection(check=check, current=True, authenticated=True)["status"] == "reusable"
    assert projection(check={}, current=False, authenticated=True)["status"] == "check_required"
    assert projection(check={"compatible": False}, current=True, authenticated=True)["status"] == "failed"


@pytest.mark.parametrize("first_action", ["model", "effort"])
def test_codex_legacy_save_probes_only_when_needed_and_reuses_valid_result(tmp_path, first_action):
    from tests.test_sdk_manager import FakeAdapter, FakeRuntimeProbe, _write_runtime_registry
    from sap_business_agents_platform.sdk_manager import SDKManager
    class CountingProbe(FakeRuntimeProbe):
        calls = 0
        async def check_model(self, *args, **kwargs):
            self.calls += 1
            return await super().check_model(*args, **kwargs)
    async def run():
        registry = tmp_path / "registry.json"
        _write_runtime_registry(registry)
        probe = CountingProbe()
        manager = SDKManager(registry, tmp_path, adapters={"python": FakeAdapter()},
                             runtime_probes={"codex": probe}, selection_path=tmp_path/"default.json")
        await manager.check_provider("codex")
        await manager.refresh_models("codex")
        assert probe.calls == 0
        if first_action == "model":
            await manager.set_default_model("codex", "test-model")
        else:
            await manager.set_reasoning_effort("codex", "test-model", "medium")
        assert probe.calls == 1
        await manager.set_default_model("codex", "test-model")
        await manager.set_reasoning_effort("codex", "test-model", "medium")
        assert probe.calls == 1
        item = manager.models("codex")["items"][0]
        assert item["selectable"] and item["compatibility_applicability"]["status"] == "reusable"
    asyncio.run(run())
