from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.harness import (
    CodexHarnessController,
    HarnessToolBroker,
    _custom_tool_kind,
    _mcp_overrides,
    _persistent_harness_counts,
    _public_https_citations,
    _sanitized_codex_env,
    _validate_internal_api_url,
)
from sap_business_agents_platform.models import RunCreate, RunMode
from sap_business_agents_platform.sap_read.embedded_odata import EmbeddedODataProvider
from sap_business_agents_platform.tool_gateway import (
    ToolAdmissionError,
    ToolAdmissionGateway,
    ToolCandidate,
    _admit_openapi,
)


class FakeSapRead:
    async def catalog(self, query: str = "", skip: int = 0, limit: int = 100):
        return {"ok": True, "data": {"items": [{"query": query}], "source_complete": True}}

    async def schema(
        self,
        service_name: str,
        entity_sets,
        query: str = "",
        *,
        odata_version: str,
        include_fields: bool = True,
        max_fields: int = 5000,
    ):
        return {
            "ok": True,
            "data": {
                "service": {"service_name": service_name, "odata_version": odata_version},
                "entities": [{"entity_set": entity_sets[0]}],
            },
        }

    async def validate_plan(self, plan, query: str = ""):
        return {"ok": True, "validated": True}

    async def execute_plan(self, plan, query: str = "", conversation_id: str | None = None):
        return {
            "ok": True,
            "results": [
                {
                    "__metadata": {"uri": "http://internal.example.invalid/sap/private"},
                    "Supplier": "17300001",
                    "FinancialAccountType": "K",
                }
            ],
            "result_count": 1,
            "source_complete": True,
            "requests": [{"method": "GET"}],
        }


class FakeSkills:
    def validate_input(self, skill_id: str, input_payload):
        if input_payload.get("schema_version") != 1:
            raise ValueError("invalid mock skill input")

    async def execute(self, skill_id: str, input_payload):
        return {
            "ok": True,
            "status": "complete",
            "read_only": True,
            "validated": True,
            "source_complete": True,
            "paging_complete": True,
            "rows": [{"BUKRS": "1710"}],
        }


def _settings(tmp_path: Path, root: Path | None = None) -> Settings:
    repository = root or Path(__file__).resolve().parents[1]
    return Settings(
        repository_root=repository,
        data_root=tmp_path / "data",
        draft_root=tmp_path / "drafts",
        skillhub_root=tmp_path / "skillhub",
        internal_api_url="http://127.0.0.1:8765",
    )


def test_broker_enforces_capability_idempotency_evidence_and_gap_gate(tmp_path: Path) -> None:
    async def scenario() -> None:
        await _broker_scenario(tmp_path)

    asyncio.run(scenario())


