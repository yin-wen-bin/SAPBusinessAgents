from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.integrations import (
    CodexAppServerIntegrationAdapter,
    IntegrationError,
    IntegrationGateway,
    IntegrationStateStore,
    UnavailableIntegrationAdapter,
    WorkBuddyIntegrationAdapter,
)


class _LocalPlugins:
    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "plugin_id": "business-agent-catalog",
                "version": "1.0.0",
                "name": {"zh": "Agent 目录", "en": "Agent catalog"},
                "description": {"zh": "本地", "en": "Local"},
                "capabilities": [
                    {"capability": "business_agents.v1", "operations": ["list"]}
                ],
                "enabled": True,
                "status": "ready",
                "health": {"ok": True},
            }
        ]


class _SDKManager:
    def list(self) -> list[dict[str, Any]]:
        return []


class _FakeAdapter:
    backend_id = "codex-app-server"
    provider_id = "codex"

    def __init__(self) -> None:
        self.invocations: list[dict[str, Any]] = []
        self.contract = {
            "native_tool": "send_mail",
            "title": "Send mail",
            "description": "Send one message",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}},
                    "cc": {"type": "array", "items": {"type": "string"}},
                    "bcc": {"type": "array", "items": {"type": "string"}},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "cc", "bcc", "subject", "body"],
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {"messageId": {"type": "string"}},
            },
        }

    def describe_capabilities(self) -> dict[str, Any]:
        return {
            "runtime_provider_id": "codex",
            "integration_backend_id": self.backend_id,
            "resource_kinds": ["mcp_server"],
            "features": {
                "catalog": True,
                "configuredDiscovery": True,
                "status": True,
                "authentication": True,
                "configuration": True,
                "directToolCall": True,
            },
            "credential_owner": "runtime",
            "readiness": "production",
            "blockers": [],
        }

    async def list_catalog(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        del force_refresh
        tool = deepcopy(self.contract)
        from sap_business_agents_platform.integrations import _tool_schema_hash

        tool["schema_hash"] = _tool_schema_hash(tool)
        return [
            {
                "source_kind": "mcp_server",
                "native_id": "outlook-email",
                "name": "Outlook Email",
                "description": "Mail connector",
                "installed": True,
                "enabled": True,
                "auth_status": "logged_in",
                "health_status": "healthy",
                "capabilities": [
                    {
                        "capability": "mail.v1",
                        "operations": ["search", "read", "draft", "send"],
                    }
                ],
                "tools": [tool],
            }
        ]

    async def begin_connection(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "outcome": "ready",
            "status": "ready",
            "auth_status": item["auth_status"],
            "health_status": item["health_status"],
            "native_connection_id": item["native_id"],
        }

    async def refresh_status(self, connection: dict[str, Any]) -> dict[str, Any]:
        del connection
        return {
            "status": "ready",
            "auth_status": "logged_in",
            "health_status": "healthy",
        }

    async def set_enabled(
        self, connection: dict[str, Any], enabled: bool
    ) -> dict[str, Any]:
        del connection
        return {
            "status": "ready" if enabled else "disabled",
            "auth_status": "logged_in",
            "health_status": "healthy",
        }

    async def get_tool_contract(
        self, native_server: str, native_tool: str
    ) -> dict[str, Any]:
        assert native_server == "outlook-email"
        assert native_tool == "send_mail"
        return deepcopy(self.contract)

    async def invoke_exact(
        self,
        connection: dict[str, Any],
        binding: dict[str, Any],
        arguments: dict[str, Any],
        approval_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        self.invocations.append(
            {
                "connection": connection,
                "binding": binding,
                "arguments": arguments,
                "approval": approval_context,
            }
        )
        return {"messageId": "native-message-1"}

    async def close(self) -> None:
        return None


class _FakeCodexTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        approval_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del approval_context
        self.calls.append((method, params or {}))
        if method == "app/list":
            return {
                "data": [
                    {
                        "id": "outlook-app",
                        "name": "Outlook",
                        "installUrl": "https://example.test/install",
                    }
                ],
                "nextCursor": None,
            }
        if method == "app/read":
            assert params == {"appIds": ["outlook-app"], "includeTools": True}
            return {
                "apps": [
                    {
                        "id": "outlook-app",
                        "name": "Outlook",
                        "description": "Outlook app",
                        "installUrl": "https://example.test/install",
                        "toolSummaries": [{"name": "search_mail"}],
                    }
                ],
                "missingAppIds": [],
            }
        if method == "app/installed":
            assert params in ({}, {"forceRefresh": True})
            return {
                "apps": [
                    {
                        "id": "outlook-app",
                        "enabled": True,
                        "callable": True,
                    }
                ]
            }
        if method == "mcpServerStatus/list":
            return {
                "data": [
                    {
                        "name": "outlook-email",
                        "authStatus": "oAuth",
                        "serverInfo": {
                            "title": "Outlook Email",
                            "description": "Mail MCP",
                        },
                        "tools": {
                            "send_mail": {
                                "name": "send_mail",
                                "description": "Send mail",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"subject": {"type": "string"}},
                                },
                            }
                        },
                    }
                ],
                "nextCursor": None,
            }
        if method == "config/value/write":
            return {}
        raise AssertionError(method)

    async def connector_thread_id(self) -> str:
        return "thread-1"

    async def close(self) -> None:
        return None


