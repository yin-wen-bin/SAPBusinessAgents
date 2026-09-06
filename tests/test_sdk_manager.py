from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.integrations import build_integration_adapters
from sap_business_agents_platform.sdk_manager import SDKDefinition, SDKManager


class FakeAdapter:
    def __init__(self, installed: str | None = "1.0.0", latest: str = "1.1.0") -> None:
        self.installed = installed
        self.latest = latest
        self.updates: list[str] = []

    def installed_version(self, definition: SDKDefinition) -> str | None:
        del definition
        return self.installed

    async def latest_version(self, definition: SDKDefinition) -> str:
        del definition
        return self.latest

    async def update(self, definition: SDKDefinition, target_version: str) -> None:
        del definition
        self.updates.append(target_version)
        self.installed = target_version


class FakeRuntimeProbe:
    def __init__(self, *, next_cursor: str | None = None, compatible: bool = True) -> None:
        self.next_cursor = next_cursor
        self.compatible = compatible
        self.authenticated = True
        self.authentication_status = "existing_login"

    async def check_authentication(self, definition: SDKDefinition) -> dict[str, Any]:
        del definition
        return {
            "authenticated": self.authenticated,
            "status": self.authentication_status,
            "error": None,
        }

    def client_version(self, definition: SDKDefinition) -> str:
        del definition
        return "1.0.0"

    async def list_models(self, definition: SDKDefinition) -> dict[str, Any]:
        del definition
        return {
            "models": [
                {
                    "id": "test-model",
                    "display_name": "Test Model",
                    "hidden": False,
                    "is_default": True,
                    "input_modalities": ["text"],
                    "default_reasoning_effort": "medium",
                    "supported_reasoning_efforts": [{"reasoning_effort": "medium"}],
                    "service_tiers": [{"id": "priority"}],
                },
                {"id": "hidden-model", "hidden": True},
            ],
            "next_cursor": self.next_cursor,
        }

    async def check_model(
        self, definition: SDKDefinition, model_id: str, workspace: Path
    ) -> dict[str, Any]:
        del definition
        assert model_id == "test-model"
        assert workspace.is_dir()
        return {
            "compatible": self.compatible,
            "status": "compatible" if self.compatible else "runtime_model_incompatible",
            "error": None if self.compatible else {
                "code": "runtime_model_incompatible",
                "message": "unsupported",
            },
        }


