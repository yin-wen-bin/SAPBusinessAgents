from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sap_business_agents_platform.assistant_ddic import DdicLabelError, field_labels
from sap_business_agents_platform.assistant_tools import AssistantToolContext, catalog, permitted
from sap_business_agents_platform.harness import HarnessToolBroker
from sap_business_agents_platform.codex_planner import _with_authoring_mcp, _workflow_assistant_tool_catalog
from sap_business_agents_platform.mcp_server import _AUTHORING_TOOLS, _SAP_TOOLS


def _context(kind: str, *, fields: tuple[tuple[str, str], ...] = ()) -> AssistantToolContext:
    return AssistantToolContext(kind, "request-sha", "draft-1", 2,
                                datetime.now(timezone.utc) + timedelta(minutes=5), fields)


class _Skills:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def list_all_approved_skills(self):
        return [{"skill_id": "sap-adt-table-export", "description": {"zh": "ADT 表导出", "en": "ADT table export"},
                 "read_only": True, "validated": True, "available": True}]

    def get(self, skill_id):
        assert skill_id == "sap-adt-table-export"
        return self.list_all_approved_skills()[0]

    def validate_input(self, skill_id, payload):
        assert skill_id == "sap-adt-table-export"
        assert payload["max_rows"] == 2
        assert payload["object"] in {"DD03L", "DD04T"}

    async def execute(self, skill_id, payload):
        self.calls.append(payload)
        rows = ([{"TABNAME": "T811C", "FIELDNAME": "CYCLE", "ROLLNAME": "KOKRS"}]
                if payload["object"] == "DD03L" else
                [{"ROLLNAME": "KOKRS", "DDLANGUAGE": payload["filters"][1]["value"],
                  "SCRTEXT_M": "成本控制范围" if payload["filters"][1]["value"] == "1" else "Controlling Area"}])
        return {"skill_id": skill_id, "read_only": True, "validated": True,
                "status": "complete", "completeness": {"source_complete": True, "paging_complete": True},
                "validation_issues": [], "scope": {"object": payload["object"], "filters": payload["filters"]},
                "rows": rows, "row_count": len(rows)}


class _Operations:
    def __init__(self) -> None:
        self.active = True
        self.events: list[dict] = []

    def assert_agent_operation(self, draft_id, operation_id, revision):
        return self.active and (draft_id, operation_id, revision) == ("draft-1", "op-1", 2)

    def append_agent_operation_tool_event(self, draft_id, operation_id, event):
        if not self.active:
            return False
        self.events.append(event)
        return True


def test_catalog_is_discovery_not_permission_and_baseline_is_isolated():
    skills = _Skills().list_all_approved_skills()
    author = catalog(_context("agent_authoring", fields=(("T811C", "CYCLE"),)), approved_skills=skills)
    by_id = {item["tool_id"]: item for item in author}
    assert by_id["sap_ddic_field_labels_get"]["available"] is True
    assert by_id["skill:sap-adt-table-export"]["available"] is False
    assert by_id["mail.v1/send"]["unavailable_reason"] == "user_confirmation_required"
    assert by_id["agent.publish"]["available"] is False
    assert by_id["sap_ddic_field_labels_get"]["contract_digest"].startswith("sha256:")
    baseline = {item["tool_id"] for item in catalog(_context("acceptance_baseline"), approved_skills=skills)}
    assert "mail.v1/send" not in baseline
    assert "agent.publish" not in baseline
    assert "sap_ddic_field_labels_get" not in baseline
    assert permitted(_context("agent_authoring"), "sap_ddic_field_labels_get") == (False, "ddic_field_scope_required")
    expired = AssistantToolContext("agent_authoring", "x", "draft-1", 2,
                                   datetime.now(timezone.utc) - timedelta(seconds=1))
    assert permitted(expired, "tool_catalog_search") == (False, "tool_session_expired")
    assert {item["name"] for item in _AUTHORING_TOOLS} == {
        "tool_catalog_search", "tool_catalog_inspect", "sap_ddic_field_labels_get"}
    assert "sap_ddic_field_labels_get" not in {item["name"] for item in _SAP_TOOLS}


def test_workflow_authoring_sees_mail_binding_but_cannot_send():
    import json
    items = json.loads(_workflow_assistant_tool_catalog("用邮箱发送结果", {"bindings": [{
        "capability": "mail.v1", "operation": "search", "enabled": True,
        "connection_enabled": True, "connection_status": "ready", "schema_hash": "sha256:test",
    }]}))
    by_id = {item["tool_id"]: item for item in items}
    assert by_id["mail.v1/search"]["available"] is False
    assert by_id["mail.v1/search"]["unavailable_reason"] == "mail_workflow_only"
    assert by_id["mail.v1/send"]["unavailable_reason"] == "user_confirmation_required"
    assert not any(item["tool_id"].startswith("sap_") for item in items)


