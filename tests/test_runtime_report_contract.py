"""Shared report contract and convergence: synthetic data, no SDK or SAP."""
import asyncio
import copy
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.harness import HarnessToolBroker, _mcp_overrides, _validated_payload_from_store
from sap_business_agents_platform.models import RunCreate, RunMode, RunPresentation
from sap_business_agents_platform.runtime_report_contract import (
    ReportContractError, contract_file, contract_reference, freeze, load_contract,
    presentation_schema, save_contract, tool_schema, tools_for_contract,
)
from sap_business_agents_platform.runtime_report_convergence import advance, ReportCorrectionStopped


def report(**changes):
    return {"title": {"zh": "离线报告", "en": "Offline report"},
        "blocks": [{"type": "notice", "text": {"zh": "仅合成数据", "en": "Synthetic data only"}}], **changes}


def make_broker(tmp_path, *, acceptance=None, runtime=None, query="Synthetic offline report"):
    settings = Settings(repository_root=Path(__file__).parents[1], data_root=tmp_path / "data",
        draft_root=tmp_path / "drafts", skillhub_root=tmp_path / "skills", internal_api_url="http://127.0.0.1:8765")
    store = RunStore(settings.database_path)
    run = "run_offline_report"
    store.create_run(run, RunCreate(mode=RunMode.free_query, query=query), runtime=runtime)
    if acceptance is not None:
        store.update_harness_state(run, {"acceptance_spec": acceptance})
    class NoSap:
        metadata_scope = None
        clear_schema_scope = None
        def __getattr__(self, name):
            raise AssertionError("SAP must not be called: " + name)
    broker = HarnessToolBroker(settings, store, NoSap(), SimpleNamespace(list_all_approved_skills=lambda: []))
    token = broker.open_session(run)
    return broker, store, run, token


@pytest.mark.parametrize("bad", [None, "invalid", [], {"unexpected": True}])
def test_root_invalid_report_is_not_repaired(tmp_path, bad):
    broker, store, run, token = make_broker(tmp_path)
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": bad}))
    assert result["ok"] is False and result["validation_ref"] is None
    assert result["repair_feedback"]["retry_scope"] == "report_only"
    assert result["validation_issues"] and "validated_report" not in result


@pytest.mark.parametrize("mutation", ["blocks_object", "title_string", "extra_root", "bad_enum", "missing_blocks", "envelope", "oversize", "row_width"])
def test_schema_and_backend_reject_structural_failures(tmp_path, mutation):
    bad = report()
    if mutation == "blocks_object": bad["blocks"] = {"type": "notice"}
    if mutation == "title_string": bad["title"] = "not bilingual"
    if mutation == "extra_root": bad["source_complete"] = True
    if mutation == "bad_enum": bad["blocks"][0]["type"] = "not-a-block"
    if mutation == "missing_blocks": del bad["blocks"]
    if mutation == "envelope": bad = {"status": "completed", "presentation": bad, "input_kind": None}
    if mutation in {"oversize", "row_width"}:
        bad["blocks"] = [{"type": "table", "columns": [{"key": "item", "label": {"zh": "条目", "en": "Item"}}],
            "rows": [{"values": [] if mutation == "row_width" else [{"zh": "1", "en": "1"}]}] * (201 if mutation == "oversize" else 1)}]
    broker, store, run, token = make_broker(tmp_path)
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": bad}))
    assert result["ok"] is False and result["validation_ref"] is None
    if mutation != "row_width":
        assert list(Draft202012Validator(tool_schema()).iter_errors({"report": bad}))
    else:
        assert any(i["constraint"] == "value_error" for i in result["validation_issues"])