class FakeSDKManager:
    def __init__(self) -> None:
        self.default_provider_id = "codex"
        self.item = {
            "provider_id": "codex",
            "sdk_id": "codex-python-sdk",
            "name": {"zh": "Codex SDK", "en": "Codex SDK"},
            "description": {"zh": "测试", "en": "Test"},
            "ecosystem": "python",
            "package_name": "openai-codex",
            "installed": True,
            "current_version": "1.0.0",
            "latest_version": None,
            "update_available": False,
            "update_enabled": True,
            "restart_required": True,
            "checked_at": None,
            "check_status": "not_checked",
            "error": None,
            "authenticated": True,
            "authentication_status": "existing_login",
            "platform_supported": True,
            "capabilities": ["planning", "resume"],
            "availability": "active",
            "enabled": True,
            "can_enable": True,
            "selectable": True,
            "selected": True,
            "default_model_id": "test-model",
            "model_source": "system_settings",
            "model_catalog_status": "ready",
            "model_catalog_complete": True,
            "model_catalog_digest": "catalog-test",
            "model_count": 1,
            "model_discovery_supported": True,
            "model_check_supported": True,
            "cli_version": "1.0.0",
            "blockers": [],
        }
        self.model = {
            "model_id": "test-model",
            "display_name": "Test Model",
            "is_sdk_default": True,
            "input_modalities": ["text"],
            "default_reasoning_effort": "medium",
            "supported_reasoning_efforts": ["medium"],
            "service_tiers": [],
            "upgrade_target": None,
            "upgrade_message": None,
            "retired": False,
            "selected": True,
            "check_status": "compatible",
            "selectable": True,
            "check": {"compatible": True},
        }

    def list(self) -> list[dict[str, Any]]:
        return [dict(self.item)]

    async def check_all(self) -> list[dict[str, Any]]:
        return [(await self.check("codex-python-sdk"))]

    async def check(self, sdk_id: str) -> dict[str, Any]:
        assert sdk_id == "codex-python-sdk"
        self.item.update(
            latest_version="1.1.0",
            update_available=True,
            checked_at="2026-08-16T00:00:00+00:00",
            check_status="checked",
        )
        return dict(self.item)

    async def check_provider(self, provider_id: str) -> dict[str, Any]:
        assert provider_id == "codex"
        return await self.check("codex-python-sdk")

    def set_default(self, provider_id: str) -> dict[str, Any]:
        assert provider_id == "codex"
        self.default_provider_id = provider_id
        return dict(self.item)

    def models(self, provider_id: str) -> dict[str, Any]:
        assert provider_id == "codex"
        return {
            "provider_id": provider_id,
            "model_catalog_complete": True,
            "catalog_status": "ready",
            "catalog_digest": "catalog-test",
            "captured_at": "2026-09-06T00:00:00+00:00",
            "default_model_id": self.item["default_model_id"],
            "items": [dict(self.model)],
        }

    async def refresh_models(self, provider_id: str) -> dict[str, Any]:
        return self.models(provider_id)

    async def check_model(self, provider_id: str, model_id: str) -> dict[str, Any]:
        assert provider_id == "codex"
        assert model_id == "test-model"
        return {"provider_id": provider_id, "item": dict(self.model), "check": {"compatible": True}}

    async def set_default_model(self, provider_id: str, model_id: str) -> dict[str, Any]:
        assert provider_id == "codex"
        assert model_id == "test-model"
        self.item["default_model_id"] = model_id
        return dict(self.item)

    def set_enabled(self, provider_id: str, enabled: bool) -> dict[str, Any]:
        assert provider_id == "codex"
        self.item["enabled"] = enabled
        self.item["selectable"] = enabled
        self.item["selected"] = enabled and self.default_provider_id == provider_id
        if not enabled:
            self.default_provider_id = None
            self.item["selected"] = False
        return dict(self.item)

    async def update(self, sdk_id: str) -> dict[str, Any]:
        assert sdk_id == "codex-python-sdk"
        self.item.update(current_version="1.1.0", update_available=False)
        return dict(self.item)


class HealthyEmbeddedProvider:
    async def health(self) -> dict[str, Any]:
        return {"ok": True, "data": {"runtime_ready": True, "read_only": True}}


