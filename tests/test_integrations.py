from __future__ import annotations

import asyncio
import json
import sqlite3
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

    def describe_setup_options(self) -> dict[str, Any]:
        return {
            "configuration_scope": "user",
            "credential_owner": "runtime",
            "modes": [],
            "blockers": [],
        }

    async def read_setup_configuration(
        self, connection: dict[str, Any]
    ) -> dict[str, Any]:
        del connection
        raise IntegrationError(
            "Setup is unavailable in this fake.",
            code="runtime_integration_configuration_unavailable",
        )

    async def validate_setup(self, draft: dict[str, Any]) -> dict[str, Any]:
        del draft
        raise IntegrationError(
            "Setup is unavailable in this fake.",
            code="runtime_integration_configuration_unavailable",
        )

    async def commit_setup(self, draft: dict[str, Any]) -> dict[str, Any]:
        del draft
        raise IntegrationError(
            "Setup is unavailable in this fake.",
            code="runtime_integration_configuration_unavailable",
        )

    async def begin_authentication(
        self, connection: dict[str, Any]
    ) -> dict[str, Any]:
        del connection
        return {
            "outcome": "open_auth_url",
            "url": "https://login.example.test/oauth",
            "status": "authentication_required",
            "auth_status": "not_logged_in",
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
        self.config: dict[str, Any] = {"mcp_servers": {}}

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
                        "oauth": {"access_token": "must-not-leak"},
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
            configured = []
            for name, value in self.config["mcp_servers"].items():
                configured.append(
                    {
                        "name": name,
                        "authStatus": (
                            "notLoggedIn"
                            if value.get("auth") == "oauth"
                            else "loggedIn"
                        ),
                        "status": "ready",
                        "serverInfo": {"title": name, "description": "Configured MCP"},
                        "tools": {
                            "search_mail": {
                                "name": "search_mail",
                                "description": "Search email",
                                "inputSchema": {"type": "object"},
                            }
                        },
                    }
                )
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
                    },
                    *configured,
                ],
                "nextCursor": None,
            }
        if method == "config/read":
            return {"config": deepcopy(self.config)}
        if method == "config/batchWrite":
            for edit in params.get("edits") or []:
                prefix, name = str(edit["keyPath"]).split(".", 1)
                assert prefix == "mcp_servers"
                self.config["mcp_servers"][name] = deepcopy(edit["value"])
            return {}
        if method == "config/mcpServer/reload":
            return {}
        if method == "mcpServer/oauth/login":
            return {"authorizationUrl": "https://login.example.test/oauth"}
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
    assert items[0]["connection_status"] == "installed_non_callable"
    assert items[0]["tools"][0]["display_only"] is True
    assert "must-not-leak" not in json.dumps(items)
    assert items[1]["auth_status"] == "logged_in"
    assert items[1]["health_status"] == "healthy"
    contract = asyncio.run(adapter.get_tool_contract("outlook-email", "send_mail"))
    assert contract["input_schema"]["properties"]["subject"]["type"] == "string"


class _FailingSourceCodexTransport(_FakeCodexTransport):
    def __init__(self, failing_method: str) -> None:
        super().__init__()
        self.failing_method = failing_method

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        approval_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method == self.failing_method:
            raise IntegrationError(
                f"{method} failed with https://example.test/path?secret=value",
                code="test_source_failed",
            )
        return await super().request(
            method, params, approval_context=approval_context
        )


class _AmbiguousWriteCodexTransport(_FakeCodexTransport):
    def __init__(self, *, write_completed: bool) -> None:
        super().__init__()
        self.write_completed = write_completed

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        approval_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method == "config/batchWrite":
            if self.write_completed:
                await super().request(
                    method, params, approval_context=approval_context
                )
            raise ConnectionError("connection closed before write acknowledgement")
        return await super().request(
            method, params, approval_context=approval_context
        )


class _FailingAuthCodexTransport(_FakeCodexTransport):
    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        approval_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method == "mcpServer/oauth/login":
            raise ConnectionError("OAuth endpoint unavailable")
        return await super().request(
            method, params, approval_context=approval_context
        )


