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


class FakeSDKManager:
    def __init__(self) -> None:
        self.item = {
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