def _write_registry(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sdks": [
                    {
                        "sdk_id": "example-python-sdk",
                        "name": {"zh": "示例 SDK", "en": "Example SDK"},
                        "description": {"zh": "用于测试。", "en": "Used for tests."},
                        "ecosystem": "python",
                        "package_name": "example-sdk",
                        "update_enabled": True,
                        "restart_required": True,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_runtime_registry(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "defaultProviderId": "codex",
                "providers": [
                    {
                        "providerId": "codex",
                        "sdkId": "codex-python-sdk",
                        "name": {"zh": "Codex", "en": "Codex"},
                        "description": {"zh": "默认", "en": "Default"},
                        "ecosystem": "python",
                        "packageName": "openai-codex",
                        "moduleName": "openai_codex",
                        "availability": "active",
                        "selectable": True,
                        "platforms": ["windows"],
                        "capabilities": ["planning"],
                        "authentication": "existing_login",
                        "providerImplemented": True,
                        "liveValidated": True,
                        "updateEnabled": True,
                        "restartRequired": True,
                        "blockers": [],
                    },
                    {
                        "providerId": "workbuddy",
                        "sdkId": "codebuddy-agent-sdk",
                        "name": {"zh": "WorkBuddy", "en": "WorkBuddy"},
                        "description": {"zh": "测试", "en": "Test"},
                        "ecosystem": "python",
                        "packageName": "codebuddy-agent-sdk",
                        "moduleName": "codebuddy_agent_sdk",
                        "availability": "active",
                        "selectable": True,
                        "platforms": ["windows"],
                        "capabilities": ["planning", "resume"],
                        "authentication": "existing_login",
                        "providerImplemented": True,
                        "liveValidated": True,
                        "updateEnabled": True,
                        "restartRequired": True,
                        "blockers": [],
                    },
                    {
                        "providerId": "deepseek-harness",
                        "sdkId": "deepseek-harness-sdk",
                        "name": {"zh": "DeepSeek", "en": "DeepSeek"},
                        "description": {"zh": "预留", "en": "Reserved"},
                        "ecosystem": "python",
                        "packageName": "deepseek-harness-sdk",
                        "moduleName": "deepseek_harness",
                        "availability": "reserved_blocked",
                        "selectable": False,
                        "platforms": ["linux-x64"],
                        "capabilities": ["planning"],
                        "authentication": "not_configured",
                        "providerImplemented": False,
                        "liveValidated": False,
                        "updateEnabled": False,
                        "restartRequired": True,
                        "blockers": ["native_windows_runtime_unavailable"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_sdk_manager_checks_and_updates_registered_sdk(tmp_path: Path) -> None:
    registry = tmp_path / "sdks.json"
    _write_registry(registry)
    adapter = FakeAdapter()
    manager = SDKManager(registry, tmp_path, adapters={"python": adapter})

    initial = manager.list()[0]
    assert initial["current_version"] == "1.0.0"
    assert initial["latest_version"] is None
    assert initial["check_status"] == "not_checked"

    checked = asyncio.run(manager.check("example-python-sdk"))
    assert checked["latest_version"] == "1.1.0"
    assert checked["update_available"] is True

    updated = asyncio.run(manager.update("example-python-sdk"))
    assert updated["current_version"] == "1.1.0"
    assert updated["update_available"] is False
    assert updated["updated_from"] == "1.0.0"
    assert adapter.updates == ["1.1.0"]


def test_runtime_selection_is_gated_and_persisted(tmp_path: Path) -> None:
    registry = tmp_path / "sdks.json"
    state = tmp_path / "runtime" / "default.json"
    _write_runtime_registry(registry)
    adapter = FakeAdapter()
    manager = SDKManager(
        registry,
        tmp_path,
        adapters={"python": adapter},
        runtime_probes={
            "codex": FakeRuntimeProbe(),
            "workbuddy": FakeRuntimeProbe(),
        },
        selection_path=state,
    )

    assert manager.default_provider_id == "codex"
    assert next(
        item for item in manager.list() if item["provider_id"] == "workbuddy"
    )["selectable"] is False

    checked = asyncio.run(manager.check_provider("workbuddy"))
    assert checked["authenticated"] is True
    assert checked["selectable"] is False
    catalog = asyncio.run(manager.refresh_models("workbuddy"))
    assert [item["model_id"] for item in catalog["items"]] == ["test-model"]
    assert catalog["model_catalog_complete"] is True
    asyncio.run(manager.set_default_model("workbuddy", "test-model"))
    enabled = manager.set_enabled("workbuddy", True)
    assert enabled["selectable"] is True
    selected = manager.set_default("workbuddy")
    assert selected["provider_id"] == "workbuddy"
    assert manager.default_provider_id == "workbuddy"

    reloaded = SDKManager(
        registry,
        tmp_path,
        adapters={"python": adapter},
        runtime_probes={
            "codex": FakeRuntimeProbe(),
            "workbuddy": FakeRuntimeProbe(),
        },
        selection_path=state,
    )
    assert reloaded.default_provider_id == "workbuddy"
    assert next(
        item for item in reloaded.list() if item["provider_id"] == "workbuddy"
    )["default_model_id"] == "test-model"

    try:
        manager.set_default("deepseek-harness")
    except Exception as exc:
        assert getattr(exc, "code", "") == "runtime_not_selectable"
    else:
        raise AssertionError("A reserved runtime must not become the default")


def test_sdk_registry_v2_defaults_integrations_to_unavailable(tmp_path: Path) -> None:
    registry = tmp_path / "sdks.json"
    _write_runtime_registry(registry)
    manager = SDKManager(
        registry,
        tmp_path,
        adapters={"python": FakeAdapter()},
        runtime_probes={"codex": FakeRuntimeProbe()},
        selection_path=tmp_path / "runtime.json",
    )

    codex = next(item for item in manager.list() if item["provider_id"] == "codex")
    assert codex["integration_runtime"]["adapter_id"] is None
    assert codex["integration_runtime"]["features"]["direct_tool_call"] is False
    assert codex["integration_runtime"]["credential_owner"] == "runtime"


def test_repository_sdk_registry_v3_exposes_multi_runtime_integration_matrix(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    manager = SDKManager(
        repository / "config" / "sdks.json",
        repository,
        adapters={"python": FakeAdapter()},
        selection_path=tmp_path / "runtime.json",
    )
    runtimes = {item["provider_id"]: item for item in manager.list()}

    assert runtimes["codex"]["integration_runtime"]["adapter_id"] == "codex-app-server"
    assert runtimes["codex"]["integration_runtime"]["features"]["direct_tool_call"] is True
    assert runtimes["workbuddy"]["integration_runtime"]["features"]["direct_tool_call"] is False
    assert runtimes["deepseek-harness"]["integration_runtime"]["readiness"] == "reserved"

    adapters = build_integration_adapters(repository, manager)
    codex = next(item for item in adapters if item.provider_id == "codex")
    assert codex.describe_capabilities()["features"]["directToolCall"] is True


def test_runtime_model_catalog_filters_hidden_and_rejects_incomplete_catalog(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "sdks.json"
    _write_runtime_registry(registry)
    manager = SDKManager(
        registry,
        tmp_path,
        adapters={"python": FakeAdapter()},
        runtime_probes={"codex": FakeRuntimeProbe(next_cursor="more")},
        selection_path=tmp_path / "runtime.json",
    )

    catalog = asyncio.run(manager.refresh_models("codex"))
    assert catalog["model_catalog_complete"] is False
    assert [item["model_id"] for item in catalog["items"]] == ["test-model"]
    try:
        asyncio.run(manager.check_model("codex", "test-model"))
    except Exception as exc:
        assert getattr(exc, "code", "") == "runtime_model_catalog_required"
    else:
        raise AssertionError("An incomplete SDK model catalog must fail closed")


def test_runtime_can_disable_all_without_changing_selected_model(tmp_path: Path) -> None:
    registry = tmp_path / "sdks.json"
    _write_runtime_registry(registry)
    manager = SDKManager(
        registry,
        tmp_path,
        adapters={"python": FakeAdapter()},
        runtime_probes={"codex": FakeRuntimeProbe()},
        selection_path=tmp_path / "runtime.json",
    )
    asyncio.run(manager.refresh_models("codex"))
    asyncio.run(manager.set_default_model("codex", "test-model"))
    assert manager.runtime_snapshot()["model"] == "test-model"

    disabled = manager.set_enabled("codex", False)
    assert disabled["enabled"] is False
    assert disabled["default_model_id"] == "test-model"
    assert manager.default_provider_id is None
    try:
        manager.runtime_snapshot()
    except Exception as exc:
        assert getattr(exc, "code", "") == "runtime_default_not_configured"
    else:
        raise AssertionError("New Runtime tasks must stop when every Runtime is disabled")

    enabled = manager.set_enabled("codex", True)
    assert enabled["enabled"] is True
    assert manager.default_provider_id is None
    manager.set_default("codex")
    assert manager.runtime_snapshot()["model"] == "test-model"


def test_authentication_state_change_invalidates_prior_model_checks(tmp_path: Path) -> None:
    registry = tmp_path / "sdks.json"
    _write_runtime_registry(registry)
    probe = FakeRuntimeProbe()
    manager = SDKManager(
        registry,
        tmp_path,
        adapters={"python": FakeAdapter()},
        runtime_probes={"codex": probe},
        selection_path=tmp_path / "runtime.json",
    )
    asyncio.run(manager.refresh_models("codex"))
    checked = asyncio.run(manager.check_model("codex", "test-model"))
    assert checked["check"]["authentication_revision"] == 0
    assert manager.models("codex")["items"][0]["check_status"] == "compatible"

    probe.authenticated = False
    probe.authentication_status = "login_required"
    asyncio.run(manager.check("codex-python-sdk"))
    assert manager.models("codex")["items"][0]["check_status"] == "check_required"

    probe.authenticated = True
    probe.authentication_status = "existing_login"
    asyncio.run(manager.check("codex-python-sdk"))
    assert manager.models("codex")["items"][0]["check_status"] == "check_required"
    rechecked = asyncio.run(manager.check_model("codex", "test-model"))
    assert rechecked["check"]["authentication_revision"] == 2
    assert manager.models("codex")["items"][0]["check_status"] == "compatible"


def test_v1_runtime_state_preserves_unsupported_legacy_model_without_fallback(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "sdks.json"
    state = tmp_path / "runtime.json"
    _write_runtime_registry(registry)
    state.write_text(
        json.dumps({"schema_version": 1, "provider_id": "codex"}), encoding="utf-8"
    )
    manager = SDKManager(
        registry,
        tmp_path,
        adapters={"python": FakeAdapter()},
        runtime_probes={"codex": FakeRuntimeProbe()},
        selection_path=state,
        legacy_model="gpt-6-astra",
    )

    asyncio.run(manager.refresh_models("codex"))
    item = next(value for value in manager.list() if value["provider_id"] == "codex")
    assert item["default_model_id"] == "gpt-6-astra"
    assert item["model_status"] == "retired"
    assert "runtime_model_retired" in item["blockers"]


def test_sdk_api_requires_explicit_update_header(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    settings = Settings(
        repository_root=repository,
        data_root=tmp_path / "data",
        draft_root=tmp_path / "drafts",
        skillhub_root=tmp_path / "skillhub",
        max_run_seconds=10,
    )
    sdk_manager = FakeSDKManager()
    app = create_app(
        settings,
        planner=object(),  # no planner operation is used by this API test
        embedded_provider=HealthyEmbeddedProvider(),
        sdk_manager=sdk_manager,
    )

    with TestClient(app) as client:
        listed = client.get("/api/system/sdks")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["current_version"] == "1.0.0"

        checked = client.post("/api/system/sdks/check")
        assert checked.status_code == 200
        assert checked.json()["items"][0]["update_available"] is True

        rejected = client.post("/api/system/sdks/codex-python-sdk/update")
        assert rejected.status_code == 403

        updated = client.post(
            "/api/system/sdks/codex-python-sdk/update",
            headers={"X-SAPBA-Action": "sdk-update"},
        )
        assert updated.status_code == 200
        assert updated.json()["item"]["current_version"] == "1.1.0"
        assert updated.json()["restart_required"] is True

        runtimes = client.get("/api/system/sdk-runtimes")
        assert runtimes.status_code == 200
        assert runtimes.json()["default_provider_id"] == "codex"
        runtime_check = client.post("/api/system/sdk-runtimes/codex/check")
        assert runtime_check.status_code == 200
        selected = client.put(
            "/api/system/sdk-runtimes/default",
            json={"provider_id": "codex"},
        )
        assert selected.status_code == 200

        models = client.get("/api/system/sdk-runtimes/codex/models")
        assert models.status_code == 200
        assert [item["model_id"] for item in models.json()["items"]] == ["test-model"]
        refreshed = client.post("/api/system/sdk-runtimes/codex/models/refresh")
        assert refreshed.status_code == 200
        checked_model = client.post(
            "/api/system/sdk-runtimes/codex/models/check", json={"model_id": "test-model"}
        )
        assert checked_model.status_code == 200
        selected_model = client.put(
            "/api/system/sdk-runtimes/codex/default-model", json={"model_id": "test-model"}
        )
        assert selected_model.status_code == 200
        rejected_disable = client.post(
            "/api/system/sdk-runtimes/codex/disable",
            json={"confirm_provider_id": "codex"},
        )
        assert rejected_disable.status_code == 403
        disabled = client.post(
            "/api/system/sdk-runtimes/codex/disable",
            headers={"X-SAPBA-Action": "runtime-disable"},
            json={"confirm_provider_id": "codex"},
        )
        assert disabled.status_code == 200
        assert sdk_manager.default_provider_id is None
        enabled = client.post("/api/system/sdk-runtimes/codex/enable")
        assert enabled.status_code == 200