def _gateway(tmp_path: Path) -> tuple[IntegrationGateway, _FakeAdapter]:
    adapter = _FakeAdapter()
    return (
        IntegrationGateway(
            _LocalPlugins(),
            _SDKManager(),
            IntegrationStateStore(tmp_path / "platform.sqlite3"),
            [adapter],
        ),
        adapter,
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        repository_root=Path(__file__).resolve().parents[1],
        data_root=tmp_path / "data",
        draft_root=tmp_path / "drafts",
        skillhub_root=tmp_path / "skillhub",
        max_run_seconds=10,
        enforce_agent_acceptance=False,
    )


def test_codex_adapter_uses_public_app_server_catalog_contract(tmp_path: Path) -> None:
    transport = _FakeCodexTransport()
    adapter = CodexAppServerIntegrationAdapter(
        tmp_path,
        {
            "features": {"directToolCall": True},
            "readiness": "production",
        },
        transport=transport,
    )

    items = asyncio.run(adapter.list_catalog())

    assert [item["source_kind"] for item in items] == ["app", "mcp_server"]
    assert items[0]["installed"] is True
    assert items[0]["tools"][0]["display_only"] is True
    assert items[1]["auth_status"] == "logged_in"
    assert items[1]["health_status"] == "healthy"
    contract = asyncio.run(adapter.get_tool_contract("outlook-email", "send_mail"))
    assert contract["input_schema"]["properties"]["subject"]["type"] == "string"


def test_unified_catalog_preserves_local_and_runtime_identity(tmp_path: Path) -> None:
    gateway, _adapter = _gateway(tmp_path)
    items = asyncio.run(gateway.list_catalog())

    assert [item["source_kind"] for item in items] == [
        "local_plugin",
        "mcp_server",
    ]
    assert items[0]["catalog_id"] == "local:business-agent-catalog"
    assert items[1]["runtime_provider_id"] == "codex"
    assert items[1]["integration_backend_id"] == "codex-app-server"
    assert items[0]["catalog_id"] != items[1]["catalog_id"]


def test_unified_catalog_and_adapter_routes_are_not_shadowed(
    tmp_path: Path,
) -> None:
    gateway, _adapter = _gateway(tmp_path)
    app = create_app(_settings(tmp_path), integration_gateway=gateway)

    with TestClient(app) as client:
        catalog = client.get("/api/plugins/catalog?offset=1&limit=1")
        adapters = client.get("/api/plugins/runtime-adapters")

    assert catalog.status_code == 200
    assert catalog.json()["total"] == 2
    assert len(catalog.json()["items"]) == 1
    assert catalog.json()["offset"] == 1
    assert catalog.json()["next_offset"] is None
    assert adapters.status_code == 200
    assert adapters.json()["items"][0]["integration_backend_id"] == "codex-app-server"
    assert adapters.json()["items"][0]["features"]["directToolCall"] is True


