from __future__ import annotations

import importlib.util
import inspect
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .codex_planner import Planner
from .manifests import AgentRepository, validate_execution
from .sapclaw import SapClawClient
from .sap_read import SapReadError
from .skills import SkillRegistry


class PluginError(RuntimeError):
    def __init__(self, message: str, *, code: str = "plugin_error", detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class PluginStatus(StrEnum):
    discovered = "discovered"
    disabled = "disabled"
    starting = "starting"
    ready = "ready"
    degraded = "degraded"
    failed = "failed"
    stopped = "stopped"


class PluginCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str = Field(pattern=r"^[a-z][a-z0-9_]*\.v[1-9][0-9]*$")
    operations: list[str] = Field(min_length=1)


class PluginTransport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    endpoint_config_key: str | None = None
    entrypoint: str | None = None
    loopback_only: bool = True


class PluginPermissions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sap_read: bool = False
    sap_write: bool = False
    arbitrary_shell: bool = False
    arbitrary_code: bool = False
    filesystem_write: bool = False


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    plugin_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
    name: dict[str, str]
    publisher: str
    enabled: bool = True
    capabilities: list[PluginCapability] = Field(min_length=1)
    transport: PluginTransport
    permissions: PluginPermissions = Field(default_factory=PluginPermissions)
    health_check: str = "health"

    @model_validator(mode="after")
    def enforce_prototype_boundary(self) -> "PluginManifest":
        if self.permissions.sap_write:
            raise ValueError("SAP write permission is forbidden in the local prototype")
        if self.permissions.arbitrary_shell or self.permissions.arbitrary_code:
            raise ValueError("arbitrary shell or code permission is forbidden")
        if not self.transport.loopback_only:
            raise ValueError("plugin transports must be loopback-only")
        seen: set[str] = set()
        for item in self.capabilities:
            if item.capability in seen:
                raise ValueError(f"duplicate capability: {item.capability}")
            seen.add(item.capability)
            if len(item.operations) != len(set(item.operations)):
                raise ValueError(f"duplicate operations in {item.capability}")
        return self


class PluginProvider(Protocol):
    async def health(self) -> dict[str, Any]: ...


@dataclass(slots=True)
class PluginRegistration:
    manifest: PluginManifest
    source: str
    provider: PluginProvider | None = None
    status: PluginStatus = PluginStatus.discovered
    last_health: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PluginBinding:
    manifest: PluginManifest
    provider: PluginProvider
    capability: PluginCapability

    def trace(self, operation: str) -> dict[str, Any]:
        return {
            "plugin_id": self.manifest.plugin_id,
            "plugin_version": self.manifest.version,
            "capability": self.capability.capability,
            "operation": operation,
        }


class PluginManager:
    """Trusted local registry. Manifests declare capabilities; code is bound explicitly."""

    def __init__(
        self,
        manifest_root: Path,
        state_path: Path,
        defaults: list[PluginManifest],
        *,
        preferred_plugins: dict[str, str] | None = None,
        runtime_enabled: dict[str, bool] | None = None,
    ) -> None:
        self.manifest_root = manifest_root
        self.state_path = state_path
        self.defaults = {item.plugin_id: item for item in defaults}
        self.preferred_plugins = dict(preferred_plugins or {})
        self.runtime_enabled = dict(runtime_enabled or {})
        self._registrations: dict[str, PluginRegistration] = {}
        self._providers: dict[str, PluginProvider] = {}
        self.rescan()

    def bind_provider(self, plugin_id: str, provider: PluginProvider) -> None:
        if plugin_id not in self._registrations:
            raise PluginError(f"Unknown plugin: {plugin_id}", code="plugin_not_found")
        self._providers[plugin_id] = provider
        registration = self._registrations[plugin_id]
        registration.provider = provider
        if registration.manifest.enabled and registration.status == PluginStatus.failed:
            registration.status = PluginStatus.discovered
            registration.error = None

    def rescan(self) -> dict[str, Any]:
        manifests = dict(self.defaults)
        sources = {plugin_id: "built-in" for plugin_id in manifests}
        errors: list[dict[str, str]] = []
        if self.manifest_root.is_dir():
            for path in sorted(self.manifest_root.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    manifest = PluginManifest.model_validate(payload)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    errors.append({"source": path.name, "error": str(exc)})
                    continue
                manifests[manifest.plugin_id] = manifest
                sources[manifest.plugin_id] = path.name

        enabled_overrides = self._read_enabled_overrides()
        previous = self._registrations
        registrations: dict[str, PluginRegistration] = {}
        for plugin_id, original in sorted(manifests.items()):
            enabled = enabled_overrides.get(plugin_id, original.enabled)
            enabled = self.runtime_enabled.get(plugin_id, enabled)
            manifest = original.model_copy(update={"enabled": enabled})
            prior = previous.get(plugin_id)
            provider = self._providers.get(plugin_id) or (prior.provider if prior else None)
            if not manifest.enabled:
                status = PluginStatus.disabled
            elif prior and prior.status in {PluginStatus.ready, PluginStatus.degraded}:
                status = prior.status
            else:
                status = PluginStatus.discovered
            registrations[plugin_id] = PluginRegistration(
                manifest=manifest,
                source=sources[plugin_id],
                provider=provider,
                status=status,
                last_health=prior.last_health if prior else None,
                error=prior.error if prior else None,
            )
        self._registrations = registrations
        return {
            "plugins": len(registrations),
            "errors": errors,
        }

    async def start(self) -> None:
        for plugin_id, registration in self._registrations.items():
            if registration.manifest.enabled:
                await self.health(plugin_id)

    async def stop(self) -> None:
        for registration in self._registrations.values():
            if registration.manifest.enabled:
                registration.status = PluginStatus.stopped

    async def health(self, plugin_id: str) -> dict[str, Any]:
        registration = self._get(plugin_id)
        if not registration.manifest.enabled:
            registration.status = PluginStatus.disabled
            result = {"ok": False, "code": "plugin_disabled"}
            registration.last_health = result
            return result
        if registration.provider is None:
            registration.status = PluginStatus.failed
            registration.error = {
                "code": "plugin_provider_unbound",
                "message": "The manifest was discovered but no trusted provider was bound.",
            }
            registration.last_health = {"ok": False, "error": registration.error}
            return registration.last_health
        registration.status = PluginStatus.starting
        try:
            result = await registration.provider.health()
            if not isinstance(result, dict):
                raise TypeError("health check did not return an object")
            registration.last_health = result
            registration.error = None
            registration.status = (
                PluginStatus.ready if result.get("ok") is True else PluginStatus.degraded
            )
            return result
        except Exception as exc:
            registration.status = PluginStatus.failed
            registration.error = {
                "code": str(getattr(exc, "code", "plugin_health_failed")),
                "message": str(exc),
            }
            registration.last_health = {"ok": False, "error": registration.error}
            return registration.last_health

    async def set_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        registration = self._get(plugin_id)
        registration.manifest = registration.manifest.model_copy(update={"enabled": enabled})
        registration.status = PluginStatus.discovered if enabled else PluginStatus.disabled
        registration.error = None
        overrides = self._read_enabled_overrides()
        overrides[plugin_id] = enabled
        self._write_enabled_overrides(overrides)
        if enabled:
            await self.health(plugin_id)
        return self.get(plugin_id)

    def list(self) -> list[dict[str, Any]]:
        return [self._public(item) for item in self._registrations.values()]

    def get(self, plugin_id: str) -> dict[str, Any]:
        return self._public(self._get(plugin_id))

    def capabilities(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for registration in self._registrations.values():
            for capability in registration.manifest.capabilities:
                rows.append(
                    {
                        "capability": capability.capability,
                        "operations": capability.operations,
                        "plugin_id": registration.manifest.plugin_id,
                        "plugin_version": registration.manifest.version,
                        "enabled": registration.manifest.enabled,
                        "status": registration.status.value,
                    }
                )
        return rows

    def resolve(self, capability: str, operation: str) -> PluginBinding:
        candidates: list[PluginBinding] = []
        unavailable: list[dict[str, str]] = []
        for registration in self._registrations.values():
            descriptor = next(
                (item for item in registration.manifest.capabilities if item.capability == capability),
                None,
            )
            if descriptor is None or operation not in descriptor.operations:
                continue
            if (
                registration.manifest.enabled
                and registration.provider is not None
                and registration.status in {PluginStatus.ready, PluginStatus.degraded}
            ):
                candidates.append(
                    PluginBinding(registration.manifest, registration.provider, descriptor)
                )
            else:
                unavailable.append(
                    {
                        "plugin_id": registration.manifest.plugin_id,
                        "status": registration.status.value,
                    }
                )
        preferred_plugin = self.preferred_plugins.get(capability)
        if preferred_plugin:
            selected = [
                item for item in candidates if item.manifest.plugin_id == preferred_plugin
            ]
            if len(selected) == 1:
                return selected[0]
            raise PluginError(
                f"Selected plugin {preferred_plugin} is unavailable for {capability}:{operation}.",
                code="selected_provider_unavailable",
                detail={
                    "selected_plugin": preferred_plugin,
                    "available_plugins": [item.manifest.plugin_id for item in candidates],
                    "unavailable": unavailable,
                },
            )
        if len(candidates) > 1:
            raise PluginError(
                f"Multiple plugins provide {capability}:{operation}.",
                code="capability_conflict",
                detail=[item.manifest.plugin_id for item in candidates],
            )
        if not candidates:
            raise PluginError(
                f"No ready plugin provides {capability}:{operation}.",
                code="capability_unavailable",
                detail=unavailable,
            )
        return candidates[0]

    async def invoke(
        self, capability: str, operation: str, *args: Any, **kwargs: Any
    ) -> Any:
        binding = self.resolve(capability, operation)
        method = getattr(binding.provider, operation, None)
        if not callable(method):
            raise PluginError(
                f"Plugin {binding.manifest.plugin_id} declares but does not implement {operation}.",
                code="plugin_contract_violation",
            )
        result = method(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    def invoke_sync(
        self, capability: str, operation: str, *args: Any, **kwargs: Any
    ) -> Any:
        binding = self.resolve(capability, operation)
        method = getattr(binding.provider, operation, None)
        if not callable(method):
            raise PluginError(
                f"Plugin {binding.manifest.plugin_id} declares but does not implement {operation}.",
                code="plugin_contract_violation",
            )
        result = method(*args, **kwargs)
        if inspect.isawaitable(result):
            raise PluginError(
                f"Plugin operation {operation} is asynchronous.",
                code="plugin_contract_violation",
            )
        return result

    def _get(self, plugin_id: str) -> PluginRegistration:
        try:
            return self._registrations[plugin_id]
        except KeyError as exc:
            raise PluginError(f"Unknown plugin: {plugin_id}", code="plugin_not_found") from exc

    def _public(self, registration: PluginRegistration) -> dict[str, Any]:
        manifest = registration.manifest
        return {
            "plugin_id": manifest.plugin_id,
            "version": manifest.version,
            "name": manifest.name,
            "publisher": manifest.publisher,
            "enabled": manifest.enabled,
            "status": registration.status.value,
            "capabilities": [item.model_dump(mode="json") for item in manifest.capabilities],
            "transport": manifest.transport.model_dump(mode="json"),
            "permissions": manifest.permissions.model_dump(mode="json"),
            "health": registration.last_health,
            "error": registration.error,
            "source": registration.source,
        }

    def _read_enabled_overrides(self) -> dict[str, bool]:
        if not self.state_path.is_file():
            return {}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        values = payload.get("enabled") if isinstance(payload, dict) else None
        if not isinstance(values, dict):
            return {}
        return {str(key): value for key, value in values.items() if isinstance(value, bool)}

    def _write_enabled_overrides(self, values: dict[str, bool]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({"enabled": values}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


class SapClawPluginProvider:
    def __init__(self, client: SapClawClient) -> None:
        self.client = client

    async def health(self) -> dict[str, Any]:
        result = await self.client.health()
        data = result.get("data") if isinstance(result, dict) else None
        runtime_ok = isinstance(data, dict) and (
            data.get("runtime_ready") is True or data.get("runtime_enabled") is True
        )
        readonly_ok = isinstance(data, dict) and data.get("read_only") is True
        return {**result, "ok": result.get("ok") is True and runtime_ok and readonly_ok}

    async def catalog(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.client.catalog(*args, **kwargs)

    async def guidance(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.client.guidance(*args, **kwargs)

    async def schema(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.client.schema(*args, **kwargs)

    async def validate_plan(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.client.validate_plan(*args, **kwargs)

    async def execute_plan(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._require_success(await self.client.execute_plan(*args, **kwargs))

    async def execute_get(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._require_success(await self.client.execute_get(*args, **kwargs))

    async def page(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._require_success(await self.client.page(*args, **kwargs))

    @staticmethod
    def _require_success(result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict) or result.get("ok") is not True:
            error = result.get("error") if isinstance(result, dict) else None
            code = "sapclaw_execution_failed"
            if isinstance(error, dict) and error.get("code"):
                code = str(error["code"])
            raise SapReadError(
                "SAPClaw returned an unsuccessful runtime envelope.",
                code=code,
                detail=result,
            )
        return result


class SkillhubPluginProvider:
    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    async def health(self) -> dict[str, Any]:
        skills = self.registry.list()
        return {
            "ok": True,
            "data": {
                "root_available": self.registry.skillhub_root.is_dir(),
                "approved_skills": len(skills),
                "read_only": True,
            },
        }

    def list(self) -> list[dict[str, Any]]:
        return self.registry.list()

    def get(self, skill_id: str) -> dict[str, Any]:
        return self.registry.get(skill_id)

    async def execute(self, skill_id: str, input_payload: dict[str, Any]) -> dict[str, Any]:
        return await self.registry.execute(skill_id, input_payload)


class CodexRuntimePluginProvider:
    def __init__(self, planner: Planner) -> None:
        self.planner = planner

    async def health(self) -> dict[str, Any]:
        return {
            "ok": importlib.util.find_spec("openai_codex") is not None,
            "data": {
                "sdk_installed": importlib.util.find_spec("openai_codex") is not None,
                "runtime": "codex_app_server",
                "starts_on_demand": True,
                "read_only_sandbox": True,
            },
        }

    async def plan(self, *args: Any, **kwargs: Any) -> Any:
        return await self.planner.plan(*args, **kwargs)

    async def ground_plan(self, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self.planner, "ground_plan", None)
        if not callable(method):
            raise PluginError(
                "Codex runtime does not support schema grounding.",
                code="operation_unavailable",
            )
        return await method(*args, **kwargs)

    async def summarize(self, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self.planner, "summarize", None)
        if not callable(method):
            raise PluginError("Codex runtime does not support summarize.", code="operation_unavailable")
        return await method(*args, **kwargs)

    async def author_draft(self, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self.planner, "author_draft", None)
        if not callable(method):
            raise PluginError("Codex runtime does not support authoring.", code="operation_unavailable")
        return await method(*args, **kwargs)

    async def review_workflow(self, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self.planner, "review_workflow", None)
        if not callable(method):
            raise PluginError("Codex runtime does not support workflow review.", code="operation_unavailable")
        return await method(*args, **kwargs)

    async def repair_workflow(self, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self.planner, "repair_workflow", None)
        if not callable(method):
            raise PluginError("Codex runtime does not support workflow repair.", code="operation_unavailable")
        return await method(*args, **kwargs)


class BusinessAgentPluginProvider:
    def __init__(self, repository: AgentRepository) -> None:
        self.repository = repository

    async def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "data": {"agents": len(self.repository.list()), "executable": len(self.repository.executable())},
        }

    def list(self) -> list[dict[str, Any]]:
        return self.repository.list()

    def executable(self) -> list[dict[str, Any]]:
        return self.repository.executable()

    def get(self, agent_id: str) -> dict[str, Any]:
        return self.repository.get(agent_id)

    def validate(self, agent_id: str) -> dict[str, Any]:
        agent = self.repository.get(agent_id)
        validate_execution(agent, f"agent:{agent_id}")
        return {"ok": True, "agent_id": agent_id}


class SapReadCapability:
    capability = "sap_read.v1"

    def __init__(self, manager: PluginManager) -> None:
        self.manager = manager

    def plugin_metadata(self, operation: str) -> dict[str, Any]:
        return self.manager.resolve(self.capability, operation).trace(operation)

    async def health(self) -> dict[str, Any]:
        return await self.manager.invoke(self.capability, "health")

    async def catalog(self, query: str = "", skip: int = 0, limit: int = 100) -> dict[str, Any]:
        return await self.manager.invoke(
            self.capability, "catalog", query=query, skip=skip, limit=limit
        )

    async def guidance(self, query: str) -> dict[str, Any]:
        return await self.manager.invoke(self.capability, "guidance", query)

    async def schema(
        self,
        service_name: str,
        entity_sets: list[str] | str,
        query: str = "",
        *,
        include_fields: bool = True,
        max_fields: int = 5000,
    ) -> dict[str, Any]:
        return await self.manager.invoke(
            self.capability,
            "schema",
            service_name,
            entity_sets,
            query,
            include_fields=include_fields,
            max_fields=max_fields,
        )

    async def validate_plan(self, plan: dict[str, Any], query: str = "") -> dict[str, Any]:
        return await self.manager.invoke(self.capability, "validate_plan", plan, query)

    async def execute_plan(
        self,
        plan: dict[str, Any],
        query: str = "",
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        return await self.manager.invoke(
            self.capability, "execute_plan", plan, query, conversation_id
        )

    async def execute_get(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self.manager.invoke(self.capability, "execute_get", request)

    async def page(self, case_id: str, skip: int = 0) -> dict[str, Any]:
        return await self.manager.invoke(self.capability, "page", case_id, skip)


class BusinessAgentCapability:
    capability = "business_agent.v1"

    def __init__(self, manager: PluginManager) -> None:
        self.manager = manager

    def list(self) -> list[dict[str, Any]]:
        return self.manager.invoke_sync(self.capability, "list")

    def executable(self) -> list[dict[str, Any]]:
        return self.manager.invoke_sync(self.capability, "executable")

    def get(self, agent_id: str) -> dict[str, Any]:
        return self.manager.invoke_sync(self.capability, "get", agent_id)

    def validate(self, agent_id: str) -> dict[str, Any]:
        return self.manager.invoke_sync(self.capability, "validate", agent_id)


class SkillCapability:
    def __init__(self, manager: PluginManager) -> None:
        self.manager = manager

    def plugin_metadata(self, operation: str) -> dict[str, Any]:
        capability = "skill_execute.v1" if operation == "execute" else "skill_catalog.v1"
        return self.manager.resolve(capability, operation).trace(operation)

    def list(self) -> list[dict[str, Any]]:
        return self.manager.invoke_sync("skill_catalog.v1", "list")

    def get(self, skill_id: str) -> dict[str, Any]:
        return self.manager.invoke_sync("skill_catalog.v1", "get", skill_id)

    async def execute(self, skill_id: str, input_payload: dict[str, Any]) -> dict[str, Any]:
        return await self.manager.invoke("skill_execute.v1", "execute", skill_id, input_payload)


class AgentRuntimeCapability:
    def __init__(self, manager: PluginManager) -> None:
        self.manager = manager

    def plugin_metadata(self, operation: str) -> dict[str, Any]:
        capability = (
            "workflow_authoring.v1"
            if operation in {"review_workflow", "repair_workflow"}
            else "authoring.v1"
            if operation == "author_draft"
            else "agent_runtime.v1"
        )
        return self.manager.resolve(capability, operation).trace(operation)

    def supports(self, operation: str) -> bool:
        capability = (
            "workflow_authoring.v1"
            if operation in {"review_workflow", "repair_workflow"}
            else "authoring.v1"
            if operation == "author_draft"
            else "agent_runtime.v1"
        )
        try:
            binding = self.manager.resolve(capability, operation)
        except PluginError:
            return False
        return callable(getattr(getattr(binding.provider, "planner", None), operation, None))

    async def plan(self, *args: Any, **kwargs: Any) -> Any:
        return await self.manager.invoke("agent_runtime.v1", "plan", *args, **kwargs)

    async def ground_plan(self, *args: Any, **kwargs: Any) -> Any:
        return await self.manager.invoke("agent_runtime.v1", "ground_plan", *args, **kwargs)

    async def summarize(self, *args: Any, **kwargs: Any) -> Any:
        return await self.manager.invoke("agent_runtime.v1", "summarize", *args, **kwargs)

    async def author_draft(self, *args: Any, **kwargs: Any) -> Any:
        return await self.manager.invoke("authoring.v1", "author_draft", *args, **kwargs)

    async def review_workflow(self, *args: Any, **kwargs: Any) -> Any:
        return await self.manager.invoke(
            "workflow_authoring.v1", "review_workflow", *args, **kwargs
        )

    async def repair_workflow(self, *args: Any, **kwargs: Any) -> Any:
        return await self.manager.invoke(
            "workflow_authoring.v1", "repair_workflow", *args, **kwargs
        )


def official_plugin_manifests() -> list[PluginManifest]:
    payloads = [
        {
            "schema_version": "1.0",
            "plugin_id": "embedded-sap-odata",
            "version": "1.0.0",
            "name": {"zh": "内嵌 SAP 只读连接器", "en": "Embedded SAP Read-only Provider"},
            "publisher": "SAPBusinessAgents",
            "enabled": True,
            "capabilities": [
                {
                    "capability": "sap_read.v1",
                    "operations": [
                        "health",
                        "catalog",
                        "guidance",
                        "schema",
                        "validate_plan",
                        "execute_plan",
                        "execute_get",
                        "page",
                    ],
                },
            ],
            "transport": {"type": "builtin", "loopback_only": True},
            "permissions": {"sap_read": True},
        },
        {
            "schema_version": "1.0",
            "plugin_id": "sapclaw-runtime",
            "version": "2.0.0",
            "name": {"zh": "SAPClaw 只读运行时", "en": "SAPClaw Read-only Runtime"},
            "publisher": "SAPBusinessAgents",
            "enabled": False,
            "capabilities": [
                {
                    "capability": "sap_read.v1",
                    "operations": ["health", "catalog", "guidance", "schema", "validate_plan", "execute_plan", "execute_get", "page"],
                },
                {
                    "capability": "mcp_tools.v1",
                    "operations": ["catalog", "schema", "validate_plan", "execute_plan", "execute_get", "page"],
                },
            ],
            "transport": {"type": "http", "endpoint_config_key": "SAPCLAW_RUNTIME_URL", "loopback_only": True},
            "permissions": {"sap_read": True},
        },
        {
            "schema_version": "1.0",
            "plugin_id": "sapskillhub",
            "version": "1.0.0",
            "name": {"zh": "SAPSkillhub 只读技能", "en": "SAPSkillhub Read-only Skills"},
            "publisher": "SAPBusinessAgents",
            "capabilities": [
                {"capability": "skill_catalog.v1", "operations": ["list", "get"]},
                {"capability": "skill_execute.v1", "operations": ["execute"]},
            ],
            "transport": {"type": "python_cli", "entrypoint": "python run.py --input input.json --output output.json", "loopback_only": True},
            "permissions": {"sap_read": True},
        },
        {
            "schema_version": "1.0",
            "plugin_id": "codex-runtime",
            "version": "0.144.4",
            "name": {"zh": "Codex 运行层", "en": "Codex Runtime"},
            "publisher": "OpenAI",
            "capabilities": [
                {"capability": "agent_runtime.v1", "operations": ["plan", "ground_plan", "summarize"]},
                {"capability": "authoring.v1", "operations": ["author_draft"]},
                {
                    "capability": "workflow_authoring.v1",
                    "operations": ["review_workflow", "repair_workflow"],
                },
            ],
            "transport": {"type": "codex_app_server", "entrypoint": "codex app-server --listen stdio://", "loopback_only": True},
            "permissions": {},
        },
        {
            "schema_version": "1.0",
            "plugin_id": "business-agent-catalog",
            "version": "2.0.0",
            "name": {"zh": "业务 Agent 包", "en": "Business Agent Packages"},
            "publisher": "SAPBusinessAgents",
            "capabilities": [
                {"capability": "business_agent.v1", "operations": ["list", "executable", "get", "validate"]},
            ],
            "transport": {"type": "builtin", "loopback_only": True},
            "permissions": {},
        },
    ]
    return [PluginManifest.model_validate(item) for item in payloads]
