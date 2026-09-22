"""Offline regressions for independent baseline discovery and deadlines."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from sap_business_agents_platform.agent_acceptance_campaigns import AgentAcceptanceJobs
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.harness import HarnessToolBroker, _compact_catalog_result, _developer_instructions
from sap_business_agents_platform.harness_query_adapter import normalize_ascending_order
from sap_business_agents_platform.models import RunCreate, RunMode
from sap_business_agents_platform.plugins import PluginManager, SapReadCapability, official_plugin_manifests
from sap_business_agents_platform.sap_read import EmbeddedODataProvider
from sap_business_agents_platform.sap_read.base import SapReadError
from tests.test_harness import _settings, FakeSapRead, FakeSkills
from tests.test_odata_v2_v4 import registry, V4_METADATA
from tests.test_agent_acceptance_campaigns import _campaign, _Coordinator, _Runtime

ROOT = Path(__file__).resolve().parents[1]


def test_all_enabled_registered_services_discoverable_offline():
    provider = EmbeddedODataProvider(base_url="", username="", password="",
        service_registry_path=ROOT / "config/odata-services.json",
        curated_catalog_path=ROOT / "config/catalog-curated-terms.json")
    async def run():
        for service in provider._service_registry.public_services():
            if not service["enabled"] or service["status"] == "blocked":
                continue
            value = _compact_catalog_result(await provider.catalog(query=service["service_name"]))
            assert any(item["service_name"] == service["service_name"] for item in value["data"]["service_candidates"])
            assert all(item["metadata_status"] == "not_checked" for item in value["data"]["service_candidates"])
        for term in ("计划费用", "财务计划条目", "计划类别", "financial planning entry item"):
            value = await provider.catalog(query=term)
            assert any(item["service_name"] == "API_FINPLANNINGENTRYITEM_SRV" for item in value["data"]["service_candidates"])
    asyncio.run(run())


def test_schema_discovery_pagination_run_cache_and_failure_recovery(tmp_path):
    calls = []
    fail = [True]
    def handler(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path.endswith("/$metadata")
        if fail[0]:
            raise httpx.ReadTimeout("fixture timeout", request=request)
        metadata = V4_METADATA.replace('<EntitySet Name="Products" EntityType="TEST.ProductType" />', '<EntitySet Name="Products" EntityType="TEST.ProductType" /><EntitySet Name="OtherProducts" EntityType="TEST.ProductType" />')
        return httpx.Response(200, text=metadata, headers={"OData-Version": "4.0"})
    provider = EmbeddedODataProvider(base_url="https://sap.example.test", username="test", password="test",
        service_registry_path=registry(tmp_path), transport=httpx.MockTransport(handler))
    async def run():
        for _ in range(3):
            with pytest.raises(SapReadError):
                await provider.schema("API_V4_TEST", [], odata_version="4.0", mode="entities", cache_scope="run")
        assert not provider._run_metadata
        fail[0] = False
        value = await provider.schema("API_V4_TEST", [], odata_version="4.0", mode="entities", limit=1, cache_scope="run")
        assert value["data"]["total_count"] == 2 and value["data"]["has_more"]
        page = await provider.schema("API_V4_TEST", [], odata_version="4.0", mode="entities", offset=1, limit=1, cache_scope="run")
        assert not page["data"]["has_more"]
        assert value["data"]["entities"] != page["data"]["entities"]
        with provider.metadata_scope("run"):
            fields = await provider.schema("API_V4_TEST", ["Products"], odata_version="4.0")
            assert fields["data"]["fields"]
        assert len(calls) == 4
        provider.password = "changed-fixture-secret"
        await provider.schema("API_V4_TEST", ["Products"], odata_version="4.0", cache_scope="run")
        assert len(calls) == 5
        await provider.schema("API_V4_TEST", ["Products"], odata_version="4.0")
        assert len(calls) == 6  # fixed/default schema still fetches live
        provider.clear_schema_scope("run")
        assert not provider._run_metadata
    asyncio.run(run())


def test_live_plugin_chain_forwards_discovery_and_scoped_metadata(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path.endswith("/$metadata")
        return httpx.Response(200, text=V4_METADATA, headers={"OData-Version": "4.0"})
    provider = EmbeddedODataProvider(base_url="https://sap.example.test", username="test", password="test",
        service_registry_path=registry(tmp_path), transport=httpx.MockTransport(handler))
    async def run():
        manager = PluginManager(tmp_path / "plugins", tmp_path / "plugin-state.json", official_plugin_manifests())
        manager.bind_provider("embedded-sap-odata", provider)
        await manager.start()
        capability = SapReadCapability(manager)
        settings = _settings(tmp_path)
        store = RunStore(settings.database_path)
        store.create_run("run", RunCreate(mode=RunMode.free_query, query="test"))
        broker = HarnessToolBroker(settings, store, capability, FakeSkills())
        token = broker.open_session("run")
        for arguments in ({"mode": "entities", "limit": 1}, {"entity_sets": ["Products"]}):
            result = await broker.handle("run", token, "sap_schema_get", {
                "service_name": "API_V4_TEST", "odata_version": "4.0", **arguments})
            assert result["ok"] is True, result
        assert len(calls) == 1
        assert provider._run_metadata
        # Default fixed-Agent calls keep the previous signature and live fetch behavior.
        await capability.schema("API_V4_TEST", ["Products"], odata_version="4.0")
        assert len(calls) == 2
        broker.close_session("run")
        assert not provider._run_metadata
    asyncio.run(run())


def test_legacy_plugin_schema_keeps_original_signature(tmp_path):
    class LegacyProvider:
        async def health(self):
            return {"ok": True}
        async def schema(self, service_name, entity_sets, query="", *, odata_version, include_fields=True, max_fields=5000):
            return {"ok": True, "data": {"fields": []}}
    async def run():
        manager = PluginManager(tmp_path / "plugins", tmp_path / "plugin-state.json", official_plugin_manifests())
        manager.bind_provider("embedded-sap-odata", LegacyProvider())
        await manager.start()
        capability = SapReadCapability(manager)
        with capability.metadata_scope("run"):
            assert (await capability.schema("API_TEST", ["Entity"], odata_version="2.0", cache_scope="run"))["ok"]
        with pytest.raises(Exception) as error:
            await capability.schema("API_TEST", [], odata_version="2.0", mode="entities")
        assert error.value.code == "schema_entity_discovery_unavailable"
        capability.clear_schema_scope("run")
    asyncio.run(run())


@pytest.mark.parametrize("value", [["CompanyCode"], ["CompanyCode asc"], [{"field": "CompanyCode", "direction": "asc"}]])
def test_equivalent_ascending_order_is_harness_only(value):
    plan = {"steps": [{"order_by": value}]}
    before = json.dumps(plan)
    result, diagnostics = normalize_ascending_order(plan)
    assert result["steps"][0]["order_by"] == ["CompanyCode"]
    assert json.dumps(plan) == before
    assert bool(diagnostics) == (value != ["CompanyCode"])


@pytest.mark.parametrize("value", [["CompanyCode desc"], ["tolower(CompanyCode)"], [{"field": "CompanyCode", "direction": "desc"}], [{"field": "CompanyCode", "direction": "asc", "nulls": "first"}], [{"field": "CompanyCode", "direction": "unknown"}]])
def test_non_equivalent_order_is_rejected(value):
    with pytest.raises(SapReadError, match="ascending"):
        normalize_ascending_order({"order_by": value})


def test_harness_validate_and_execute_share_order_adapter(tmp_path):
    async def run():
        settings = _settings(tmp_path)
        store = RunStore(settings.database_path)
        store.create_run("run", RunCreate(mode=RunMode.free_query, query="test"))
        sap = FakeSapRead()
        seen = []
        async def validate(plan, query):
            seen.append(plan)
            return {"ok": True}
        sap.validate_plan = validate
        broker = HarnessToolBroker(settings, store, sap, FakeSkills())
        token = broker.open_session("run")
        for tool in ("sap_query_validate", "sap_query_execute"):
            result = await broker.handle("run", token, tool, {"plan": {"http_method": "GET", "order_by": [{"field": "CompanyCode", "direction": "asc"}]}})
            assert result["ok"] is True and result["normalization_diagnostics"]
        assert seen and all(plan["order_by"] == ["CompanyCode"] for plan in seen)
    asyncio.run(run())


def test_schema_transient_retries_do_not_open_permanent_circuit(tmp_path):
    async def run():
        settings = _settings(tmp_path)
        store = RunStore(settings.database_path)
        store.create_run("run", RunCreate(mode=RunMode.free_query, query="test"))
        sap = FakeSapRead()
        attempts = []
        async def schema(*args, **kwargs):
            attempts.append(1)
            if len(attempts) <= 3:
                raise SapReadError("timeout", code="sap_read_timeout")
            return {"ok": True, "data": {"fields": []}}
        sap.schema = schema
        broker = HarnessToolBroker(settings, store, sap, FakeSkills())
        token = broker.open_session("run")
        arguments = {"service_name": "API_TEST", "odata_version": "2.0", "entity_sets": ["Entity"]}
        for attempt in range(4):
            result = await broker.handle("run", token, "sap_schema_get", {**arguments, "tool_call_id": f"call_{attempt}"})
        assert result["ok"] is True and len(attempts) == 4
        assert len(store.list_harness_tool_calls("run")) == 4
    asyncio.run(run())


def test_restricted_275_rows_are_not_empty_and_deadlines_are_monotonic(tmp_path, monkeypatch):
    async def run():
        settings = _settings(tmp_path)
        store = RunStore(settings.database_path)
        store.create_run("run", RunCreate(mode=RunMode.free_query, query="test"))
        broker = HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
        token = broker.open_session("run")
        store.update_harness_state("run", {"time_budget": {"hard_limit_seconds": 600, "query_seconds_granted": 540, "finalization_seconds_reserved": 60}})
        restricted = broker._save_evidence("run", "sap_skill", {"rows": [], "row_count": 275, "rows_redacted": True, "source_complete": True})
        empty = broker._save_evidence("run", "sap_live", {"rows": [], "row_count": 0, "source_complete": True})
        result = await broker.handle("run", token, "sap_evidence_read", {"evidence_ref": restricted})
        assert result["ok"] is False and result["code"] == "evidence_rows_restricted"
        assert result["row_count"] == 275 and "rows" not in result
        assert (await broker.handle("run", token, "sap_evidence_read", {"evidence_ref": empty}))["row_count"] == 0
        assert broker._elapsed_seconds("run") == 0  # queued time excluded
        clock = [1000.0]
        monkeypatch.setattr("sap_business_agents_platform.harness.time.monotonic", lambda: clock[0])
        store.update_run("run", started_at=datetime.now(timezone.utc).isoformat())
        broker._elapsed_seconds("run")
        clock[0] += 540
        assert broker.review_deadline("run")["deadline_phase"] == "finalizing"
        assessed = broker._assess_evidence("run", {"evidence_refs": [empty], "missing_evidence": ["missing"]})
        assert assessed["gap_token"] is None
        replay = broker._completed_tool_replay("run", "sap_evidence_assess", {"ok": True, "gap_token": "sha256:fixture", "skill_eligible": True})
        assert replay["gap_token"] is None and replay["skill_eligible"] is False
        with pytest.raises(Exception) as error:
            await broker._execute_skill("run", {"gap_token": "old-token"})
        assert error.value.code == "harness_finalization_only"
        clock[0] += 60
        assert broker.review_deadline("run")["deadline_phase"] == "deadline_exceeded"
        with pytest.raises(Exception) as error:
            broker._save_evidence("run", "sap_live", {"rows": ["late"]})
        assert error.value.code == "harness_deadline_exceeded"
    asyncio.run(run())


def test_prompt_uses_snapshot_budget():
    prompt = _developer_instructions(budget={"hard_limit_seconds": 600, "finalization_seconds_reserved": 60})
    assert "540 seconds" in prompt and "600 seconds" in prompt and "last 60 seconds" in prompt
    assert "30-minute" not in prompt and "two schema timeouts" not in prompt


def test_metadata_timeouts_do_not_become_field_absence_in_harness(tmp_path):
    def handler(request):
        raise httpx.ReadTimeout("fixture", request=request)
    provider = EmbeddedODataProvider(base_url="https://sap.example.test", username="test", password="test",
        service_registry_path=registry(tmp_path), transport=httpx.MockTransport(handler))
    async def run():
        with provider.metadata_scope("run"):
            result = await provider.validate_plan({"service_name": "API_V4_TEST", "odata_version": "4.0", "entity_set": "Products", "http_method": "GET", "select_fields": ["ID"]})
        codes = [item["code"] for item in result["validation_issues"]]
        assert "sap_read_timeout" in codes
        assert "schema_drift_field_unavailable" not in codes
    asyncio.run(run())


def test_late_tool_cannot_save_evidence_after_cancel(tmp_path):
    async def run():
        settings = _settings(tmp_path)
        store = RunStore(settings.database_path)
        store.create_run("run", RunCreate(mode=RunMode.free_query, query="test"))
        sap = FakeSapRead()
        ready = asyncio.Event()
        async def execute(*args, **kwargs):
            ready.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {"ok": True, "rows": [{"late": True}], "source_complete": True}
        sap.execute_plan = execute
        broker = HarnessToolBroker(settings, store, sap, FakeSkills())
        token = broker.open_session("run")
        task = asyncio.create_task(broker.handle("run", token, "sap_query_execute", {"plan": {"http_method": "GET"}}))
        await ready.wait()
        assert await broker.cancel_tools("run", timeout=0.1)
        result = await task
        assert result["code"] == "harness_run_closed"
        assert not (settings.data_root / "harness/run/evidence").exists()
    asyncio.run(run())


def test_sdk_cleanup_has_bounded_window():
    from sap_business_agents_platform.runtime_execution import owned_client, RuntimeExecutionError
    class Client:
        async def __aenter__(self):
            return self
        async def close(self):
            await asyncio.Event().wait()
    async def run():
        with pytest.raises(RuntimeExecutionError, match="runtime_cleanup_incomplete"):
            async with owned_client(Client(), cleanup_timeout=0.01):
                pass
    asyncio.run(run())


def test_timeout_classification_keeps_latest_tool_diagnosis(tmp_path):
    from sap_business_agents_platform.agent_acceptance_campaigns import AcceptanceCampaignOperationalError
    async def run():
        settings = _settings(tmp_path)
        store = RunStore(settings.database_path)
        campaign, _operation = _campaign(store, tmp_path)
        store.create_run("run", RunCreate(mode=RunMode.free_query, query="test"))
        store.update_run("run", status="running", started_at=(datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat())
        broker = HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
        broker.open_session("run")
        store.update_harness_state("run", {"time_budget": {"hard_limit_seconds": 600, "query_seconds_granted": 540, "finalization_seconds_reserved": 60}})
        coordinator = SimpleNamespace(harness=SimpleNamespace(broker=broker))
        jobs = AgentAcceptanceJobs(settings, store, None, coordinator, None)
        with pytest.raises(AcceptanceCampaignOperationalError) as error:
            await jobs._wait_run(campaign["draft_id"], campaign["campaign_id"], "run", 600)
        assert error.value.code == "agent_acceptance_stage_timeout"
        assert error.value.failure_category == "environment"
        assert error.value.diagnostics["tool_call_count"] == 0
    asyncio.run(run())


def test_cleanup_failure_retains_operation_and_safe_diagnostics(tmp_path):
    async def run():
        store = RunStore(tmp_path / "state.sqlite3")
        campaign, operation = _campaign(store, tmp_path)
        store.create_run("run", RunCreate(mode=RunMode.free_query, query="test"))
        store.begin_harness_tool_call(call_id="fail", run_id="run", tool_name="sap_evidence_read", request_hash="hash", safe_input={})
        store.complete_harness_tool_call("fail", status="failed", output={"ok": False, "code": "evidence_rows_restricted", "message": "secret body", "rows": ["private"]})
        coordinator = _Coordinator()
        coordinator.cancel_acceptance_run = AsyncMock(return_value=False)
        jobs = AgentAcceptanceJobs(SimpleNamespace(data_root=tmp_path), store, None, coordinator, _Runtime())
        jobs.active_runs[campaign["campaign_id"]] = "run"
        await jobs._finish_not_tested(campaign["draft_id"], campaign["campaign_id"], operation["operation_id"], "interrupted", "agent_acceptance_stage_timeout")
        current = jobs.get(campaign["draft_id"], campaign["campaign_id"])
        assert current["phase"] == "cleanup" and current["active"]
        assert store.get_agent_operation(campaign["draft_id"])
        assert current["report"]["verdict"] == "NOT_TESTED"
        assert current["report"]["diagnostics"]["last_error_code"] == "evidence_rows_restricted"
        assert "secret body" not in json.dumps(current["report"])
        assert "private" not in json.dumps(current["report"])
    asyncio.run(run())


def test_skill_anchor_coverage_stops_before_any_external_replay(tmp_path):
    async def run():
        sap = SimpleNamespace(validate_plan=AsyncMock(), execute_plan=AsyncMock())
        coordinator = SimpleNamespace(sap_read=sap, harness=SimpleNamespace(broker=SimpleNamespace(_read_evidence=lambda *_: ({}, {}))))
        jobs = AgentAcceptanceJobs(None, None, None, coordinator, None)
        value = await jobs._capture_source_anchor_within_deadline("draft", "campaign", "run", {"result": {"tool_calls": [
            {"tool": "sap_query_execute", "evidence_ref": "ev_first"},
            {"tool": "sap_skill_execute", "evidence_ref": "ev_second"},
        ]}}, position="before")
        assert value["reason_code"] == "source_anchor_coverage_missing"
        sap.validate_plan.assert_not_awaited()
        sap.execute_plan.assert_not_awaited()
    asyncio.run(run())


def test_safe_diagnostics_do_not_call_repaired_query_errors_unresolved(tmp_path):
    from sap_business_agents_platform.harness_diagnostics import run_diagnostics
    store = RunStore(tmp_path / "state.sqlite3")
    store.create_run("run", RunCreate(mode=RunMode.free_query, query="test"))
    def record(call_id, tool, service, output):
        store.begin_harness_tool_call(call_id=call_id, run_id="run", tool_name=tool,
            request_hash=call_id, safe_input={"plan": {"service_name": service, "entity_set": "Entity", "odata_version": "2.0"}})
        store.complete_harness_tool_call(call_id, status="failed" if output.get("ok") is False else "completed", output=output)
    record("failure", "sap_query_validate", "API_A", {"ok": False, "validation_issues": [{"code": "schema_field_not_sortable", "value": "secret"}]})
    record("other", "sap_query_validate", "API_B", {"ok": True})
    assert run_diagnostics(store, "run")["unresolved_issues"]
    record("repaired", "sap_query_validate", "API_A", {"ok": True})
    diagnostic = run_diagnostics(store, "run")
    assert diagnostic["last_error_code"] == "schema_field_not_sortable"
    assert diagnostic["unresolved_issues"] == []
    assert "secret" not in json.dumps(diagnostic)
