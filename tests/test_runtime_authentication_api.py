"""Unified HTTP contract with fake authentication; never touches official caches."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient
from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.runtime_authentication import error


class Auth:
    cleanup_complete = True
    account_url = "https://chatgpt.com/"
    usage_url = None
    configuration_url = "https://learn.chatgpt.com/docs/auth"
    def __init__(self): self.calls = []
    def capabilities(self): return dict(check=True, login=True, logout=True, switch_account=True)
    async def check(self):
        self.calls.append("check")
        return dict(authenticated=True, identity="private@example.test", source="official_cli", method="browser")
    async def execute(self, action, event):
        self.calls.append(action)
        return dict(authenticated=action != "logout", source="official_cli", method="browser")


class Healthy:
    async def health(self): return {"status": "healthy", "provider": "test"}


def test_authentication_api_confirmation_origin_idempotency_and_safe_errors(tmp_path):
    adapter = Auth()
    root = Path(__file__).resolve().parents[1]
    manager = SimpleNamespace(default_provider_id="codex", list=lambda: [
        {"provider_id": "codex", "sdk_id": "codex-python-sdk", "authenticated": True, "enabled": True, "selectable": True}])
    app = create_app(Settings(repository_root=root, data_root=tmp_path / "data", draft_root=tmp_path / "drafts", skillhub_root=tmp_path / "skills"),
        planner=object(), sdk_manager=manager, embedded_provider=Healthy(), authentication_adapters={"codex": adapter})
    base = "/api/system/sdk-runtimes/codex/authentication"
    header = {"X-SAPBA-Action": "runtime-authentication", "Origin": "http://127.0.0.1:4321"}
    with TestClient(app) as client:
        status = client.get(base)
        assert status.status_code == 200
        assert status.headers["cache-control"] == "no-store"
        assert adapter.calls == []
        assert client.post(base + "/check").status_code == 403
        assert client.post(base + "/check", headers={**header, "Origin": "https://evil.test"}).status_code == 403
        assert client.post(base + "/check", headers=header).json()["authentication"]["identity"] == "pr***@example.test"
        body = {"requestId": "same", "action": "logout"}
        assert client.post(base + "/operations", headers=header, json=body).status_code == 403
        rejected = client.post(base + "/operations", headers=header, json={**body, "secret": "must-not-echo"})
        assert rejected.status_code == 422 and "must-not-echo" not in rejected.text
        body["confirm_provider_id"] = "codex"
        first = client.post(base + "/operations", headers=header, json=body)
        assert first.status_code == 202
        second = client.post(base + "/operations", headers=header, json=body)
        assert first.json()["operation_id"] == second.json()["operation_id"]
        conflict = client.post(base + "/operations", headers=header, json={**body, "action": "switch_account"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "runtime_authentication_request_conflict"
        operation = client.get(base + "/operations/" + first.json()["operation_id"])
        assert operation.status_code == 200
        assert "fingerprint" not in operation.json()["operation"]
        assert adapter.calls.count("logout") == 1
        assert client.get(base.replace("codex", "other")).status_code == 404