def test_authoring_mcp_config_adds_only_broker_server_without_sdk_policy_change():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Config:
        launch_args_override: tuple[str, ...]

    config = Config(("codex", "--disable", "plugins", "app-server", "--listen", "stdio://"))
    client = SimpleNamespace(_client=SimpleNamespace(_sync=SimpleNamespace(config=config)))
    updated = _with_authoring_mcp(client, {"internal_api_url": "http://127.0.0.1:8765",
                                           "operation_id": "op-1", "capability": "opaque"})
    args = updated._client._sync.config.launch_args_override
    assert ("--disable", "plugins") == args[1:3]
    assert any("mcp_servers.sap_authoring_catalog.enabled=true" in item for item in args)
    assert any("--mode" in item and "authoring" in item for item in args)
    assert "app-server" in args


def test_ddic_adapter_returns_only_labels_and_verified_evidence_hashes():
    skills = _Skills()
    output = asyncio.run(field_labels(skills, {"table": "T811C", "field": "CYCLE"}))
    assert output["labels"] == {"zh": "成本控制范围", "en": "Controlling Area"}
    assert output["source_complete"] is True
    assert all(value.startswith("sha256:") for value in output["evidence_sha256"].values())
    assert "rows" not in output
    assert [item["object"] for item in skills.calls] == ["DD03L", "DD04T", "DD04T"]


def test_ddic_missing_translation_is_not_invented_and_incomplete_source_blocks():
    class MissingTranslation(_Skills):
        async def execute(self, skill_id, payload):
            result = await super().execute(skill_id, payload)
            if payload["object"] == "DD04T" and payload["filters"][1]["value"] == "1":
                result["rows"] = []
                result["row_count"] = 0
            return result

    class Incomplete(_Skills):
        async def execute(self, skill_id, payload):
            result = await super().execute(skill_id, payload)
            result["completeness"]["paging_complete"] = False
            return result

    class WrongScope(_Skills):
        async def execute(self, skill_id, payload):
            result = await super().execute(skill_id, payload)
            result["scope"]["filters"] = []
            return result

    result = asyncio.run(field_labels(MissingTranslation(), {"table": "T811C", "field": "CYCLE"}))
    assert result["labels"]["zh"] is None
    assert result["missing_translations"] == ["zh"]
    try:
        asyncio.run(field_labels(Incomplete(), {"table": "T811C", "field": "CYCLE"}))
    except DdicLabelError as exc:
        assert exc.code == "ddic_evidence_incomplete"
    else:
        raise AssertionError("Incomplete Skill evidence must be rejected")
    try:
        asyncio.run(field_labels(WrongScope(), {"table": "T811C", "field": "CYCLE"}))
    except DdicLabelError as exc:
        assert exc.code == "ddic_scope_mismatch"
    else:
        raise AssertionError("A Skill response with a different SAP scope must be rejected")


def test_authoring_session_rejects_out_of_scope_and_cancelled_calls():
    async def scenario():
        broker = object.__new__(HarnessToolBroker)
        broker.store = _Operations()
        broker.skills = _Skills()
        broker._authoring_sessions = {}
        broker._authoring_tool_tasks = {}
        token = broker.open_authoring_session("draft-1", "op-1", 2, "request-sha",
            datetime.now(timezone.utc) + timedelta(minutes=5), (("T811C", "CYCLE"),))
        denied = await broker.handle("op-1", token, "sap_ddic_field_labels_get",
                                     {"table": "T001", "field": "BUKRS"})
        assert denied["code"] == "tool_parameter_outside_task_scope"
        assert not broker.skills.calls
        assert (await broker.handle("op-1", token, "agent.publish", {}))["code"] == "user_confirmation_required"
        found = await broker.handle("op-1", token, "sap_ddic_field_labels_get",
                                    {"table": "T811C", "field": "CYCLE"})
        assert found["ok"] is True
        draft_id, secret, context, contracts = broker._authoring_sessions["op-1"]
        broker._authoring_sessions["op-1"] = (draft_id, secret, context,
            {**contracts, "sap_ddic_field_labels_get": "sha256:stale"})
        assert (await broker.handle("op-1", token, "sap_ddic_field_labels_get",
                                    {"table": "T811C", "field": "CYCLE"}))["code"] == "tool_contract_changed"
        broker.store.active = False
        assert (await broker.handle("op-1", token, "sap_ddic_field_labels_get",
                                    {"table": "T811C", "field": "CYCLE"}))["code"] == "tool_session_stale"
        assert len(broker.skills.calls) == 3
    asyncio.run(scenario())


def test_closing_authoring_session_cancels_owned_skill_read():
    async def scenario():
        entered = asyncio.Event()

        class SlowSkills(_Skills):
            async def execute(self, skill_id, payload):
                entered.set()
                await asyncio.sleep(30)
                return await super().execute(skill_id, payload)

        broker = object.__new__(HarnessToolBroker)
        broker.store = _Operations()
        broker.skills = SlowSkills()
        broker._authoring_sessions = {}
        broker._authoring_tool_tasks = {}
        token = broker.open_authoring_session("draft-1", "op-1", 2, "request-sha",
            datetime.now(timezone.utc) + timedelta(minutes=5), (("T811C", "CYCLE"),))
        task = asyncio.create_task(broker.handle("op-1", token, "sap_ddic_field_labels_get",
                                                 {"table": "T811C", "field": "CYCLE"}))
        await asyncio.wait_for(entered.wait(), 2)
        broker.close_authoring_session("op-1")
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("The owned Skill read must be cancelled")
        assert not broker._authoring_sessions

    asyncio.run(scenario())
