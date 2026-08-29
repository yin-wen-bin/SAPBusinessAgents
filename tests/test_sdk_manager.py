from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
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
    async def check_authentication(self, definition: SDKDefinition) -> dict[str, Any]:
        del definition
        return {"authenticated": True, "status": "existing_login", "error": None}


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
            "selectable": True,
            "selected": True,
            "blockers": [],
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
    assert checked["selectable"] is True
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

    try:
        manager.set_default("deepseek-harness")
    except Exception as exc:
        assert getattr(exc, "code", "") == "runtime_not_selectable"
    else:
        raise AssertionError("A reserved runtime must not become the default")


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