def test_legal_minimal_report_and_strict_canonical_terminal(tmp_path):
    broker, store, run, token = make_broker(tmp_path)
    source = report()
    saved = copy.deepcopy(source)
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": source}))
    assert result["ok"] and result["report_validated"] and source == saved
    assert not list(Draft202012Validator(tool_schema()).iter_errors({"report": source}))
    assert result["validated_report"]["validation_ref"] == result["validation_ref"]
    assert not list(Draft202012Validator(presentation_schema(strict=True)).iter_errors(result["validated_report"]))
    assert result["validated_report"]["blocks"][0]["claim_scope"] == "diagnostic"
    assert "_validated_report" not in result


@pytest.mark.parametrize("kind,content", [
    ("text", {"text": {"zh": "摘要", "en": "Summary"}}),
    ("notice", {"text": {"zh": "限制", "en": "Limitation"}}),
    ("key_value", {"entries": [{"label": {"zh": "状态", "en": "Status"}, "value": {"zh": "未知", "en": "Unknown"}}]}),
    ("metrics", {"metrics": [{"id": "offline_count", "label": {"zh": "数量", "en": "Count"}, "value": {"zh": "0", "en": "0"}}]}),
    ("table", {"columns": [{"key": "item", "label": {"zh": "条目", "en": "Item"}}], "rows": []}),
    ("bullet_list", {"items": [{"zh": "建议", "en": "Suggestion"}]}),
])
def test_all_existing_block_types_keep_their_legal_defaults(tmp_path, kind, content):
    broker, store, run, token = make_broker(tmp_path)
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": report(blocks=[{"type": kind, **content}])}))
    assert result["ok"] is True
    Draft202012Validator(presentation_schema(strict=True)).validate(result["validated_report"])


def test_complete_empty_source_remains_distinct_from_missing_evidence(tmp_path):
    broker, store, run, token = make_broker(tmp_path)
    ref = broker._save_evidence(run, "sap_live", {"ok": True, "source_complete": True, "results": []})
    # A saved file alone is not admitted evidence: register the synthetic
    # completed Broker call in this temporary test store, as production does.
    store.begin_harness_tool_call(call_id="synthetic-evidence", run_id=run,
        tool_name="sap_query_execute", request_hash="synthetic-only", safe_input={})
    store.complete_harness_tool_call("synthetic-evidence", status="completed", output={"ok": True,
        "evidence_ref": ref, "source_type": "sap_live", "source_complete": True,
        "claim_scope": "customer_business_fact", "row_count": 0}, evidence_ref=ref)
    source = report(blocks=[{"type": "table", "claim_scope": "customer_business_fact",
        "columns": [{"key": "item", "label": {"zh": "条目", "en": "Item"}}], "rows": [],
        "evidence_refs": [ref], "source_complete": True}])
    before = broker.snapshot(run)[1]
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": source}))
    assert result["ok"] and result["validated_report"]["blocks"][0]["total_rows"] == 0
    assert broker.snapshot(run)[1] == before


def test_ordinary_projection_is_not_registered_and_backend_rejects_even_null(tmp_path):
    assert "acceptance_projection" not in tool_schema()["properties"]
    broker, store, run, token = make_broker(tmp_path)
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": report(), "acceptance_projection": None}))
    assert any(i["code"] == "acceptance_projection_not_requested" for i in result["validation_issues"])
    assert result["validation_ref"] is None


def test_acceptance_schema_and_authorization_share_exact_projection(tmp_path):
    from sap_business_agents_platform.assistant_tools import contract_digest
    broker, store, run, token = make_broker(tmp_path, acceptance={"record_fields": ["item"]})
    contract = broker.report_contract(run)
    assert "acceptance_projection" in contract["input_schema"]["required"]
    state = store.get_harness_state(run)
    assert state["assistant_tool_authorization"]["tool_contracts"]["sap_final_report_validate"] == contract_digest("sap_final_report_validate", contract)
    assert "item" in contract["input_schema"]["properties"]["acceptance_projection"]["properties"]["records"]["items"]["properties"]


