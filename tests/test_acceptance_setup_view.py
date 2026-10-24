"""Dedicated GET view is display-only and skips full management assembly."""
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from tests.test_sdk_manager import FakeSDKManager, HealthyEmbeddedProvider


def test_setup_view_only_returns_identity_revision_and_formal_runtime(tmp_path, monkeypatch):
    manager = FakeSDKManager()
    calls = []
    snapshot = {"provider_id": "codex", "model": "unchanged", "authentication_binding_revision": 12}
    manager.runtime_snapshot = lambda: calls.append("snapshot") or dict(snapshot)
    app = create_app(Settings(repository_root=Path(__file__).resolve().parents[1], data_root=tmp_path / "data",
        draft_root=tmp_path / "drafts", skillhub_root=tmp_path / "skills"), planner=object(),
        embedded_provider=HealthyEmbeddedProvider(), sdk_manager=manager)
    app.state.store.save_agent_authoring_draft({"draft_id": "fixture", "agent_id": "offline-fixture",
        "revision": 5, "source_type": "blank", "status": "draft", "path": str(tmp_path / "draft"),
        "metadata": {"conversation": ["not returned"]}}, package={"manifest": {}, "rules": None})
    # Bypass app lifespan warmups: only exercise the registered GET handlers.
    client = TestClient(app)
    with patch("subprocess.Popen", side_effect=AssertionError("CLI forbidden")), patch("httpx.AsyncClient.send", side_effect=AssertionError("network forbidden")):
        monkeypatch.setattr(app.state.agent_lifecycle, "get_draft", lambda *a: (_ for _ in ()).throw(AssertionError("full draft forbidden")))
        value = client.get("/api/authoring/agents/fixture?view=acceptance_setup")
        assert value.status_code == 200
        assert set(value.json()) == {"draft_id", "revision", "formal_acceptance_runtime", "formal_acceptance_runtime_digest"}
        assert value.json()["formal_acceptance_runtime"] == snapshot
        assert len(value.json()["formal_acceptance_runtime_digest"]) == 64
        assert calls == ["snapshot"]
        assert client.get("/api/authoring/agents/missing?view=acceptance_setup").status_code == 404
        assert client.get("/api/authoring/agents/fixture?view=other").status_code == 422
        manager.runtime_snapshot = lambda: (_ for _ in ()).throw(type("Failure", (Exception,), {"code": "runtime_authentication_binding_invalid"})())
        failed = client.get("/api/authoring/agents/fixture?view=acceptance_setup").json()
        assert failed["formal_acceptance_runtime"] == {"available": False, "error": {"code": "runtime_authentication_binding_invalid"}}
        assert "formal_acceptance_runtime_digest" not in failed
        monkeypatch.setattr(app.state.agent_lifecycle, "get_draft", lambda _: {"ordinary": "unchanged response"})
        assert client.get("/api/authoring/agents/fixture").json()["ordinary"] == "unchanged response"
