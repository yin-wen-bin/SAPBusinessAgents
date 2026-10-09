from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.free_query_history import FreeQueryHistory
from sap_business_agents_platform.factory import AgentDraftService, DraftError
from sap_business_agents_platform.models import RunCreate, RunMode, RunResult, RunStatus, Completeness
from tests.test_platform_runtime import _settings, FakePlanner, FakeEmbeddedProvider

NOW = datetime(2030, 1, 31, tzinfo=timezone.utc)
PLAN = {"service_name": "API_PURCHASEORDER_PROCESS_SRV", "odata_version": "2.0",
        "entity_set": "A_PurchaseOrder", "http_method": "GET", "plan_kind": "direct",
        "filters": [], "top": 10}


def seed(store, run_id, *, age=0, status="completed", session=None, iteration=1, query_origin="user"):
    stamp = (NOW - timedelta(days=age)).isoformat()
    store.create_run(run_id, RunCreate(mode=RunMode.free_query, query="Synthetic query"), query_origin=query_origin)
    result = RunResult(run_id=run_id, mode=RunMode.free_query, plan=PLAN,
                       completeness=Completeness(source_complete=True, business_complete=True))
    store.update_run(run_id, status=RunStatus(status), plan_json=PLAN, result_json=result,
                     completed_at=stamp if status in {"completed", "inconclusive", "failed", "cancelled"} else None)
    with store._connect() as db:
        db.execute("UPDATE runs SET created_at=? WHERE run_id=?", (stamp, run_id))
    if session:
        if iteration == 1:
            store.create_free_query_session(session_id=session, run_id=run_id,
                original_query="Synthetic original question", runtime={})
        else:
            store.create_free_query_iteration(session_id=session, iteration=iteration,
                run_id=run_id, parent_iteration=iteration - 1, feedback="Synthetic follow-up",
                feedback_type="scope", execution_action="requery", decision={})
        with store._connect() as db:
            db.execute("UPDATE free_query_iterations SET created_at=?,completed_at=?,result_digest='synthetic-digest' WHERE run_id=?", (stamp, stamp, run_id))
        store.update_free_query_session(session, status="reviewing")


def service(tmp_path):
    store = RunStore(tmp_path / "data" / "platform.sqlite3")
    history = FreeQueryHistory(store, tmp_path / "data", now=lambda: NOW)
    return store, history


def test_system_queries_are_excluded_and_their_audit_evidence_never_expires(tmp_path):
    store, history = service(tmp_path)
    seed(store, "manual", age=1, session="user-session")
    seed(store, "expired-manual", age=31)
    seed(store, "acceptance", age=31, session="acceptance-session", query_origin="system")
    seed(store, "acceptance-follow-up", age=31, session="acceptance-session", iteration=2, query_origin="system")
    seed(store, "sample", age=31, query_origin="system")
    for run_id in ("acceptance", "acceptance-follow-up", "sample"):
        store.append_event(run_id, "audit_evidence", {"synthetic": True})
        directory = history.data_root / "artifacts" / run_id
        directory.mkdir(parents=True)
        (directory / "evidence.txt").write_text("synthetic")
    listing = history.list(limit=1)
    assert listing["total"] == 1
    assert listing["items"][0]["history_id"] == "user-session"
    with pytest.raises(KeyError):
        history.retry_query("acceptance-session")
    # Internal authoring can still use system evidence outside user history.
    with history.protect(session_id="acceptance-session"):
        asyncio.run(history.cleanup())
    with pytest.raises(KeyError):
        store.get_run("expired-manual")
    for run_id in ("acceptance", "acceptance-follow-up", "sample"):
        assert store.get_run(run_id).query_origin == "system"
        assert store.events_after(run_id)[0].type == "audit_evidence"
        assert (history.data_root / "artifacts" / run_id / "evidence.txt").exists()
    assert store.get_free_query_session("acceptance-session")