def test_precise_feedback_is_value_free(tmp_path):
    broker, store, run, token = make_broker(tmp_path)
    raw_secret = "must-not-leak-secret-or-SAP-value"
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": report(blocks={"private": raw_secret})}))
    issue = next(i for i in result["validation_issues"] if i["path"] == "/report/blocks")
    assert issue["expected"] == "array" and issue["actual_type"] == "object"
    assert raw_secret not in json.dumps(result) and "repair_action" in issue


def test_extra_field_feedback_identifies_parent_and_trusted_fields_without_repair(tmp_path):
    broker, store, run, token = make_broker(tmp_path)
    source = report()
    secret_key, secret_value = "private-business-key", "private-business-value"
    source["blocks"][0][secret_key] = secret_value
    original = copy.deepcopy(source)
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": source}))
    issue = next(i for i in result["validation_issues"] if i["constraint"] == "extra_forbidden")
    assert issue["path"] == "/report/blocks/0/<unknown>"
    assert issue["object_location"] == "/report/blocks/0" and issue["expected"] == "absent"
    assert issue["allowed_properties"] == list(presentation_schema()["properties"]["blocks"]["items"]["properties"])
    assert "Remove undeclared properties" in issue["repair_action"]
    assert secret_key not in json.dumps(result) and secret_value not in json.dumps(result)
    assert source == original and result["validation_ref"] is None and not result["ok"]
    # A model must submit its own new candidate; the platform never strips keys.
    assert not RunPresentation.model_validate(report()).model_dump().get(secret_key)


def test_enum_feedback_returns_only_authoritative_schema_values(tmp_path):
    broker, store, run, token = make_broker(tmp_path)
    source = report()
    source["blocks"][0]["tone"] = "private-invalid-value"
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": source}))
    issue = next(i for i in result["validation_issues"] if i["path"] == "/report/blocks/0/tone")
    assert issue["allowed_values"] == ["neutral", "success", "warning", "error", "info"]
    assert "private-invalid-value" not in json.dumps(result) and result["validation_ref"] is None


def test_known_shape_failure_keeps_actionable_checker_reason_without_content(tmp_path):
    broker, store, run, token = make_broker(tmp_path)
    source = report(blocks=[{"type": "notice", "text": None, "title": {"zh": "private", "en": "private"}}])
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": source}))
    issue = result["validation_issues"][0]
    assert issue["message"] == "presentation block notice has no display content"
    assert "verified evidence" in issue["repair_action"] and "private" not in json.dumps(result)
    assert result["validation_ref"] is None and source["blocks"][0]["text"] is None


def test_invalid_scalar_types_are_not_silently_coerced(tmp_path):
    broker, store, run, token = make_broker(tmp_path)
    invalid = report()
    invalid["blocks"][0].update(display_truncated="false", total_rows="0")
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": invalid}))
    assert not result["ok"] and result["validation_ref"] is None
    assert {i["constraint"] for i in result["validation_issues"]} >= {"bool_type", "int_type"}


def test_tool_catalog_inspection_uses_the_run_specific_contract_digest(tmp_path):
    from sap_business_agents_platform.assistant_tools import contract_digest
    broker, store, run, token = make_broker(tmp_path, acceptance={"record_fields": ["item"]})
    result = asyncio.run(broker.handle(run, token, "tool_catalog_inspect", {"tool_id": "sap_final_report_validate"}))
    assert result["ok"] and result["tool"]["contract_digest"] == contract_digest("sap_final_report_validate", broker.report_contract(run))


def test_unknown_and_incomplete_skill_evidence_still_rejected(tmp_path):
    broker, store, run, token = make_broker(tmp_path)
    bad = report()
    bad["blocks"][0].update(claim_scope="customer_business_fact", evidence_refs=["unknown"])
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": bad}))
    assert result["ok"] is False and any(i["code"] == "unknown_evidence_ref" for i in result["validation_issues"])