def test_broker_does_not_repeat_unknown_call_after_process_recovery(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        store = RunStore(settings.database_path)
        run_id = "run_recovery"
        store.create_run(run_id, RunCreate(mode=RunMode.free_query, query="supplier status"))
        arguments = {"query": "supplier"}
        request_hash = hashlib.sha256(
            json.dumps(
                {"tool": "sap_catalog_search", "arguments": arguments},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        store.begin_harness_tool_call(
            call_id="lost_call",
            run_id=run_id,
            tool_name="sap_catalog_search",
            request_hash=request_hash,
            safe_input=arguments,
        )
        broker = HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
        result = await broker.handle(
            run_id,
            broker.open_session(run_id),
            "sap_catalog_search",
            {**arguments, "tool_call_id": "retry_call"},
        )
        assert result["code"] == "tool_call_recovery_unknown"
        assert len(store.list_harness_tool_calls(run_id)) == 1

    asyncio.run(scenario())


def test_store_recovers_only_nonterminal_free_query_runs(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "platform.sqlite3")
    store.create_run("free", RunCreate(mode=RunMode.free_query, query="supplier status"))
    store.update_run("free", status="planning", thread_id="thread_1")
    store.create_run(
        "fixed",
        RunCreate(mode=RunMode.agent, agentId="inventory-health-balancing", input={}),
    )
    assert [item.run_id for item in store.list_recoverable_free_query_runs()] == ["free"]


def test_harness_steer_interrupt_and_persistent_counts(tmp_path: Path) -> None:
    class ActiveTurn:
        def __init__(self) -> None:
            self.steered: list[str] = []
            self.interrupted = False

        async def steer(self, value: str) -> None:
            self.steered.append(value)

        async def interrupt(self) -> None:
            self.interrupted = True

    async def scenario() -> None:
        settings = _settings(tmp_path)
        store = RunStore(settings.database_path)
        run_id = "run_active"
        store.create_run(run_id, RunCreate(mode=RunMode.free_query, query="supplier status"))
        controller = CodexHarnessController(
            settings,
            store,
            HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills()),
        )
        turn = ActiveTurn()
        controller._active_turns[run_id] = turn
        assert await controller.steer(run_id, "company 1710") is True
        assert await controller.interrupt(run_id) is True
        assert turn.steered == ["company 1710"]
        assert turn.interrupted is True
        store.append_event(run_id, "web_search_completed", {})
        store.append_event(run_id, "tool_discovery_completed", {"count": 3})
        store.append_event(run_id, "tool_admission_passed", {})
        assert _persistent_harness_counts(store, run_id) == (1, 3, 1)

    asyncio.run(scenario())


async def _broker_scenario(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = RunStore(settings.database_path)
    run_id = "run_harness"
    store.create_run(run_id, RunCreate(mode=RunMode.free_query, query="supplier open items"))
    broker = HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
    token = broker.open_session(run_id)

    denied = await broker.handle(run_id, "wrong", "sap_catalog_search", {"query": "x"})
    assert denied["code"] == "harness_capability_denied"

    first = await broker.handle(
        run_id, token, "sap_catalog_search", {"query": "supplier", "tool_call_id": "call_1"}
    )
    replay = await broker.handle(
        run_id, token, "sap_catalog_search", {"query": "supplier", "tool_call_id": "call_2"}
    )
    assert first["ok"] is True
    assert replay["idempotent_replay"] is True
    failed = await broker.handle(
        run_id, token, "unknown_tool", {"value": 1, "tool_call_id": "failed_1"}
    )
    failed_replay = await broker.handle(
        run_id, token, "unknown_tool", {"value": 1, "tool_call_id": "failed_2"}
    )
    assert failed["ok"] is False
    assert failed_replay["idempotent_replay"] is True

    await broker.handle(
        run_id,
        token,
        "sap_schema_get",
        {
            "service_name": "API_GLACCOUNTLINEITEM",
            "odata_version": "2.0",
            "entity_sets": ["GLAccountLineItem"],
        },
    )
    plan = {
        "service_name": "API_GLACCOUNTLINEITEM",
        "odata_version": "2.0",
        "entity_set": "GLAccountLineItem",
        "http_method": "GET",
    }
    await broker.handle(run_id, token, "sap_query_validate", {"plan": plan})
    executed = await broker.handle(run_id, token, "sap_query_execute", {"plan": plan})
    assert executed["source_type"] == "sap_live"
    assert executed["source_complete"] is True
    assert "__metadata" not in executed["preview"]["rows"][0]
    read = await broker.handle(
        run_id, token, "sap_evidence_read", {"evidence_ref": executed["evidence_ref"]}
    )
    assert read["rows"][0]["FinancialAccountType"] == "K"

    assessed = await broker.handle(
        run_id,
        token,
        "sap_evidence_assess",
        {
            "question": "payment status",
            "evidence_refs": [executed["evidence_ref"]],
            "missing_evidence": ["payment settlement evidence"],
        },
    )
    assert assessed["adt_eligible"] is True
    malformed = await broker.handle(
        run_id,
        token,
        "sap_skill_execute",
        {
            "skill_id": "sap-adt-table-export",
            "gap_token": assessed["gap_token"],
            "input": {"source_type": "table", "object": "BSAK", "max_rows": 2},
        },
    )
    assert malformed["ok"] is False
    skill = await broker.handle(
        run_id,
        token,
        "sap_skill_execute",
        {
            "skill_id": "sap-adt-table-export",
            "gap_token": assessed["gap_token"],
            "input": {
                "schema_version": 1,
                "source_type": "table",
                "object": "BSAK",
                "fields": ["BUKRS"],
                "filters": [{"field": "BUKRS", "operator": "EQ", "value": "1710"}],
                "max_rows": 2,
            },
        },
    )
    assert skill["source_type"] == "sap_skill"
    reused = await broker.handle(
        run_id,
        token,
        "sap_skill_execute",
        {
            "skill_id": "sap-adt-table-export",
            "gap_token": assessed["gap_token"],
            "input": {},
        },
    )
    assert reused["code"] == "gap_token_invalid"
    saved_skill_call = next(
        item
        for item in store.list_harness_tool_calls(run_id)
        if item["tool_name"] == "sap_skill_execute"
        and item["safe_input"].get("gap_token")
    )
    assert saved_skill_call["safe_input"]["gap_token"].startswith("sha256:")
    assert assessed["gap_token"] not in json.dumps(saved_skill_call["safe_input"])

    discovered = await broker.handle(
        run_id,
        token,
        "tool_discovery_search",
        {"query": "safe compute", "capability": "statistics"},
    )
    candidate_id = discovered["candidates"][0]["candidate_id"]
    activated = await broker.handle(
        run_id,
        token,
        "tool_discovery_activate",
        {"candidate_id": candidate_id},
    )
    assert activated["candidate"]["active"] is True
    pending_gap = await broker.handle(
        run_id,
        token,
        "sap_evidence_assess",
        {
            "question": "bank settlement",
            "evidence_refs": [executed["evidence_ref"]],
            "missing_evidence": ["independent bank evidence"],
        },
    )
    assert pending_gap["adt_eligible"] is True
    broker.close_session(run_id)
    recovered = HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
    recovered_token = recovered.open_session(run_id)
    inspected = await recovered.handle(
        run_id,
        recovered_token,
        "tool_discovery_inspect",
        {"candidate_id": candidate_id},
    )
    assert inspected["candidate"]["active"] is True
    recovered_skill = await recovered.handle(
        run_id,
        recovered_token,
        "sap_skill_execute",
        {
            "skill_id": "sap-adt-table-export",
            "gap_token": pending_gap["gap_token"],
            "input": {
                "schema_version": 1,
                "source_type": "table",
                "object": "REGUH",
                "fields": ["ZBUKR"],
                "filters": [{"field": "ZBUKR", "operator": "EQ", "value": "1710"}],
                "max_rows": 2,
            },
        },
    )
    assert recovered_skill["source_type"] == "sap_skill"


def test_dynamic_gateway_only_runs_admitted_pure_compute() -> None:
    asyncio.run(_dynamic_gateway_scenario())


async def _dynamic_gateway_scenario() -> None:
    gateway = ToolAdmissionGateway()
    found = await gateway.search("run", query="safe compute", capability="statistics")
    candidate_id = found["candidates"][0]["candidate_id"]
    gateway.activate("run", candidate_id)
    result = await gateway.execute(
        "run",
        candidate_id=candidate_id,
        operation_id="evaluate",
        parameters={
            "language": "python",
            "code": "sum(values) / len(values)",
            "inputs": {"values": [2, 4, 6]},
        },
    )
    assert result["result"] == 4
    with pytest.raises(ToolAdmissionError):
        await gateway.execute(
            "run",
            candidate_id=candidate_id,
            operation_id="evaluate",
            parameters={"language": "python", "code": "__import__('os').environ", "inputs": {}},
        )


def test_dynamic_gateway_rejects_private_manifest_before_network() -> None:
    asyncio.run(_private_manifest_scenario())


async def _private_manifest_scenario() -> None:
    gateway = ToolAdmissionGateway()
    with pytest.raises(ToolAdmissionError, match="local or private"):
        await gateway.search(
            "run", query="private", manifest_url="https://127.0.0.1/openapi.json"
        )


def test_external_openapi_admits_and_executes_only_schema_valid_get() -> None:
    asyncio.run(_external_openapi_scenario())


async def _external_openapi_scenario() -> None:
    spec = {
        "openapi": "3.0.1",
        "info": {"title": "Public status", "version": "1.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/status": {
                "get": {
                    "operationId": "readStatus",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"ok": {"type": "boolean"}},
                                        "required": ["ok"],
                                        "additionalProperties": False,
                                    }
                                }
                            }
                        }
                    },
                },
                "post": {"operationId": "writeStatus", "responses": {"204": {}}},
            }
        },
    }

    class Response:
        is_redirect = False
        content = b'{"ok":true}'

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json():
            return {"ok": True}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def request(self, method, url, **_kwargs):
            assert method == "GET"
            assert url == "https://api.example.com/status"
            return Response()

    with patch(
        "sap_business_agents_platform.tool_gateway._require_public_https",
        new=AsyncMock(),
    ):
        operations, reason = await _admit_openapi(spec, "https://api.example.com/openapi.json")
        assert reason == ""
        assert [item["method"] for item in operations] == ["GET"]
        gateway = ToolAdmissionGateway()
        candidate = ToolCandidate(
            candidate_id="openapi.fixture",
            name="Public status",
            source="https://api.example.com/openapi.json",
            version="1.0",
            source_hash="sha256:" + "a" * 64,
            admission="admitted",
            reason="validated fixture",
            operations=operations,
        )
        gateway._candidates["run"] = {candidate.candidate_id: candidate}
        gateway.activate("run", candidate.candidate_id)
        with patch("sap_business_agents_platform.tool_gateway.httpx.AsyncClient", return_value=Client()):
            result = await gateway.execute(
                "run",
                candidate_id=candidate.candidate_id,
                operation_id="readStatus",
                parameters={},
            )
        assert result["result"] == {"ok": True}


