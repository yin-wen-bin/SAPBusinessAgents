from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.engine import _count_free_query_top_bounds
from sap_business_agents_platform.harness import (
    CodexHarnessController,
    HarnessToolBroker,
    _best_effort_interrupt,
    _evidence_sources_complete,
    _effective_missing_evidence,
    _executed_plans_from_calls,
    _latest_assessed_missing_evidence,
    _latest_validated_presentation,
    _custom_tool_kind,
    _developer_instructions,
    _mcp_overrides,
    _plan_business_contract_issue,
    _persistent_harness_counts,
    _public_https_citations,
    _sanitized_codex_env,
    _validated_presentation_snapshot,
    _validated_payload_from_store,
    _validate_internal_api_url,
)
from sap_business_agents_platform.models import (
    LocalizedText,
    PresentationBlock,
    RunCreate,
    RunMode,
    RunPresentation,
)
from sap_business_agents_platform.sap_read.embedded_odata import EmbeddedODataProvider


def test_harness_does_not_guess_adt_stable_keys_and_knows_vbuv_sparse_semantics() -> None:
    instructions = _developer_instructions()

    assert "order_by is optional" in instructions
    assert "never infer a stable key" in instructions
    assert "VBUV is sparse" in instructions
    assert "omit order_by so the Skill resolves the live key" in instructions
    assert "InventoryStockType='01'" in instructions
    assert "sap_inventory_fifo_assess" in instructions


def test_inventory_health_plan_contract_requires_unrestricted_complete_history() -> None:
    stock_plan = {
        "entity_set": "A_MatlStkInAcctMod",
        "filters": [
            {"field": "Material", "operator": "eq", "value": "FG129"},
            {"field": "Plant", "operator": "eq", "value": "1710"},
            {"field": "StorageLocation", "operator": "eq", "value": "171A"},
        ],
    }
    issue = _plan_business_contract_issue("检查库存健康和 FIFO 账龄", stock_plan)
    assert issue and issue["code"] == "inventory_unrestricted_scope_required"

    movement_plan = {
        "entity_set": "A_MaterialDocumentItem",
        "filters": [
            {"field": "Material", "operator": "eq", "value": "FG129"},
            {"field": "Plant", "operator": "eq", "value": "1710"},
            {"field": "StorageLocation", "operator": "eq", "value": "171A"},
            {"field": "InventoryStockType", "operator": "eq", "value": "01"},
            {"field": "InventorySpecialStockType", "operator": "eq", "value": ""},
            {"field": "MaterialDocumentYear", "operator": "ge", "value": "2024"},
        ],
        "select_fields": [
            "MaterialDocumentYear",
            "MaterialDocument",
            "MaterialDocumentItem",
            "Batch",
            "DebitCreditCode",
            "QuantityInBaseUnit",
            "MaterialBaseUnit",
            "InventoryStockType",
            "InventorySpecialStockType",
        ],
    }
    issue = _plan_business_contract_issue("check FIFO inventory health", movement_plan)
    assert issue and issue["code"] == "inventory_fifo_full_history_required"

    invalid_batch_plan = {
        "entity_set": "Batch",
        "filters": [
            {"field": "Material", "operator": "eq", "value": "FG129"},
            {"field": "BatchIdentifyingPlant", "operator": "eq", "value": "1710"},
        ],
        "select_fields": [
            "Material",
            "BatchIdentifyingPlant",
            "Batch",
            "ShelfLifeExpirationDate",
        ],
    }
    issue = _plan_business_contract_issue("check FIFO inventory health", invalid_batch_plan)
    assert issue and issue["code"] == "inventory_batch_material_scope_required"

    valid_batch_plan = {
        **invalid_batch_plan,
        "filters": [{"field": "Material", "operator": "eq", "value": "FG129"}],
    }
    assert _plan_business_contract_issue("check FIFO inventory health", valid_batch_plan) is None
from sap_business_agents_platform.tool_gateway import (
    ToolAdmissionError,
    ToolAdmissionGateway,
    ToolCandidate,
    _admit_openapi,
)


def test_empty_inconclusive_harness_audit_plan_has_no_top_bound() -> None:
    assert _count_free_query_top_bounds(
        {"kind": "sap_business_agents_harness", "runtime": "codex_app_server", "steps": []}
    ) == 0


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