def test_three_stagnant_failures_preserve_calls_and_block_late_success(tmp_path):
    broker, store, run, token = make_broker(tmp_path)
    async def scenario():
        results = []
        for index in range(3):
            results.append(await broker.handle(run, token, "sap_final_report_validate", {
                "report": report(title={"zh": str(index), "en": str(index)}, blocks={}), "tool_call_id": "report_" + str(index)}))
        replay = await broker.handle(run, token, "sap_final_report_validate", {
            "report": report(title={"zh": "2", "en": "2"}, blocks={}), "tool_call_id": "report_2"})
        assert replay["idempotent_replay"] is True
        late = await broker.handle(run, token, "sap_final_report_validate", {"report": report()})
        assert late["ok"] is False and late["terminal_validation_failure"] is True
        denied = await broker.handle(run, token, "sap_query_execute", {})
        assert denied["code"] == "report_correction_stalled"
        return results
    results = asyncio.run(scenario())
    assert [r["report_convergence"]["without_progress"] for r in results] == [1, 2, 3]
    assert results[-1]["code"] == "report_correction_stalled"
    assert len(store.list_harness_tool_calls(run)) == 3
    assert _validated_payload_from_store(store, run, []) is None


def test_repair_success_resets_stagnation_and_changed_idempotency_body_conflicts(tmp_path):
    broker, store, run, token = make_broker(tmp_path)
    async def scenario():
        await broker.handle(run, token, "sap_final_report_validate", {"report": report(blocks={}), "tool_call_id": "fixed"})
        replay = await broker.handle(run, token, "sap_final_report_validate", {"report": report(blocks={}), "tool_call_id": "fixed"})
        assert replay["idempotent_replay"]
        conflict = await broker.handle(run, token, "sap_final_report_validate", {"report": report(), "tool_call_id": "fixed"})
        assert conflict["code"] == "harness_tool_call_conflict"
        return await broker.handle(run, token, "sap_final_report_validate", {"report": report()})
    assert asyncio.run(scenario())["ok"]
    assert store.get_harness_state(run)["report_convergence"] is None


def test_concurrent_report_calls_serialize_without_duplicate_count(tmp_path):
    broker, store, run, token = make_broker(tmp_path)
    async def scenario():
        return await asyncio.gather(*(broker.handle(run, token, "sap_final_report_validate", {
            "report": report(blocks={}), "tool_call_id": "same"}) for _ in range(8)))
    results = asyncio.run(scenario())
    assert len(store.list_harness_tool_calls(run)) == 1
    assert store.get_harness_state(run)["report_convergence"]["without_progress"] == 1
    assert sum(r.get("idempotent_replay") is True for r in results) == 7


def test_alternating_errors_cannot_reset_forever_but_first_real_repair_can():
    first = {"code": "acceptance_report_schema_invalid", "path": "/report/title", "constraint": "model_type"}
    second = {**first, "path": "/report/blocks", "constraint": "list_type"}
    state = {}
    for issues in ([first], [second], [first], [second], [first], [second]):
        state = advance(state, issues=issues, acceptance=False, legacy_progress={}, relevant_generation="fixed")
    assert state["terminal"] and state["without_progress"] == 3


def test_new_unrelated_evidence_generations_never_reset_the_same_blocker():
    state = {}
    issue = {"code": "customer_fact_requires_sap_evidence", "path": "/report/blocks/0"}
    for index in range(3):
        state = advance(state, issues=[issue], acceptance=False, legacy_progress={}, relevant_generation=str(index))
    assert state["terminal"] and state["without_progress"] == 3


def test_partial_array_repair_counts_only_a_new_best_result():
    state = {}
    errors = [{"code": "acceptance_report_schema_invalid", "path": "/report/blocks/" + str(i) + "/title", "constraint": "model_type"} for i in range(5)]
    for count in (5, 3, 4, 3):
        state = advance(state, issues=errors[:count], acceptance=False, legacy_progress={}, relevant_generation="fixed")
    assert state["terminal"] and state["without_progress"] == 3


