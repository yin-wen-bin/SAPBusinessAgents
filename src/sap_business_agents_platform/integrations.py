from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from pydantic import RootModel


MAIL_CAPABILITY = "mail.v1"
MAIL_OPERATIONS = {"search", "read", "draft", "send"}
READ_ONLY_MAIL_OPERATIONS = {"search", "read"}
INTEGRATION_FEATURES = (
    "catalog",
    "configuredDiscovery",
    "status",
    "authentication",
    "configuration",
    "directToolCall",
)


class IntegrationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "integration_error",
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class RuntimeIntegrationAdapter(Protocol):
    backend_id: str
    provider_id: str

    def describe_capabilities(self) -> dict[str, Any]: ...

    async def list_catalog(self, force_refresh: bool = False) -> list[dict[str, Any]]: ...

    async def begin_connection(self, item: dict[str, Any]) -> dict[str, Any]: ...

    async def refresh_status(self, connection: dict[str, Any]) -> dict[str, Any]: ...

    async def set_enabled(
        self, connection: dict[str, Any], enabled: bool
    ) -> dict[str, Any]: ...

    async def get_tool_contract(
        self, native_server: str, native_tool: str
    ) -> dict[str, Any]: ...

    async def invoke_exact(
        self,
        connection: dict[str, Any],
        binding: dict[str, Any],
        arguments: dict[str, Any],
        approval_context: dict[str, Any] | None,
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class _CodexInvocationApproval:
    allowed: bool = False
    draft_digest: str | None = None
    idempotency_key: str | None = None


class _JsonObject(RootModel[dict[str, Any]]):
    pass


class CodexAppServerTransport:
    """Small public-SDK wrapper around the Codex App Server JSON-RPC API."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._approval = _CodexInvocationApproval()
        self._thread_id: str | None = None

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        approval_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            client = await self._client_instance()
            self._approval = _CodexInvocationApproval(
                allowed=bool((approval_context or {}).get("verified")),
                draft_digest=str((approval_context or {}).get("draft_digest") or "")
                or None,
                idempotency_key=str((approval_context or {}).get("idempotency_key") or "")
                or None,
            )
            try:
                response = await asyncio.to_thread(
                    client.request,
                    method,
                    params,
                    response_model=_JsonObject,
                )
            finally:
                self._approval = _CodexInvocationApproval()
            return deepcopy(response.root)

    async def connector_thread_id(self) -> str:
        async with self._lock:
            if self._thread_id:
                return self._thread_id
            client = await self._client_instance()
            response = await asyncio.to_thread(
                client.thread_start,
                {
                    "cwd": str(self.repository_root),
                    "ephemeral": True,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "developerInstructions": (
                        "This thread is reserved for the platform Integration Gateway. "
                        "Do not inspect SAP credentials, use shell tools, or call any tool "
                        "except the exact MCP tool requested by the gateway."
                    ),
                },
            )
            payload = response.model_dump(mode="json")
            thread = payload.get("thread") or payload
            self._thread_id = str(thread.get("id") or thread.get("threadId") or "")
            if not self._thread_id:
                raise IntegrationError(
                    "Codex App Server did not return a connector thread id.",
                    code="runtime_integration_thread_unavailable",
                )
            return self._thread_id

    async def close(self) -> None:
        async with self._lock:
            client, self._client = self._client, None
            self._thread_id = None
            if client is not None:
                await asyncio.to_thread(client.close)

    async def _client_instance(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai_codex.client import CodexClient, CodexConfig
        except ImportError as exc:
            raise IntegrationError(
                "The Codex Python SDK is not installed.",
                code="runtime_integration_sdk_not_installed",
            ) from exc
        client = CodexClient(
            CodexConfig(cwd=str(self.repository_root), experimental_api=True),
            approval_handler=self._approval_handler,
        )
        await asyncio.to_thread(client.start)
        try:
            await asyncio.to_thread(client.initialize)
        except Exception:
            await asyncio.to_thread(client.close)
            raise
        self._client = client
        return client

    def _approval_handler(
        self, method: str, params: dict[str, Any] | None
    ) -> dict[str, Any]:
        # External integrations never inherit command, filesystem, or arbitrary
        # MCP permissions from the host Codex session.
        if method == "tool/requestUserInput" and self._approval.allowed:
            answers: dict[str, Any] = {}
            for question in (params or {}).get("questions") or []:
                if not isinstance(question, dict):
                    continue
                question_id = str(question.get("id") or "")
                options = question.get("options") or []
                accepted = next(
                    (
                        str(item.get("label") or "")
                        for item in options
                        if isinstance(item, dict)
                        and str(item.get("label") or "").strip().lower()
                        in {"accept", "approve", "allow"}
                    ),
                    "",
                )
                if question_id and accepted:
                    answers[question_id] = {"answers": [accepted]}
            return {"answers": answers}
        if "approval" in method.lower():
            return {"decision": "decline"}
        if "elicitation" in method.lower():
            return {"action": "decline"}
        return {}


class CodexAppServerIntegrationAdapter:
    backend_id = "codex-app-server"
    provider_id = "codex"

    def __init__(
        self,
        repository_root: Path,
        capabilities: dict[str, Any],
        *,
        transport: CodexAppServerTransport | Any | None = None,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.capabilities = deepcopy(capabilities)
        self.transport = transport or CodexAppServerTransport(self.repository_root)
        self._catalog: list[dict[str, Any]] | None = None
        self._tools: dict[tuple[str, str], dict[str, Any]] = {}

    def describe_capabilities(self) -> dict[str, Any]:
        return _adapter_description(
            self.provider_id, self.backend_id, self.capabilities
        )

    async def list_catalog(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        if self._catalog is not None and not force_refresh:
            return deepcopy(self._catalog)
        apps = await self._list_apps(force_refresh=force_refresh)
        servers = await self._list_mcp_servers()
        self._catalog = [*apps, *servers]
        return deepcopy(self._catalog)

    async def begin_connection(self, item: dict[str, Any]) -> dict[str, Any]:
        if item.get("source_kind") == "app":
            if item.get("installed"):
                return {
                    "outcome": "ready",
                    "status": "ready",
                    "auth_status": item.get("auth_status") or "unknown",
                    "health_status": item.get("health_status") or "unknown",
                    "native_connection_id": item["native_id"],
                }
            install_url = str(item.get("install_url") or "")
            if not install_url:
                raise IntegrationError(
                    "This Codex App does not expose an installation URL.",
                    code="runtime_integration_install_url_unavailable",
                )
            return {
                "outcome": "open_install_url",
                "url": install_url,
                "status": "installation_required",
                "auth_status": "unknown",
                "health_status": "unknown",
                "native_connection_id": item["native_id"],
            }

        auth_status = str(item.get("auth_status") or "unknown")
        if auth_status in {"not_logged_in", "expired", "reauthentication_required"}:
            response = await self.transport.request(
                "mcpServer/oauth/login", {"name": item["native_id"]}
            )
            url = str(response.get("authorizationUrl") or response.get("url") or "")
            return {
                "outcome": "open_auth_url",
                "url": url,
                "status": "authentication_required",
                "auth_status": auth_status,
                "health_status": item.get("health_status") or "unknown",
                "native_connection_id": item["native_id"],
            }
        return {
            "outcome": "ready",
            "status": "ready" if item.get("enabled", True) else "disabled",
            "auth_status": auth_status,
            "health_status": item.get("health_status") or "unknown",
            "native_connection_id": item["native_id"],
        }

    async def refresh_status(self, connection: dict[str, Any]) -> dict[str, Any]:
        native_id = str(connection.get("native_id") or "")
        source_kind = str(connection.get("source_kind") or "")
        items = await self.list_catalog(force_refresh=True)
        item = next(
            (
                value
                for value in items
                if value.get("native_id") == native_id
                and value.get("source_kind") == source_kind
            ),
            None,
        )
        if item is None:
            return {
                "status": "unavailable",
                "auth_status": "unknown",
                "health_status": "unavailable",
                "blocker": "runtime_catalog_item_unavailable",
            }
        status = "ready"
        if not item.get("installed", True):
            status = "installation_required"
        elif not item.get("enabled", True):
            status = "disabled"
        elif item.get("auth_status") in {
            "not_logged_in",
            "expired",
            "reauthentication_required",
        }:
            status = "authentication_required"
        elif item.get("health_status") in {"failed", "unavailable"}:
            status = "unhealthy"
        return {
            "status": status,
            "auth_status": item.get("auth_status") or "unknown",
            "health_status": item.get("health_status") or "unknown",
            "metadata": {"catalog_snapshot": item},
        }

    async def set_enabled(
        self, connection: dict[str, Any], enabled: bool
    ) -> dict[str, Any]:
        native_id = _safe_config_segment(str(connection.get("native_id") or ""))
        source_kind = str(connection.get("source_kind") or "")
        prefix = "apps" if source_kind == "app" else "mcp_servers"
        await self.transport.request(
            "config/value/write",
            {
                "keyPath": f"{prefix}.{native_id}.enabled",
                "value": bool(enabled),
                "mergeStrategy": "replace",
            },
        )
        self._catalog = None
        return await self.refresh_status(connection)

    async def get_tool_contract(
        self, native_server: str, native_tool: str
    ) -> dict[str, Any]:
        await self.list_catalog(force_refresh=True)
        contract = self._tools.get((native_server, native_tool))
        if contract is None:
            raise IntegrationError(
                "The selected Runtime tool is not available.",
                code="runtime_integration_tool_unavailable",
                detail={"server": native_server, "tool": native_tool},
            )
        return deepcopy(contract)

    async def invoke_exact(
        self,
        connection: dict[str, Any],
        binding: dict[str, Any],
        arguments: dict[str, Any],
        approval_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        thread_id = await self.transport.connector_thread_id()
        response = await self.transport.request(
            "mcpServer/tool/call",
            {
                "threadId": thread_id,
                "server": str(binding["native_server"]),
                "tool": str(binding["native_tool"]),
                "arguments": deepcopy(arguments),
            },
            approval_context=approval_context,
        )
        return response

    async def close(self) -> None:
        await self.transport.close()

    async def _list_apps(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        raw_apps: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 100}
            if force_refresh:
                params["forceRefetch"] = True
            if cursor:
                params["cursor"] = cursor
            response = await self.transport.request("app/list", params)
            page = response.get("data") or response.get("items") or []
            raw_apps.extend(item for item in page if isinstance(item, dict))
            cursor = str(response.get("nextCursor") or response.get("next_cursor") or "")
            if not cursor:
                break
        details: dict[str, dict[str, Any]] = {}
        app_ids = [str(item.get("id") or "") for item in raw_apps if item.get("id")]
        for start in range(0, len(app_ids), 100):
            try:
                response = await self.transport.request(
                    "app/read",
                    {"appIds": app_ids[start : start + 100], "includeTools": True},
                )
                for item in response.get("apps") or []:
                    if isinstance(item, dict) and item.get("id"):
                        details[str(item["id"])] = item
            except Exception:
                continue
        installed: dict[str, dict[str, Any]] = {}
        try:
            response = await self.transport.request(
                "app/installed",
                {"forceRefresh": True} if force_refresh else {},
            )
            installed = {
                str(item.get("id") or ""): item
                for item in response.get("apps") or []
                if isinstance(item, dict) and item.get("id")
            }
        except Exception:
            installed = {}
        apps: list[dict[str, Any]] = []
        for raw in raw_apps:
            app_id = str(raw.get("id") or "")
            if not app_id:
                continue
            detail = details.get(app_id) or raw
            installed_item = installed.get(app_id)
            tools = _normalize_native_tools(
                detail.get("toolSummaries") or detail.get("tools") or []
            )
            for tool in tools:
                tool["display_only"] = True
            apps.append(
                {
                    "source_kind": "app",
                    "native_id": app_id,
                    "name": str(detail.get("name") or app_id),
                    "description": str(detail.get("description") or ""),
                    "icon_url": detail.get("iconUrl") or raw.get("logoUrl"),
                    "install_url": detail.get("installUrl") or raw.get("installUrl"),
                    "installed": installed_item is not None,
                    "enabled": bool(
                        installed_item.get("enabled")
                        if installed_item is not None
                        else raw.get("isEnabled", True)
                    ),
                    "auth_status": "runtime_managed",
                    "health_status": (
                        "healthy"
                        if installed_item is not None and installed_item.get("callable")
                        else "unknown"
                    ),
                    "capabilities": _infer_capabilities(app_id, tools),
                    "tools": tools,
                    "metadata": {"native_snapshot": _safe_metadata(detail)},
                }
            )
        return apps

    async def _list_mcp_servers(self) -> list[dict[str, Any]]:
        servers: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 100, "detail": "toolsAndAuthOnly"}
            if cursor:
                params["cursor"] = cursor
            response = await self.transport.request("mcpServerStatus/list", params)
            page = response.get("data") or response.get("items") or []
            for raw in page:
                if not isinstance(raw, dict):
                    continue
                server_id = str(
                    raw.get("name") or raw.get("id") or raw.get("serverName") or ""
                )
                if not server_id:
                    continue
                tools = _normalize_native_tools(raw.get("tools") or {})
                for tool in tools:
                    self._tools[(server_id, tool["native_tool"])] = tool
                servers.append(
                    {
                        "source_kind": "mcp_server",
                        "native_id": server_id,
                        "name": str(
                            (raw.get("serverInfo") or {}).get("title")
                            or raw.get("displayName")
                            or server_id
                        ),
                        "description": str(
                            (raw.get("serverInfo") or {}).get("description")
                            or raw.get("description")
                            or ""
                        ),
                        "installed": True,
                        "enabled": raw.get("enabled") is not False,
                        "auth_status": _normalize_auth_status(raw),
                        "health_status": (
                            _normalize_health_status(raw)
                            if _normalize_health_status(raw) != "unknown"
                            else ("healthy" if tools else "unknown")
                        ),
                        "capabilities": _infer_capabilities(server_id, tools),
                        "tools": tools,
                        "metadata": {"native_snapshot": _safe_metadata(raw)},
                    }
                )
            cursor = str(response.get("nextCursor") or response.get("next_cursor") or "")
            if not cursor:
                break
        return servers


class UnavailableIntegrationAdapter:
    def __init__(
        self, provider_id: str, backend_id: str | None, capabilities: dict[str, Any]
    ) -> None:
        self.provider_id = provider_id
        self.backend_id = backend_id or f"{provider_id}-unavailable"
        self.capabilities = deepcopy(capabilities)

    def describe_capabilities(self) -> dict[str, Any]:
        return _adapter_description(
            self.provider_id, self.backend_id, self.capabilities
        )

    async def list_catalog(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        del force_refresh
        return []

    async def begin_connection(self, item: dict[str, Any]) -> dict[str, Any]:
        del item
        self._unavailable()

    async def refresh_status(self, connection: dict[str, Any]) -> dict[str, Any]:
        del connection
        return {
            "status": "unavailable",
            "auth_status": "unknown",
            "health_status": "unavailable",
            "blocker": self._blocker(),
        }

    async def set_enabled(
        self, connection: dict[str, Any], enabled: bool
    ) -> dict[str, Any]:
        del connection, enabled
        self._unavailable()

    async def get_tool_contract(
        self, native_server: str, native_tool: str
    ) -> dict[str, Any]:
        del native_server, native_tool
        self._unavailable()

    async def invoke_exact(
        self,
        connection: dict[str, Any],
        binding: dict[str, Any],
        arguments: dict[str, Any],
        approval_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        del connection, binding, arguments, approval_context
        self._unavailable()

    async def close(self) -> None:
        return None

    def _blocker(self) -> str:
        blockers = self.capabilities.get("blockers") or []
        return str(blockers[0] if blockers else "runtime_integration_invoke_unavailable")

    def _unavailable(self) -> None:
        raise IntegrationError(
            "This Runtime Integration adapter cannot call tools.",
            code="runtime_integration_invoke_unavailable",
            detail={"provider_id": self.provider_id, "blockers": self.capabilities.get("blockers") or []},
        )


class WorkBuddyIntegrationAdapter(UnavailableIntegrationAdapter):
    pass


class IntegrationStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def list_connections(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM integration_connections ORDER BY backend_id, native_id"
            ).fetchall()
        return [self._connection(row) for row in rows]

    def get_connection(self, connection_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM integration_connections WHERE connection_id = ?",
                (connection_id,),
            ).fetchone()
        if row is None:
            raise IntegrationError("Integration connection not found.", code="integration_connection_not_found")
        return self._connection(row)

    def upsert_connection(self, value: dict[str, Any]) -> dict[str, Any]:
        now = _timestamp()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO integration_connections (
                    connection_id, catalog_id, backend_id, provider_id, source_kind,
                    native_id, native_connection_id, display_name, status,
                    auth_status, health_status, enabled, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(connection_id) DO UPDATE SET
                    native_connection_id=excluded.native_connection_id,
                    display_name=excluded.display_name,
                    status=excluded.status,
                    auth_status=excluded.auth_status,
                    health_status=excluded.health_status,
                    enabled=excluded.enabled,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    value["connection_id"],
                    value["catalog_id"],
                    value["backend_id"],
                    value["provider_id"],
                    value["source_kind"],
                    value["native_id"],
                    value.get("native_connection_id") or value["native_id"],
                    value.get("display_name") or value["native_id"],
                    value.get("status") or "unknown",
                    value.get("auth_status") or "unknown",
                    value.get("health_status") or "unknown",
                    1 if value.get("enabled", True) else 0,
                    _json(value.get("metadata") or {}),
                    value.get("created_at") or now,
                    now,
                ),
            )
            conn.commit()
        return self.get_connection(str(value["connection_id"]))

    def save_binding(self, value: dict[str, Any]) -> dict[str, Any]:
        now = _timestamp()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO integration_bindings (
                    binding_id, connection_id, capability, operation, native_server,
                    native_tool, input_schema_json, output_schema_json, schema_hash,
                    read_only, side_effect, approval_policy, enabled, snapshot_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(connection_id, capability, operation) DO UPDATE SET
                    binding_id=excluded.binding_id,
                    native_server=excluded.native_server,
                    native_tool=excluded.native_tool,
                    input_schema_json=excluded.input_schema_json,
                    output_schema_json=excluded.output_schema_json,
                    schema_hash=excluded.schema_hash,
                    read_only=excluded.read_only,
                    side_effect=excluded.side_effect,
                    approval_policy=excluded.approval_policy,
                    enabled=excluded.enabled,
                    snapshot_json=excluded.snapshot_json,
                    updated_at=excluded.updated_at
                """,
                (
                    value["binding_id"], value["connection_id"], value["capability"],
                    value["operation"], value["native_server"], value["native_tool"],
                    _json(value["input_schema"]), _json(value.get("output_schema") or {}),
                    value["schema_hash"], 1 if value.get("read_only") else 0,
                    1 if value.get("side_effect") else 0,
                    value.get("approval_policy") or "none",
                    1 if value.get("enabled") else 0,
                    _json(value.get("snapshot") or {}), now, now,
                ),
            )
            conn.commit()
        return self.get_binding(str(value["binding_id"]))

    def get_binding(self, binding_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM integration_bindings WHERE binding_id = ?", (binding_id,)
            ).fetchone()
        if row is None:
            raise IntegrationError("Integration binding not found.", code="integration_binding_not_found")
        return self._binding(row)

    def list_bindings(self, connection_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if connection_id:
                rows = conn.execute(
                    "SELECT * FROM integration_bindings WHERE connection_id = ? ORDER BY capability, operation",
                    (connection_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM integration_bindings ORDER BY connection_id, capability, operation"
                ).fetchall()
        return [self._binding(row) for row in rows]

    def save_action(self, value: dict[str, Any]) -> dict[str, Any]:
        now = _timestamp()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO integration_actions (
                    action_id, run_id, action_type, status, connection_id, binding_id,
                    draft_json, draft_digest, idempotency_key, result_json,
                    approved_by, approved_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(action_id) DO UPDATE SET
                    status=excluded.status, result_json=excluded.result_json,
                    approved_by=excluded.approved_by, approved_at=excluded.approved_at,
                    updated_at=excluded.updated_at
                """,
                (
                    value["action_id"], value["run_id"], value["action_type"], value["status"],
                    value.get("connection_id"), value.get("binding_id"), _json(value["draft"]),
                    value["draft_digest"], value["idempotency_key"],
                    _json(value.get("result")) if value.get("result") is not None else None,
                    value.get("approved_by"), value.get("approved_at"),
                    value.get("created_at") or now, now,
                ),
            )
            conn.commit()
        return self.get_action(str(value["action_id"]))

    def get_action(self, action_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM integration_actions WHERE action_id = ?", (action_id,)
            ).fetchone()
        if row is None:
            raise IntegrationError("Integration action not found.", code="integration_action_not_found")
        return self._action(row)

    def list_actions(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM integration_actions WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [self._action(row) for row in rows]

    def find_action_by_idempotency(self, key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM integration_actions WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return self._action(row) if row is not None else None

    def claim_mail_send(
        self, action_id: str, expected_draft_digest: str
    ) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM integration_actions WHERE action_id = ?", (action_id,)
            ).fetchone()
            if row is None:
                raise IntegrationError(
                    "Integration action not found.",
                    code="integration_action_not_found",
                )
            action = self._action(row)
            if action["draft_digest"] != expected_draft_digest:
                raise IntegrationError(
                    "The mail draft changed after review.",
                    code="integration_draft_tampered",
                )
            if action["status"] in {"sent", "rejected"}:
                return action
            if action["status"] == "sending":
                raise IntegrationError(
                    "This mail send is already in progress.",
                    code="integration_action_in_progress",
                )
            if action["status"] == "send_outcome_unknown":
                raise IntegrationError(
                    "The previous send outcome is unknown; automatic retry is blocked.",
                    code="integration_send_outcome_unknown",
                )
            if action["status"] != "pending_approval":
                raise IntegrationError(
                    "This action is not awaiting send approval.",
                    code="integration_action_not_approvable",
                )
            conn.execute(
                "UPDATE integration_actions SET status = ?, updated_at = ? WHERE action_id = ?",
                ("sending", _timestamp(), action_id),
            )
            conn.commit()
        return self.get_action(action_id)

    def reject_mail_send(
        self, action_id: str, expected_draft_digest: str, actor: str
    ) -> dict[str, Any]:
        now = _timestamp()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM integration_actions WHERE action_id = ?", (action_id,)
            ).fetchone()
            if row is None:
                raise IntegrationError(
                    "Integration action not found.",
                    code="integration_action_not_found",
                )
            action = self._action(row)
            if action["draft_digest"] != expected_draft_digest:
                raise IntegrationError(
                    "The mail draft changed after review.",
                    code="integration_draft_tampered",
                )
            if action["status"] in {"sent", "rejected"}:
                return action
            if action["status"] != "pending_approval":
                raise IntegrationError(
                    "This action can no longer be rejected safely.",
                    code="integration_action_in_progress",
                )
            conn.execute(
                "UPDATE integration_actions SET status = ?, approved_by = ?, approved_at = ?, updated_at = ? WHERE action_id = ?",
                ("rejected", actor, now, now, action_id),
            )
            conn.commit()
        return self.get_action(action_id)

    def audit(self, event_type: str, subject_id: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO integration_audit_events (event_id, event_type, subject_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (f"integration_event_{uuid.uuid4().hex}", event_type, subject_id, _json(payload), _timestamp()),
            )
            conn.commit()

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS integration_connections (
                    connection_id TEXT PRIMARY KEY,
                    catalog_id TEXT NOT NULL,
                    backend_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    native_id TEXT NOT NULL,
                    native_connection_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    auth_status TEXT NOT NULL,
                    health_status TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(backend_id, source_kind, native_id)
                );
                CREATE TABLE IF NOT EXISTS integration_bindings (
                    binding_id TEXT PRIMARY KEY,
                    connection_id TEXT NOT NULL REFERENCES integration_connections(connection_id),
                    capability TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    native_server TEXT NOT NULL,
                    native_tool TEXT NOT NULL,
                    input_schema_json TEXT NOT NULL,
                    output_schema_json TEXT NOT NULL,
                    schema_hash TEXT NOT NULL,
                    read_only INTEGER NOT NULL,
                    side_effect INTEGER NOT NULL,
                    approval_policy TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(connection_id, capability, operation)
                );
                CREATE TABLE IF NOT EXISTS integration_actions (
                    action_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    connection_id TEXT,
                    binding_id TEXT,
                    draft_json TEXT NOT NULL,
                    draft_digest TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    result_json TEXT,
                    approved_by TEXT,
                    approved_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS integration_audit_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _connection(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["enabled"] = bool(value["enabled"])
        value["metadata"] = _loads(value.pop("metadata_json"), {})
        return value

    @staticmethod
    def _binding(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["read_only"] = bool(value["read_only"])
        value["side_effect"] = bool(value["side_effect"])
        value["enabled"] = bool(value["enabled"])
        value["input_schema"] = _loads(value.pop("input_schema_json"), {})
        value["output_schema"] = _loads(value.pop("output_schema_json"), {})
        value["snapshot"] = _loads(value.pop("snapshot_json"), {})
        return value

    @staticmethod
    def _action(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["draft"] = _loads(value.pop("draft_json"), {})
        raw_result = value.pop("result_json")
        value["result"] = _loads(raw_result, None) if raw_result else None
        return value


class IntegrationGateway:
    def __init__(
        self,
        local_plugins: Any,
        sdk_manager: Any,
        state: IntegrationStateStore,
        adapters: list[RuntimeIntegrationAdapter],
    ) -> None:
        self.local_plugins = local_plugins
        self.sdk_manager = sdk_manager
        self.state = state
        self.adapters = {adapter.backend_id: adapter for adapter in adapters}
        self._catalog: dict[str, dict[str, Any]] = {}
        self._adapter_errors: dict[str, dict[str, str]] = {}

    def runtime_adapters(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for adapter in sorted(self.adapters.values(), key=lambda item: item.provider_id):
            value = adapter.describe_capabilities()
            if adapter.backend_id in self._adapter_errors:
                value["last_error"] = deepcopy(self._adapter_errors[adapter.backend_id])
            result.append(value)
        return result

    async def workflow_catalog(self, *, force_refresh: bool = False) -> dict[str, Any]:
        connections = {
            item["connection_id"]: item for item in self.state.list_connections()
        }
        if force_refresh:
            catalog = await self.list_catalog(
                force_refresh=True, capability=MAIL_CAPABILITY
            )
        else:
            catalog = [
                deepcopy(item)
                for item in self._catalog.values()
                if item.get("source_kind") != "local_plugin"
                and _has_capability(item, MAIL_CAPABILITY)
            ]
            if not catalog:
                for connection in connections.values():
                    snapshot = (connection.get("metadata") or {}).get(
                        "catalog_snapshot"
                    )
                    if isinstance(snapshot, dict) and _has_capability(
                        snapshot, MAIL_CAPABILITY
                    ):
                        catalog.append(
                            {
                                **deepcopy(snapshot),
                                "catalog_id": connection["catalog_id"],
                                "source_kind": connection["source_kind"],
                                "integration_backend_id": connection["backend_id"],
                                "runtime_provider_id": connection["provider_id"],
                                "native_id": connection["native_id"],
                                "name": connection["display_name"],
                                "connection": deepcopy(connection),
                                "connection_status": connection["status"],
                                "auth_status": connection["auth_status"],
                                "compatibility": {"supported": True, "blockers": []},
                            }
                        )
        bindings: list[dict[str, Any]] = []
        for binding in self.state.list_bindings():
            connection = connections.get(binding["connection_id"])
            bindings.append(
                {
                    "binding_id": binding["binding_id"],
                    "capability": binding["capability"],
                    "operation": binding["operation"],
                    "integration_backend_id": (
                        connection.get("backend_id") if connection else None
                    ),
                    "runtime_provider_id": (
                        connection.get("provider_id") if connection else None
                    ),
                    "connection_id": binding["connection_id"],
                    "connection_status": (
                        connection.get("status") if connection else "unavailable"
                    ),
                    "connection_enabled": bool(
                        connection and connection.get("enabled")
                    ),
                    "native_server": binding["native_server"],
                    "native_tool": binding["native_tool"],
                    "schema_hash": binding["schema_hash"],
                    "input_schema": deepcopy(binding["input_schema"]),
                    "output_schema": deepcopy(binding["output_schema"]),
                    "read_only": binding["read_only"],
                    "side_effect": binding["side_effect"],
                    "approval_policy": binding["approval_policy"],
                    "enabled": binding["enabled"],
                }
            )
        safe_items = [
            {
                "catalog_id": item["catalog_id"],
                "source_kind": item["source_kind"],
                "integration_backend_id": item["integration_backend_id"],
                "runtime_provider_id": item["runtime_provider_id"],
                "native_id": item["native_id"],
                "name": item["name"],
                "capabilities": deepcopy(item.get("capabilities") or []),
                "connection_id": (item.get("connection") or {}).get("connection_id"),
                "connection_status": item.get("connection_status"),
                "auth_status": item.get("auth_status"),
                "compatibility": deepcopy(item.get("compatibility") or {}),
            }
            for item in catalog
            if item.get("source_kind") != "local_plugin"
        ]
        value = {"items": safe_items, "bindings": bindings}
        value["digest"] = _digest(value)
        return value

    async def list_catalog(
        self,
        *,
        force_refresh: bool = False,
        provider_id: str | None = None,
        source_kind: str | None = None,
        status: str | None = None,
        capability: str | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for plugin in self.local_plugins.list():
            catalog_id = f"local:{plugin['plugin_id']}"
            item = {
                "catalog_id": catalog_id,
                "source_kind": "local_plugin",
                "integration_backend_id": "platform-local",
                "runtime_provider_id": None,
                "native_id": plugin["plugin_id"],
                "name": (plugin.get("name") or {}).get("zh")
                or (plugin.get("name") or {}).get("en")
                or plugin["plugin_id"],
                "display": deepcopy(plugin.get("name") or {}),
                "description": deepcopy(plugin.get("description") or {}),
                "capabilities": deepcopy(plugin.get("capabilities") or []),
                "tools": [],
                "installed": True,
                "enabled": bool(plugin.get("enabled")),
                "connection_status": plugin.get("status") or "unknown",
                "auth_status": "runtime_managed",
                "health_status": "healthy" if (plugin.get("health") or {}).get("ok") else "unknown",
                "connection": None,
                "bindings": [],
                "compatibility": {"supported": True, "blockers": []},
                "metadata": {"local_plugin": plugin},
            }
            self._catalog[catalog_id] = item
            items.append(item)
        connections = {
            (item["backend_id"], item["source_kind"], item["native_id"]): item
            for item in self.state.list_connections()
        }
        bindings_by_connection: dict[str, list[dict[str, Any]]] = {}
        for binding in self.state.list_bindings():
            bindings_by_connection.setdefault(binding["connection_id"], []).append(
                binding
            )
        for adapter in self.adapters.values():
            try:
                native_items = await adapter.list_catalog(force_refresh=force_refresh)
                self._adapter_errors.pop(adapter.backend_id, None)
            except Exception as exc:
                native_items = []
                # Adapter discovery failures are returned through capability status;
                # they never make local plugins disappear.
                self._adapter_errors[adapter.backend_id] = {
                    "code": str(getattr(exc, "code", "runtime_catalog_unavailable")),
                    "message": str(exc),
                }
            for raw in native_items:
                catalog_id = _catalog_id(adapter.backend_id, str(raw["source_kind"]), str(raw["native_id"]))
                connection = connections.get(
                    (adapter.backend_id, str(raw["source_kind"]), str(raw["native_id"]))
                )
                normalized = {
                    "catalog_id": catalog_id,
                    "source_kind": raw["source_kind"],
                    "integration_backend_id": adapter.backend_id,
                    "runtime_provider_id": adapter.provider_id,
                    "native_id": raw["native_id"],
                    "name": raw.get("name") or raw["native_id"],
                    "display": {"zh": raw.get("name") or raw["native_id"], "en": raw.get("name") or raw["native_id"]},
                    "description": {"zh": raw.get("description") or "", "en": raw.get("description") or ""},
                    "icon_url": raw.get("icon_url"),
                    "install_url": raw.get("install_url"),
                    "capabilities": deepcopy(raw.get("capabilities") or []),
                    "tools": deepcopy(raw.get("tools") or []),
                    "installed": bool(raw.get("installed", True)),
                    "enabled": bool(connection["enabled"] if connection else raw.get("enabled", True)),
                    "connection_status": connection["status"] if connection else _catalog_connection_status(raw),
                    "auth_status": connection["auth_status"] if connection else raw.get("auth_status", "unknown"),
                    "health_status": connection["health_status"] if connection else raw.get("health_status", "unknown"),
                    "connection": deepcopy(connection),
                    "bindings": deepcopy(
                        bindings_by_connection.get(
                            str((connection or {}).get("connection_id") or ""), []
                        )
                    ),
                    "compatibility": {
                        "supported": bool(adapter.describe_capabilities().get("features", {}).get("directToolCall")),
                        "blockers": deepcopy(adapter.describe_capabilities().get("blockers") or []),
                    },
                    "metadata": deepcopy(raw.get("metadata") or {}),
                }
                self._catalog[catalog_id] = normalized
                items.append(normalized)
        return [
            deepcopy(item)
            for item in items
            if (not provider_id or item.get("runtime_provider_id") == provider_id)
            and (not source_kind or item.get("source_kind") == source_kind)
            and (not status or item.get("connection_status") == status)
            and (not capability or _has_capability(item, capability))
        ]

    async def connect(self, catalog_id: str) -> dict[str, Any]:
        if catalog_id not in self._catalog:
            await self.list_catalog()
        item = self._catalog.get(catalog_id)
        if item is None or item.get("source_kind") == "local_plugin":
            raise IntegrationError("Plugin catalog item not found.", code="plugin_catalog_item_not_found")
        adapter = self._adapter(str(item["integration_backend_id"]))
        result = await adapter.begin_connection(item)
        connection_id = _stable_id(
            "connection", adapter.backend_id, str(item["source_kind"]), str(item["native_id"])
        )
        connection = self.state.upsert_connection(
            {
                "connection_id": connection_id,
                "catalog_id": catalog_id,
                "backend_id": adapter.backend_id,
                "provider_id": adapter.provider_id,
                "source_kind": item["source_kind"],
                "native_id": item["native_id"],
                "native_connection_id": result.get("native_connection_id") or item["native_id"],
                "display_name": item["name"],
                "status": result.get("status") or "unknown",
                "auth_status": result.get("auth_status") or "unknown",
                "health_status": result.get("health_status") or "unknown",
                "enabled": item.get("enabled", True),
                "metadata": {"catalog_snapshot": item},
            }
        )
        self.state.audit("connection_started", connection_id, {"outcome": result.get("outcome"), "status": connection["status"]})
        return {**result, "connection": connection}

    async def refresh_connection(self, connection_id: str) -> dict[str, Any]:
        connection = self.state.get_connection(connection_id)
        result = await self._adapter(connection["backend_id"]).refresh_status(connection)
        connection.update(
            status=result.get("status") or connection["status"],
            auth_status=result.get("auth_status") or connection["auth_status"],
            health_status=result.get("health_status") or connection["health_status"],
            metadata={**connection.get("metadata", {}), **(result.get("metadata") or {})},
        )
        saved = self.state.upsert_connection(connection)
        self.state.audit("connection_refreshed", connection_id, {"status": saved["status"], "auth_status": saved["auth_status"]})
        return saved

    async def set_connection_enabled(
        self, connection_id: str, enabled: bool
    ) -> dict[str, Any]:
        connection = self.state.get_connection(connection_id)
        result = await self._adapter(connection["backend_id"]).set_enabled(connection, enabled)
        connection.update(
            enabled=enabled,
            status=result.get("status") or ("ready" if enabled else "disabled"),
            auth_status=result.get("auth_status") or connection["auth_status"],
            health_status=result.get("health_status") or connection["health_status"],
        )
        saved = self.state.upsert_connection(connection)
        self.state.audit("connection_enabled_changed", connection_id, {"enabled": enabled})
        return saved

    async def bind_tool(
        self,
        connection_id: str,
        capability: str,
        operation: str,
        *,
        native_server: str,
        native_tool: str,
        enabled: bool = False,
        expected_schema_hash: str | None = None,
        argument_mapping: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _validate_capability_operation(capability, operation)
        connection = self.state.get_connection(connection_id)
        if native_server != connection["native_id"]:
            raise IntegrationError(
                "The native server must match the fixed connection.",
                code="integration_binding_connection_mismatch",
            )
        adapter = self._adapter(connection["backend_id"])
        contract = await adapter.get_tool_contract(native_server, native_tool)
        schema_hash = _tool_schema_hash(contract)
        if expected_schema_hash and expected_schema_hash != schema_hash:
            raise IntegrationError(
                "The Runtime tool contract changed.",
                code="tool_contract_changed",
                detail={"expected": expected_schema_hash, "actual": schema_hash},
            )
        is_send = capability == MAIL_CAPABILITY and operation == "send"
        binding = self.state.save_binding(
            {
                "binding_id": _stable_id("binding", connection_id, capability, operation),
                "connection_id": connection_id,
                "capability": capability,
                "operation": operation,
                "native_server": native_server,
                "native_tool": native_tool,
                "input_schema": contract.get("input_schema") or {"type": "object"},
                "output_schema": contract.get("output_schema") or {},
                "schema_hash": schema_hash,
                "read_only": operation in READ_ONLY_MAIL_OPERATIONS,
                "side_effect": is_send,
                "approval_policy": "always" if is_send else "none",
                # Sending remains off until the user explicitly enables this exact binding.
                "enabled": bool(enabled) if not is_send else bool(enabled),
                "snapshot": {
                    "contract": contract,
                    "backend_id": connection["backend_id"],
                    "provider_id": connection["provider_id"],
                    "argument_mapping": deepcopy(argument_mapping or {}),
                },
            }
        )
        self.state.audit("tool_binding_saved", binding["binding_id"], {"capability": capability, "operation": operation, "schema_hash": schema_hash, "enabled": binding["enabled"]})
        return binding

    async def invoke_binding(
        self,
        binding_id: str,
        arguments: dict[str, Any],
        *,
        approval_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        binding, connection, adapter = await self._prepare_invocation(
            binding_id, arguments, approval_context=approval_context
        )
        result = await adapter.invoke_exact(
            connection, binding, arguments, approval_context
        )
        self.state.audit("integration_tool_invoked", binding_id, {"operation": binding["operation"], "argument_digest": _digest(arguments), "result_digest": _digest(result)})
        return result

    async def _prepare_invocation(
        self,
        binding_id: str,
        arguments: dict[str, Any],
        *,
        approval_context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], RuntimeIntegrationAdapter]:
        binding = self.state.get_binding(binding_id)
        connection = self.state.get_connection(binding["connection_id"])
        if not connection["enabled"] or connection["status"] != "ready":
            raise IntegrationError("The integration connection is not ready.", code="connection_required")
        if not binding["enabled"]:
            raise IntegrationError("The integration tool binding is disabled.", code="permission_required")
        if binding["side_effect"] and not (approval_context or {}).get("verified"):
            raise IntegrationError("This integration action requires approval.", code="integration_approval_required")
        adapter = self._adapter(connection["backend_id"])
        current = await adapter.get_tool_contract(binding["native_server"], binding["native_tool"])
        current_hash = _tool_schema_hash(current)
        if current_hash != binding["schema_hash"]:
            raise IntegrationError(
                "The Runtime tool contract changed after the workflow was published.",
                code="tool_contract_changed",
                detail={"expected": binding["schema_hash"], "actual": current_hash},
            )
        errors = sorted(Draft202012Validator(binding["input_schema"]).iter_errors(arguments), key=lambda item: list(item.path))
        if errors:
            raise IntegrationError(
                "Integration arguments do not match the bound tool contract.",
                code="integration_arguments_invalid",
                detail={"paths": ["/" + "/".join(str(part) for part in error.path) for error in errors[:10]]},
            )
        return binding, connection, adapter

    def create_mail_draft(
        self,
        run_id: str,
        draft: dict[str, Any],
        *,
        connection_id: str | None = None,
        binding_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        normalized = _validate_mail_draft(draft)
        digest = _digest(normalized)
        key = idempotency_key or _stable_id("mail", run_id, digest, binding_id or "draft")
        existing = self.state.find_action_by_idempotency(key)
        if existing is not None:
            if existing["draft_digest"] != digest:
                raise IntegrationError("The idempotency key was reused with another draft.", code="integration_idempotency_conflict")
            return existing
        action = self.state.save_action(
            {
                "action_id": f"integration_action_{uuid.uuid4().hex[:16]}",
                "run_id": run_id,
                "action_type": "mail.send" if binding_id else "mail.draft",
                "status": "pending_approval" if binding_id else "draft",
                "connection_id": connection_id,
                "binding_id": binding_id,
                "draft": normalized,
                "draft_digest": digest,
                "idempotency_key": key,
            }
        )
        self.state.audit("mail_draft_created", action["action_id"], {"run_id": run_id, "draft_digest": digest, "recipient_count": len(normalized["to"]) + len(normalized["cc"]) + len(normalized["bcc"])})
        return action

    async def decide_mail_action(
        self,
        action_id: str,
        *,
        decision: str,
        expected_draft_digest: str,
        actor: str,
    ) -> dict[str, Any]:
        action = self.state.get_action(action_id)
        if action["draft_digest"] != expected_draft_digest:
            raise IntegrationError("The mail draft changed after review.", code="integration_draft_tampered")
        if action["status"] in {"sent", "rejected"}:
            return action
        if decision == "reject":
            saved = self.state.reject_mail_send(
                action_id, expected_draft_digest, actor
            )
            self.state.audit("mail_send_rejected", action_id, {"actor": actor})
            return saved
        if decision != "approve":
            raise IntegrationError("Mail decision must be approve or reject.", code="integration_decision_invalid")
        if action["action_type"] != "mail.send" or not action.get("binding_id"):
            raise IntegrationError("This draft has no send action.", code="integration_send_binding_required")
        binding = self.state.get_binding(str(action["binding_id"]))
        canonical_arguments = _mail_send_arguments(action["draft"])
        mapping = (binding.get("snapshot") or {}).get("argument_mapping") or {}
        arguments = (
            _render_binding_mapping(mapping, {"draft": canonical_arguments})
            if mapping
            else canonical_arguments
        )
        approval_context = {
            "verified": True,
            "draft_digest": action["draft_digest"],
            "idempotency_key": action["idempotency_key"],
        }
        prepared_binding, connection, adapter = await self._prepare_invocation(
            action["binding_id"],
            arguments,
            approval_context=approval_context,
        )
        action = self.state.claim_mail_send(action_id, expected_draft_digest)
        if action["status"] in {"sent", "rejected"}:
            return action
        try:
            result = await adapter.invoke_exact(
                connection,
                prepared_binding,
                arguments,
                approval_context,
            )
            self.state.audit(
                "integration_tool_invoked",
                action["binding_id"],
                {
                    "operation": prepared_binding["operation"],
                    "argument_digest": _digest(arguments),
                    "result_digest": _digest(result),
                },
            )
        except Exception as exc:
            action.update(
                status="send_outcome_unknown",
                result={
                    "error_code": str(getattr(exc, "code", "integration_send_failed")),
                    "error_type": type(exc).__name__,
                },
            )
            self.state.save_action(action)
            self.state.audit(
                "mail_send_outcome_unknown",
                action_id,
                {
                    "actor": actor,
                    "draft_digest": action["draft_digest"],
                    "error_code": str(
                        getattr(exc, "code", "integration_send_failed")
                    ),
                },
            )
            raise
        action.update(status="sent", result=result, approved_by=actor, approved_at=_timestamp())
        saved = self.state.save_action(action)
        self.state.audit("mail_send_completed", action_id, {"actor": actor, "draft_digest": action["draft_digest"], "native_message_id": _native_message_id(result)})
        return saved

    async def close(self) -> None:
        for adapter in self.adapters.values():
            await adapter.close()

    def _adapter(self, backend_id: str) -> RuntimeIntegrationAdapter:
        adapter = self.adapters.get(backend_id)
        if adapter is None:
            raise IntegrationError("Integration backend is unavailable.", code="runtime_adapter_unavailable")
        return adapter


def build_integration_adapters(
    repository_root: Path, sdk_manager: Any
) -> list[RuntimeIntegrationAdapter]:
    adapters: list[RuntimeIntegrationAdapter] = []
    for runtime in sdk_manager.list():
        provider_id = str(runtime.get("provider_id") or "")
        integration = deepcopy(runtime.get("integration_runtime") or {})
        backend_id = integration.get("adapter_id")
        if provider_id == "codex" and backend_id == "codex-app-server":
            adapters.append(CodexAppServerIntegrationAdapter(repository_root, integration))
        elif provider_id == "workbuddy":
            adapters.append(WorkBuddyIntegrationAdapter(provider_id, backend_id, integration))
        else:
            adapters.append(UnavailableIntegrationAdapter(provider_id, backend_id, integration))
    return adapters


def _adapter_description(
    provider_id: str, backend_id: str, capabilities: dict[str, Any]
) -> dict[str, Any]:
    features = capabilities.get("features") or {}
    aliases = {
        "catalog": "catalog",
        "configuredDiscovery": "configured_discovery",
        "status": "status",
        "authentication": "authentication",
        "configuration": "configuration",
        "directToolCall": "direct_tool_call",
    }
    return {
        "runtime_provider_id": provider_id,
        "integration_backend_id": backend_id,
        "resource_kinds": deepcopy(capabilities.get("resource_kinds") or []),
        "features": {
            name: bool(features.get(name, features.get(aliases[name])))
            for name in INTEGRATION_FEATURES
        },
        "credential_owner": capabilities.get("credential_owner") or "runtime",
        "readiness": capabilities.get("readiness") or "unavailable",
        "blockers": deepcopy(capabilities.get("blockers") or []),
    }


def _normalize_native_tools(raw_tools: Any) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if isinstance(raw_tools, dict):
        iterable = [
            {"name": name, **value}
            if isinstance(value, dict)
            else {"name": name}
            for name, value in raw_tools.items()
        ]
    elif isinstance(raw_tools, list):
        iterable = raw_tools
    else:
        iterable = []
    for raw in iterable:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("id") or "")
        if not name:
            continue
        contract = {
            "native_tool": name,
            "title": str(raw.get("title") or raw.get("displayName") or name),
            "description": str(raw.get("description") or ""),
            "input_schema": deepcopy(raw.get("inputSchema") or raw.get("input_schema") or {"type": "object"}),
            "output_schema": deepcopy(raw.get("outputSchema") or raw.get("output_schema") or {}),
        }
        contract["schema_hash"] = _tool_schema_hash(contract)
        tools.append(contract)
    return tools


def _infer_capabilities(native_id: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    haystack = " ".join(
        [native_id, *[str(tool.get("native_tool") or "") + " " + str(tool.get("description") or "") for tool in tools]]
    ).lower()
    if not any(word in haystack for word in ("mail", "email", "outlook", "gmail", "message")):
        return []
    operations: list[str] = []
    for operation, words in {
        "search": ("search", "list", "query", "find"),
        "read": ("read", "get", "fetch"),
        "send": ("send", "reply", "forward"),
    }.items():
        if any(any(word in str(tool.get("native_tool") or "").lower() for word in words) for tool in tools):
            operations.append(operation)
    operations.append("draft")
    return [{"capability": MAIL_CAPABILITY, "operations": sorted(set(operations))}]


def _normalize_auth_status(raw: dict[str, Any]) -> str:
    value = raw.get("authStatus") or raw.get("auth_status") or raw.get("auth")
    if isinstance(value, dict):
        value = value.get("status") or value.get("state")
    normalized = str(value or "unknown").strip().lower().replace("-", "_")
    aliases = {
        "loggedin": "logged_in",
        "notloggedin": "not_logged_in",
        "authenticated": "logged_in",
        "unauthenticated": "not_logged_in",
        "oauth": "logged_in",
        "bearertoken": "logged_in",
    }
    return aliases.get(normalized.replace("_", ""), normalized)


def _normalize_health_status(raw: dict[str, Any]) -> str:
    value = raw.get("healthStatus") or raw.get("health_status") or raw.get("status")
    if isinstance(value, dict):
        value = value.get("status") or value.get("state")
    normalized = str(value or "unknown").strip().lower().replace("-", "_")
    if normalized in {"ok", "ready", "connected", "healthy"}:
        return "healthy"
    if normalized in {"error", "failed", "unhealthy"}:
        return "failed"
    return normalized


def _catalog_connection_status(raw: dict[str, Any]) -> str:
    if not raw.get("installed", True):
        return "installation_required"
    if not raw.get("enabled", True):
        return "disabled"
    if raw.get("auth_status") in {"not_logged_in", "expired", "reauthentication_required"}:
        return "authentication_required"
    if raw.get("health_status") in {"failed", "unavailable"}:
        return "unhealthy"
    return "available"


def _validate_capability_operation(capability: str, operation: str) -> None:
    if capability != MAIL_CAPABILITY or operation not in MAIL_OPERATIONS - {"draft"}:
        raise IntegrationError("Unsupported normalized integration operation.", code="integration_operation_unsupported")


def _tool_schema_hash(contract: dict[str, Any]) -> str:
    return _digest(
        {
            "input_schema": contract.get("input_schema") or {"type": "object"},
            "output_schema": contract.get("output_schema") or {},
        }
    )


def _validate_mail_draft(draft: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(draft, dict):
        raise IntegrationError("Mail draft must be an object.", code="mail_draft_invalid")
    result = {
        "to": _email_list(draft.get("to"), required=True),
        "cc": _email_list(draft.get("cc") or []),
        "bcc": _email_list(draft.get("bcc") or []),
        "subject": str(draft.get("subject") or "").strip(),
        "body_text": str(draft.get("body_text") or draft.get("bodyText") or ""),
        "reply_to_message_id": str(draft.get("reply_to_message_id") or draft.get("replyToMessageId") or "") or None,
    }
    if not result["subject"] or len(result["subject"]) > 998:
        raise IntegrationError("Mail subject is required and must be at most 998 characters.", code="mail_draft_invalid")
    if not result["body_text"] or len(result["body_text"]) > 1_000_000:
        raise IntegrationError("Mail body is required and is too large.", code="mail_draft_invalid")
    return result


def _email_list(value: Any, *, required: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise IntegrationError("Mail recipients must be an array.", code="mail_draft_invalid")
    emails: list[str] = []
    for item in value:
        email = str(item or "").strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise IntegrationError("Mail draft contains an invalid recipient.", code="mail_draft_invalid")
        if email not in emails:
            emails.append(email)
    if required and not emails:
        raise IntegrationError("Mail draft requires at least one recipient.", code="mail_draft_invalid")
    return emails


def _mail_send_arguments(draft: dict[str, Any]) -> dict[str, Any]:
    # This canonical shape must be bound to a native tool with a compatible schema.
    # Runtime-specific reshaping is intentionally not guessed at send time.
    return {
        "to": deepcopy(draft["to"]),
        "cc": deepcopy(draft["cc"]),
        "bcc": deepcopy(draft["bcc"]),
        "subject": draft["subject"],
        "body": draft["body_text"],
        **(
            {"reply_to_message_id": draft["reply_to_message_id"]}
            if draft.get("reply_to_message_id")
            else {}
        ),
    }


_BINDING_TEMPLATE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _render_binding_mapping(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _render_binding_mapping(item, context)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_render_binding_mapping(item, context) for item in value]
    if not isinstance(value, str):
        return value
    exact = _BINDING_TEMPLATE.fullmatch(value)
    if exact:
        return _binding_lookup(context, exact.group(1))
    return _BINDING_TEMPLATE.sub(
        lambda match: str(_binding_lookup(context, match.group(1))), value
    )


def _binding_lookup(context: dict[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise IntegrationError(
                "Integration argument mapping references an unavailable value.",
                code="integration_argument_mapping_invalid",
                detail={"path": path},
            )
        current = current[part]
    return current


def _native_message_id(result: dict[str, Any]) -> str | None:
    for key in ("messageId", "message_id", "id"):
        if result.get(key):
            return str(result[key])
    structured = result.get("structuredContent") or result.get("structured_content")
    if isinstance(structured, dict):
        for key in ("messageId", "message_id", "id"):
            if structured.get(key):
                return str(structured[key])
    return None


def _has_capability(item: dict[str, Any], capability: str) -> bool:
    return any(
        str(value.get("capability") or "") == capability
        for value in item.get("capabilities") or []
        if isinstance(value, dict)
    )


def _safe_config_segment(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise IntegrationError("Runtime configuration id is invalid.", code="runtime_configuration_id_invalid")
    return value


def _safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    # Native catalog metadata is not an authentication store. Drop common token
    # fields defensively before persisting a catalog snapshot.
    return {
        str(key): deepcopy(item)
        for key, item in value.items()
        if str(key).lower() not in {"token", "access_token", "refreshtoken", "refresh_token", "password", "secret"}
    }


def _catalog_id(backend_id: str, source_kind: str, native_id: str) -> str:
    return _stable_id("catalog", backend_id, source_kind, native_id)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