def test_mcp_server_lists_only_declared_read_only_tools(tmp_path: Path) -> None:
    payloads = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                }
            ),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-m", "sap_business_agents_platform.mcp_server", "--mode", "tools"],
        input=payloads + "\n",
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    tools = responses[1]["result"]["tools"]
    assert {item["name"] for item in tools} == {
        "tool_discovery_search",
        "tool_discovery_inspect",
        "tool_discovery_activate",
        "external_tool_execute",
        "safe_compute",
    }
    assert all(item["annotations"]["readOnlyHint"] is True for item in tools)
    safe_compute = next(item for item in tools if item["name"] == "safe_compute")
    assert safe_compute["inputSchema"]["required"] == ["language", "code", "inputs"]


def test_app_server_overrides_disable_inherited_mcp_and_strip_sap_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        '[mcp_servers.sapclaw_runtime]\ncommand="legacy"\n', encoding="utf-8"
    )
    settings = _settings(tmp_path)
    with patch("sap_business_agents_platform.harness.Path.home", return_value=tmp_path):
        overrides = _mcp_overrides(settings, "run", "cap", sys.executable)
    assert "mcp_servers.sapclaw_runtime.enabled=false" in overrides
    assert any(item.startswith("mcp_servers.sap_business_agents.command=") for item in overrides)
    assert not any("SAP_PASSWORD" in item for item in overrides)

    monkeypatch.setenv("SAP_PASSWORD", "secret")
    monkeypatch.setenv("SAP_ADT_TOKEN", "secret")
    sanitized = _sanitized_codex_env()
    assert sanitized["SAP_PASSWORD"] == ""
    assert sanitized["SAP_ADT_TOKEN"] == ""