def test_internal_queries_default_to_system_even_after_restart(tmp_path):
    store, history = service(tmp_path)
    store.create_run("run_internal", RunCreate(mode=RunMode.free_query, query="Synthetic internal query"))
    store.append_event("run_internal", "run_queued", {"mode": "free_query"})
    reopened = RunStore(store.path)
    assert reopened.get_run("run_internal").query_origin == "system"
    assert FreeQueryHistory(reopened, history.data_root, now=lambda: NOW).list()["total"] == 0


def test_legacy_origin_migration_preserves_manual_queries_and_excludes_system_sources(tmp_path):
    store, history = service(tmp_path)
    seed(store, "manual-first", session="manual-session")
    seed(store, "manual-follow-up", session="manual-session", iteration=2)
    seed(store, "legacy-manual")
    seed(store, "acceptance_native", session="acceptance-session")
    seed(store, "run_acceptance_follow_up", session="acceptance-session", iteration=2)
    seed(store, "run_cli_acceptance")
    seed(store, "run_sample")
    seed(store, "run_campaign")
    seed(store, "unknown")
    for run_id in ("manual-first", "manual-follow-up", "legacy-manual", "acceptance_native",
                   "run_acceptance_follow_up", "run_cli_acceptance", "run_sample", "run_campaign"):
        store.append_event(run_id, "run_queued", {"mode": "free_query"})
    store.update_harness_state("run_cli_acceptance", {"acceptance_spec": {"record_fields": ["document"]}})
    store.append_event("run_sample", "sample_discovery_started", {})
    store.append_event("run_campaign", "run_queued", {"mode": "free_query", "acceptance_campaign": True})
    with store._connect() as db:
        db.execute("ALTER TABLE runs DROP COLUMN query_origin")
    reopened = RunStore(store.path)
    manual = {"manual-first", "manual-follow-up", "legacy-manual"}
    assert {run.run_id for run in reopened.list_runs() if run.query_origin == "user"} == manual
    assert {row["history_id"] for row in FreeQueryHistory(reopened, history.data_root, now=lambda: NOW).list()["items"]} == {"manual-session", "legacy-manual"}
    # Migrating and excluding system records keeps their runtime evidence intact.
    assert reopened.get_run("run_cli_acceptance")
    assert reopened.get_harness_state("run_cli_acceptance")["acceptance_spec"]
    assert reopened.get_free_query_session("acceptance-session")


def test_upgrade_revokes_pending_history_deletion_of_system_evidence(tmp_path):
    store, history = service(tmp_path)
    seed(store, "run_acceptance", age=31, session="acceptance-session")
    store.update_harness_state("run_acceptance", {"acceptance_spec": {"record_fields": ["document"]}})
    directory = history.data_root / "artifacts" / "run_acceptance"
    directory.mkdir(parents=True)
    (directory / "audit.txt").write_text("synthetic")
    with store._connect() as db:
        db.execute("ALTER TABLE runs DROP COLUMN query_origin")
        db.execute("INSERT INTO free_query_history_cleanup VALUES (?,?,?,?,?,?)",
                   ("acceptance-session", "acceptance-session", json.dumps(["run_acceptance"]), "[]", NOW.isoformat(), "pending"))
        db.execute("INSERT INTO free_query_history_expired_runs VALUES (?,?)", ("run_acceptance", "acceptance-session"))
    reopened = RunStore(store.path)
    assert reopened.get_run("run_acceptance").query_origin == "system"
    assert reopened.get_free_query_session("acceptance-session")
    asyncio.run(FreeQueryHistory(reopened, history.data_root, now=lambda: NOW).cleanup())
    assert (directory / "audit.txt").exists()
    with reopened._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM free_query_history_cleanup").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM free_query_history_expired_runs").fetchone()[0] == 0


def test_mixed_origin_session_is_not_eligible_for_user_history_cleanup(tmp_path):
    store, history = service(tmp_path)
    seed(store, "first", age=31, session="mixed")
    seed(store, "system-evidence", age=31, session="mixed", iteration=2, query_origin="system")
    seed(store, "latest", age=31, session="mixed", iteration=3)
    assert history.list()["total"] == 0
    asyncio.run(history.cleanup())
    assert store.get_run("system-evidence")