def test_codex_catalog_sources_fail_independently(tmp_path: Path) -> None:
    app_failure = CodexAppServerIntegrationAdapter(
        tmp_path,
        {"features": {"directToolCall": True}},
        transport=_FailingSourceCodexTransport("app/list"),
    )
    mcp_items = asyncio.run(app_failure.list_catalog(force_refresh=True))
    assert [item["source_kind"] for item in mcp_items] == ["mcp_server"]
    app_status = app_failure.describe_capabilities()["source_status"]["app"]
    assert app_status["status"] == "error"
    assert "secret=value" not in app_status["error"]["message"]

    mcp_failure = CodexAppServerIntegrationAdapter(
        tmp_path,
        {"features": {"directToolCall": True}},
        transport=_FailingSourceCodexTransport("mcpServerStatus/list"),
    )
    app_items = asyncio.run(mcp_failure.list_catalog(force_refresh=True))
    assert [item["source_kind"] for item in app_items] == ["app"]
    assert mcp_failure.describe_capabilities()["source_status"]["mcp_server"][
        "status"
    ] == "error"


def test_http_mcp_setup_writes_user_config_and_starts_oauth(tmp_path: Path) -> None:
    transport = _FakeCodexTransport()
    adapter = CodexAppServerIntegrationAdapter(
        tmp_path,
        {
            "resource_kinds": ["app", "mcp_server"],
            "features": {
                "catalog": True,
                "configuredDiscovery": True,
                "status": True,
                "authentication": True,
                "configuration": True,
                "directToolCall": True,
            },
            "readiness": "production",
        },
        transport=transport,
    )
    gateway = IntegrationGateway(
        _LocalPlugins(),
        _SDKManager(),
        IntegrationStateStore(tmp_path / "platform.sqlite3"),
        [adapter],
    )

    options = gateway.setup_options("mail.v1")
    assert options[0]["enabled"] is True
    assert [mode["id"] for mode in options[0]["modes"]] == [
        "runtime_app",
        "http_mcp",
    ]

    draft = asyncio.run(
        gateway.create_setup_draft(
            {
                "integration_backend_id": "codex-app-server",
                "mode": "http_mcp",
                "native_id": "mail-team",
                "display_name": "Team Mail",
                "configuration": {
                    "url": "https://mail.example.test/mcp?tenant=team",
                    "auth_mode": "oauth",
                    "enabled_tools": ["search_mail"],
                },
            }
        )
    )
    validation = asyncio.run(gateway.validate_setup_draft(draft["setup_id"]))
    assert validation["valid"] is True

    committed = asyncio.run(gateway.commit_setup_draft(draft["setup_id"]))
    assert committed["outcome"] == "open_auth_url"
    assert committed["url"] == "https://login.example.test/oauth"
    assert committed["connection"]["metadata"]["configuration_origin"] == (
        "platform_user_config"
    )
    assert committed["connection"]["status"] == "authentication_required"
    saved_config = transport.config["mcp_servers"]["mail-team"]
    assert saved_config == {
        "url": "https://mail.example.test/mcp?tenant=team",
        "enabled": True,
        "default_tools_approval_mode": "prompt",
        "auth": "oauth",
        "enabled_tools": ["search_mail"],
    }
    repeated = asyncio.run(gateway.commit_setup_draft(draft["setup_id"]))
    assert repeated["outcome"] == "already_committed"
    assert len(
        [call for call in transport.calls if call[0] == "config/batchWrite"]
    ) == 1


def test_http_mcp_setup_confirms_or_locks_ambiguous_writes(tmp_path: Path) -> None:
    async def exercise(write_completed: bool, database_name: str) -> tuple[
        IntegrationGateway, dict[str, Any]
    ]:
        adapter = CodexAppServerIntegrationAdapter(
            tmp_path,
            {"features": {"configuration": True, "directToolCall": True}},
            transport=_AmbiguousWriteCodexTransport(
                write_completed=write_completed
            ),
        )
        gateway = IntegrationGateway(
            _LocalPlugins(),
            _SDKManager(),
            IntegrationStateStore(tmp_path / database_name),
            [adapter],
        )
        draft = await gateway.create_setup_draft(
            {
                "integration_backend_id": "codex-app-server",
                "mode": "http_mcp",
                "native_id": f"ambiguous-{write_completed}",
                "display_name": "Ambiguous Mail",
                "configuration": {
                    "url": "https://mail.example.test/mcp",
                    "auth_mode": "none",
                },
            }
        )
        return gateway, draft

    confirmed_gateway, confirmed_draft = asyncio.run(
        exercise(True, "confirmed.sqlite3")
    )
    confirmed = asyncio.run(
        confirmed_gateway.commit_setup_draft(confirmed_draft["setup_id"])
    )
    assert confirmed["connection"]["status"] == "ready"

    unknown_gateway, unknown_draft = asyncio.run(
        exercise(False, "unknown.sqlite3")
    )
    try:
        asyncio.run(unknown_gateway.commit_setup_draft(unknown_draft["setup_id"]))
    except IntegrationError as exc:
        assert exc.code == "configuration_outcome_unknown"
    else:
        raise AssertionError("An unconfirmed config write must fail closed")
    assert unknown_gateway.get_setup_draft(unknown_draft["setup_id"])[
        "status"
    ] == "configuration_outcome_unknown"