@pytest.mark.parametrize("change", ["schema", "file", "model", "query"])
def test_frozen_contract_change_is_rejected_without_refreezing(tmp_path, change):
    broker, store, run, token = make_broker(tmp_path)
    if change == "file":
        path = contract_file(broker.settings.data_root, run)
        path.write_bytes(path.read_bytes() + b" ")
    elif change == "schema":
        value = store.get_harness_state(run)["report_contract"]
        value["input_schema"]["properties"]["report"] = {"type": "object"}
        store.update_harness_state(run, {"report_contract": value})
    elif change == "model":
        from sap_business_agents_platform.models import RuntimeSnapshot
        store.update_run(run, runtime_json=RuntimeSnapshot(provider_id="codex", model="changed", sdk_id="offline", configuration_digest="changed").model_dump(mode="json"))
    else:
        store.update_run(run, query="changed request")
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": report()}))
    assert result["ok"] is False and result["code"] in {"report_contract_changed", "tool_session_stale"}
    assert not store.list_harness_tool_calls(run)


def test_missing_contract_file_is_not_rebuilt_by_get_or_execution(tmp_path):
    broker, store, run, token = make_broker(tmp_path)
    path = contract_file(broker.settings.data_root, run)
    path.unlink()
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": report()}))
    assert result["code"] == "report_contract_missing" and not path.exists()


def test_codex_mcp_and_workbuddy_receive_same_frozen_contract(tmp_path, monkeypatch):
    from sap_business_agents_platform.mcp_server import _SAP_TOOLS, serve
    from io import StringIO
    broker, store, run, token = make_broker(tmp_path)
    contract = broker.report_contract(run)
    ref = contract_reference(broker.settings.data_root, run)
    monkeypatch.setenv("SAPBA_REPORT_CONTRACT_FILE", ref["path"])
    monkeypatch.setenv("SAPBA_REPORT_CONTRACT_SHA256", ref["sha256"])
    monkeypatch.setenv("SAPBA_REPORT_CONTRACT_ROOT", str(broker.settings.data_root))
    monkeypatch.setenv("SAPBA_HARNESS_RUN_ID", run)
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"))
    output = StringIO()
    monkeypatch.setattr("sys.stdout", output)
    serve("sap")
    codex = json.loads(output.getvalue())["result"]["tools"]
    worker = tools_for_contract(_SAP_TOOLS, contract)
    assert next(t["inputSchema"] for t in codex if t["name"] == "sap_final_report_validate") == next(t["inputSchema"] for t in worker if t["name"] == "sap_final_report_validate")


def test_codex_early_close_and_stalled_stop_use_same_saved_state(tmp_path):
    from sap_business_agents_platform.runtime_harness import report_tool_completed
    broker, store, run, token = make_broker(tmp_path)
    turn = SimpleNamespace(interrupt=AsyncMock())
    owner = SimpleNamespace(store=store, broker=broker)
    async def scenario():
        await broker.handle(run, token, "sap_final_report_validate", {"report": report()})
        payload = await report_tool_completed(owner, run, turn)
        assert payload["presentation"]["validation_ref"]
        for _ in range(3):
            await broker.handle(run, token, "sap_final_report_validate", {"report": report(blocks={})})
        with pytest.raises(ReportCorrectionStopped):
            await report_tool_completed(owner, run, turn)
    asyncio.run(scenario())
    assert turn.interrupt.await_count == 2


@pytest.mark.parametrize("cancelled", [False, True])
def test_closed_or_cancelled_run_does_not_apply_report(tmp_path, cancelled):
    broker, store, run, token = make_broker(tmp_path)
    store.update_run(run, **({"cancel_requested": True} if cancelled else {"status": "inconclusive"}))
    result = asyncio.run(broker.handle(run, token, "sap_final_report_validate", {"report": report()}))
    assert result["ok"] is False and not store.list_harness_tool_calls(run)