def test_history_groups_sessions_and_paginates_before_loading_results(tmp_path):
    store, history = service(tmp_path)
    seed(store, "old-round", age=12, session="session-a")
    seed(store, "new-round", age=1, session="session-a", iteration=2)
    seed(store, "failed", age=2, status="failed", session="session-b")
    seed(store, "legacy", age=3)
    seed(store, "expired", age=30)
    with store._connect() as db:
        db.executemany("INSERT INTO runs(run_id,mode,status,input_json,created_at) VALUES (?,'agent','completed','{}',?)",
                       [(f"agent-{i}", NOW.isoformat()) for i in range(205)])
    first = history.list(limit=2)
    assert first["total"] == 3
    assert [row["history_id"] for row in first["items"]] == ["session-a", "session-b"]
    assert first["items"][0]["iteration"] == 2
    assert "result" not in first["items"][0] and "plan" not in first["items"][0]
    assert first["items"][1]["can_create_draft"] is False
    assert history.list(limit=2, offset=2)["items"][0]["session_id"] is None


def test_retention_ignores_view_acceptance_and_draft_timestamps(tmp_path):
    store, history = service(tmp_path)
    seed(store, "round-1", age=29, session="session")
    before = history.list()["items"][0]["expires_at"]
    store.update_free_query_session("session", status="satisfied", accepted_at=NOW.isoformat())
    assert history.list()["items"][0]["expires_at"] == before
    seed(store, "round-2", age=1, session="session", iteration=2)
    assert history.list()["items"][0]["expires_at"] == (NOW + timedelta(days=29)).isoformat()
    history.update_settings(1)
    assert history.list()["items"] == []  # equality is expired
    reopened = FreeQueryHistory(RunStore(store.path), history.data_root, now=lambda: NOW)
    assert reopened.settings() == {"retention_days": 1}


def test_cleanup_removes_private_state_and_files_but_retains_drafts(tmp_path):
    store, history = service(tmp_path)
    seed(store, "expired", age=30, session="session")
    seed(store, "keep", age=29)
    store.append_event("expired", "synthetic", {"data": "synthetic"})
    draft_dir = tmp_path / "drafts" / "draft_saved"
    draft_dir.mkdir(parents=True)
    (draft_dir / "agent.json").write_text("{}")
    with store._connect() as db:
        db.execute("INSERT INTO drafts VALUES ('draft_saved','expired','validated',?,'{}','{}',?)", (str(draft_dir), NOW.isoformat()))
    for folder in ("artifacts", "restricted-artifacts", "harness"):
        directory = history.data_root / folder / "expired"
        directory.mkdir(parents=True)
        (directory / "synthetic.txt").write_text("synthetic")
    asyncio.run(history.cleanup())
    with pytest.raises(KeyError): store.get_run("expired")
    with pytest.raises(KeyError): store.get_free_query_session("session")
    assert store.get_draft("draft_saved").path == str(draft_dir)
    assert (draft_dir / "agent.json").exists()
    assert store.get_run("keep")
    for folder in ("artifacts", "restricted-artifacts", "harness"):
        assert not (history.data_root / folder / "expired").exists()
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM events WHERE run_id='expired'").fetchone()[0] == 0


def test_cleanup_resumes_after_file_failure_and_hides_pending_record(tmp_path, monkeypatch):
    store, history = service(tmp_path)
    seed(store, "expired", age=31, session="session")
    directory = history.data_root / "artifacts" / "expired"
    directory.mkdir(parents=True)
    import sap_business_agents_platform.free_query_history as module
    original = module.shutil.rmtree
    monkeypatch.setattr(module.shutil, "rmtree", lambda _: (_ for _ in ()).throw(OSError("synthetic locked file")))
    asyncio.run(history.cleanup())
    assert history.list()["items"] == []
    with pytest.raises(KeyError): store.get_run("expired")
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM free_query_history_cleanup").fetchone()[0] == 1
    monkeypatch.setattr(module.shutil, "rmtree", original)
    restarted = FreeQueryHistory(RunStore(store.path), history.data_root, now=lambda: NOW)
    asyncio.run(restarted.start())
    assert not directory.exists()
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM free_query_history_cleanup").fetchone()[0] == 0