def test_http_mcp_setup_stays_committed_when_oauth_start_fails(tmp_path: Path) -> None:
    adapter = CodexAppServerIntegrationAdapter(
        tmp_path,
        {"features": {"configuration": True, "directToolCall": True}},
        transport=_FailingAuthCodexTransport(),
    )
    gateway = IntegrationGateway(
        _LocalPlugins(),
        _SDKManager(),
        IntegrationStateStore(tmp_path / "platform.sqlite3"),
        [adapter],
    )
    draft = asyncio.run(
        gateway.create_setup_draft(
            {
                "integration_backend_id": "codex-app-server",
                "mode": "http_mcp",
                "native_id": "oauth-retry-mail",
                "display_name": "OAuth Retry Mail",
                "configuration": {
                    "url": "https://mail.example.test/mcp",
                    "auth_mode": "oauth",
                },
            }
        )
    )

    result = asyncio.run(gateway.commit_setup_draft(draft["setup_id"]))

    assert result["outcome"] == "authentication_required"
    assert result["connection"]["status"] == "authentication_required"
    assert result["authentication_warning"]["code"] == (
        "runtime_integration_authentication_unavailable"
    )
    assert gateway.get_setup_draft(draft["setup_id"])["status"] == "committed"
    assert asyncio.run(gateway.commit_setup_draft(draft["setup_id"]))[
        "outcome"
    ] == "already_committed"


def test_http_mcp_setup_rejects_secrets_before_persistence(tmp_path: Path) -> None:
    adapter = CodexAppServerIntegrationAdapter(
        tmp_path,
        {"features": {"configuration": True, "directToolCall": True}},
        transport=_FakeCodexTransport(),
    )
    state = IntegrationStateStore(tmp_path / "platform.sqlite3")
    gateway = IntegrationGateway(_LocalPlugins(), _SDKManager(), state, [adapter])
    try:
        asyncio.run(
            gateway.create_setup_draft(
                {
                    "integration_backend_id": "codex-app-server",
                    "mode": "http_mcp",
                    "native_id": "mail",
                    "display_name": "Mail",
                    "configuration": {
                        "url": "https://mail.example.test/mcp",
                        "auth_mode": "oauth",
                        "password": "must-not-persist",
                    },
                }
            )
        )
    except IntegrationError as exc:
        assert exc.code == "integration_setup_forbidden_field"
    else:
        raise AssertionError("Secret setup fields must be rejected")
    assert b"must-not-persist" not in (tmp_path / "platform.sqlite3").read_bytes()

    try:
        asyncio.run(
            gateway.create_setup_draft(
                {
                    "integration_backend_id": "codex-app-server",
                    "mode": "http_mcp",
                    "native_id": "mail-query-secret",
                    "display_name": "Mail",
                    "configuration": {
                        "url": "https://mail.example.test/mcp?access_token=must-not-persist",
                        "auth_mode": "oauth",
                    },
                }
            )
        )
    except IntegrationError as exc:
        assert exc.code == "integration_setup_url_secret_forbidden"
    else:
        raise AssertionError("Secret MCP URL query parameters must be rejected")
    assert b"must-not-persist" not in (tmp_path / "platform.sqlite3").read_bytes()


