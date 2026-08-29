from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .codex_planner import CodexPlanner, Planner
from .config import Settings
from .database import RunStore
from .engine import RunCoordinator, RunExecutionError, presentation_table_page
from .factory import AgentDraftService, DraftError
from .harness import CodexHarnessController, HarnessToolBroker
from .manifests import AgentRepository
from .models import (
    DraftAuthoringCreate,
    DraftCreate,
    DraftInput,
    RunCreate,
    RunInput,
    TERMINAL_STATUSES,
    WorkflowCompositionCreate,
    WorkflowCompositionInput,
    WorkflowDraftCreate,
    WorkflowDraftUpdate,
    WorkflowPublishRequest,
    WorkflowValidationRequest,
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
from .sdk_manager import SDKManager, SDKManagerError
from .skills import SkillRegistry
from .workflow_factory import WorkflowDraftError, WorkflowDraftService
from .workflows import WorkflowError, WorkflowRepository


LOGGER = logging.getLogger(__name__)


class LocalConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, str] = Field(default_factory=dict)


class PluginEnableUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


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
        relationship_catalog_path=settings.repository_root / "config" / "business-relationships.json",
        service_registry_path=settings.odata_service_registry_path,
        catalog_seed_path=settings.catalog_seed_path,
        curated_catalog_path=settings.repository_root / "config" / "catalog-curated-terms.json",
        normalization_catalog_path=settings.repository_root / "config" / "sap-value-normalization.json",
    )
    selected_provider = "embedded"
    selected_plugin_id = "embedded-sap-odata"
    planner_supplied = planner is not None
    planner = planner or CodexPlanner(settings.repository_root, model=settings.codex_model)
    plugin_manager = PluginManager(
        settings.plugin_manifest_root,
        settings.plugin_state_path,
        official_plugin_manifests(),
        preferred_plugins={"sap_read.v2": selected_plugin_id},
        runtime_enabled={"embedded-sap-odata": True},
    )
    plugin_manager.bind_provider("embedded-sap-odata", embedded)
    plugin_manager.bind_provider("sapskillhub", SkillhubPluginProvider(skill_registry))
    plugin_manager.bind_provider("codex-runtime", CodexRuntimePluginProvider(planner))
    plugin_manager.bind_provider(
        "business-agent-catalog", BusinessAgentPluginProvider(agents)
    )
    sap_read = SapReadCapability(plugin_manager)
    skills = SkillCapability(plugin_manager)
    agent_runtime = AgentRuntimeCapability(plugin_manager)
    business_agents = BusinessAgentCapability(plugin_manager)
    workflows = WorkflowRepository(settings.repository_root / "workflows", business_agents)
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
    sdk_registry = sdk_manager or SDKManager(
        settings.repository_root / "config" / "sdks.json",
        settings.repository_root,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await plugin_manager.start()
        await coordinator.start()
        try:
            yield
        finally:
            await coordinator.stop()
            await plugin_manager.stop()

    app = FastAPI(
        title="SAPBusinessAgents Local Prototype",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:4321",
            "http://localhost:4321",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
        ],
        allow_methods=["GET", "POST", "PUT"],
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
    app.state.sdk_manager = sdk_registry

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
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
            "codex_sdk_installed": importlib.util.find_spec("openai_codex") is not None,
            "free_query_runtime": {
                "selected": settings.free_query_runtime,
                "harness_enabled": harness is not None,
                "protocol": "agent_runtime.v2" if harness is not None else "agent_runtime.v1",
                "native_web_search": harness is not None,
                "automatic_fallback": False,
            },
            "executable_agents": len(agents.executable()),
            "published_workflows": len(workflows.list()),
            "approved_skills": len(skill_registry.list()),
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

    @app.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str) -> dict[str, Any]:
        try:
            return business_agents.get(agent_id)
        except KeyError as exc:
            raise HTTPException(404, "Agent not found") from exc
        except PluginError as exc:
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from exc

    @app.get("/api/workflows")
    def list_workflows() -> list[dict[str, Any]]:
        try:
            return workflows.list()
        except WorkflowError as exc:
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from exc

    @app.get("/api/workflows/{workflow_id}")
    def get_workflow(workflow_id: str) -> dict[str, Any]:
        try:
            return workflows.get(workflow_id)
        except KeyError as exc:
            raise HTTPException(404, "Workflow not found") from exc
        except WorkflowError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc

    @app.post("/api/runs", status_code=202)
    async def create_run(payload: RunCreate) -> dict[str, str]:
        try:
            run_id = await coordinator.submit(payload)
            return {"run_id": run_id, "status": "queued"}
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

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        try:
            return store.get_run(run_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Run not found") from exc

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
            mode = await coordinator.provide_input(run_id, payload.input)
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
            return skills.list()
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
            return draft.model_dump(mode="json")
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
            return draft.model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Run or workflow gap not found") from exc
        except WorkflowDraftError as exc:
            raise HTTPException(
                409, {"code": exc.code, "message": str(exc), "detail": exc.detail}
            ) from exc
        except DraftError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/authoring/drafts/{draft_id}")
    def get_draft(draft_id: str) -> dict[str, Any]:
        try:
            return store.get_draft(draft_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Draft not found") from exc

    @app.post("/api/authoring/drafts/{draft_id}/validate")
    def validate_draft(draft_id: str) -> dict[str, Any]:
        try:
            return drafts.validate(draft_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Draft not found") from exc

    @app.post("/api/authoring/drafts/{draft_id}/input")
    def revise_draft(draft_id: str, payload: DraftInput) -> dict[str, Any]:
        try:
            return drafts.add_review_input(draft_id, payload.input).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Draft not found") from exc
        except DraftError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/authoring/drafts/{draft_id}/apply")
    def apply_draft(draft_id: str) -> dict[str, Any]:
        try:
            return drafts.apply(draft_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, "Draft not found") from exc
        except (DraftError, subprocess.CalledProcessError) as exc:
            raise HTTPException(409, str(exc)) from exc

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
                draft_id, payload.input, auto_discover=payload.auto_discover
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
            "data_root": str(settings.data_root),
        }

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