def test_active_and_authoring_sources_survive_cleanup(tmp_path):
    store, history = service(tmp_path)
    seed(store, "active", age=31, status="running")
    seed(store, "source", age=29)
    with history.protect(run_id="source"):
        history.now = lambda: NOW + timedelta(days=2)
        asyncio.run(history.cleanup())
        assert store.get_run("source")
    asyncio.run(history.cleanup())
    with pytest.raises(KeyError): store.get_run("source")
    assert store.get_run("active")


def test_retry_prefills_requirements_without_results_or_secret_inputs(tmp_path):
    store, history = service(tmp_path)
    seed(store, "run", session="session")
    store.create_free_query_feedback_request(feedback_request_id="feedback", session_id="session",
        base_iteration=1, feedback="Synthetic failed follow-up", feedback_type_hint=None, locale="en")
    store.update_free_query_feedback_request("feedback", status="failed", completed=True)
    prompt = history.retry_query("session")["query"]
    assert prompt == "Synthetic original question\n\nSynthetic failed follow-up"
    assert "result" not in prompt


def test_retry_includes_public_clarifications_and_omits_secure_input(tmp_path):
    store, history = service(tmp_path)
    seed(store, "run", session="session")
    store.append_event("run", "input_received", {"input": "Synthetic public clarification"})
    store.append_event("run", "input_received", {"input": "private", "secure_fields": ["receipt_reference"]})
    store.append_event("run", "secure_input_received", {"input": "private"})
    assert history.retry_query("session")["query"] == "Synthetic original question\n\nSynthetic public clarification"


def test_waiting_cleanup_retries_tool_closure_before_deleting(tmp_path):
    store, history = service(tmp_path)
    seed(store, "waiting", age=31, status="waiting_input", session="session")

    class Coordinator:
        attempts = 0
        async def cancel(self, run_id):
            store.update_run(run_id, status=RunStatus.cancelled)
        async def cancel_acceptance_run(self, run_id):
            self.attempts += 1
            return self.attempts > 1

    coordinator = Coordinator()
    history.coordinator = coordinator
    asyncio.run(history.cleanup())
    assert history.list()["items"] == []
    assert store.get_run("waiting").status == RunStatus.cancelled
    asyncio.run(history.cleanup())
    assert coordinator.attempts == 2
    with pytest.raises(KeyError): store.get_run("waiting")


def test_cleanup_waits_for_scheduler_to_release_source(tmp_path):
    store, history = service(tmp_path)
    seed(store, "waiting", age=31, status="waiting_input")
    store.create_execution_job(job_id="job", workload_class="free_query", subject_id="waiting")
    store.claim_execution_job("job", lease_owner="test", lease_expires_at=NOW.isoformat())
    asyncio.run(history.cleanup())
    assert store.get_run("waiting")
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM free_query_history_cleanup").fetchone()[0] == 0


def test_cleanup_defers_cancelled_run_with_incomplete_harness_closure(tmp_path):
    store, history = service(tmp_path)
    seed(store, "cancelled", age=31, status="cancelled")
    store.update_harness_state("cancelled", {"cleanup_incomplete": True})
    asyncio.run(history.cleanup())
    assert store.get_run("cancelled")
    with store._connect() as db:
        assert db.execute("SELECT status FROM free_query_history_cleanup").fetchone()[0] == "cancelling"


def test_cleanup_rejects_path_escape_and_retains_retry_job(tmp_path):
    store, history = service(tmp_path)
    seed(store, "../outside", age=31)
    outside = history.data_root / "outside"
    outside.mkdir(parents=True)
    (outside / "keep.txt").write_text("keep")
    asyncio.run(history.cleanup())
    assert (outside / "keep.txt").read_text() == "keep"
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM free_query_history_cleanup").fetchone()[0] == 1


