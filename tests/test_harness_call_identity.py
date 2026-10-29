"""Run-scoped invocation identity, using only synthetic SAP responses."""
import asyncio
import copy
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.harness import HarnessToolBroker
from sap_business_agents_platform.models import RunCreate, RunMode
from tests.test_harness import FakeSapRead, FakeSkills, _settings
from tests.test_runtime_report_contract import report


@pytest.fixture
def setup_broker(tmp_path):
    settings = _settings(tmp_path)
    store = RunStore(settings.database_path)
    sap = FakeSapRead()
    sap.execute_plan = AsyncMock(wraps=sap.execute_plan)
    sap.catalog = AsyncMock(wraps=sap.catalog)
    broker = HarnessToolBroker(settings, store, sap, FakeSkills())
    return broker, store, sap


def open_run(broker, store, run):
    store.create_run(run, RunCreate(mode=RunMode.free_query, query="Synthetic query"))
    return broker.open_session(run)


def arguments(tool):
    if tool == "sap_catalog_search":
        return {"query": "supplier"}
    if tool == "sap_final_report_validate":
        return {"report": report()}
    return {"plan": {"service_name": "S", "odata_version": "2.0", "entity_set": "Items"}}


@pytest.mark.parametrize("tool", [
    "sap_query_execute", "sap_query_validate", "sap_catalog_search", "sap_final_report_validate",
])
@pytest.mark.parametrize("same_body", [True, False])
def test_different_runs_can_reuse_invocation_id(setup_broker, tool, same_body):
    broker, store, sap = setup_broker

    async def scenario():
        outputs = []
        for index in range(2):
            run = f"run_{index}"
            token = open_run(broker, store, run)
            body = arguments(tool)
            if not same_body:
                if tool == "sap_final_report_validate":
                    body["report"]["title"] = {"zh": str(index), "en": str(index)}
                else:
                    body["query"] = f"Synthetic query {index}"
            outputs.append(await broker.handle(run, token, tool, {
                **body, "tool_call_id": "ap-open-items-17300003-20221231"}))
        assert all(output["ok"] for output in outputs)
        assert all(not output.get("idempotent_replay") for output in outputs)
        first, second = (store.list_harness_tool_calls(f"run_{i}")[0] for i in range(2))
        assert first["call_id"] != second["call_id"]
        assert first["status"] == second["status"] == "completed"
        if tool == "sap_query_execute":
            assert sap.execute_plan.await_count == 2
            for index, output in enumerate(outputs):
                evidence = broker.settings.data_root / "harness" / f"run_{index}" / "evidence"
                assert (evidence / f"{output['evidence_ref']}.json").is_file()

    asyncio.run(scenario())


@pytest.mark.parametrize("tool", ["sap_query_execute", "sap_catalog_search", "sap_final_report_validate"])
def test_same_run_replay_survives_broker_recreation_and_rejects_changed_body(setup_broker, tool):
    broker, store, sap = setup_broker

    async def scenario():
        run = "run_replay"
        token = open_run(broker, store, run)
        body = {**arguments(tool), "tool_call_id": "repeat"}
        first = await broker.handle(run, token, tool, copy.deepcopy(body))
        recovered = HarnessToolBroker(broker.settings, RunStore(store.path), sap, FakeSkills())
        token = recovered.open_session(run)
        replay = await recovered.handle(run, token, tool, copy.deepcopy(body))
        assert replay == {**first, "idempotent_replay": True}
        changed = copy.deepcopy(body)
        if tool == "sap_final_report_validate":
            changed["report"]["title"] = {"zh": "changed", "en": "changed"}
        else:
            changed["query"] = "changed"
        conflict = await recovered.handle(run, token, tool, changed)
        assert conflict["code"] == "harness_tool_call_conflict"
        assert len(store.list_harness_tool_calls(run)) == 1
        if tool == "sap_query_execute":
            assert sap.execute_plan.await_count == 1
        if tool == "sap_catalog_search":
            assert sap.catalog.await_count == 1

    asyncio.run(scenario())


def test_concurrent_retries_execute_once_and_keep_other_runs_independent(setup_broker):
    broker, store, sap = setup_broker

    async def scenario():
        tokens = {run: open_run(broker, store, run) for run in ("run_a", "run_b")}
        body = {**arguments("sap_query_execute"), "tool_call_id": "same"}
        outputs = await asyncio.gather(*(broker.handle(run, tokens[run], "sap_query_execute",
            copy.deepcopy(body)) for run in ("run_a", "run_b") for _ in range(3)))
        assert all(output["ok"] for output in outputs)
        assert sum(bool(output.get("idempotent_replay")) for output in outputs) == 4
        assert sap.execute_plan.await_count == 2
        assert all(len(store.list_harness_tool_calls(run)) == 1 for run in tokens)

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["completed", "failed", "running"])
@pytest.mark.parametrize("tool", ["sap_query_execute", "sap_final_report_validate"])
def test_legacy_ids_replay_in_original_run_without_blocking_new_run(setup_broker, tool, status):
    broker, store, sap = setup_broker

    async def scenario():
        run = "run_legacy"
        token = open_run(broker, store, run)
        body = arguments(tool)
        invocation_id = "ap-open-items-17300003-20221231"
        request_hash = hashlib.sha256(json.dumps(
            {"tool": tool, "arguments": body}, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if tool == "sap_final_report_validate":
            request_hash = hashlib.sha256(f"{request_hash}:{invocation_id}".encode()).hexdigest()
        store.begin_harness_tool_call(call_id=invocation_id, run_id=run, tool_name=tool,
            request_hash=request_hash, safe_input=body)
        old_output = {"ok": status == "completed", "legacy": True}
        if status != "running":
            store.complete_harness_tool_call(invocation_id, status=status, output=old_output)
        original = copy.deepcopy(store.list_harness_tool_calls(run))
        replay = await broker.handle(run, token, tool, {**body, "tool_call_id": invocation_id})
        if status == "running":
            assert replay["code"] == "tool_call_recovery_unknown"
        else:
            assert replay == {**old_output, "idempotent_replay": True}
        assert sap.execute_plan.await_count == 0
        new_run = "run_after_legacy"
        result = await broker.handle(new_run, open_run(broker, store, new_run), tool,
            {**arguments(tool), "tool_call_id": invocation_id})
        assert result["ok"] and not result.get("idempotent_replay")
        assert store.list_harness_tool_calls(run) == original
        assert len(store.list_harness_tool_calls(new_run)) == 1

    asyncio.run(scenario())