def test_http_mcp_setup_api_lifecycle_and_external_edit_guard(tmp_path: Path) -> None:
    adapter = CodexAppServerIntegrationAdapter(
        tmp_path,
        {
            "features": {
                "configuration": True,
                "authentication": True,
                "directToolCall": True,
            }
        },
        transport=_FakeCodexTransport(),
    )
    gateway = IntegrationGateway(
        _LocalPlugins(),
        _SDKManager(),
        IntegrationStateStore(tmp_path / "platform.sqlite3"),
        [adapter],
    )
    app = create_app(_settings(tmp_path), integration_gateway=gateway)

    with TestClient(app) as client:
        options = client.get("/api/plugins/setup-options?capability=mail.v1")
        created = client.post(
            "/api/plugins/setup-drafts",
            json={
                "integration_backend_id": "codex-app-server",
                "mode": "http_mcp",
                "native_id": "frontdesk-mail",
                "display_name": "Frontdesk Mail",
                "configuration": {
                    "url": "https://mail.example.test/mcp",
                    "auth_mode": "bearer_env",
                    "bearer_token_env_var": "FRONTDESK_MAIL_TOKEN",
                    "tool_timeout_sec": 45,
                },
            },
        )
        setup_id = created.json()["setup"]["setup_id"]
        fetched = client.get(f"/api/plugins/setup-drafts/{setup_id}")
        validated = client.post(
            f"/api/plugins/setup-drafts/{setup_id}/validate"
        )
        committed = client.post(f"/api/plugins/setup-drafts/{setup_id}/commit")
        repeated = client.post(f"/api/plugins/setup-drafts/{setup_id}/commit")

    assert options.status_code == 200
    assert options.json()["items"][0]["configuration_scope"] == "user"
    assert created.status_code == 201
    assert fetched.status_code == 200
    assert validated.json()["valid"] is True
    assert committed.status_code == 200
    assert committed.json()["connection"]["metadata"]["configuration_origin"] == (
        "platform_user_config"
    )
    assert repeated.json()["outcome"] == "already_committed"
    response_text = " ".join(
        [created.text, fetched.text, validated.text, committed.text, repeated.text]
    )
    assert "FRONTDESK_MAIL_TOKEN" in response_text
    assert "token-secret-value" not in response_text

    external = next(
        item
        for item in asyncio.run(gateway.list_catalog(force_refresh=True))
        if item["native_id"] == "outlook-email"
        and item["source_kind"] == "mcp_server"
    )
    external_connection = asyncio.run(gateway.connect(external["catalog_id"]))[
        "connection"
    ]
    try:
        asyncio.run(
            gateway.create_setup_draft(
                {
                    "integration_backend_id": "codex-app-server",
                    "mode": "http_mcp",
                    "connection_id": external_connection["connection_id"],
                }
            )
        )
    except IntegrationError as exc:
        assert exc.code == "integration_setup_external_configuration_read_only"
    else:
        raise AssertionError("Runtime-external MCP configuration must remain read-only")


def test_http_mcp_setup_validation_and_expiry(tmp_path: Path) -> None:
    adapter = CodexAppServerIntegrationAdapter(
        tmp_path,
        {"features": {"configuration": True, "directToolCall": True}},
        transport=_FakeCodexTransport(),
    )
    state = IntegrationStateStore(tmp_path / "platform.sqlite3")
    gateway = IntegrationGateway(_LocalPlugins(), _SDKManager(), state, [adapter])
    draft = asyncio.run(
        gateway.create_setup_draft(
            {
                "integration_backend_id": "codex-app-server",
                "mode": "http_mcp",
                "native_id": "bad id",
                "display_name": "Invalid Mail",
                "configuration": {
                    "url": "http://mail.example.test/mcp#fragment",
                    "auth_mode": "bearer_env",
                    "bearer_token_env_var": "not an env name",
                },
            }
        )
    )
    validation = asyncio.run(gateway.validate_setup_draft(draft["setup_id"]))
    codes = {error["code"] for error in validation["errors"]}
    assert {
        "runtime_configuration_id_invalid",
        "integration_setup_https_required",
        "integration_setup_url_fragment_forbidden",
        "integration_setup_environment_variable_invalid",
    }.issubset(codes)

    with sqlite3.connect(state.path) as conn:
        conn.execute(
            "UPDATE integration_setup_drafts SET expires_at = ? WHERE setup_id = ?",
            ("2000-01-01T00:00:00+00:00", draft["setup_id"]),
        )
        conn.commit()
    try:
        gateway.get_setup_draft(draft["setup_id"])
    except IntegrationError as exc:
        assert exc.code == "integration_setup_draft_not_found"
    else:
        raise AssertionError("An expired mutable setup draft must be removed lazily")


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
    assert adapter.describe_setup_options()["modes"] == []
    try:
        asyncio.run(adapter.validate_setup({}))
    except IntegrationError as exc:
        assert exc.code == "runtime_integration_configuration_unavailable"
    else:
        raise AssertionError("WorkBuddy setup must remain unavailable")
    try:
        asyncio.run(adapter.invoke_exact({}, {}, {}, None))
    except IntegrationError as exc:
        assert exc.code == "runtime_integration_invoke_unavailable"
    else:
        raise AssertionError("WorkBuddy direct tool calls must remain unavailable")

    reserved = UnavailableIntegrationAdapter("claude-agent", None, {})
    assert reserved.describe_capabilities()["readiness"] == "unavailable"