def test_conversion_checks_latest_feedback_and_rejects_older_round(tmp_path):
    store, history = service(tmp_path)
    seed(store, "old", session="session")
    seed(store, "latest", session="session", iteration=2)
    drafts = AgentDraftService(_settings(tmp_path), store)
    assert drafts.source_eligibility("old") == "query_not_successful"
    assert drafts.source_eligibility("latest") is None
    store.create_free_query_feedback_request(feedback_request_id="feedback", session_id="session",
        base_iteration=2, feedback="Synthetic failed follow-up", feedback_type_hint=None, locale="en")
    store.update_free_query_feedback_request("feedback", status="failed", completed=True)
    assert drafts.source_eligibility("latest") == "query_not_successful"


def test_presentation_iteration_can_convert_with_original_execution_plan(tmp_path):
    store, history = service(tmp_path)
    seed(store, "source", session="session")
    seed(store, "presentation", session="session", iteration=2)
    store.update_run("presentation", plan_json={"kind": "sap_business_agents_harness", "steps": []})
    with store._connect() as db:
        db.execute("UPDATE free_query_iterations SET execution_action='reinterpret',source_run_id='source' WHERE run_id='presentation'")
    store.update_free_query_session("session", status="satisfied", accepted_iteration=2,
        accepted_result_digest="synthetic-digest")
    drafts = AgentDraftService(_settings(tmp_path), store)
    assert drafts.source_eligibility("presentation", "session") is None
    draft = asyncio.run(drafts.create_from_session("session", module="MM"))
    assert draft.origin["free_query_session"]["execution_source_run_id"] == "source"
    assert Path(draft.path, "agent.json").exists()


def test_submitted_query_is_automatically_saved_without_acceptance(tmp_path):
    from tests.test_platform_runtime import create_app as harness_app, _wait
    settings = _settings(tmp_path)
    app = harness_app(settings, planner=FakePlanner(), embedded_provider=FakeEmbeddedProvider())
    with TestClient(app) as client:
        assert client.get("/api/free-query-sessions").json()["items"] == []
        created = client.post("/api/runs", json={"mode": "free_query", "query": "Synthetic submitted question"}).json()
        run = _wait(client, created["run_id"])
        row = client.get("/api/free-query-sessions").json()["items"][0]
        assert row["session_id"] == created["session_id"]
        assert row["query"] == "Synthetic submitted question"
        assert row["status"] == run["status"] == "completed"
        assert run["query_origin"] == "user"
        assert row["draft_id"] is None and row["can_create_draft"] is True
    reopened = FreeQueryHistory(RunStore(settings.database_path), settings.data_root)
    assert reopened.list()["items"][0]["session_id"] == created["session_id"]


def test_public_and_native_acceptance_submissions_are_excluded_from_history(tmp_path, monkeypatch):
    from tests.test_platform_runtime import create_app as harness_app, _wait
    app = harness_app(_settings(tmp_path), planner=FakePlanner(), embedded_provider=FakeEmbeddedProvider())
    with TestClient(app) as client:
        user = client.post("/api/runs", json={"mode": "free_query", "query": "Synthetic user query"}).json()
        _wait(client, user["run_id"])
        request = {"mode": "free_query", "query": "Synthetic system query", "acceptanceSpec": {"record_fields": ["document"]}}
        public = client.post("/api/runs", json=request)
        assert public.status_code == 202, public.text
        public_run = _wait(client, public.json()["run_id"])
        assert public_run["query_origin"] == "system"
        monkeypatch.setattr(app.state.coordinator, "_schedule_run", AsyncMock())
        native = client.portal.call(partial(app.state.coordinator.submit_acceptance_query,
            RunCreate.model_validate(request), runtime=app.state.store.get_run(user["run_id"]).runtime.model_dump(mode="json")))
        assert app.state.store.get_run(native).query_origin == "system"
        listing = client.get("/api/free-query-sessions").json()
        assert listing["total"] == 1
        assert listing["items"][0]["run_id"] == user["run_id"]
        # Origin is server metadata, not an admitted client parameter.
        assert client.post("/api/runs", json={**request, "query_origin": "user"}).status_code == 422
        assert client.post("/api/runs", json={"mode": "free_query", "query": "Synthetic", "queryOrigin": "system"}).status_code == 422