class InventorySapRead(FakeSapRead):
    async def execute_plan(self, plan, query: str = "", conversation_id: str | None = None):
        del query, conversation_id
        entity = str(plan.get("entity_set") or "")
        if entity == "A_MatlStkInAcctMod":
            rows = [
                {
                    "Material": "FG129",
                    "Plant": "1710",
                    "StorageLocation": "171A",
                    "Batch": "B001",
                    "InventoryStockType": "01",
                    "InventorySpecialStockType": "",
                    "MatlWrhsStkQtyInMatlBaseUnit": "4500",
                    "MaterialBaseUnit": "PC",
                }
            ]
            return {
                "ok": True,
                "source_complete": True,
                "data": {"results": rows, "source_complete": True},
                "step_results": {"step_1": {"results": rows, "source_complete": True}},
            }
        if plan.get("plan_kind") == "multi_step":
            items = [
                {
                    "MaterialDocumentYear": "2024",
                    "MaterialDocument": "1",
                    "MaterialDocumentItem": "1",
                    "Material": "FG129",
                    "Plant": "1710",
                    "StorageLocation": "171A",
                    "Batch": "B001",
                    "InventoryStockType": "01",
                    "InventorySpecialStockType": "",
                    "QuantityInBaseUnit": "4400",
                    "MaterialBaseUnit": "PC",
                    "DebitCreditCode": "S",
                },
                {
                    "MaterialDocumentYear": "2026",
                    "MaterialDocument": "2",
                    "MaterialDocumentItem": "1",
                    "Material": "FG129",
                    "Plant": "1710",
                    "StorageLocation": "171A",
                    "Batch": "B001",
                    "InventoryStockType": "01",
                    "InventorySpecialStockType": "",
                    "QuantityInBaseUnit": "100",
                    "MaterialBaseUnit": "PC",
                    "DebitCreditCode": "S",
                },
            ]
            headers = [
                {
                    "MaterialDocumentYear": "2024",
                    "MaterialDocument": "1",
                    "PostingDate": "2024-03-11",
                    "CreationDate": "2024-03-11",
                    "CreationTime": "PT9H0M0S",
                },
                {
                    "MaterialDocumentYear": "2026",
                    "MaterialDocument": "2",
                    "PostingDate": "2026-08-23",
                    "CreationDate": "2026-08-23",
                    "CreationTime": "PT8H0M0S",
                },
            ]
            return {
                "ok": True,
                "source_complete": True,
                "data": {"results": headers, "source_complete": True},
                "step_results": {
                    "movement_items": {"results": items, "source_complete": True},
                    "movement_headers": {"results": headers, "source_complete": True},
                },
            }
        if entity == "Batch":
            rows = [
                {
                    "Material": "FG129",
                    "BatchIdentifyingPlant": "",
                    "Batch": "B001",
                    "ShelfLifeExpirationDate": None,
                }
            ]
            return {
                "ok": True,
                "source_complete": True,
                "data": {"results": rows, "source_complete": True},
                "step_results": {"step_1": {"results": rows, "source_complete": True}},
            }
        return await super().execute_plan(plan)

class FakeSkills:
    def list(self):
        return [
            {
                "skill_id": skill_id,
                "read_only": True,
                "validated": True,
                "available": True,
            }
            for skill_id in (
                "sap-adt-table-export",
                "sap-production-order-cost-analysis",
                "sap-wbs-object-resolver",
            )
        ]

    def get(self, skill_id: str):
        for skill in self.list():
            if skill["skill_id"] == skill_id:
                return skill
        raise KeyError(skill_id)

    def validate_input(self, skill_id: str, input_payload):
        if input_payload.get("schema_version") != 1:
            raise ValueError("invalid mock skill input")
        if skill_id == "sap-wbs-object-resolver" and set(input_payload) != {
            "schema_version",
            "wbs_external_id",
            "company_code",
        }:
            raise ValueError("invalid mock resolver input")

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


