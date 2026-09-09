from pathlib import Path

from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from tests.test_sdk_manager import FakeSDKManager, HealthyEmbeddedProvider


class EffortManager(FakeSDKManager):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def check_model(self, provider_id, model_id, reasoning_effort=None):
        self.calls.append(("check", provider_id, model_id, reasoning_effort))
        return {"check": {"compatible": True}}

    async def set_reasoning_effort(self, provider_id, model_id, reasoning_effort):
        self.calls.append(("save", provider_id, model_id, reasoning_effort))
        return {"model_id": model_id, "reasoning_effort": reasoning_effort}


def test_identity_and_effort_http_contracts_are_isolated(tmp_path):
    manager = EffortManager()
    settings = Settings(repository_root=Path(__file__).resolve().parents[1],
                        data_root=tmp_path / "data", draft_root=tmp_path / "drafts",
                        skillhub_root=tmp_path / "skillhub")
    app = create_app(settings, planner=object(), embedded_provider=HealthyEmbeddedProvider(), sdk_manager=manager)
    with TestClient(app) as client:
        create = client.post("/api/authoring/agents", json={"source": "blank", "agentId": "  api-test-agent  ", "module": "Common"})
        assert create.status_code == 201, create.text
        draft = create.json()
        assert draft["agent_id"] == "api-test-agent"
        assert not draft["technical_identity"]["confirmed"]
        url = f'/api/authoring/agents/{draft["draft_id"]}/technical-id'
        check = client.post(url + "/check", json={"agentId": "api-test-agent"})
        assert check.status_code == 200
        assert check.json()["reserved"] is False
        invalid = client.post(url + "/check", json={"agentId": "con"})
        assert invalid.status_code == 409
        assert invalid.json()["detail"]["code"] == "agent_technical_id_invalid"
        confirm = client.put(url, json={"agentId": "api-test-agent", "expectedRevision": 1})
        assert confirm.status_code == 200
        assert confirm.json()["revision"] == 1
        assert confirm.json()["technical_identity"]["confirmed"]
        rename = client.put(url, json={"agentId": "api-receivables-check", "expectedRevision": 1})
        assert rename.status_code == 200
        assert rename.json()["revision"] == 2
        conflict = client.put(url, json={"agentId": "another-agent", "expectedRevision": 1})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "agent_draft_conflict"
        duplicate = client.post("/api/authoring/agents", json={"source": "blank", "agentId": "api-receivables-check", "module": "Common"})
        assert duplicate.status_code == 409
        assert client.post("/api/authoring/agents/missing/technical-id/check", json={"agentId": "valid-agent"}).status_code == 404
        assert client.post("/api/authoring/agents", json={"source": "blank", "agentId": "invalid--agent", "module": "Common"}).status_code == 422

        base = "/api/system/sdk-runtimes/codex"
        assert client.post(base + "/models/check", json={"model_id": "test-model", "reasoning_effort": "high"}).status_code == 200
        saved = client.put(base + "/models/test-model/reasoning-effort", json={"reasoning_effort": "high"})
        assert saved.status_code == 200
        assert saved.json()["item"]["reasoning_effort"] == "high"
        assert manager.calls == [("check", "codex", "test-model", "high"), ("save", "codex", "test-model", "high")]
        assert manager.item["default_model_id"] == "test-model"
        assert client.put(base + "/models/test-model/reasoning-effort", json={"reasoning_effort": ""}).status_code == 422


def test_legacy_apply_cannot_publish_without_identity_confirmation(tmp_path, monkeypatch):
    from sap_business_agents_platform.models import DraftRecord
    settings = Settings(repository_root=Path(__file__).resolve().parents[1], data_root=tmp_path / "data", draft_root=tmp_path / "drafts", skillhub_root=tmp_path / "skillhub")
    app = create_app(settings, planner=object(), embedded_provider=HealthyEmbeddedProvider(), sdk_manager=EffortManager())
    with TestClient(app) as client:
        source = DraftRecord(draft_id="draft_api_legacy", run_id="run_mock", status="validated", path=str(tmp_path / "source"), created_at="2026-09-09T00:00:00Z", validation={"valid": True})
        app.state.store.save_draft(source)
        monkeypatch.setattr(app.state.drafts, "apply", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No publication allowed")))
        response = client.post(f"/api/authoring/drafts/{source.draft_id}/apply")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "agent_managed_publication_required"
