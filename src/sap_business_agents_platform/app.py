from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import hmac
import importlib.util
import io
import json
import logging
import secrets
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .codex_planner import CodexPlanner, Planner
from .agent_lifecycle import AgentLifecycleError, AgentLifecycleService
from .config import Settings
from .database import RunStore
from .engine import RunCoordinator, RunExecutionError, presentation_table_page
from .factory import AgentDraftService, DraftError
from .harness import CodexHarnessController, HarnessToolBroker
from .manifests import AgentRepository
from .models import (
    AgentActivateRequest,
    AgentAuthoringCreate,
    AgentDeleteRequest,
    AgentDraftDeleteRequest,
    AgentDraftUpdate,
    AgentFeedbackRequest,
    AgentLifecycleRequest,
    AgentLiveValidationRequest,
    AgentPublishRequest,
    AgentUndoRequest,
    AgentVersionDraftRequest,
    ArtifactDeleteRequest,
    ArtifactRevealRequest,
    DraftAuthoringCreate,
    DraftCreate,
    DraftInput,
    FreeQueryAccept,
    FreeQueryFeedback,
    FreeQueryFeedbackCancel,
    FreeQueryFeedbackInput,
    FreeQuerySessionCreate,
    RoleMatchingFeedback,
    RoleMatchingPreflightRequest,
    RoleMatchingSessionCreate,
    RoleMatchingWorkflowDraftRequest,
    RunCreate,
    RunInput,
    TERMINAL_STATUSES,
    WorkflowCompositionCreate,
    WorkflowCompositionInput,
    WorkflowDeleteRequest,
    WorkflowDesignAccept,
    WorkflowDraftCreate,
    WorkflowDraftUpdate,
    WorkflowFeedbackInput,
    WorkflowFeedbackRequest,
    WorkflowLifecycleRequest,
    WorkflowPublishRequest,
    WorkflowValidationRequest,
    WorkflowValidationAccept,
    WorkflowVersionDraftRequest,
    WorkflowUndoRequest,
)
from .plugins import (
    AgentRuntimeCapability,
    BusinessAgentCapability,
    BusinessAgentPluginProvider,
    CodexRuntimePluginProvider,
    PluginError,
    PluginManager,
    SapReadCapability,
    SkillCapability,
    SkillhubPluginProvider,
    official_plugin_manifests,
)
from .sap_read import EmbeddedODataProvider, SapReadError, SapReadProvider
from .runtime import (
    CodexRuntimeProbe,
    RuntimeRouter,
    StaticRuntimeRouter,
    WorkBuddyRuntimeProbe,
)
from .role_matching import RoleMatchingError, RoleMatchingService
from .sdk_manager import SDKManager, SDKManagerError
from .skills import SkillRegistry
from .restricted_artifacts import RestrictedArtifactError, RestrictedArtifactStore
from .workbuddy_planner import WorkBuddyPlanner
from .workflow_factory import WorkflowDraftError, WorkflowDraftService
from .workflows import WorkflowError, WorkflowManagementService, WorkflowRepository
from .workflow_presentation import (
    compose_workflow_presentation,
    workflow_ap_scopes_csv,
    workflow_markdown_report,
    workflow_orders_csv,
    workflow_presentation_table_page,
)


LOGGER = logging.getLogger(__name__)


def _safe_csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        text = str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


class DraftImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, str] = Field(default_factory=dict)


class PluginEnableUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class RuntimeDefaultUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str