def test_free_query_budget_extends_only_for_validated_progress_then_finalizes(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        settings = replace(
            _settings(tmp_path),
            max_free_query_seconds=1800,
            free_query_initial_seconds=900,
            free_query_extension_seconds=300,
            free_query_finalization_seconds=300,
        )
        store = RunStore(settings.database_path)
        run_id = "run_adaptive_budget"
        store.create_run(
            run_id,
            RunCreate(mode=RunMode.free_query, query="Query SAP evidence"),
        )
        broker = HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
        token = broker.open_session(run_id)
        call_id = "call_valid_plan"
        store.begin_harness_tool_call(
            call_id=call_id,
            run_id=run_id,
            tool_name="sap_query_validate",
            request_hash="validated-plan",
            safe_input={"plan": {"service_name": "API_TEST"}},
        )
        store.complete_harness_tool_call(
            call_id,
            status="completed",
            output={"ok": True},
            evidence_ref=None,
        )
        store.update_run(
            run_id,
            started_at=(datetime.now(timezone.utc) - timedelta(seconds=901)).isoformat(),
        )
        first = broker.review_deadline(run_id)
        assert first["query_seconds_granted"] == 1200
        assert first["extension_count"] == 1
        assert first["deadline_phase"] == "querying"

        store.update_run(
            run_id,
            started_at=(datetime.now(timezone.utc) - timedelta(seconds=1201)).isoformat(),
        )
        second = broker.review_deadline(run_id)
        assert second["deadline_phase"] == "finalizing"
        before_calls = len(store.list_harness_tool_calls(run_id))
        blocked = await broker.handle(
            run_id, token, "sap_catalog_search", {"query": "another source"}
        )
        assert blocked["code"] == "harness_finalization_only"
        assert len(store.list_harness_tool_calls(run_id)) == before_calls

        store.update_run(
            run_id,
            started_at=(datetime.now(timezone.utc) - timedelta(seconds=1801)).isoformat(),
        )
        hard = broker.review_deadline(run_id)
        assert hard["deadline_phase"] == "deadline_exceeded"

    asyncio.run(scenario())


def test_broker_fifo_assessment_reconciles_only_unrestricted_stock(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        store = RunStore(settings.database_path)
        run_id = "run_inventory_fifo"
        store.create_run(
            run_id,
            RunCreate(
                mode=RunMode.free_query,
                query="检查 FG129 库存健康，按 FIFO 计算慢动和呆滞库存",
            ),
        )
        broker = HarnessToolBroker(
            settings, store, InventorySapRead(), FakeSkills()
        )
        token = broker.open_session(run_id)
        exact_filters = [
            {"field": "Material", "operator": "eq", "value": "FG129"},
            {"field": "Plant", "operator": "eq", "value": "1710"},
            {"field": "StorageLocation", "operator": "eq", "value": "171A"},
            {"field": "InventoryStockType", "operator": "eq", "value": "01"},
            {"field": "InventorySpecialStockType", "operator": "eq", "value": ""},
        ]
        stock_plan = {
            "service_name": "API_MATERIAL_STOCK_SRV",
            "odata_version": "2.0",
            "entity_set": "A_MatlStkInAcctMod",
            "http_method": "GET",
            "filters": exact_filters,
            "select_fields": [
                "Material",
                "Plant",
                "StorageLocation",
                "Batch",
                "InventoryStockType",
                "InventorySpecialStockType",
                "MatlWrhsStkQtyInMatlBaseUnit",
                "MaterialBaseUnit",
            ],
            "order_by": ["Material", "Plant", "StorageLocation"],
        }
        stock_initial = await broker.handle(
            run_id,
            token,
            "sap_query_execute",
            {"plan": stock_plan, "query": "Initial unrestricted stock snapshot"},
        )
        movement_plan = {
            "schema_version": "1.0",
            "plan_kind": "multi_step",
            "steps": [
                {
                    "step_id": "movement_items",
                    "service_name": "API_MATERIAL_DOCUMENT_SRV",
                    "odata_version": "2.0",
                    "entity_set": "A_MaterialDocumentItem",
                    "http_method": "GET",
                    "filters": exact_filters,
                    "select_fields": [
                        "MaterialDocumentYear",
                        "MaterialDocument",
                        "MaterialDocumentItem",
                        "Material",
                        "Plant",
                        "StorageLocation",
                        "Batch",
                        "DebitCreditCode",
                        "QuantityInBaseUnit",
                        "MaterialBaseUnit",
                        "InventoryStockType",
                        "InventorySpecialStockType",
                    ],
                    "order_by": [
                        "MaterialDocumentYear",
                        "MaterialDocument",
                        "MaterialDocumentItem",
                    ],
                },
                {
                    "step_id": "movement_headers",
                    "service_name": "API_MATERIAL_DOCUMENT_SRV",
                    "odata_version": "2.0",
                    "entity_set": "A_MaterialDocumentHeader",
                    "http_method": "GET",
                    "filters": [],
                    "filter_from_previous": [
                        {
                            "source_step_id": "movement_items",
                            "field": "MaterialDocumentYear",
                            "source_field": "MaterialDocumentYear",
                        },
                        {
                            "source_step_id": "movement_items",
                            "field": "MaterialDocument",
                            "source_field": "MaterialDocument",
                        },
                    ],
                    "select_fields": [
                        "MaterialDocumentYear",
                        "MaterialDocument",
                        "PostingDate",
                        "CreationDate",
                        "CreationTime",
                    ],
                    "order_by": ["MaterialDocumentYear", "MaterialDocument"],
                },
            ],
        }
        movement = await broker.handle(
            run_id, token, "sap_query_execute", {"plan": movement_plan}
        )
        stock_confirmation = await broker.handle(
            run_id,
            token,
            "sap_query_execute",
            {"plan": stock_plan, "query": "Confirmation unrestricted stock snapshot"},
        )
        batch = await broker.handle(
            run_id,
            token,
            "sap_query_execute",
            {
                "plan": {
                    "service_name": "API_BATCH_SRV",
                    "odata_version": "2.0",
                    "entity_set": "Batch",
                    "http_method": "GET",
                        "filters": [
                            {"field": "Material", "operator": "eq", "value": "FG129"},
                        ],
                    "select_fields": [
                        "Material",
                        "BatchIdentifyingPlant",
                        "Batch",
                        "ShelfLifeExpirationDate",
                    ],
                    "order_by": ["Material", "BatchIdentifyingPlant", "Batch"],
                }
            },
        )
        assessed = await broker.handle(
            run_id,
            token,
            "sap_inventory_fifo_assess",
            {
                "material": "FG129",
                "plant": "1710",
                "storage_location": "171A",
                "snapshot_date": "2026-08-23",
                "slow_moving_days": 90,
                "obsolete_days": 180,
                "expiry_days": 90,
                "stock_initial_evidence_ref": stock_initial["evidence_ref"],
                "stock_confirmation_evidence_ref": stock_confirmation["evidence_ref"],
                "movement_item_evidence_ref": movement["evidence_ref"],
                "movement_header_evidence_ref": movement["evidence_ref"],
                "batch_evidence_ref": batch["evidence_ref"],
            },
        )

        assert assessed["ok"] is True, assessed
        assert assessed["assessment_valid"] is True
        assert assessed["aging_complete"] is True
        assert assessed["business_complete"] is False
        assert assessed["result"]["expiry_evidence_complete"] is False
        assert assessed["result"]["missing_expiry_date_batch_count"] == 1
        assert assessed["result"]["batch_expiry_details"][0]["batch"] == "B001"
        assert assessed["result"]["current_unrestricted_stock"] == "4500"
        assert assessed["result"]["below_threshold_stock_quantity"] == "100"
        assert assessed["result"]["slow_moving_only_stock_quantity"] == "0"
        assert assessed["result"]["obsolete_stock_quantity"] == "4400"
        report = assessed["rule_result"]["business_report"]
        assert report["records"][0]["material"] == "FG129"
        assert report["records"][0]["source_complete"] is True
        assert {item["id"]: item["value"] for item in report["metrics"]}[
            "obsolete_stock_quantity"
        ] == "4400"
        presentation = RunPresentation(
            title=LocalizedText(zh="库存健康", en="Inventory health"),
            blocks=[
                PresentationBlock(
                    type="notice",
                    text=LocalizedText(
                        zh="FIFO 评估有效，但一个批次缺少效期。",
                        en="The FIFO assessment is valid, but one batch lacks an expiration date.",
                    ),
                    claim_scope="customer_business_fact",
                    evidence_refs=[stock_initial["evidence_ref"]],
                )
            ],
        ).model_dump(mode="json")
        validated = await broker.handle(
            run_id, token, "sap_final_report_validate", {"report": presentation}
        )
        assert validated["ok"] is True

    asyncio.run(scenario())


def test_zero_tool_limit_is_unlimited_and_invalid_values_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAPBA_MAX_TOOL_CALLS", "0")
    assert Settings.from_env(tmp_path).max_tool_calls is None
    monkeypatch.setenv("SAPBA_MAX_TOOL_CALLS", "-1")
    with pytest.raises(ValueError, match="non-negative integer"):
        Settings.from_env(tmp_path)
    monkeypatch.setenv("SAPBA_MAX_TOOL_CALLS", "not-a-number")
    with pytest.raises(ValueError, match="non-negative integer"):
        Settings.from_env(tmp_path)


def test_final_report_validation_is_outside_budget_and_updates_phases(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = replace(_settings(tmp_path), max_tool_calls=1)
        store = RunStore(settings.database_path)
        run_id = "run_budget"
        store.create_run(run_id, RunCreate(mode=RunMode.free_query, query="supplier status"))
        store.update_run(run_id, status="planning")
        broker = HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
        token = broker.open_session(run_id)
        catalog = await broker.handle(run_id, token, "sap_catalog_search", {"query": "supplier"})
        assert catalog["ok"] is True
        assert store.get_run(run_id).status.value == "validating"
        blocked = await broker.handle(
            run_id,
            token,
            "sap_schema_get",
            {
                "service_name": "API_GLACCOUNTLINEITEM",
                "odata_version": "2.0",
                "entity_sets": ["GLAccountLineItem"],
            },
        )
        assert blocked["code"] == "harness_tool_limit_reached"
        report = RunPresentation(
            title=LocalizedText(zh="诊断", en="Diagnostic"),
            blocks=[
                PresentationBlock(
                    type="notice",
                    text=LocalizedText(zh="没有客户事实。", en="No customer facts."),
                    claim_scope="diagnostic",
                )
            ],
        ).model_dump(mode="json")
        validated = await broker.handle(
            run_id, token, "sap_final_report_validate", {"report": report}
        )
        assert validated["ok"] is True
        assert validated["validation_ref"].startswith("validation_")
        assert "_validated_report" not in validated
        assert store.get_run(run_id).status.value == "running"
        calls = store.list_harness_tool_calls(run_id)
        assert [item["tool_name"] for item in calls] == [
            "sap_catalog_search",
            "sap_final_report_validate",
        ]
        stored = calls[-1]
        assert "_validated_report" in stored["output"]
        copied_with_drift = RunPresentation.model_validate(report)
        copied_with_drift.validation_ref = validated["validation_ref"]
        copied_with_drift.title.en = "Model changed this after validation"
        recovered = _validated_presentation_snapshot(
            copied_with_drift.validation_ref, calls
        )
        assert recovered is not None
        assert recovered.title.en == "Diagnostic"
        assert recovered.validation_ref == validated["validation_ref"]

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


def test_best_effort_interrupt_does_not_mask_timeout() -> None:
    class MissingTurn:
        async def interrupt(self) -> None:
            error = RuntimeError("thread not found")
            error.code = "-32600"  # type: ignore[attr-defined]
            raise error

    assert asyncio.run(_best_effort_interrupt(MissingTurn())) == "-32600"


def test_timeout_can_recover_latest_immutable_validated_report(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = RunStore(settings.database_path)
    run_id = "run_recover_report"
    store.create_run(run_id, RunCreate(mode=RunMode.free_query, query="status"))
    broker = HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
    token = broker.open_session(run_id)
    report = RunPresentation(
        title=LocalizedText(zh="结果", en="Result"),
        blocks=[
            PresentationBlock(
                type="notice",
                text=LocalizedText(zh="诊断", en="Diagnostic"),
                claim_scope="diagnostic",
            )
        ],
    ).model_dump(mode="json")
    result = asyncio.run(
        broker.handle(run_id, token, "sap_final_report_validate", {"report": report})
    )

    recovered = _latest_validated_presentation(store.list_harness_tool_calls(run_id))

    assert recovered is not None
    assert recovered.validation_ref == result["validation_ref"]
    assert recovered.title.en == "Result"


def test_source_completeness_is_derived_from_executed_sap_evidence() -> None:
    assert _evidence_sources_complete(
        [
            {"source_type": "sap_live", "source_complete": True},
            {"source_type": "sap_live", "source_complete": True},
            {"source_type": "web_reference", "source_complete": False},
        ]
    ) is True
    assert _evidence_sources_complete(
        [{"source_type": "sap_skill", "source_complete": False}]
    ) is False


def test_incomplete_skill_evidence_is_diagnostic_only(tmp_path: Path) -> None:
    class IncompleteSkills(FakeSkills):
        async def execute(self, skill_id: str, input_payload):
            del skill_id, input_payload
            return {
                "ok": False,
                "status": "inconclusive",
                "read_only": True,
                "validated": True,
                "source_complete": False,
                "paging_complete": False,
                "rows": [],
            }

    async def scenario() -> None:
        settings = _settings(tmp_path)
        store = RunStore(settings.database_path)
        run_id = "run_incomplete_skill_scope"
        store.create_run(run_id, RunCreate(mode=RunMode.free_query, query="supplier status"))
        broker = HarnessToolBroker(settings, store, FakeSapRead(), IncompleteSkills())
        token = broker.open_session(run_id)
        plan = {
            "service_name": "API_TEST_SRV",
            "odata_version": "2.0",
            "entity_set": "A_Test",
            "http_method": "GET",
        }
        await broker.handle(run_id, token, "sap_catalog_search", {"query": "supplier"})
        await broker.handle(
            run_id,
            token,
            "sap_schema_get",
            {
                "service_name": "API_TEST_SRV",
                "odata_version": "2.0",
                "entity_sets": ["A_Test"],
            },
        )
        await broker.handle(run_id, token, "sap_query_validate", {"plan": plan})
        executed = await broker.handle(
            run_id,
            token,
            "sap_query_execute",
            {"plan": plan},
        )
        gap = await broker.handle(
            run_id,
            token,
            "sap_evidence_assess",
            {
                "question": "supplier status",
                "evidence_refs": [executed["evidence_ref"]],
                "missing_evidence": ["payment settlement evidence"],
            },
        )
        skill = await broker.handle(
            run_id,
            token,
            "sap_skill_execute",
            {
                "skill_id": "sap-adt-table-export",
                "gap_token": gap["gap_token"],
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
        assert "evidence_ref" in skill, skill
        customer_report = RunPresentation(
            title=LocalizedText(zh="结果", en="Result"),
            blocks=[
                PresentationBlock(
                    type="notice",
                    text=LocalizedText(zh="客户事实", en="Customer fact"),
                    claim_scope="customer_business_fact",
                    evidence_refs=[skill["evidence_ref"]],
                )
            ],
        ).model_dump(mode="json")
        rejected = await broker.handle(
            run_id, token, "sap_final_report_validate", {"report": customer_report}
        )
        assert rejected["ok"] is False
        assert rejected["validation_issues"][0]["code"] == "customer_fact_requires_sap_evidence"

        customer_report["blocks"][0]["claim_scope"] = "diagnostic"
        accepted = await broker.handle(
            run_id, token, "sap_final_report_validate", {"report": customer_report}
        )
        assert accepted["ok"] is True

    asyncio.run(scenario())


def test_validated_report_can_finish_without_waiting_for_turn_teardown(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = RunStore(settings.database_path)
    run_id = "run_early_validated"
    store.create_run(run_id, RunCreate(mode=RunMode.free_query, query="customer items"))
    broker = HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
    token = broker.open_session(run_id)
    executed = asyncio.run(
        broker.handle(
            run_id,
            token,
            "sap_query_execute",
            {
                "plan": {
                    "service_name": "API_TEST_SRV",
                    "odata_version": "2.0",
                    "entity_set": "A_Test",
                    "http_method": "GET",
                }
            },
        )
    )
    assessed = asyncio.run(
        broker.handle(
            run_id,
            token,
            "sap_evidence_assess",
            {
                "question": "historical status",
                "evidence_refs": [executed["evidence_ref"]],
                "missing_evidence": ["historical_dunning_evidence"],
            },
        )
    )
    assert assessed["missing_evidence"] == ["historical_dunning_evidence"]
    report = RunPresentation(
        title=LocalizedText(zh="结果", en="Result"),
        blocks=[
            PresentationBlock(
                type="text",
                text=LocalizedText(zh="已确认应收。", en="Receivables confirmed."),
                claim_scope="customer_business_fact",
                evidence_refs=[executed["evidence_ref"]],
            )
        ],
    ).model_dump(mode="json")
    asyncio.run(
        broker.handle(run_id, token, "sap_final_report_validate", {"report": report})
    )

    raw_calls = store.list_harness_tool_calls(run_id)
    assert _latest_assessed_missing_evidence(raw_calls) == ["historical_dunning_evidence"]
    payload = _validated_payload_from_store(store, run_id, broker.snapshot(run_id)[1])
    assert payload is not None
    assert payload["status"] == "inconclusive"
    assert payload["business_complete"] is False
    assert payload["missing_evidence"] == ["historical_dunning_evidence"]
    assert payload["evidence_refs"] == [executed["evidence_ref"]]
    assert payload["executed_plans"] == _executed_plans_from_calls(raw_calls)
    assert payload["executed_plans"] == [
        {
            "service_name": "API_TEST_SRV",
            "odata_version": "2.0",
            "entity_set": "A_Test",
            "http_method": "GET",
            "evidence_ref": executed["evidence_ref"],
        }
    ]


def test_refined_sap_read_requires_final_gap_reassessment() -> None:
    assessed_gap = {
        "tool_name": "sap_evidence_assess",
        "status": "completed",
        "output": {"missing_evidence": ["mrp_evidence"]},
    }
    refined_read = {
        "tool_name": "sap_query_execute",
        "status": "completed",
        "output": {"source_complete": True},
    }
    cleared_gap = {
        "tool_name": "sap_evidence_assess",
        "status": "completed",
        "output": {"missing_evidence": []},
    }

    assert _effective_missing_evidence(
        ["mrp_evidence"], [assessed_gap, refined_read]
    ) == ["mrp_evidence", "evidence_reassessment_required"]
    assert _effective_missing_evidence(
        ["mrp_evidence"], [assessed_gap, refined_read, cleared_gap]
    ) == []


def test_account_item_plan_requires_declared_transaction_pair_and_rejects_guessed_ledger() -> None:
    question = "List customer transaction-currency amount as of the cutoff"
    missing_pair = _plan_business_contract_issue(
        question,
        {
            "entity_set": "A_OperationalAcctgDocItemCube",
            "select_fields": ["TransactionCurrency"],
            "filters": [],
        },
    )
    assert missing_pair is not None
    assert missing_pair["code"] == "paired_transaction_amount_required"
    assert _plan_business_contract_issue(
        question,
        {
            "entity_set": "A_OperationalAcctgDocItemCube",
            "select_fields": ["AmountInTransactionCurrency", "TransactionCurrency"],
            "filters": [],
        },
    ) is None
    guessed_ledger = _plan_business_contract_issue(
        question,
        {
            "entity_set": "GLAccountLineItem",
            "select_fields": ["AmountInTransactionCurrency", "TransactionCurrency"],
            "filters": [{"field": "Ledger", "operator": "eq", "value": "0L"}],
        },
    )
    assert guessed_ledger is not None
    assert guessed_ledger["code"] == "unverified_ledger_scope_rejected"


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
    presentation = RunPresentation(
        title=LocalizedText(zh="供应商状态", en="Supplier status"),
        blocks=[
            PresentationBlock(
                type="text",
                text=LocalizedText(zh="已找到供应商项目。", en="A supplier item was found."),
                claim_scope="customer_business_fact",
                evidence_refs=[executed["evidence_ref"]],
            )
        ],
    ).model_dump(mode="json")
    validated_report = await broker.handle(
        run_id, token, "sap_final_report_validate", {"report": presentation}
    )
    assert validated_report["ok"] is True
    assert validated_report["report_hash"].startswith("sha256:")
    invalid_presentation = json.loads(json.dumps(presentation))
    invalid_presentation["blocks"][0]["evidence_refs"] = ["ev_000000000000000000000000"]
    invalid_report = await broker.handle(
        run_id, token, "sap_final_report_validate", {"report": invalid_presentation}
    )
    assert invalid_report["ok"] is False
    assert invalid_report["validation_issues"][0]["code"] == "unknown_evidence_ref"

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


def test_broker_issues_input_bound_tokens_for_any_approved_read_only_skill(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        store = RunStore(settings.database_path)
        run_id = "run_generic_skill"
        store.create_run(
            run_id,
            RunCreate(mode=RunMode.free_query, query="production order cost variance"),
        )
        broker = HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
        token = broker.open_session(run_id)
        await broker.handle(run_id, token, "sap_catalog_search", {"query": "production cost"})
        await broker.handle(
            run_id,
            token,
            "sap_schema_get",
            {
                "service_name": "API_TEST_SRV",
                "odata_version": "2.0",
                "entity_sets": ["A_Test"],
            },
        )
        plan = {
            "service_name": "API_TEST_SRV",
            "odata_version": "2.0",
            "entity_set": "A_Test",
            "http_method": "GET",
        }
        await broker.handle(run_id, token, "sap_query_validate", {"plan": plan})
        executed = await broker.handle(run_id, token, "sap_query_execute", {"plan": plan})
        skill_input = {
            "schema_version": 1,
            "manufacturing_order": "1001233",
            "fiscal_year": "2020",
            "period": 11,
            "target_cost_variant": 1,
        }
        assessed = await broker.handle(
            run_id,
            token,
            "sap_evidence_assess",
            {
                "question": "production order cost variance",
                "evidence_refs": [executed["evidence_ref"]],
                "missing_evidence": ["plan target and actual production costs"],
                "skill_id": "sap-production-order-cost-analysis",
                "skill_input": skill_input,
            },
        )
        assert assessed["skill_eligible"] is True
        assert assessed["adt_eligible"] is False
        assert assessed["skill_id"] == "sap-production-order-cost-analysis"
        assert assessed["skill_input_hash"].startswith("sha256:")
        stored_assessment = next(
            call
            for call in store.list_harness_tool_calls(run_id)
            if call["tool_name"] == "sap_evidence_assess"
            and (call.get("safe_input") or {}).get("question")
            == "production order cost variance"
        )
        assert stored_assessment["output"]["gap_token"].startswith("sha256:")
        assert assessed["gap_token"] not in json.dumps(stored_assessment)

        missing_input = await broker.handle(
            run_id,
            token,
            "sap_evidence_assess",
            {
                "question": "missing Skill input",
                "evidence_refs": [executed["evidence_ref"]],
                "missing_evidence": ["cost evidence without a bound input"],
                "skill_id": "sap-production-order-cost-analysis",
            },
        )
        assert missing_input["code"] == "skill_input_required"
        unapproved = await broker.handle(
            run_id,
            token,
            "sap_evidence_assess",
            {
                "question": "unapproved Skill",
                "evidence_refs": [executed["evidence_ref"]],
                "missing_evidence": ["unapproved evidence"],
                "skill_id": "unapproved-skill",
                "skill_input": {"schema_version": 1},
            },
        )
        assert unapproved["code"] == "skill_not_approved"

        other_run_id = "run_generic_skill_other"
        store.create_run(
            other_run_id,
            RunCreate(mode=RunMode.free_query, query="other run"),
        )
        other_token = broker.open_session(other_run_id)
        cross_run = await broker.handle(
            other_run_id,
            other_token,
            "sap_skill_execute",
            {
                "skill_id": "sap-production-order-cost-analysis",
                "gap_token": assessed["gap_token"],
                "input": skill_input,
            },
        )
        assert cross_run["code"] == "gap_token_invalid"

        mismatched = await broker.handle(
            run_id,
            token,
            "sap_skill_execute",
            {
                "skill_id": "sap-production-order-cost-analysis",
                "gap_token": assessed["gap_token"],
                "input": {**skill_input, "period": 12},
            },
        )
        assert mismatched["code"] == "gap_token_input_mismatch"
        wrong_skill = await broker.handle(
            run_id,
            token,
            "sap_skill_execute",
            {
                "skill_id": "sap-adt-table-export",
                "gap_token": assessed["gap_token"],
                "input": skill_input,
            },
        )
        assert wrong_skill["code"] == "gap_token_invalid"
        completed = await broker.handle(
            run_id,
            token,
            "sap_skill_execute",
            {
                "skill_id": "sap-production-order-cost-analysis",
                "gap_token": assessed["gap_token"],
                "input": skill_input,
            },
        )
        assert completed["source_type"] == "sap_skill"
        reused = await broker.handle(
            run_id,
            token,
            "sap_skill_execute",
            {
                "skill_id": "sap-production-order-cost-analysis",
                "gap_token": assessed["gap_token"],
                "input": skill_input,
            },
        )
        assert reused["idempotent_replay"] is True

        pending = await broker.handle(
            run_id,
            token,
            "sap_evidence_assess",
            {
                "question": "production order cost variance after restart",
                "evidence_refs": [executed["evidence_ref"]],
                "missing_evidence": ["restart-safe cost evidence"],
                "skill_id": "sap-production-order-cost-analysis",
                "skill_input": skill_input,
            },
        )
        broker.close_session(run_id)
        recovered = HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
        recovered_token = recovered.open_session(run_id)
        recovered_skill = await recovered.handle(
            run_id,
            recovered_token,
            "sap_skill_execute",
            {
                "skill_id": "sap-production-order-cost-analysis",
                "gap_token": pending["gap_token"],
                "input": skill_input,
            },
        )
        assert recovered_skill["source_type"] == "sap_skill"

    asyncio.run(scenario())


def test_dynamic_gateway_only_runs_admitted_pure_compute() -> None:
    asyncio.run(_dynamic_gateway_scenario())


def test_broker_binds_wbs_resolver_token_to_exact_skill_and_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        store = RunStore(settings.database_path)
        run_id = "run_wbs_resolver"
        store.create_run(run_id, RunCreate(mode=RunMode.free_query, query="resolve WBS"))
        broker = HarnessToolBroker(settings, store, FakeSapRead(), FakeSkills())
        token = broker.open_session(run_id)
        await broker.handle(run_id, token, "sap_catalog_search", {"query": "WBS"})
        await broker.handle(
            run_id,
            token,
            "sap_schema_get",
            {"service_name": "API_TEST_SRV", "odata_version": "2.0", "entity_sets": ["A_Test"]},
        )
        plan = {"service_name": "API_TEST_SRV", "odata_version": "2.0", "entity_set": "A_Test", "http_method": "GET"}
        await broker.handle(run_id, token, "sap_query_validate", {"plan": plan})
        executed = await broker.handle(run_id, token, "sap_query_execute", {"plan": plan})
        resolver_input = {"schema_version": 1, "wbs_external_id": "P-100.01", "company_code": "1710"}
        assessed = await broker.handle(
            run_id,
            token,
            "sap_evidence_assess",
            {
                "question": "resolve one WBS",
                "evidence_refs": [executed["evidence_ref"]],
                "missing_evidence": ["authoritative WBS relationship"],
                "skill_id": "sap-wbs-object-resolver",
                "skill_input": resolver_input,
            },
        )
        assert assessed["skill_eligible"] is True
        changed = await broker.handle(
            run_id,
            token,
            "sap_skill_execute",
            {"skill_id": "sap-wbs-object-resolver", "gap_token": assessed["gap_token"], "input": {**resolver_input, "company_code": "1010"}},
        )
        assert changed["code"] == "gap_token_input_mismatch"
        wrong_skill = await broker.handle(
            run_id,
            token,
            "sap_skill_execute",
            {"skill_id": "sap-production-order-cost-analysis", "gap_token": assessed["gap_token"], "input": resolver_input},
        )
        assert wrong_skill["code"] == "gap_token_invalid"
        completed = await broker.handle(
            run_id,
            token,
            "sap_skill_execute",
            {"skill_id": "sap-wbs-object-resolver", "gap_token": assessed["gap_token"], "input": resolver_input},
        )
        assert completed["source_type"] == "sap_skill"
        replay = await broker.handle(
            run_id,
            token,
            "sap_skill_execute",
            {"skill_id": "sap-wbs-object-resolver", "gap_token": assessed["gap_token"], "input": resolver_input},
        )
        assert replay["idempotent_replay"] is True

    asyncio.run(scenario())


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
        '[mcp_servers.unapproved_external_runtime]\ncommand="external"\n', encoding="utf-8"
    )
    settings = _settings(tmp_path)
    with patch("sap_business_agents_platform.harness.Path.home", return_value=tmp_path):
        overrides = _mcp_overrides(settings, "run", "cap", sys.executable)
    assert "mcp_servers.unapproved_external_runtime.enabled=false" in overrides
    assert any(item.startswith("mcp_servers.sap_business_agents.command=") for item in overrides)
    assert not any("SAP_PASSWORD" in item for item in overrides)
    assert any(
        item == 'mcp_servers.sap_business_agents.env.PYTHONUTF8="1"'
        for item in overrides
    )
    assert any(
        item == 'mcp_servers.sap_business_agents.env.PYTHONIOENCODING="utf-8"'
        for item in overrides
    )

    monkeypatch.setenv("SAP_PASSWORD", "secret")
    monkeypatch.setenv("SAP_ADT_TOKEN", "secret")
    sanitized = _sanitized_codex_env()
    assert sanitized["SAP_PASSWORD"] == ""
    assert sanitized["SAP_ADT_TOKEN"] == ""
    assert sanitized["PYTHONUTF8"] == "1"
    assert sanitized["PYTHONIOENCODING"] == "utf-8"


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