@pytest.mark.parametrize("origin", ["user", "system"])
@pytest.mark.parametrize("feedback_type", ["scope_or_filter", "presentation"])
def test_follow_up_inherits_origin_for_requery_and_evidence_reuse(tmp_path, origin, feedback_type):
    from tests.test_platform_runtime import create_app as harness_app, FeedbackPlanner, _wait, _wait_feedback
    app = harness_app(_settings(tmp_path), planner=FeedbackPlanner(), embedded_provider=FakeEmbeddedProvider())
    with TestClient(app) as client:
        request = RunCreate(mode=RunMode.free_query, query="Synthetic purchase order query")
        if origin == "user":
            run_id = client.post("/api/runs", json=request.model_dump(mode="json", by_alias=True)).json()["run_id"]
        else:
            run_id = client.portal.call(app.state.coordinator.submit, request)
        first = _wait(client, run_id)
        assert first["status"] == "completed", first.get("error")
        session_id = app.state.store.get_free_query_session_by_run(run_id)["session_id"]
        feedback = client.post(f"/api/free-query-sessions/{session_id}/feedback",
            json={"baseIteration": 1, "feedback": "Synthetic follow-up", "feedbackTypeHint": feedback_type, "locale": "en"})
        assert feedback.status_code == 202, feedback.text
        reviewed = _wait_feedback(client, session_id, feedback.json()["feedback_request_id"])
        assert reviewed["status"] == "iteration_created", reviewed
        latest = _wait(client, reviewed["run_id"])
        assert latest["status"] == "completed", latest.get("error")
        assert latest["query_origin"] == origin
        listing = client.get("/api/free-query-sessions").json()
        assert listing["total"] == (1 if origin == "user" else 0)
        if origin == "user":
            assert listing["items"][0]["iteration"] == 2
            assert listing["items"][0]["run_id"] == latest["run_id"]


@pytest.mark.parametrize("status", ["failed", "cancelled", "inconclusive", "running"])
def test_conversion_rejects_unsuccessful_sources_at_service_boundary(tmp_path, status):
    store, history = service(tmp_path)
    seed(store, "run", status=status)
    drafts = AgentDraftService(_settings(tmp_path), store)
    assert drafts.source_eligibility("run") == "query_not_successful"
    with pytest.raises(DraftError): asyncio.run(drafts.create_from_run("run"))
    assert not _settings(tmp_path).draft_root.exists()


def test_api_settings_validation_history_and_draft_survives_expiry(tmp_path):
    app = create_app(_settings(tmp_path), planner=FakePlanner(), embedded_provider=FakeEmbeddedProvider())
    history = app.state.query_history
    history.now = lambda: NOW
    seed(app.state.store, "run", age=1, session="session")
    with TestClient(app) as client:
        assert client.get("/api/system/free-query-history").json() == {"retention_days": 30}
        for value in (0, -1, 1.5, True, "30", None):
            assert client.put("/api/system/free-query-history", json={"retention_days": value}).status_code == 422
        listing = client.get("/api/free-query-sessions").json()
        assert listing["items"][0]["can_create_draft"] is True
        assert client.get("/api/free-query-history/session/retry-query").json()["query"]
        accepted = client.post("/api/free-query-sessions/session/accept", json={"iteration": 1, "expectedResultDigest": "synthetic-digest"})
        assert accepted.status_code == 200
        draft = client.post("/api/free-query-sessions/session/agent-draft", json={"module": "MM"})
        assert draft.status_code == 201, draft.text
        payload = draft.json()
        assert client.post("/api/free-query-sessions/session/agent-draft", json={"module": "MM"}).json()["draft_id"] == payload["draft_id"]
        assert client.put("/api/system/free-query-history", json={"retention_days": 1}).status_code == 200
        client.portal.call(history.cleanup)
        assert client.get("/api/runs/run").status_code == 404
        assert client.get("/api/free-query-sessions/session").status_code == 404
        assert client.get(f"/api/authoring/agents/{payload['managed_draft_id']}").status_code == 200