def test_custom_tool_wrapper_classifies_native_web_and_forbidden_host_tools() -> None:
    kind, topic = _custom_tool_kind(
        {
            "type": "customToolCall",
            "input": 'const r=await tools.web__run({search_query:[{q:"SAP OData V4"}]});',
        }
    )
    assert kind == "web_search"
    assert topic == "SAP OData V4"
    assert _custom_tool_kind(
        {"type": "customToolCall", "input": 'await tools.exec_command({cmd:"whoami"})'}
    )[0] == "forbidden"
    assert _custom_tool_kind(
        {"type": "customToolCall", "input": 'await tools.finance({ticker:"SAP"})'}
    )[0] == "forbidden"
    _validate_internal_api_url("http://127.0.0.1:8765")
    with pytest.raises(RuntimeError, match="capability_isolation_failed"):
        _validate_internal_api_url("https://example.com")
    assert _public_https_citations(
        {
            "output": (
                "See https://developers.openai.com/api/docs/models/gpt-5.6-sol?tracking=1 "
                "and reject https://127.0.0.1/private plus https://portal.internal/tool."
            )
        }
    ) == ["https://developers.openai.com/api/docs/models/gpt-5.6-sol"]


def test_chinese_catalog_query_finds_supplier_open_item_service(tmp_path: Path) -> None:
    asyncio.run(_catalog_scenario(tmp_path))


async def _catalog_scenario(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    provider = EmbeddedODataProvider(
        base_url="",
        username="",
        password="",
        client="",
        service_registry_path=root / "config" / "odata-services.json",
        catalog_seed_path=root / "data" / "catalog-seed" / "catalog.json",
    )
    result = await provider.catalog(
        query="查询供应商17300001在公司1710下截止2018/10/01的未清项目与付款状态",
        limit=20,
    )
    matches = {
        (item["service_name"], item["entity_set"])
        for item in result["data"]["items"]
    }
    assert ("API_GLACCOUNTLINEITEM", "GLAccountLineItem") in matches


def test_health_exposes_harness_without_public_internal_authority(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["free_query_runtime"]["harness_enabled"] is True
        denied = client.post(
            "/api/internal/harness/tools/sap_catalog_search",
            json={"arguments": {"query": "x"}},
        )
        assert denied.status_code == 403
