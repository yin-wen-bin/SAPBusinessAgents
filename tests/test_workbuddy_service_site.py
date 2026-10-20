"""Offline site routing and account isolation; no real login/model/SAP calls."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError, digest
from sap_business_agents_platform import workbuddy_site as site
from sap_business_agents_platform.runtime_authentication import RuntimeAuthenticationService
from sap_business_agents_platform.runtime_authentication_adapters import safe_auth_url
from sap_business_agents_platform.sdk_manager import SDKManager


def managed(tmp_path, monkeypatch):
    manager = WorkBuddyManager(tmp_path)
    release = {"environment_digest": "a" * 64, "sdk_version": "fixture", "cli_version": "fixture",
               "files": {"fixture": "b" * 64}, "python": "fixture", "worker": "fixture", "cli": "fixture",
               "service_site_contract": "1"}
    monkeypatch.setattr(manager.environment, "release", lambda *_: release)
    manager.environment.mutate(lambda state: state.update(environment_digest=release["environment_digest"]))
    return manager, release


class Account:
    cleanup_complete = True
    usage_url = None
    account_url = "https://copilot.tencent.com/"
    configuration_url = "https://www.codebuddy.cn/docs/cli/iam"
    def __init__(self, manager):
        self.manager, self.calls = manager, []
    def context(self): return self.manager.site_configuration(required=True)
    def capabilities(self): return dict(check=True, login=True, logout=True, switch_account=True)
    async def check(self, *, context):
        self.calls.append(("check", context))
        return dict(authenticated=True, identity="private@example.test", method="browser", source="official_cli")
    async def execute(self, action, event, *, context):
        self.calls.append((action, context))
        return dict(authenticated=action != "logout", identity="private@example.test", method="browser", source="official_cli")


def registry(tmp_path, monkeypatch):
    wb, release = managed(tmp_path, monkeypatch)
    manager = SDKManager.__new__(SDKManager)
    manager.workbuddy, manager._runtime_config = wb, {"default_provider_id": "codex", "revision": 7}
    account = Account(wb)
    auth = RuntimeAuthenticationService(tmp_path / "auth", {"workbuddy": account, "codex": SimpleNamespace()},
        changed=manager.apply_authentication_state)
    manager.authentication_service = auth
    return manager, account, auth, release


def test_default_migration_is_guarded_and_codex_unchanged(tmp_path, monkeypatch):
    manager, account, auth, release = registry(tmp_path, monkeypatch)
    before = copy.deepcopy(manager._runtime_config)
    assert manager.workbuddy.site_configuration()["service_site"] == "china"
    with pytest.raises(WorkBuddyError, match="migration_required"):
        asyncio.run(auth.start("workbuddy", "login", "not-ready"))
    manager.migrate_workbuddy_site()
    current = manager.workbuddy.site_configuration(required=True)
    assert current["service_site"] == "china" and current["service_site_revision"] == 1
    assert account.calls == [] and auth.view("workbuddy")["authenticated"] is None
    assert manager._runtime_config == before
    assert manager.workbuddy.environment.state()["environment_digest"] == release["environment_digest"]


def test_switch_preserves_evidence_and_never_reuses_old_checks(tmp_path, monkeypatch):
    manager, release = managed(tmp_path, monkeypatch)
    china = manager.save_site("china", 0, "first")
    bound = site.binding(china)
    evidence = {"status": "validated", "environment_digest": release["environment_digest"], **bound}
    model = {"compatible": True, "actual_model": "old-site-model", "model_identity_source": "assistant_message", "environment_digest": release["environment_digest"], **bound}
    manager.environment.mutate(lambda state: state.update(models={"model": model}, capabilities={"history": evidence},
        authenticated=True, authentication_site=bound, authentication_environment=release["environment_digest"]))
    previous = copy.deepcopy(manager.environment.state())
    foreign = manager.save_site("international", 1, "next")
    assert foreign["service_site_revision"] == 2 and manager.environment.state()["authenticated"] is False
    assert manager.environment.state()["models"] == previous["models"]
    assert manager.environment.state()["capabilities"] == previous["capabilities"]
    # The new site's SDK must advertise the ID independently; history alone
    # cannot make an old-site model appear in this site's directory.
    assert manager.models()["items"] == []
    manager.environment.mutate(lambda state: state.update(model_discovery={
        "source": "locked_cli_help", "models": ["model"],
        "environment_digest": release["environment_digest"], **site.binding(foreign)}))
    item = manager.models()["items"][0]
    assert not item["selectable"] and item["check_stale"] and not item["retired"]
    assert item["actual_model"] is None and not item["identity_known"]
    assert item["check"]["actual_model"] == "old-site-model"  # History remains available.
    assert manager.save_site("international", 1, "next")["service_site_digest"] == foreign["service_site_digest"]
    with pytest.raises(WorkBuddyError, match="request_conflict"):
        manager.save_site("china", 1, "next")
    with pytest.raises(WorkBuddyError, match="revision_conflict"):
        manager.save_site("china", 1, "stale")
    back = manager.save_site("china", 2, "back")
    assert not site.matches(model, back)
    with pytest.raises(WorkBuddyError, match="service_site_changed"):
        manager.bound_snapshot(bound)


@pytest.mark.parametrize("block", ["enabled", "selected", "queued", "active", "cleanup", "external"])
def test_switch_fails_closed_without_mutation(tmp_path, monkeypatch, block):
    manager, _ = managed(tmp_path, monkeypatch)
    manager.save_site("china", 0, "init")
    selected = block == "selected"
    if block == "enabled": manager.environment.mutate(lambda s: s.update(enabled=True))
    if block == "queued": manager.supervisor.queued.add("queued")
    if block == "active": manager.supervisor.active["live"] = {}
    if block == "cleanup": monkeypatch.setattr(manager.supervisor, "reconcile", lambda: [{"status": "cleanup_pending"}])
    if block == "external": monkeypatch.setenv("CODEBUDDY_BASE_URL", "https://private.invalid")
    before = manager.environment.state()
    with pytest.raises(Exception): manager.save_site("international", 1, "blocked", selected=selected)
    assert manager.environment.state() == before


def test_auth_actions_freeze_site_and_retries_cannot_cross_sites(tmp_path, monkeypatch):
    manager, account, auth, _ = registry(tmp_path, monkeypatch)
    manager.migrate_workbuddy_site()
    async def run():
        first = await auth.start("workbuddy", "login", "same")
        await auth.tasks[first["operation_id"]]
        assert auth.operation("workbuddy", first["operation_id"])["service_site"] == "china"
        await auth.check("workbuddy")
        original = copy.deepcopy(manager.workbuddy.environment.state()["authentication_site"])
        manager.save_workbuddy_site("international", 1, "switch")
        assert not auth.view("workbuddy")["authenticated"]
        assert account.calls == [("login", original), ("check", original)]
        with pytest.raises(Exception, match="Authentication") as failure:
            await auth.start("workbuddy", "login", "same")
        assert failure.value.code == "runtime_authentication_request_conflict"
        with pytest.raises(Exception) as failure:
            await auth.start("workbuddy", "login", "different", service_site_digest=original["service_site_digest"])
        assert failure.value.code == "workbuddy_service_site_changed"
        await auth.check("workbuddy")
        assert account.calls[-1][1]["service_site"] == "international"
    asyncio.run(run())


def test_account_mutation_lock_blocks_site_save(tmp_path, monkeypatch):
    manager, account, auth, _ = registry(tmp_path, monkeypatch)
    manager.migrate_workbuddy_site()
    before = manager.workbuddy.environment.state()
    async def run():
        async with auth.use("workbuddy", auth.bind("workbuddy", {}), require_authenticated=False):
            with pytest.raises(Exception) as failure:
                manager.save_workbuddy_site("international", 1, "blocked")
            assert failure.value.code == "runtime_authentication_busy"
    # Before verification, binding is intentionally rejected. Simulate a successful check.
    asyncio.run(auth.check("workbuddy"))
    before = manager.workbuddy.environment.state()
    asyncio.run(run())
    assert manager.workbuddy.environment.state() == before


@pytest.mark.parametrize("name,environment", [("china", "internal"), ("international", "external")])
def test_auth_and_model_probe_share_explicit_routing(tmp_path, monkeypatch, name, environment):
    manager, release = managed(tmp_path, monkeypatch)
    from tests.workbuddy_integration_fakes import install_certificate, probe_result
    install_certificate(manager, release)
    current = site.binding(manager.save_site(name, 0, "init"))
    calls = []
    async def probe(**kwargs):
        calls.append(kwargs)
        assert site.matches(kwargs["snapshot"], current)
        if kwargs["operation"] == "authentication": return {"authenticated": True, "status": "existing_login"}
        return await probe_result(manager, kwargs, "hy4-preview")
    monkeypatch.setattr(manager.supervisor, "run", probe)
    asyncio.run(manager.check())
    result = asyncio.run(manager.check_model("hy4-preview"))
    assert result["check"]["compatible"] is True
    assert site.routing(result["check"])["sdk_environment"] == environment
    assert manager.runtime_snapshot("hy4-preview", require_enabled=False)["service_site"] == name
    assert [call["operation"] for call in calls] == ["authentication", "model_check"]


def test_login_urls_are_site_specific_and_never_rewritten():
    cn = site.routing(site.make_configuration("china", 1))
    assert safe_auth_url("workbuddy", "https://copilot.tencent.com/login", hosts=cn["auth_hosts"])
    assert not safe_auth_url("workbuddy", "https://codebuddy.ai/login", hosts=cn["auth_hosts"])
    assert not safe_auth_url("workbuddy", "https://copilot.tencent.com.attacker.invalid/login", hosts=cn["auth_hosts"])


def test_site_api_origin_validation_and_idempotency(tmp_path, monkeypatch):
    from sap_business_agents_platform.app import create_app
    from sap_business_agents_platform.config import Settings
    from tests.test_runtime_authentication_api import Healthy
    manager, account, auth, _ = registry(tmp_path, monkeypatch)
    manager.migrate_workbuddy_site()
    manager.list = lambda: [{"provider_id": "workbuddy", "sdk_id": "workbuddy-python-sdk"}]
    root = Path(__file__).resolve().parents[1]
    app = create_app(Settings(repository_root=root, data_root=tmp_path / "data", draft_root=tmp_path / "drafts", skillhub_root=tmp_path / "skills"),
        planner=object(), sdk_manager=manager, embedded_provider=Healthy(), authentication_adapters={"workbuddy": account})
    base = "/api/system/sdk-runtimes/workbuddy/service-site"
    headers = {"X-SAPBA-Action": "runtime-authentication", "Origin": "http://127.0.0.1:4321"}
    with TestClient(app) as client:
        body = {"service_site": "international", "expected_revision": 1, "requestId": "same"}
        assert client.put(base, json=body).status_code == 403
        assert client.put(base, json={**body, "service_site": "enterprise"}, headers=headers).status_code == 422
        first = client.put(base, json=body, headers=headers)
        assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
        assert client.put(base, json=body, headers=headers).json()["service_site_configuration"]["service_site_digest"] == first.json()["service_site_configuration"]["service_site_digest"]
        assert client.put(base, json={**body, "service_site": "china"}, headers=headers).status_code == 409
        assert account.calls == []


@pytest.mark.parametrize("fails", [False, True])
def test_late_auth_check_cannot_overwrite_new_site(tmp_path, monkeypatch, fails):
    manager, account, auth, _ = registry(tmp_path, monkeypatch)
    manager.migrate_workbuddy_site()
    async def late(*, context):
        # Simulate an external writer/recovery while a check is in flight.
        new = site.make_configuration("international", 2)
        manager.workbuddy.environment.mutate(lambda state: state.update(**new))
        auth.invalidate_configuration("workbuddy", new)
        if fails:
            raise RuntimeError("synthetic check error")
        return dict(authenticated=True, method="browser", source="official_cli")
    monkeypatch.setattr(account, "check", late)
    asyncio.run(auth.check("workbuddy"))
    assert auth.state["accounts"]["workbuddy"]["service_site"] == "international"
    assert auth.state["accounts"]["workbuddy"]["status"] == "not_checked"
    assert auth.state["accounts"]["workbuddy"]["authenticated"] is None


def test_invalid_workbuddy_site_has_safe_projection_without_touching_codex(tmp_path, monkeypatch):
    manager, account, auth, _ = registry(tmp_path, monkeypatch)
    manager.migrate_workbuddy_site()
    monkeypatch.setattr(account, "site_view", lambda: site.routing(account.context()), raising=False)
    codex = copy.deepcopy(auth.state["accounts"].get("codex"))
    manager.workbuddy.environment.mutate(lambda state: state.update(service_site_digest="wrong"))
    value = auth.view("workbuddy")
    assert value["failure_code"] == "workbuddy_service_site_invalid"
    assert not value["capabilities"]["login"] and value["account_url"] is None
    assert auth.state["accounts"].get("codex") == codex


@pytest.mark.parametrize("name", ["china", "international"])
def test_auth_control_messages_share_environment_through_recheck_and_logout(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location("site_login_helper", Path(__file__).resolve().parents[1] / "scripts/login-workbuddy-runtime.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    route = site.routing(site.make_configuration(name, 1))
    messages = []
    def response(identifier, value=None):
        return dict(type="control_response", response=dict(request_id=identifier, subtype="success", response=value or {}))
    sequences = [
        [response("login_init"), dict(type="control_request", request_id="url", request=dict(subtype="auth_url_callback", authState=dict(authUrl="https://" + route["auth_hosts"][0] + "/login"))),
         dict(type="control_request", request_id="done", request=dict(subtype="auth_result_callback", success=True, userinfo=dict(userId="synthetic")))],
        [response("login_init", dict(account=dict(userId="synthetic", token="test-only")))],
        [response("logout_init"), response("logout")],
    ]
    class Transport:
        def __init__(self): self.items = sequences.pop(0)
        async def connect(self): pass
        async def close(self): pass
        async def write(self, value): messages.append(json.loads(value))
        async def read_messages(self):
            for message in self.items: yield message
    async def run():
        result = await helper.run_authentication(Transport, check_only=False, method_id="external", no_browser=True,
            emit=lambda *_: None, environment=route["sdk_environment"], auth_hosts=route["auth_hosts"])
        assert result["authenticated"]
        await helper.run_logout(Transport, environment=route["sdk_environment"])
    asyncio.run(run())
    requests = [message["request"] for message in messages if message["type"] == "control_request"]
    assert [request["subtype"] for request in requests] == ["initialize", "authenticate", "initialize", "initialize", "logout"]
    assert all(request["environment"] == route["sdk_environment"] and request["endpoint"] is None for request in requests)