def test_mail_send_is_disabled_then_requires_exact_draft_approval(
    tmp_path: Path,
) -> None:
    gateway, adapter = _gateway(tmp_path)
    items = asyncio.run(gateway.list_catalog())
    external = next(item for item in items if item["source_kind"] == "mcp_server")
    connected = asyncio.run(gateway.connect(external["catalog_id"]))["connection"]
    disabled = asyncio.run(
        gateway.bind_tool(
            connected["connection_id"],
            "mail.v1",
            "send",
            native_server="outlook-email",
            native_tool="send_mail",
        )
    )
    assert disabled["enabled"] is False
    assert disabled["approval_policy"] == "always"

    action = gateway.create_mail_draft(
        "run_1",
        {
            "to": ["user@example.com"],
            "subject": "Review",
            "body_text": "Please review the SAP evidence.",
        },
        connection_id=connected["connection_id"],
        binding_id=disabled["binding_id"],
    )
    assert action["status"] == "pending_approval"
    try:
        asyncio.run(
            gateway.decide_mail_action(
                action["action_id"],
                decision="approve",
                expected_draft_digest=action["draft_digest"],
                actor="tester",
            )
        )
    except IntegrationError as exc:
        assert exc.code == "permission_required"
    else:
        raise AssertionError("A disabled mail.send binding must fail closed")

    enabled = asyncio.run(
        gateway.bind_tool(
            connected["connection_id"],
            "mail.v1",
            "send",
            native_server="outlook-email",
            native_tool="send_mail",
            enabled=True,
            expected_schema_hash=disabled["schema_hash"],
        )
    )
    assert enabled["enabled"] is True
    try:
        asyncio.run(
            gateway.decide_mail_action(
                action["action_id"],
                decision="approve",
                expected_draft_digest="sha256:" + "0" * 64,
                actor="tester",
            )
        )
    except IntegrationError as exc:
        assert exc.code == "integration_draft_tampered"
    else:
        raise AssertionError("A changed draft digest must block sending")

    sent = asyncio.run(
        gateway.decide_mail_action(
            action["action_id"],
            decision="approve",
            expected_draft_digest=action["draft_digest"],
            actor="tester",
        )
    )
    assert sent["status"] == "sent"
    assert sent["result"]["messageId"] == "native-message-1"
    assert len(adapter.invocations) == 1
    repeated = asyncio.run(
        gateway.decide_mail_action(
            action["action_id"],
            decision="approve",
            expected_draft_digest=action["draft_digest"],
            actor="tester",
        )
    )
    assert repeated["status"] == "sent"
    assert len(adapter.invocations) == 1


def test_schema_drift_blocks_exact_tool_invocation(tmp_path: Path) -> None:
    gateway, adapter = _gateway(tmp_path)
    external = asyncio.run(gateway.list_catalog())[1]
    connection = asyncio.run(gateway.connect(external["catalog_id"]))["connection"]
    binding = asyncio.run(
        gateway.bind_tool(
            connection["connection_id"],
            "mail.v1",
            "send",
            native_server="outlook-email",
            native_tool="send_mail",
            enabled=True,
        )
    )
    adapter.contract["input_schema"]["properties"]["importance"] = {
        "type": "string"
    }
    try:
        asyncio.run(
            gateway.invoke_binding(
                binding["binding_id"],
                {
                    "to": ["user@example.com"],
                    "cc": [],
                    "bcc": [],
                    "subject": "Subject",
                    "body": "Body",
                },
                approval_context={"verified": True},
            )
        )
    except IntegrationError as exc:
        assert exc.code == "tool_contract_changed"
    else:
        raise AssertionError("Schema drift must block an existing binding")


def test_workbuddy_and_reserved_adapters_fail_closed() -> None:
    capabilities = {
        "features": {"configuredDiscovery": True, "status": True},
        "readiness": "preview",
        "blockers": ["integration_direct_tool_call_unvalidated"],
    }
    adapter = WorkBuddyIntegrationAdapter(
        "workbuddy", "workbuddy-mcp", capabilities
    )
    assert adapter.describe_capabilities()["features"]["directToolCall"] is False
    try:
        asyncio.run(adapter.invoke_exact({}, {}, {}, None))
    except IntegrationError as exc:
        assert exc.code == "runtime_integration_invoke_unavailable"
    else:
        raise AssertionError("WorkBuddy direct tool calls must remain unavailable")

    reserved = UnavailableIntegrationAdapter("claude-agent", None, {})
    assert reserved.describe_capabilities()["readiness"] == "unavailable"