def create_app(
    settings: Settings | None = None,
    *,
    planner: Planner | None = None,
    embedded_provider: SapReadProvider | None = None,
    sdk_manager: SDKManager | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    store = RunStore(settings.database_path)
    agents = AgentRepository(settings.repository_root / "agents")
    skill_registry = SkillRegistry(
        settings.skillhub_root, settings.repository_root / "config" / "skills.json"
    )
    embedded = embedded_provider or EmbeddedODataProvider(
        base_url=settings.sap_base_url,
        username=settings.sap_username,
        password=settings.sap_password,
        client=settings.sap_client,
        verify_ssl=settings.sap_verify_ssl,
        auth_type=settings.sap_auth_type,
        timeout_seconds=settings.sap_odata_timeout_seconds,
        max_results=settings.sap_max_results,
        page_size=settings.sap_page_size,
        max_concurrent_requests=settings.max_concurrent_sap_gets,
        relationship_catalog_path=settings.repository_root / "config" / "business-relationships.json",
        service_registry_path=settings.odata_service_registry_path,
        catalog_seed_path=settings.catalog_seed_path,
        curated_catalog_path=settings.repository_root / "config" / "catalog-curated-terms.json",
        normalization_catalog_path=settings.repository_root / "config" / "sap-value-normalization.json",
    )
    selected_provider = "embedded"
    selected_plugin_id = "embedded-sap-odata"
    codex_sdk_installed = importlib.util.find_spec("openai_codex") is not None
    planner_supplied = planner is not None
    sdk_registry = sdk_manager or SDKManager(
        settings.repository_root / "config" / "sdks.json",
        settings.repository_root,
        runtime_probes={
            "codex": CodexRuntimeProbe(),
            "workbuddy": WorkBuddyRuntimeProbe(settings.repository_root),
        },
        selection_path=settings.sdk_runtime_state_path,
    )
    if planner_supplied:
        runtime_planner = StaticRuntimeRouter(planner)
    else:
        runtime_planner = RuntimeRouter(
            sdk_registry,
            {
                "codex": CodexPlanner(
                    settings.repository_root, model=settings.codex_model
                ),
                "workbuddy": WorkBuddyPlanner(settings.repository_root),
            },
        )
    plugin_manager = PluginManager(
        settings.plugin_manifest_root,
        settings.plugin_state_path,
        official_plugin_manifests(),
        preferred_plugins={"sap_read.v2": selected_plugin_id},
        runtime_enabled={"embedded-sap-odata": True},
    )
    plugin_manager.bind_provider("embedded-sap-odata", embedded)
    plugin_manager.bind_provider("sapskillhub", SkillhubPluginProvider(skill_registry))
    plugin_manager.bind_provider(
        "codex-runtime", CodexRuntimePluginProvider(runtime_planner)
    )
    plugin_manager.bind_provider(
        "business-agent-catalog", BusinessAgentPluginProvider(agents)
    )
    sap_read = SapReadCapability(plugin_manager)
    skills = SkillCapability(plugin_manager)
    agent_runtime = AgentRuntimeCapability(plugin_manager)
    business_agents = BusinessAgentCapability(plugin_manager)
    workflows = WorkflowRepository(
        settings.repository_root / "workflows", business_agents, store
    )
    health_catalog_counts = {
        "executable_agents": 0,
        "published_workflows": 0,
        "approved_skills": 0,
    }
    harness_broker = HarnessToolBroker(settings, store, sap_read, skills)
    harness = (
        CodexHarnessController(settings, store, harness_broker)
        if settings.free_query_runtime == "harness" and not planner_supplied
        else None
    )
    coordinator = RunCoordinator(
        settings,
        store,
        business_agents,
        sap_read,
        skills,
        agent_runtime,
        workflows,
        harness=harness,
    )
    drafts = AgentDraftService(settings, store, agent_runtime)
    workflow_drafts = WorkflowDraftService(
        settings, store, business_agents, coordinator, sap_read, agent_runtime
    )
    workflow_management = WorkflowManagementService(
        repository_root=settings.repository_root,
        repository=workflows,
        store=store,
        drafts=workflow_drafts,
    )
    agent_lifecycle = AgentLifecycleService(
        settings,
        store,
        agents,
        workflows,
        coordinator,
        agent_runtime,
        drafts,
        skills=skills,
    )
    role_matching = RoleMatchingService(
        settings, store, business_agents, agent_runtime, workflow_drafts
    )
    restricted_artifacts = RestrictedArtifactStore(
        settings.data_root,
        store,
        retention_days=settings.restricted_artifact_retention_days,
    )
    artifact_csrf_token = secrets.token_urlsafe(32)
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await plugin_manager.start()
        health_catalog_counts.update(
            executable_agents=len(agents.executable()),
            published_workflows=len(workflows.list()),
            approved_skills=len(skill_registry.list()),
        )
        await coordinator.start()
        await role_matching.start()
        try:
            yield
        finally:
            await role_matching.stop()
            await coordinator.stop()
            await plugin_manager.stop()

    app = FastAPI(
        title="SAPBusinessAgents Local Prototype",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.local_ui_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.store = store
    app.state.harness_broker = harness_broker
    app.state.agents = agents
    app.state.business_agents = business_agents
    app.state.plugin_manager = plugin_manager
    app.state.sap_read = sap_read
    app.state.sap_read_provider = selected_provider
    app.state.skills = skills
    app.state.agent_runtime = agent_runtime
    app.state.coordinator = coordinator
    app.state.drafts = drafts
    app.state.workflows = workflows
    app.state.workflow_drafts = workflow_drafts
    app.state.workflow_management = workflow_management
    app.state.agent_lifecycle = agent_lifecycle
    app.state.role_matching = role_matching
    app.state.restricted_artifacts = restricted_artifacts
    app.state.sdk_manager = sdk_registry

    @app.exception_handler(RequestValidationError)
    async def safe_role_matching_validation_error(
        request: Request, exc: RequestValidationError
    ) -> Response:
        if not request.url.path.startswith("/api/role-matching/"):
            return await request_validation_exception_handler(request, exc)
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {
                        "type": str(item.get("type") or "value_error"),
                        "loc": list(item.get("loc") or []),
                        "msg": str(item.get("msg") or "Invalid role-matching input."),
                    }
                    for item in exc.errors()
                ]
            },
        )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        default_runtime_id = str(
            getattr(sdk_registry, "default_provider_id", "codex")
        )
        sap_read_plugin = plugin_manager.get(selected_plugin_id)
        sap_read_status = sap_read_plugin.get("health") or {
            "ok": False,
            "error": {"code": "health_not_run", "message": "Provider health check has not run."},
        }
        return {
            "ok": True,
            "service": "sap-business-agents-local",
            "loopback_only": True,
            "sap_read": {
                "selected_provider": selected_provider,
                "plugin_id": selected_plugin_id,
                "status": sap_read_plugin["status"],
                **sap_read_status,
            },
            "codex_sdk_installed": codex_sdk_installed,
            "free_query_runtime": {
                "selected": default_runtime_id,
                "execution_mode": (
                    "harness"
                    if harness is not None and default_runtime_id == "codex"
                    else "validated_plan"
                ),
                "harness_enabled": harness is not None and default_runtime_id == "codex",
                "protocol": (
                    "agent_runtime.v2"
                    if harness is not None and default_runtime_id == "codex"
                    else "agent_runtime.v1"
                ),
                "native_web_search": harness is not None and default_runtime_id == "codex",
                "automatic_fallback": False,
            },
            **health_catalog_counts,
            "plugins": {
                "total": len(plugin_manager.list()),
                "ready": sum(
                    item["status"] == "ready" for item in plugin_manager.list()
                ),
            },
        }

    @app.get("/api/plugins")
    def list_plugins() -> list[dict[str, Any]]:
        return plugin_manager.list()

    @app.get("/api/plugins/{plugin_id}")
    def get_plugin(plugin_id: str) -> dict[str, Any]:
        try:
            return plugin_manager.get(plugin_id)
        except PluginError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/capabilities")
    def list_capabilities() -> list[dict[str, Any]]:
        return plugin_manager.capabilities()

    @app.get("/api/providers/sap-read")
    def list_sap_read_providers() -> dict[str, Any]:
        providers = [
            item
            for item in plugin_manager.list()
            if any(
                capability.get("capability") == "sap_read.v2"
                for capability in item.get("capabilities") or []
            )
        ]
        return {
            "selected_provider": selected_provider,
            "selected_plugin_id": selected_plugin_id,
            "items": providers,
            "automatic_fallback": False,
        }

    @app.post("/api/providers/sap-read/{provider_id}/health")
    async def check_sap_read_provider(provider_id: str) -> dict[str, Any]:
        try:
            plugin = plugin_manager.get(provider_id)
            if not any(
                capability.get("capability") == "sap_read.v2"
                for capability in plugin.get("capabilities") or []
            ):
                raise HTTPException(404, "Not a SAP read Provider")
            health_result = await plugin_manager.health(provider_id)
            return {"plugin": plugin_manager.get(provider_id), "health": health_result}
        except PluginError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/plugins/rescan")
    async def rescan_plugins() -> dict[str, Any]:
        result = plugin_manager.rescan()
        await plugin_manager.start()
        return {**result, "items": plugin_manager.list()}

    @app.post("/api/plugins/{plugin_id}/health")
    async def check_plugin_health(plugin_id: str) -> dict[str, Any]:
        try:
            health_result = await plugin_manager.health(plugin_id)
            return {"plugin": plugin_manager.get(plugin_id), "health": health_result}
        except PluginError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.put("/api/plugins/{plugin_id}/enabled")
    async def set_plugin_enabled(
        plugin_id: str, payload: PluginEnableUpdate
    ) -> dict[str, Any]:
        try:
            return await plugin_manager.set_enabled(plugin_id, payload.enabled)
        except PluginError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/agents")
    def list_agents(executable: bool = False) -> list[dict[str, Any]]:
        try:
            return business_agents.executable() if executable else business_agents.list()
        except PluginError as exc:
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from exc

    @app.get("/api/agents/catalog")
    def managed_agent_catalog(state: str = Query("all", pattern="^(active|inactive|all)$")) -> list[dict[str, Any]]:
        try:
            return agent_lifecycle.catalog(state)
        except AgentLifecycleError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc), "detail": exc.detail}) from exc

    @app.get("/api/agents/{agent_id}/versions")
    def list_agent_versions(agent_id: str) -> list[dict[str, Any]]:
        try:
            return agent_lifecycle.versions(agent_id)
        except KeyError as exc:
            raise HTTPException(404, "Agent not found") from exc

    @app.get("/api/agents/{agent_id}/versions/{version}")
    def get_agent_version(agent_id: str, version: str) -> dict[str, Any]:
        try:
            return agent_lifecycle.version(agent_id, version)
        except KeyError as exc:
            raise HTTPException(404, "Agent version not found") from exc

    @app.post("/api/agents/{agent_id}/versions/draft", status_code=201)
    def create_agent_version_draft(agent_id: str, payload: AgentVersionDraftRequest) -> dict[str, Any]:
        try:
            return agent_lifecycle.create_version_draft(
                agent_id,
                bump=payload.bump,
                expected_version=payload.expected_version,
                expected_hash=payload.expected_agent_hash,
            )
        except (AgentLifecycleError, KeyError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.post("/api/agents/{agent_id}/deactivate")
    def deactivate_agent(agent_id: str, payload: AgentLifecycleRequest) -> dict[str, Any]:
        try:
            return agent_lifecycle.deactivate(agent_id, payload)
        except (AgentLifecycleError, KeyError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.post("/api/agents/{agent_id}/activate")
    def activate_agent(agent_id: str, payload: AgentActivateRequest) -> dict[str, Any]:
        try:
            return agent_lifecycle.activate(agent_id, payload)
        except (AgentLifecycleError, KeyError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.post("/api/agents/{agent_id}/rollback")
    def rollback_agent(agent_id: str, payload: AgentActivateRequest) -> dict[str, Any]:
        try:
            return agent_lifecycle.rollback(agent_id, payload)
        except (AgentLifecycleError, KeyError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.delete("/api/agents/{agent_id}")
    def delete_agent(agent_id: str, payload: AgentDeleteRequest) -> dict[str, Any]:
        try:
            return agent_lifecycle.delete(agent_id, payload)
        except (AgentLifecycleError, KeyError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str) -> dict[str, Any]:
        try:
            return business_agents.get(agent_id)
        except KeyError as exc:
            raise HTTPException(404, "Agent not found") from exc
        except PluginError as exc:
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from exc

    @app.post("/api/role-matching/preflight")
    def preflight_role_matching(payload: RoleMatchingPreflightRequest) -> dict[str, Any]:
        try:
            return role_matching.preflight(
                payload.paths, role_description=payload.role_description
            )
        except Exception as exc:
            code = str(getattr(exc, "code", "role_matching_preflight_failed"))
            raise HTTPException(409, {"code": code, "message": str(exc), "detail": getattr(exc, "detail", None)}) from exc

    @app.post("/api/role-matching/sessions", status_code=202)
    async def create_role_matching_session(payload: RoleMatchingSessionCreate) -> dict[str, Any]:
        try:
            return await role_matching.create(
                paths=payload.paths,
                role_description=payload.role_description,
                locale=payload.locale,
                consent=payload.consent_to_runtime,
            )
        except RoleMatchingError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc), "detail": exc.detail}) from exc

    @app.get("/api/role-matching/sessions/{session_id}")
    def get_role_matching_session(session_id: str) -> dict[str, Any]:
        try:
            return role_matching.get(session_id)
        except KeyError as exc:
            raise HTTPException(404, "Role-matching session not found") from exc

    @app.get("/api/role-matching/sessions/{session_id}/documents")
    def get_role_matching_documents(session_id: str) -> list[dict[str, Any]]:
        try:
            return role_matching.documents(session_id)
        except KeyError as exc:
            raise HTTPException(404, "Role-matching session not found") from exc

    @app.get("/api/role-matching/sessions/{session_id}/revisions")
    def get_role_matching_revisions(session_id: str) -> list[dict[str, Any]]:
        try:
            return role_matching.revisions(session_id)
        except KeyError as exc:
            raise HTTPException(404, "Role-matching session not found") from exc

    @app.get("/api/role-matching/sessions/{session_id}/revisions/{revision}")
    def get_role_matching_revision(session_id: str, revision: int) -> dict[str, Any]:
        try:
            return role_matching.revision(session_id, revision)
        except KeyError as exc:
            raise HTTPException(404, "Role-matching revision not found") from exc

    @app.get("/api/role-matching/sessions/{session_id}/events")
    async def role_matching_events(
        request: Request, session_id: str, after: int = Query(0, ge=0)
    ) -> StreamingResponse:
        try:
            role_matching.get(session_id)
        except KeyError as exc:
            raise HTTPException(404, "Role-matching session not found") from exc

        async def stream() -> AsyncIterator[str]:
            last_event_id = request.headers.get("last-event-id", "")
            try:
                resumed = int(last_event_id) if last_event_id else 0
            except ValueError:
                resumed = 0
            sequence = max(after, resumed)
            while True:
                if await request.is_disconnected():
                    return
                events = store.role_matching_events_after(session_id, sequence)
                for event in events:
                    sequence = int(event["sequence"])
                    data = json.dumps(event, ensure_ascii=False)
                    yield f"id: {sequence}\nevent: {event['type']}\ndata: {data}\n\n"
                session = role_matching.get(session_id)
                if session["status"] in {"completed", "failed", "cancelled"} and not events:
                    return
                if not events:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.35)

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/role-matching/sessions/{session_id}/feedback", status_code=202)
    async def submit_role_matching_feedback(
        session_id: str, payload: RoleMatchingFeedback
    ) -> dict[str, Any]:
        try:
            return await role_matching.feedback(
                session_id,
                base_revision=payload.base_revision,
                message=payload.message,
                mode=payload.rematch_mode,
                added_paths=payload.added_paths,
                added_role_description=payload.added_role_description,
                excluded_document_ids=payload.excluded_document_ids,
            )
        except KeyError as exc:
            raise HTTPException(404, "Role-matching session not found") from exc
        except RoleMatchingError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc), "detail": exc.detail}) from exc

    @app.post("/api/role-matching/sessions/{session_id}/cancel", status_code=202)
    async def cancel_role_matching_session(session_id: str) -> dict[str, Any]:
        try:
            return await role_matching.cancel(session_id)
        except KeyError as exc:
            raise HTTPException(404, "Role-matching session not found") from exc

    @app.post(
        "/api/role-matching/sessions/{session_id}/workflow-suggestions/{suggestion_id}/draft",
        status_code=201,
    )
    def create_role_matching_workflow_draft(
        session_id: str, suggestion_id: str, payload: RoleMatchingWorkflowDraftRequest
    ) -> dict[str, Any]:
        try:
            draft = role_matching.create_workflow_draft(
                session_id, suggestion_id, revision=payload.revision,
                catalog_digest=payload.expected_catalog_digest,
            )
            return draft.model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Role-matching session or revision not found") from exc
        except RoleMatchingError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc), "detail": exc.detail}) from exc

    @app.get("/api/role-matching/sessions/{session_id}/revisions/{revision}/report.md")
    def download_role_matching_report(session_id: str, revision: int) -> Response:
        try:
            content = role_matching.markdown(session_id, revision)
        except KeyError as exc:
            raise HTTPException(404, "Role-matching revision not found") from exc
        return Response(content, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="role-matching-report.md"'})

    @app.get("/api/role-matching/sessions/{session_id}/revisions/{revision}/report.json")
    def download_role_matching_json(session_id: str, revision: int) -> Response:
        try:
            payload = role_matching.revision(session_id, revision)
        except RoleMatchingError as exc:
            raise HTTPException(status_code=404, detail={"code": exc.code, "message": str(exc)}) from exc
        return Response(
            json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="role-matching-report.json"'},
        )

    @app.get("/api/role-matching/sessions/{session_id}/revisions/{revision}/{kind}.csv")
    def download_role_matching_csv(session_id: str, revision: int, kind: str) -> Response:
        if kind not in {"operations", "agent_matches", "workflow_suggestions", "agent_gaps"}:
            raise HTTPException(404, "Role-matching table not found")
        try:
            content = role_matching.csv(session_id, revision, kind)
        except KeyError as exc:
            raise HTTPException(404, "Role-matching revision not found") from exc
        return Response("\ufeff" + content, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'})

    @app.delete("/api/role-matching/sessions/{session_id}", status_code=204)
    async def delete_role_matching_session(session_id: str) -> Response:
        try:
            await role_matching.delete(session_id)
        except KeyError as exc:
            raise HTTPException(404, "Role-matching session not found") from exc
        except RoleMatchingError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc), "detail": exc.detail}) from exc
        return Response(status_code=204)

    @app.get("/api/workflows")
    def list_workflows() -> list[dict[str, Any]]:
        try:
            return workflows.list()
        except WorkflowError as exc:
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from exc

    @app.get("/api/workflows/catalog")
    def workflow_catalog(state: str = Query(default="active")) -> list[dict[str, Any]]:
        try:
            return workflows.catalog(state)
        except WorkflowError as exc:
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from exc

    @app.get("/api/workflows/{workflow_id}/versions")
    def workflow_versions(workflow_id: str) -> list[dict[str, Any]]:
        try:
            return workflows.versions(workflow_id)
        except KeyError as exc:
            raise HTTPException(404, "Workflow not found") from exc
        except WorkflowError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc

    @app.get("/api/workflows/{workflow_id}/versions/{version}")
    def workflow_version(workflow_id: str, version: str) -> dict[str, Any]:
        try:
            return workflows.get_version(workflow_id, version)
        except KeyError as exc:
            raise HTTPException(404, "Workflow version not found") from exc
        except WorkflowError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc

    @app.post("/api/workflows/{workflow_id}/versions/draft", status_code=201)
    def create_workflow_version_draft(
        workflow_id: str, payload: WorkflowVersionDraftRequest
    ) -> dict[str, Any]:
        try:
            return workflow_management.create_version_draft(
                workflow_id,
                bump=payload.bump,
                expected_version=payload.expected_version,
                expected_workflow_hash=payload.expected_workflow_hash,
            )
        except KeyError as exc:
            raise HTTPException(404, "Workflow not found") from exc
        except (WorkflowError, WorkflowDraftError) as exc:
            raise HTTPException(
                409,
                {
                    "code": getattr(exc, "code", "workflow_management_failed"),
                    "message": str(exc),
                    "detail": getattr(exc, "detail", None),
                },
            ) from exc

    @app.post("/api/workflows/{workflow_id}/deactivate")
    def deactivate_workflow(
        workflow_id: str, payload: WorkflowLifecycleRequest
    ) -> dict[str, Any]:
        try:
            return workflow_management.deactivate(
                workflow_id,
                expected_version=payload.expected_version,
                expected_workflow_hash=payload.expected_workflow_hash,
                reason=payload.reason,
            )
        except KeyError as exc:
            raise HTTPException(404, "Workflow not found") from exc
        except (WorkflowError, subprocess.CalledProcessError) as exc:
            raise HTTPException(
                409,
                {
                    "code": getattr(exc, "code", "workflow_management_failed"),
                    "message": str(exc),
                    "detail": getattr(exc, "detail", None),
                },
            ) from exc

    @app.post("/api/workflows/{workflow_id}/activate")
    def activate_workflow(
        workflow_id: str, payload: WorkflowLifecycleRequest
    ) -> dict[str, Any]:
        try:
            return workflow_management.activate(
                workflow_id,
                expected_version=payload.expected_version,
                expected_workflow_hash=payload.expected_workflow_hash,
                reason=payload.reason,
            )
        except KeyError as exc:
            raise HTTPException(404, "Workflow not found") from exc
        except (WorkflowError, subprocess.CalledProcessError) as exc:
            raise HTTPException(
                409,
                {
                    "code": getattr(exc, "code", "workflow_management_failed"),
                    "message": str(exc),
                    "detail": getattr(exc, "detail", None),
                },
            ) from exc

    @app.delete("/api/workflows/{workflow_id}")
    def delete_workflow(
        workflow_id: str, payload: WorkflowDeleteRequest
    ) -> dict[str, Any]:
        try:
            return workflow_management.delete(
                workflow_id,
                expected_version=payload.expected_version,
                expected_workflow_hash=payload.expected_workflow_hash,
                confirm_workflow_id=payload.confirm_workflow_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "Workflow not found") from exc
        except (WorkflowError, subprocess.CalledProcessError) as exc:
            raise HTTPException(
                409,
                {
                    "code": getattr(exc, "code", "workflow_management_failed"),
                    "message": str(exc),
                    "detail": getattr(exc, "detail", None),
                },
            ) from exc

    @app.get("/api/workflows/{workflow_id}")
    def get_workflow(workflow_id: str) -> dict[str, Any]:
        try:
            return workflows.get(workflow_id)
        except KeyError as exc:
            raise HTTPException(404, "Workflow not found") from exc
        except WorkflowError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc

    @app.post("/api/runs", status_code=202)
    async def create_run(payload: RunCreate) -> dict[str, Any]:
        try:
            run_id = await coordinator.submit(payload)
            response: dict[str, Any] = {"run_id": run_id, "status": "queued"}
            if payload.mode.value == "free_query":
                session = store.get_free_query_session_by_run(run_id)
                if session is not None:
                    response.update(
                        {
                            "session_id": session["session_id"],
                            "iteration": session["current_iteration"],
                        }
                    )
            return response
        except KeyError as exc:
            raise HTTPException(404, "Agent or workflow not found") from exc
        except (RunExecutionError, WorkflowError) as exc:
            detail = getattr(exc, "detail", None)
            safe_fields: list[str] = []
            if isinstance(detail, dict):
                if detail.get("field"):
                    safe_fields.append(str(detail["field"]))
                if isinstance(detail.get("fields"), list):
                    safe_fields.extend(str(field) for field in detail["fields"])
            LOGGER.warning(
                "run_create_rejected code=%s fields=%s",
                getattr(exc, "code", "run_rejected"),
                sorted(set(safe_fields)),
            )
            raise HTTPException(
                409,
                {
                    "code": getattr(exc, "code", "run_rejected"),
                    "message": str(exc),
                    "detail": getattr(exc, "detail", None),
                },
            ) from exc

    @app.get("/api/runs")
    def list_runs(limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in store.list_runs(limit)]

    @app.post("/api/free-query-sessions", status_code=201)
    def create_free_query_session(payload: FreeQuerySessionCreate) -> dict[str, Any]:
        try:
            return coordinator.create_free_query_session_from_run(payload.source_run_id)
        except KeyError as exc:
            raise HTTPException(404, "Run not found") from exc
        except RunExecutionError as exc:
            raise HTTPException(
                409,
                {"code": exc.code, "message": str(exc), "detail": exc.detail},
            ) from exc

    @app.get("/api/free-query-sessions/{session_id}")
    def get_free_query_session(session_id: str) -> dict[str, Any]:
        try:
            result = coordinator.free_query_session(session_id)
            imported = store.get_draft_import(result["draft_id"]) if result.get("draft_id") else None
            result["managed_draft_id"] = imported["managed_draft_id"] if imported else None
            return result
        except KeyError as exc:
            raise HTTPException(404, "Free-query session not found") from exc

    @app.get("/api/free-query-sessions/{session_id}/iterations/{iteration}")
    def get_free_query_iteration(session_id: str, iteration: int) -> dict[str, Any]:
        try:
            session = coordinator.free_query_session(session_id)
        except KeyError as exc:
            raise HTTPException(404, "Free-query session not found") from exc
        match = next(
            (item for item in session["iterations"] if item["iteration"] == iteration),
            None,
        )
        if match is None:
            raise HTTPException(404, "Free-query iteration not found")
        return match

    @app.post("/api/free-query-sessions/{session_id}/feedback", status_code=202)
    async def submit_free_query_feedback(
        session_id: str, payload: FreeQueryFeedback
    ) -> dict[str, Any]:
        try:
            return await coordinator.submit_free_query_feedback(session_id, payload)
        except KeyError as exc:
            raise HTTPException(404, "Free-query session not found") from exc
        except RunExecutionError as exc:
            raise HTTPException(
                409,
                {"code": exc.code, "message": str(exc), "detail": exc.detail},
            ) from exc

    @app.post("/api/free-query-sessions/{session_id}/feedback-input", status_code=202)
    async def submit_free_query_feedback_input(
        session_id: str, payload: FreeQueryFeedbackInput
    ) -> dict[str, Any]:
        try:
            return await coordinator.provide_free_query_feedback_input(
                session_id, payload.base_iteration, payload.input
            )
        except KeyError as exc:
            raise HTTPException(404, "Free-query session not found") from exc
        except RunExecutionError as exc:
            raise HTTPException(
                409,
                {"code": exc.code, "message": str(exc), "detail": exc.detail},
            ) from exc

    @app.get(
        "/api/free-query-sessions/{session_id}/feedback-requests/{feedback_request_id}"
    )
    def get_free_query_feedback_request(
        session_id: str, feedback_request_id: str
    ) -> dict[str, Any]:
        try:
            return coordinator.free_query_feedback_request(
                session_id, feedback_request_id
            )
        except KeyError as exc:
            raise HTTPException(404, "Free-query feedback request not found") from exc

    @app.get(
        "/api/free-query-sessions/{session_id}/feedback-requests/{feedback_request_id}/events"
    )
    async def free_query_feedback_events(
        request: Request,
        session_id: str,
        feedback_request_id: str,
        after: int = Query(0, ge=0),
    ) -> StreamingResponse:
        try:
            coordinator.free_query_feedback_request(session_id, feedback_request_id)
        except KeyError as exc:
            raise HTTPException(404, "Free-query feedback request not found") from exc

        async def stream() -> AsyncIterator[str]:
            last_event_id = request.headers.get("last-event-id", "")
            try:
                resumed_after = int(last_event_id) if last_event_id else 0
            except ValueError:
                resumed_after = 0
            sequence = max(after, resumed_after)
            while True:
                if await request.is_disconnected():
                    return
                events = coordinator.free_query_feedback_events(
                    session_id, feedback_request_id, sequence
                )
                for event in events:
                    sequence = int(event["sequence"])
                    data = json.dumps(event, ensure_ascii=False)
                    yield (
                        f"id: {sequence}\nevent: {event['type']}\ndata: {data}\n\n"
                    )
                latest = coordinator.free_query_feedback_request(
                    session_id, feedback_request_id
                )
                if latest["status"] in {
                    "iteration_created",
                    "new_session_required",
                    "completed",
                    "failed",
                    "cancelled",
                } and not events:
                    return
                if not events:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.35)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/api/free-query-sessions/{session_id}/feedback-requests/{feedback_request_id}/cancel"
    )
    async def cancel_free_query_feedback(
        session_id: str,
        feedback_request_id: str,
        _payload: FreeQueryFeedbackCancel,
    ) -> dict[str, Any]:
        try:
            return await coordinator.cancel_free_query_feedback(
                session_id, feedback_request_id
            )
        except KeyError as exc:
            raise HTTPException(404, "Free-query feedback request not found") from exc

    @app.post("/api/free-query-sessions/{session_id}/accept")
    def accept_free_query_result(
        session_id: str, payload: FreeQueryAccept
    ) -> dict[str, Any]:
        try:
            return coordinator.accept_free_query_result(
                session_id, payload.iteration, payload.expected_result_digest
            )
        except KeyError as exc:
            raise HTTPException(404, "Free-query session not found") from exc
        except RunExecutionError as exc:
            raise HTTPException(
                409,
                {"code": exc.code, "message": str(exc), "detail": exc.detail},
            ) from exc

    @app.post("/api/free-query-sessions/{session_id}/reopen")
    def reopen_free_query_session(session_id: str) -> dict[str, Any]:
        try:
            return coordinator.reopen_free_query_session(session_id)
        except KeyError as exc:
            raise HTTPException(404, "Free-query session not found") from exc
        except RunExecutionError as exc:
            raise HTTPException(
                409,
                {"code": exc.code, "message": str(exc), "detail": exc.detail},
            ) from exc

    def attach_management_draft(draft: Any) -> dict[str, Any]:
        payload = draft.model_dump(mode="json")
        try:
            payload.update(agent_lifecycle.import_source_draft(draft.draft_id))
        except (AgentLifecycleError, OSError, ValueError, KeyError):
            payload.update(managed_draft_id=None, management_import_status="failed",
                           management_import_error="management_import_failed")
        return payload

    def guard_source_draft_write(draft_id: str) -> None:
        imported = store.get_draft_import(draft_id)
        if imported:
            raise HTTPException(409, {"code": "draft_managed_elsewhere",
                "message": "Continue editing and publishing in Agent management.",
                "managed_draft_id": imported["managed_draft_id"]})

    @app.post("/api/free-query-sessions/{session_id}/agent-draft", status_code=201)
    async def create_free_query_session_agent_draft(
        session_id: str,
    ) -> dict[str, Any]:
        try:
            draft = await drafts.create_from_session(session_id)
            return attach_management_draft(draft)
        except KeyError as exc:
            raise HTTPException(404, "Free-query session not found") from exc
        except DraftError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        try:
            record = store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(404, "Run not found") from exc
        payload = record.model_dump(mode="json")
        free_query_session = store.get_free_query_session_by_run(run_id)
        if free_query_session is not None:
            iteration = store.get_free_query_iteration_by_run(run_id)
            payload["free_query_session"] = {
                "session_id": free_query_session["session_id"],
                "status": free_query_session["status"],
                "current_iteration": free_query_session["current_iteration"],
                "accepted_iteration": free_query_session["accepted_iteration"],
                "iteration": iteration["iteration"] if iteration else None,
            }
        if record.result and record.result.mode.value == "workflow":
            derived = record.result.workflow_presentation or compose_workflow_presentation(
                record.result
            )
            if derived:
                payload["result"]["workflow_presentation"] = derived
        return payload

    def workflow_view_for(run_id: str) -> tuple[Any, dict[str, Any]]:
        try:
            record = store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(404, "Run not found") from exc
        if record.result is None:
            raise HTTPException(409, "Run result is not available")
        view = record.result.workflow_presentation or compose_workflow_presentation(
            record.result
        )
        if not view:
            raise HTTPException(404, "Workflow presentation is not available")
        return record.result, view

    @app.get("/api/runs/{run_id}/workflow-presentation")
    def get_workflow_presentation(run_id: str) -> dict[str, Any]:
        _, view = workflow_view_for(run_id)
        return view

    @app.get("/api/runs/{run_id}/workflow-presentation/tables/{table_id}/rows")
    def get_workflow_presentation_rows(
        run_id: str,
        table_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=200),
    ) -> dict[str, Any]:
        result, _ = workflow_view_for(run_id)
        try:
            return workflow_presentation_table_page(
                result, table_id, offset=offset, limit=limit
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/runs/{run_id}/workflow-report.md")
    def download_workflow_report(run_id: str) -> Response:
        _, view = workflow_view_for(run_id)
        return Response(
            workflow_markdown_report(view),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="workflow-report.md"'},
        )

    @app.get("/api/runs/{run_id}/workflow-orders.csv")
    def download_workflow_orders(run_id: str) -> Response:
        _, view = workflow_view_for(run_id)
        return Response(
            "\ufeff" + workflow_orders_csv(view),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="workflow-orders.csv"'},
        )

    @app.get("/api/runs/{run_id}/workflow-ap-scopes.csv")
    def download_workflow_scopes(run_id: str) -> Response:
        _, view = workflow_view_for(run_id)
        return Response(
            "\ufeff" + workflow_ap_scopes_csv(view),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="workflow-ap-scopes.csv"'},
        )

    @app.get("/api/runs/{run_id}/presentation/blocks/{block_index}/rows")
    def get_presentation_table_rows(
        run_id: str,
        block_index: int,
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=200),
    ) -> dict[str, Any]:
        try:
            record = store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(404, "Run not found") from exc
        if record.result is None:
            raise HTTPException(409, "Run result is not available")
        output_schema: dict[str, Any] | None = None
        if record.agent_id:
            try:
                manifest = agents.get(record.agent_id)
            except KeyError:
                manifest = {}
            execution = manifest.get("execution") if isinstance(manifest, dict) else None
            candidate = execution.get("outputSchema") if isinstance(execution, dict) else None
            output_schema = candidate if isinstance(candidate, dict) else None
        try:
            return presentation_table_page(
                record.result,
                block_index,
                offset=offset,
                limit=limit,
                output_schema=output_schema,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/runs/{run_id}/artifacts/{name}")
    def download_artifact(run_id: str, name: str) -> FileResponse:
        try:
            record = store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(404, "Run not found") from exc
        allowed = {
            str(item.get("name")): str(item.get("media_type") or "application/octet-stream")
            for item in (record.result.artifacts if record.result else [])
        }
        if name not in allowed or Path(name).name != name:
            raise HTTPException(404, "Artifact not found")
        path = (settings.data_root / "artifacts" / run_id / name).resolve()
        artifact_root = (settings.data_root / "artifacts" / run_id).resolve()
        if artifact_root not in path.parents or not path.is_file():
            raise HTTPException(404, "Artifact not found")
        return FileResponse(path, media_type=allowed[name], filename=name)

    restricted_headers = {
        "Cache-Control": "no-store",
        "Content-Security-Policy": "sandbox",
        "X-Content-Type-Options": "nosniff",
    }

    def _require_local_artifact_request(request: Request, csrf: str | None = None) -> None:
        origin = str(request.headers.get("origin") or "")
        allowed_origins = set(settings.local_ui_origins)
        if origin not in allowed_origins:
            raise HTTPException(403, "Restricted evidence is available only to the local UI.")
        if csrf is not None and not hmac.compare_digest(csrf, artifact_csrf_token):
            raise HTTPException(403, "Restricted evidence CSRF validation failed.")

    def _consume_reveal_token(
        run_id: str,
        artifact_id: str,
        operation: str,
        token: str | None,
    ) -> dict[str, Any]:
        if not token:
            raise HTTPException(401, "A reveal token is required.")
        try:
            artifact = store.get_structured_artifact(run_id, artifact_id)
        except KeyError as exc:
            raise HTTPException(404, "Restricted evidence not found") from exc
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        if not store.consume_artifact_reveal_token(
            token_hash=token_hash,
            run_id=run_id,
            artifact_id=artifact_id,
            operation=operation,
            artifact_sha256=str(artifact["sha256"]),
            now=now,
        ):
            raise HTTPException(401, "Reveal token is invalid, expired, or already used.")
        return artifact

    def _artifact_cursor(
        *, run_id: str, artifact_id: str, artifact_sha256: str, offset: int
    ) -> str:
        payload = json.dumps(
            {
                "run_id": run_id,
                "artifact_id": artifact_id,
                "sha256": artifact_sha256,
                "offset": offset,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(
            artifact_csrf_token.encode("utf-8"), payload, hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")

    def _artifact_cursor_offset(
        cursor: str,
        *,
        run_id: str,
        artifact_id: str,
        artifact_sha256: str,
    ) -> int:
        try:
            encoded = cursor + "=" * (-len(cursor) % 4)
            decoded = base64.urlsafe_b64decode(encoded.encode("ascii"))
            payload, signature = decoded[:-32], decoded[-32:]
            expected = hmac.new(
                artifact_csrf_token.encode("utf-8"), payload, hashlib.sha256
            ).digest()
            value = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(400, "Restricted evidence cursor is invalid.") from exc
        if not hmac.compare_digest(signature, expected) or (
            value.get("run_id") != run_id
            or value.get("artifact_id") != artifact_id
            or value.get("sha256") != artifact_sha256
        ):
            raise HTTPException(400, "Restricted evidence cursor is invalid.")
        offset = value.get("offset")
        if not isinstance(offset, int) or offset < 0:
            raise HTTPException(400, "Restricted evidence cursor is invalid.")
        return offset

    @app.get("/api/security/csrf")
    def artifact_csrf(request: Request) -> JSONResponse:
        _require_local_artifact_request(request)
        return JSONResponse({"csrf_token": artifact_csrf_token}, headers=restricted_headers)

    @app.post("/api/runs/{run_id}/structured-artifacts/{artifact_id}/reveal")
    def reveal_structured_artifact(
        request: Request,
        run_id: str,
        artifact_id: str,
        payload: ArtifactRevealRequest,
        x_sapba_csrf: str = Header(alias="X-SAPBA-CSRF"),
    ) -> JSONResponse:
        _require_local_artifact_request(request, x_sapba_csrf)
        try:
            artifact = store.get_structured_artifact(run_id, artifact_id)
            RestrictedArtifactStore._require_available(artifact)
        except KeyError as exc:
            raise HTTPException(404, "Restricted evidence not found") from exc
        except RestrictedArtifactError as exc:
            raise HTTPException(410, {"code": exc.code, "message": str(exc)}) from exc
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(minutes=2)
        store.save_artifact_reveal_token(
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            run_id=run_id,
            artifact_id=artifact_id,
            operation=payload.operation,
            artifact_sha256=str(artifact["sha256"]),
            expires_at=expires.isoformat(),
        )
        store.append_event(
            run_id,
            "restricted_artifact_reveal_authorized",
            {"artifact_id": artifact_id, "operation": payload.operation},
        )
        return JSONResponse(
            {"token": token, "operation": payload.operation, "expires_at": expires.isoformat()},
            headers=restricted_headers,
        )

    @app.get("/api/runs/{run_id}/structured-artifacts/{artifact_id}/rows")
    def structured_artifact_rows(
        request: Request,
        run_id: str,
        artifact_id: str,
        offset: int = Query(0, ge=0),
        cursor: str | None = Query(default=None),
        limit: int = Query(20, ge=1, le=200),
        x_sapba_reveal_token: str | None = Header(default=None, alias="X-SAPBA-Reveal-Token"),
    ) -> JSONResponse:
        _require_local_artifact_request(request)
        artifact = _consume_reveal_token(
            run_id, artifact_id, "rows", x_sapba_reveal_token
        )
        if cursor:
            offset = _artifact_cursor_offset(
                cursor,
                run_id=run_id,
                artifact_id=artifact_id,
                artifact_sha256=str(artifact["sha256"]),
            )
        try:
            rows = restricted_artifacts.rows(run_id, artifact_id)
        except RestrictedArtifactError as exc:
            raise HTTPException(410, {"code": exc.code, "message": str(exc)}) from exc
        store.append_event(
            run_id,
            "restricted_artifact_revealed",
            {"artifact_id": artifact_id, "operation": "rows"},
        )
        next_offset = offset + limit
        return JSONResponse(
            {
                "offset": offset,
                "limit": limit,
                "total_rows": len(rows),
                "rows": rows[offset : next_offset],
                "next_cursor": (
                    _artifact_cursor(
                        run_id=run_id,
                        artifact_id=artifact_id,
                        artifact_sha256=str(artifact["sha256"]),
                        offset=next_offset,
                    )
                    if next_offset < len(rows)
                    else None
                ),
            },
            headers=restricted_headers,
        )

    @app.get("/api/runs/{run_id}/structured-artifacts/{artifact_id}/download")
    def structured_artifact_download(
        request: Request,
        run_id: str,
        artifact_id: str,
        x_sapba_reveal_token: str | None = Header(default=None, alias="X-SAPBA-Reveal-Token"),
    ) -> Response:
        _require_local_artifact_request(request)
        _consume_reveal_token(run_id, artifact_id, "download", x_sapba_reveal_token)
        try:
            rows = restricted_artifacts.rows(run_id, artifact_id)
        except RestrictedArtifactError as exc:
            raise HTTPException(410, {"code": exc.code, "message": str(exc)}) from exc
        store.append_event(
            run_id,
            "restricted_artifact_revealed",
            {"artifact_id": artifact_id, "operation": "download"},
        )
        columns = sorted({str(key) for row in rows for key in row})
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _safe_csv_cell(row.get(key)) for key in columns})
        headers = {
            **restricted_headers,
            "Content-Disposition": f'attachment; filename="{artifact_id}.csv"',
        }
        return Response(
            content="\ufeff" + buffer.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers=headers,
        )

    @app.delete("/api/runs/{run_id}/structured-artifacts/{artifact_id}")
    def delete_structured_artifact(
        request: Request,
        run_id: str,
        artifact_id: str,
        payload: ArtifactDeleteRequest,
        x_sapba_csrf: str = Header(alias="X-SAPBA-CSRF"),
    ) -> JSONResponse:
        _require_local_artifact_request(request, x_sapba_csrf)
        try:
            artifact = restricted_artifacts.delete(run_id, artifact_id, reason=payload.reason)
        except KeyError as exc:
            raise HTTPException(404, "Restricted evidence not found") from exc
        store.append_event(
            run_id,
            "restricted_artifact_deleted",
            {"artifact_id": artifact_id, "reason": payload.reason},
        )
        return JSONResponse(
            RestrictedArtifactStore.public_ref(artifact), headers=restricted_headers
        )

    @app.get("/api/runs/{run_id}/events")
    async def run_events(
        request: Request,
        run_id: str,
        after: int = Query(0, ge=0),
    ) -> StreamingResponse:
        try:
            store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(404, "Run not found") from exc

        async def stream() -> AsyncIterator[str]:
            last_event_id = request.headers.get("last-event-id", "")
            try:
                resumed_after = int(last_event_id) if last_event_id else 0
            except ValueError:
                resumed_after = 0
            sequence = max(after, resumed_after)
            while True:
                if await request.is_disconnected():
                    return
                events = store.events_after(run_id, sequence)
                for event in events:
                    sequence = event.sequence
                    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                    yield f"id: {event.sequence}\nevent: {event.type}\ndata: {data}\n\n"
                record = store.get_run(run_id)
                if record.status in TERMINAL_STATUSES and not events:
                    return
                if not events:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.35)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/runs/{run_id}/input", status_code=202)
    async def provide_input(run_id: str, payload: RunInput) -> dict[str, str]:
        try:
            mode = await coordinator.provide_input(
                run_id, payload.input, payload.sensitive_inputs
            )
        except KeyError as exc:
            raise HTTPException(404, "Run not found") from exc
        except RunExecutionError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"run_id": run_id, "status": "accepted", "mode": mode}

    @app.post("/api/runs/{run_id}/cancel", status_code=202)
    async def cancel_run(run_id: str) -> dict[str, str]:
        try:
            await coordinator.cancel(run_id)
        except KeyError as exc:
            raise HTTPException(404, "Run not found") from exc
        return {"run_id": run_id, "status": "cancellation_requested"}

    @app.post(
        "/api/internal/harness/tools/{tool_name}",
        include_in_schema=False,
    )
    async def harness_tool_call(
        tool_name: str,
        payload: dict[str, Any],
        x_sapba_run: str = Header(default=""),
        x_sapba_capability: str = Header(default=""),
    ) -> dict[str, Any]:
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            raise HTTPException(422, "arguments must be an object")
        result = await harness_broker.handle(
            x_sapba_run, x_sapba_capability, tool_name, dict(arguments)
        )
        if result.get("code") == "harness_capability_denied":
            raise HTTPException(403, "Harness capability denied")
        return result

    @app.get("/api/tools/sap-read")
    async def sap_read_tools(query: str = "") -> dict[str, Any]:
        try:
            return await sap_read.catalog(query=query, limit=100)
        except (SapReadError, PluginError) as exc:
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from exc

    @app.get("/api/tools/skills")
    def skill_tools() -> list[dict[str, Any]]:
        try:
            return skills.list_all_approved_skills()
        except PluginError as exc:
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from exc

    @app.post("/api/runs/{run_id}/create-agent-draft", status_code=201)
    async def create_draft(run_id: str, payload: DraftCreate) -> dict[str, Any]:
        try:
            origin = _agent_draft_origin(workflow_drafts, payload)
            draft = await drafts.create_from_run(run_id, payload.correction, origin=origin)
            if origin:
                workflow_drafts.link_agent_draft(
                    str(origin["workflow_draft_id"]), str(origin["gap_id"]), draft.draft_id
                )
            return attach_management_draft(draft)
        except KeyError as exc:
            raise HTTPException(404, "Run or workflow gap not found") from exc
        except WorkflowDraftError as exc:
            raise HTTPException(
                409, {"code": exc.code, "message": str(exc), "detail": exc.detail}
            ) from exc
        except DraftError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/authoring/drafts", status_code=201)
    async def create_authoring_draft(payload: DraftAuthoringCreate) -> dict[str, Any]:
        try:
            origin = _agent_draft_origin(workflow_drafts, payload)
            draft = await drafts.create_from_run(payload.run_id, payload.correction, origin=origin)
            if origin:
                workflow_drafts.link_agent_draft(
                    str(origin["workflow_draft_id"]), str(origin["gap_id"]), draft.draft_id
                )
            return attach_management_draft(draft)
        except KeyError as exc:
            raise HTTPException(404, "Run or workflow gap not found") from exc
        except WorkflowDraftError as exc:
            raise HTTPException(
                409, {"code": exc.code, "message": str(exc), "detail": exc.detail}
            ) from exc
        except DraftError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/authoring/drafts/{draft_id}/import-to-management")
    def import_authoring_draft(draft_id: str, _payload: DraftImportRequest | None = None) -> dict[str, Any]:
        try:
            return agent_lifecycle.import_source_draft(draft_id)
        except KeyError as exc:
            raise HTTPException(404, "Source draft not found") from exc
        except AgentLifecycleError as exc:
            raise _agent_lifecycle_http_error(exc) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(409, {"code": "management_import_failed", "message": "The draft could not be imported; its source is retained."}) from exc

    @app.get("/api/authoring/drafts/{draft_id}")
    def get_draft(draft_id: str) -> dict[str, Any]:
        try:
            return store.get_draft(draft_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Draft not found") from exc

    @app.post("/api/authoring/drafts/{draft_id}/validate")
    def validate_draft(draft_id: str) -> dict[str, Any]:
        guard_source_draft_write(draft_id)
        try:
            return drafts.validate(draft_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Draft not found") from exc

    @app.post("/api/authoring/drafts/{draft_id}/input")
    def revise_draft(draft_id: str, payload: DraftInput) -> dict[str, Any]:
        guard_source_draft_write(draft_id)
        try:
            return drafts.add_review_input(draft_id, payload.input).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Draft not found") from exc
        except DraftError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/authoring/drafts/{draft_id}/apply")
    def apply_draft(draft_id: str) -> dict[str, Any]:
        guard_source_draft_write(draft_id)
        try:
            return drafts.apply(draft_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Draft not found") from exc
        except (DraftError, subprocess.CalledProcessError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/authoring/agents", status_code=201)
    async def create_managed_agent_draft(payload: AgentAuthoringCreate) -> dict[str, Any]:
        try:
            return await agent_lifecycle.create(payload)
        except (AgentLifecycleError, KeyError, DraftError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.get("/api/authoring/agents")
    def list_managed_agent_drafts(
        state: str = Query(default="all", pattern="^(all|unpublished)$"),
    ) -> list[dict[str, Any]]:
        return agent_lifecycle.list_drafts(state)

    @app.get("/api/authoring/agents/{draft_id}")
    def get_managed_agent_draft(draft_id: str) -> dict[str, Any]:
        try:
            return agent_lifecycle.get_draft(draft_id)
        except KeyError as exc:
            raise HTTPException(404, "Agent draft not found") from exc

    @app.put("/api/authoring/agents/{draft_id}")
    def update_managed_agent_draft(draft_id: str, payload: AgentDraftUpdate) -> dict[str, Any]:
        try:
            return agent_lifecycle.update(draft_id, payload)
        except (AgentLifecycleError, KeyError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.delete("/api/authoring/agents/{draft_id}")
    def delete_managed_agent_draft(
        draft_id: str, payload: AgentDraftDeleteRequest
    ) -> dict[str, Any]:
        try:
            return agent_lifecycle.delete_draft(draft_id, payload)
        except (AgentLifecycleError, KeyError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.post("/api/authoring/agents/{draft_id}/feedback")
    async def revise_managed_agent_with_runtime(
        draft_id: str, payload: AgentFeedbackRequest
    ) -> dict[str, Any]:
        try:
            draft = agent_lifecycle.get_draft(draft_id)
            turns = draft.get("conversation") or []
            if payload.base_turn != len(turns):
                raise AgentLifecycleError(
                    "Agent conversation changed.", code="agent_conversation_conflict"
                )
            return await agent_lifecycle.feedback(draft_id, payload)
        except (AgentLifecycleError, KeyError, PluginError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.post("/api/authoring/agents/{draft_id}/undo")
    def undo_managed_agent_revision(draft_id: str, payload: AgentUndoRequest) -> dict[str, Any]:
        try:
            return agent_lifecycle.undo(
                draft_id,
                expected_revision=payload.base_revision,
                target_revision=payload.target_revision,
            )
        except (AgentLifecycleError, KeyError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.post("/api/authoring/agents/{draft_id}/validate")
    def validate_managed_agent_draft(draft_id: str) -> dict[str, Any]:
        try:
            return agent_lifecycle.validate(draft_id)
        except (AgentLifecycleError, KeyError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.post("/api/authoring/agents/{draft_id}/live-validate", status_code=202)
    async def live_validate_managed_agent(
        draft_id: str, payload: AgentLiveValidationRequest
    ) -> dict[str, Any]:
        try:
            return await agent_lifecycle.live_validate(
                draft_id,
                input_value=payload.input,
                auto_discover=payload.auto_discover,
            )
        except (AgentLifecycleError, RunExecutionError, KeyError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.get("/api/authoring/agents/{draft_id}/validation-report")
    def managed_agent_validation_report(draft_id: str) -> dict[str, Any]:
        try:
            return agent_lifecycle.validation_report(draft_id)
        except (AgentLifecycleError, KeyError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.post("/api/authoring/agents/{draft_id}/publish")
    def publish_managed_agent(draft_id: str, payload: AgentPublishRequest) -> dict[str, Any]:
        try:
            current = agent_lifecycle.get_draft(draft_id)
            if int(current["revision"]) != payload.expected_revision:
                raise AgentLifecycleError(
                    "Agent draft revision changed.", code="agent_draft_conflict"
                )
            return agent_lifecycle.publish(draft_id, payload)
        except (AgentLifecycleError, KeyError, subprocess.CalledProcessError) as exc:
            raise _agent_lifecycle_http_error(exc) from exc

    @app.post("/api/authoring/workflows", status_code=201)
    def create_workflow_draft(payload: WorkflowDraftCreate) -> dict[str, Any]:
        try:
            return workflow_drafts.create(
                payload.title, payload.description, payload.workflow
            ).model_dump(mode="json")
        except (WorkflowDraftError, WorkflowError, KeyError) as exc:
            raise HTTPException(
                409, {"code": getattr(exc, "code", "workflow_draft_error"), "message": str(exc)}
            ) from exc

    @app.post("/api/authoring/workflows/compose", status_code=202)
    async def compose_workflow_draft(payload: WorkflowCompositionCreate) -> dict[str, Any]:
        try:
            return workflow_drafts.start_composition(
                payload.requirement, payload.locale
            ).model_dump(mode="json")
        except (WorkflowDraftError, WorkflowError, PluginError) as exc:
            raise HTTPException(
                409,
                {
                    "code": getattr(exc, "code", "workflow_composition_failed"),
                    "message": str(exc),
                    "detail": getattr(exc, "detail", None),
                },
            ) from exc

    @app.get("/api/authoring/workflows/{draft_id}")
    def get_workflow_draft(draft_id: str) -> dict[str, Any]:
        try:
            return workflow_drafts.get(draft_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft not found") from exc

    @app.put("/api/authoring/workflows/{draft_id}")
    def update_workflow_draft(
        draft_id: str, payload: WorkflowDraftUpdate
    ) -> dict[str, Any]:
        try:
            return workflow_drafts.update(
                draft_id, payload.expected_revision, payload.workflow
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft not found") from exc
        except (WorkflowDraftError, WorkflowError) as exc:
            raise HTTPException(
                409,
                {
                    "code": getattr(exc, "code", "workflow_draft_error"),
                    "message": str(exc),
                    "detail": getattr(exc, "detail", None),
                },
            ) from exc

    @app.get("/api/authoring/workflows/{draft_id}/revisions")
    def workflow_draft_revisions(draft_id: str) -> dict[str, Any]:
        try:
            return {"items": workflow_drafts.revisions(draft_id)}
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft not found") from exc

    @app.get("/api/authoring/workflows/{draft_id}/conversation")
    def workflow_conversation(draft_id: str) -> dict[str, Any]:
        try:
            return workflow_drafts.conversation(draft_id)
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft not found") from exc

    @app.post("/api/authoring/workflows/{draft_id}/feedback", status_code=202)
    async def workflow_feedback(
        draft_id: str, payload: WorkflowFeedbackRequest
    ) -> dict[str, Any]:
        try:
            return workflow_drafts.submit_feedback(
                draft_id,
                base_turn=payload.base_turn,
                base_revision=payload.base_revision,
                feedback=payload.feedback,
                feedback_type_hint=payload.feedback_type_hint,
                locale=payload.locale,
                validation_run_id=payload.validation_run_id,
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft not found") from exc
        except (WorkflowDraftError, PluginError) as exc:
            raise HTTPException(
                409,
                {"code": getattr(exc, "code", "workflow_feedback_failed"), "message": str(exc), "detail": getattr(exc, "detail", None)},
            ) from exc

    @app.post("/api/authoring/workflows/{draft_id}/feedback-input", status_code=202)
    async def workflow_feedback_input(
        draft_id: str, payload: WorkflowFeedbackInput
    ) -> dict[str, Any]:
        try:
            return workflow_drafts.provide_feedback_input(
                draft_id, base_turn=payload.base_turn, value=payload.input
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft not found") from exc
        except WorkflowDraftError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc), "detail": exc.detail}) from exc

    @app.post("/api/authoring/workflows/{draft_id}/accept-design")
    def accept_workflow_design(
        draft_id: str, payload: WorkflowDesignAccept
    ) -> dict[str, Any]:
        try:
            return workflow_drafts.accept_design(
                draft_id,
                base_turn=payload.base_turn,
                revision=payload.revision,
                workflow_hash=payload.workflow_hash,
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft not found") from exc
        except WorkflowDraftError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc), "detail": exc.detail}) from exc

    @app.post("/api/authoring/workflows/{draft_id}/accept-validation")
    def accept_workflow_validation(
        draft_id: str, payload: WorkflowValidationAccept
    ) -> dict[str, Any]:
        try:
            return workflow_drafts.accept_validation(
                draft_id,
                validation_run_id=payload.validation_run_id,
                validation_report_digest=payload.validation_report_digest,
                accepted_gap_codes=payload.accepted_gap_codes,
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft or validation run not found") from exc
        except WorkflowDraftError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc), "detail": exc.detail}) from exc

    @app.post("/api/authoring/workflows/{draft_id}/undo")
    def undo_workflow_revision(
        draft_id: str, payload: WorkflowUndoRequest
    ) -> dict[str, Any]:
        try:
            return workflow_drafts.undo(
                draft_id,
                base_turn=payload.base_turn,
                base_revision=payload.base_revision,
                target_revision=payload.target_revision,
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft or revision not found") from exc
        except (WorkflowDraftError, WorkflowError) as exc:
            raise HTTPException(409, {"code": getattr(exc, "code", "workflow_undo_failed"), "message": str(exc), "detail": getattr(exc, "detail", None)}) from exc

    @app.get("/api/authoring/workflows/{draft_id}/validation-attempts")
    def workflow_validation_attempts(draft_id: str) -> dict[str, Any]:
        try:
            return {"items": workflow_drafts.validation_attempts(draft_id)}
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft not found") from exc

    @app.get("/api/authoring/workflows/{draft_id}/validation-attempts/{run_id}/report")
    def workflow_validation_attempt_report(draft_id: str, run_id: str) -> dict[str, Any]:
        try:
            return workflow_drafts.validation_attempt_report(draft_id, run_id)
        except KeyError as exc:
            raise HTTPException(404, "Workflow validation report not found") from exc

    @app.post("/api/authoring/workflows/{draft_id}/composition-input", status_code=202)
    async def continue_workflow_composition(
        draft_id: str, payload: WorkflowCompositionInput
    ) -> dict[str, Any]:
        try:
            return workflow_drafts.provide_composition_input(
                draft_id, payload.input
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft not found") from exc
        except WorkflowDraftError as exc:
            raise HTTPException(
                409, {"code": exc.code, "message": str(exc), "detail": exc.detail}
            ) from exc

    @app.post("/api/authoring/workflows/{draft_id}/reconcile", status_code=202)
    async def reconcile_workflow_draft(draft_id: str) -> dict[str, Any]:
        try:
            return workflow_drafts.reconcile(draft_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft not found") from exc
        except (WorkflowDraftError, PluginError) as exc:
            raise HTTPException(
                409,
                {
                    "code": getattr(exc, "code", "workflow_reconcile_failed"),
                    "message": str(exc),
                    "detail": getattr(exc, "detail", None),
                },
            ) from exc

    @app.get("/api/authoring/workflows/{draft_id}/gaps/{gap_id}")
    def get_workflow_gap(draft_id: str, gap_id: str, locale: str = "zh") -> dict[str, Any]:
        try:
            return workflow_drafts.gap(
                draft_id, gap_id, locale="en" if locale == "en" else "zh"
            )
        except KeyError as exc:
            raise HTTPException(404, "Workflow gap not found") from exc

    @app.post("/api/authoring/workflows/{draft_id}/validate", status_code=202)
    async def validate_workflow_draft(
        draft_id: str, payload: WorkflowValidationRequest
    ) -> dict[str, Any]:
        try:
            draft = await workflow_drafts.validate_live(
                draft_id,
                payload.input,
                auto_discover=payload.auto_discover,
                expectations=[
                    item.model_dump(mode="json") for item in payload.expectations
                ],
            )
            return draft.model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft not found") from exc
        except (WorkflowDraftError, WorkflowError, PluginError, SapReadError) as exc:
            raise HTTPException(
                409,
                {
                    "code": getattr(exc, "code", "workflow_validation_failed"),
                    "message": str(exc),
                    "detail": getattr(exc, "detail", None),
                },
            ) from exc

    @app.post("/api/authoring/workflows/{draft_id}/publish")
    def publish_workflow_draft(
        draft_id: str, payload: WorkflowPublishRequest
    ) -> dict[str, Any]:
        try:
            return workflow_drafts.publish(
                draft_id,
                acknowledge_inconclusive=payload.acknowledge_inconclusive,
                validation_run_id=payload.validation_run_id,
                validation_report_digest=payload.validation_report_digest,
                accepted_gap_codes=payload.accepted_gap_codes,
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft not found") from exc
        except (WorkflowDraftError, WorkflowError, subprocess.CalledProcessError) as exc:
            raise HTTPException(
                409,
                {
                    "code": getattr(exc, "code", "workflow_publish_failed"),
                    "message": str(exc),
                    "detail": getattr(exc, "detail", None),
                },
            ) from exc

    @app.get("/api/authoring/workflows/{draft_id}/validation-report")
    def workflow_validation_report(draft_id: str) -> dict[str, Any]:
        try:
            return workflow_drafts.validation_report(draft_id)
        except KeyError as exc:
            raise HTTPException(404, "Workflow draft or validation run not found") from exc
        except WorkflowDraftError as exc:
            raise HTTPException(
                409,
                {
                    "code": exc.code,
                    "message": str(exc),
                    "detail": exc.detail,
                },
            ) from exc

    @app.get("/api/authoring/workflows/{draft_id}/validation-artifacts/{name}")
    def workflow_validation_artifact(draft_id: str, name: str) -> FileResponse:
        try:
            path, media_type = workflow_drafts.validation_artifact(draft_id, name)
        except KeyError as exc:
            raise HTTPException(404, "Validation artifact not found") from exc
        except WorkflowDraftError as exc:
            raise HTTPException(
                409,
                {
                    "code": exc.code,
                    "message": str(exc),
                    "detail": exc.detail,
                },
            ) from exc
        return FileResponse(path, media_type=media_type, filename=name)

    @app.get("/api/config/status")
    def config_status() -> dict[str, Any]:
        return {
            "sap_read_provider": selected_provider,
            "sap_base_url_configured": bool(settings.sap_base_url),
            "sap_credentials_configured": bool(settings.sap_username and settings.sap_password),
            "sap_client_configured": bool(settings.sap_client),
            "sap_verify_ssl": settings.sap_verify_ssl,
            "sap_env_file_configured": bool(settings.sap_env_file),
            "skillhub_root": str(settings.skillhub_root),
            "skillhub_available": settings.skillhub_root.is_dir(),
            "codex_sdk_installed": importlib.util.find_spec("openai_codex") is not None,
            "codex_model": settings.codex_model,
            "default_agent_runtime": str(
                getattr(sdk_registry, "default_provider_id", "codex")
            ),
            "data_root": str(settings.data_root),
        }

    @app.get("/api/system/sdk-runtimes")
    def list_sdk_runtimes() -> dict[str, Any]:
        return {
            "default_provider_id": str(
                getattr(sdk_registry, "default_provider_id", "codex")
            ),
            "items": sdk_registry.list(),
        }

    @app.post("/api/system/sdk-runtimes/check")
    async def check_all_sdk_runtimes() -> dict[str, Any]:
        return {
            "default_provider_id": str(
                getattr(sdk_registry, "default_provider_id", "codex")
            ),
            "items": await sdk_registry.check_all(),
        }

    @app.post("/api/system/sdk-runtimes/{provider_id}/check")
    async def check_sdk_runtime(provider_id: str) -> dict[str, Any]:
        try:
            check_provider = getattr(sdk_registry, "check_provider", None)
            if not callable(check_provider):
                raise SDKManagerError(
                    "Runtime checks are unavailable.",
                    code="runtime_check_unavailable",
                )
            return {"item": await check_provider(provider_id)}
        except SDKManagerError as exc:
            status_code = 404 if exc.code == "runtime_not_found" else 409
            raise HTTPException(
                status_code,
                {"code": exc.code, "message": str(exc), "detail": exc.detail},
            ) from exc

    @app.put("/api/system/sdk-runtimes/default")
    def set_default_sdk_runtime(payload: RuntimeDefaultUpdate) -> dict[str, Any]:
        try:
            set_default = getattr(sdk_registry, "set_default", None)
            if not callable(set_default):
                raise SDKManagerError(
                    "Runtime selection is unavailable.",
                    code="runtime_selection_unavailable",
                )
            item = set_default(payload.provider_id)
            return {
                "default_provider_id": payload.provider_id,
                "item": item,
                "message": "The default Agent Runtime was updated for new tasks.",
            }
        except SDKManagerError as exc:
            status_code = 404 if exc.code == "runtime_not_found" else 409
            raise HTTPException(
                status_code,
                {"code": exc.code, "message": str(exc), "detail": exc.detail},
            ) from exc

    @app.get("/api/system/sdks")
    def list_sdks() -> dict[str, Any]:
        return {"items": sdk_registry.list()}

    @app.post("/api/system/sdks/check")
    async def check_all_sdks() -> dict[str, Any]:
        return {"items": await sdk_registry.check_all()}

    @app.post("/api/system/sdks/{sdk_id}/check")
    async def check_sdk(sdk_id: str) -> dict[str, Any]:
        try:
            return {"item": await sdk_registry.check(sdk_id)}
        except SDKManagerError as exc:
            status_code = 404 if exc.code == "sdk_not_found" else 409
            raise HTTPException(status_code, {"code": exc.code, "message": str(exc)}) from exc

    @app.post("/api/system/sdks/{sdk_id}/update")
    async def update_sdk(
        sdk_id: str,
        x_sapba_action: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if x_sapba_action != "sdk-update":
            raise HTTPException(403, "SDK update confirmation header is required")
        try:
            item = await sdk_registry.update(sdk_id)
        except SDKManagerError as exc:
            status_code = 404 if exc.code == "sdk_not_found" else 409
            raise HTTPException(status_code, {"code": exc.code, "message": str(exc)}) from exc
        return {
            "item": item,
            "message": "SDK update completed.",
            "restart_required": bool(item.get("restart_required")),
        }

    @app.put("/api/config/{target}")
    def update_local_config(target: str, payload: LocalConfigUpdate) -> dict[str, Any]:
        if target != "sap":
            raise HTTPException(404, "Unknown local configuration target")
        env_path = settings.repository_root / ".env"
        allowed = {
            "SAP_BASE_URL",
            "SAP_ODATA_BASE_URL",
            "SAP_USERNAME",
            "SAP_PASSWORD",
            "SAP_CLIENT",
            "SAP_VERIFY_SSL",
            "SAP_AUTH_TYPE",
            "SAP_ODATA_TIMEOUT_MS",
        }
        unknown = sorted(set(payload.values).difference(allowed))
        if unknown:
            raise HTTPException(400, "Unsupported configuration keys: " + ", ".join(unknown))
        _merge_env(env_path, payload.values)
        return {
            "ok": True,
            "target": target,
            "configured_keys": sorted(payload.values),
            "restart_required": True,
        }

    return app


def _agent_lifecycle_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "Agent or Agent draft not found")
    code = str(getattr(exc, "code", "agent_management_failed"))
    status = 404 if code in {"agent_not_found", "agent_version_not_found"} else 409
    return HTTPException(
        status,
        {
            "code": code,
            "message": str(exc),
            "detail": getattr(exc, "detail", None),
        },
    )


def _agent_draft_origin(workflow_drafts: WorkflowDraftService, payload: Any) -> dict[str, Any]:
    workflow_draft_id = getattr(payload, "workflow_draft_id", None)
    gap_id = getattr(payload, "gap_id", None)
    if bool(workflow_draft_id) != bool(gap_id):
        raise WorkflowDraftError(
            "workflowDraftId and gapId must be provided together.",
            code="workflow_gap_origin_invalid",
        )
    if not workflow_draft_id or not gap_id:
        return {}
    gap = workflow_drafts.gap(str(workflow_draft_id), str(gap_id), locale="zh")["gap"]
    return {
        "workflow_draft_id": str(workflow_draft_id),
        "gap_id": str(gap_id),
        "gap_contract": gap,
    }


def _merge_env(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                current[key.strip()] = value
    current.update({key: value.replace("\r", "").replace("\n", "") for key, value in updates.items()})
    path.write_text("".join(f"{key}={value}\n" for key, value in sorted(current.items())), encoding="utf-8")
