"""Maintenance regressions with fake registry/installer/auth; no SDK, model or SAP."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.sdk_manager import SDKManager, SDKManagerError
from tests.test_sdk_manager import FakeAdapter, FakeSDKManager, HealthyEmbeddedProvider, _write_registry, _write_runtime_registry
from tests.test_runtime_authentication import service


def manager(tmp_path):
    registry = tmp_path / "sdks.json"
    _write_registry(registry)
    adapter = FakeAdapter()
    instance = SDKManager(registry, tmp_path, adapters={"python": adapter})
    return instance, adapter


def test_versions_only_never_reads_authentication_models_or_runtime_snapshot(tmp_path, monkeypatch):
    instance, adapter = manager(tmp_path)
    original = instance._runtime_config.copy()
    def forbidden(*args, **kwargs):
        pytest.fail("versions-only check entered an execution/authentication path")
    instance.authentication_service = SimpleNamespace(check=forbidden, view=forbidden)
    monkeypatch.setattr(instance, "_snapshot", forbidden)
    result = asyncio.run(instance.check_versions("example-python-sdk"))
    assert result["latest_version"] == "1.1.0"
    assert result["version_check"]["status"] == "checked"
    assert adapter.updates == []
    assert instance._runtime_config == original
    assert instance._state["example-python-sdk"]["authenticated"] is True
    assert instance._state["example-python-sdk"]["checked_at"] is None


def test_version_failure_is_unknown_not_already_current(tmp_path):
    instance, adapter = manager(tmp_path)
    async def failure(*args):
        raise SDKManagerError("Registry unavailable", code="sdk_registry_unavailable")
    adapter.latest_version = failure
    result = asyncio.run(instance.check_versions("example-python-sdk"))
    assert result["latest_version"] is None
    assert result["update_available"] is False
    assert result["version_check"]["error"] == {"code": "sdk_registry_unavailable", "phase": "version_check"}
    assert adapter.updates == []
    assert instance._state["example-python-sdk"]["error"] is None
    with pytest.raises(SDKManagerError) as caught:
        asyncio.run(instance.update("example-python-sdk"))
    assert caught.value.code == "sdk_registry_unavailable"
    assert adapter.updates == []


def test_workbuddy_missing_environment_never_falls_back_to_legacy_repair(tmp_path):
    registry = tmp_path / "sdks.json"
    _write_runtime_registry(registry)
    instance = SDKManager(registry, tmp_path, adapters={"python": FakeAdapter()})
    calls = []
    async def repair():
        calls.append("repair")
        return {"item": {"provider_id": "workbuddy", "current_version": "0.3.247"}, "restart_required": False}
    instance.workbuddy.update = repair
    result = asyncio.run(instance.check_versions("codebuddy-agent-sdk"))
    assert result["current_version"] is None and result["latest_version"]
    assert result["version_check_supported"] is True
    assert calls == []
    with pytest.raises(SDKManagerError) as update:
        asyncio.run(instance.update("codebuddy-agent-sdk"))
    assert update.value.code == "workbuddy_environment_not_installed"
    assert calls == []


@pytest.mark.parametrize("block", ["business", "cleanup", "lease", "authentication", "check"])
def test_maintenance_rejects_activity_before_installer(tmp_path, block):
    async def scenario():
        auth, _ = service(tmp_path / "auth", busy=lambda provider: [{"code": "cleanup_pending" if block == "cleanup" else "runtime_authentication_business_active"}] if block in {"business", "cleanup"} else [])
        if block == "lease": auth.leases["codex"] = 1
        if block == "check": auth.checking.add("codex")
        if block == "authentication":
            auth.state["operations"]["fixture"] = {"operation_id": "fixture", "provider_id": "codex", "status": "running", "created_at": "now"}
        with pytest.raises(SDKManagerError):
            async with auth.sdk_maintenance("codex"):
                pytest.fail("installer should never start")
        assert not auth.sdk_maintenance_active
    asyncio.run(scenario())


def test_maintenance_blocks_new_leases_and_checks_without_holding_lock_across_await(tmp_path):
    async def balanced():
        auth, adapter = service(tmp_path)
        binding = auth.bind("codex", {})
        def probe():
            with auth.lock:
                return True
        try:
            async with auth.sdk_maintenance("codex"):
                assert await asyncio.wait_for(asyncio.to_thread(probe), 1)
                with pytest.raises(SDKManagerError) as blocked:
                    async with auth.use("codex", binding): pass
                assert blocked.value.code == "sdk_maintenance_active"
                with pytest.raises(SDKManagerError): await auth.check("codex")
                assert adapter.calls == []
                raise ValueError("fake installer failure")
        except ValueError:
            pass
        assert not auth.sdk_maintenance_active
        async with auth.use("codex", binding): pass
    asyncio.run(balanced())


@pytest.mark.parametrize("failure", ["package", "not_applied"])
def test_installer_failure_keeps_safe_phase_and_never_claims_success(tmp_path, failure):
    instance, adapter = manager(tmp_path)
    if failure == "package":
        async def fail(*args): raise SDKManagerError("Package manager failed", code="sdk_package_manager_failed")
        adapter.update = fail
    else:
        async def no_change(*args): pass
        adapter.update = no_change
    with pytest.raises(SDKManagerError) as caught:
        asyncio.run(instance.update("example-python-sdk"))
    assert caught.value.detail["phase"] == ("installation" if failure == "package" else "version_confirmation")


def test_cancelled_request_waits_for_existing_installer_before_releasing_guard(tmp_path):
    async def scenario():
        instance, adapter = manager(tmp_path)
        auth, _ = service(tmp_path / "auth")
        # The one-provider fixture has a different provider ID; use its adapter
        # only for the maintenance reservation, never for an official check.
        provider = instance.definitions[0].provider_id
        auth.adapters[provider] = auth.adapters["codex"]
        instance.authentication_service = auth
        started, release = asyncio.Event(), asyncio.Event()
        async def install(*args):
            started.set()
            await release.wait()
            adapter.installed = "1.1.0"
        adapter.update = install
        request = asyncio.create_task(instance.update("example-python-sdk"))
        await started.wait()
        request.cancel()
        await asyncio.sleep(0)
        assert provider in auth.sdk_maintenance_active
        assert not request.done()
        release.set()
        with pytest.raises(asyncio.CancelledError): await request
        assert not auth.sdk_maintenance_active
    asyncio.run(scenario())


def test_existing_check_route_has_explicit_version_scope_and_default_full_behavior(tmp_path):
    from pathlib import Path
    sdk = FakeSDKManager()
    calls = []
    async def versions(sdk_id):
        calls.append(sdk_id)
        return {"sdk_id": sdk_id, "version_check": {"status": "checked"}}
    sdk.check_versions = versions
    settings = Settings(repository_root=Path(__file__).resolve().parents[1], data_root=tmp_path / "data", draft_root=tmp_path / "drafts", skillhub_root=tmp_path / "skillhub")
    app = create_app(settings, planner=object(), embedded_provider=HealthyEmbeddedProvider(), sdk_manager=sdk)
    with TestClient(app) as client:
        assert client.post("/api/system/sdks/codex-python-sdk/check?scope=versions").json()["item"]["version_check"]["status"] == "checked"
        assert calls == ["codex-python-sdk"]
        assert client.post("/api/system/sdks/codex-python-sdk/check").json()["item"]["check_status"] == "checked"
        assert client.post("/api/system/sdks/codex-python-sdk/check?scope=anything").status_code == 422
