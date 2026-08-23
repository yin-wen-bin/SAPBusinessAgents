from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.engine import _default_presentation, _validate_input
from sap_business_agents_platform.harness import _turn_prompt
from sap_business_agents_platform.manifests import AgentRepository, ManifestError, validate_execution
from sap_business_agents_platform.models import Completeness, RunMode, RunResult
from sap_business_agents_platform.rules import assess_api_evidence, resolve_inventory_health_window
from sap_business_agents_platform.sap_read.embedded_odata import EmbeddedODataProvider


ROOT = Path(__file__).resolve().parents[1]


def _embedded(*rows: dict[str, object], complete: bool = True) -> dict[str, object]:
    return {
        "ok": True,
        "status": "completed",
        "source_complete": complete,
        "source_truncated": not complete,
        "data": {"results": list(rows), "source_complete": complete},
        "step_results": {
            "step_1": {"results": list(rows), "source_complete": complete}
        },
    }


def _movement_history(
    *events: tuple[date, str, str, str, str],
    complete: bool = True,
) -> dict[str, object]:
    """Build (posting date, quantity, direction, batch, HHMMSS) movement evidence."""
    items: list[dict[str, object]] = []
    headers: list[dict[str, object]] = []
    for index, (posting, quantity, direction, batch, creation_time) in enumerate(events, 1):
        year = str(posting.year)
        document = f"{index:010d}"
        items.append(
            {
                "MaterialDocumentYear": year,
                "MaterialDocument": document,
                "MaterialDocumentItem": "0001",
                "Material": "TG10",
                "Plant": "1710",
                "StorageLocation": "171A",
                "Batch": batch,
                "InventoryStockType": "01",
                "InventorySpecialStockType": "",
                "QuantityInBaseUnit": quantity,
                "MaterialBaseUnit": "EA",
                "DebitCreditCode": direction,
                "GoodsMovementType": "101" if direction == "S" else "201",
            }
        )
        headers.append(
            {
                "MaterialDocumentYear": year,
                "MaterialDocument": document,
                "PostingDate": posting.isoformat(),
                "CreationDate": posting.isoformat(),
                "CreationTime": f"PT{int(creation_time[:2])}H{int(creation_time[2:4])}M{int(creation_time[4:])}S",
            }
        )
    return {
        "ok": True,
        "status": "completed",
        "source_complete": complete,
        "source_truncated": not complete,
        "step_results": {
            "movement_date_items": {"results": items, "source_complete": complete},
            "movement_date_headers": {"results": headers, "source_complete": complete},
        },
    }


def _window(**values: object) -> dict[str, object]:
    run_input = {
        "material": "TG10",
        "plant": "1710",
        "storage_location": "171A",
        **values,
    }
    return resolve_inventory_health_window({"run_input": run_input})


def _evaluate(
    *,
    run_input: dict[str, object],
    stock: dict[str, object] | None = None,
    movement: dict[str, object] | None = None,
    batch: dict[str, object] | None = None,
    api_complete: dict[str, bool] | None = None,
) -> dict[str, object]:
    window = resolve_inventory_health_window({"run_input": run_input})
    requested = {
        "stock": True,
        "movement": window["movement_check_requested"],
        "batch_expiry": window["check_expiry"],
    }
    stock_payload = stock if stock is not None else _embedded()
    return evaluate_business_agent(
        {
            "agent_id": "inventory-health-balancing",
            "run_input": run_input,
            "window": window,
            "assessment": {
                "api_complete": api_complete
                or {"stock": True, "movement": True, "batch_expiry": True},
                "needs_adt": {"stock": False, "movement": False, "batch_expiry": False},
            },
            "evidence": {
                "stock_initial": stock_payload,
                "stock_confirmation": stock_payload,
                "movement": movement
                if movement is not None
                else {"status": "skipped", "reason": "condition_false", "source_complete": True},
                "batch_expiry": batch
                if batch is not None
                else {"status": "skipped", "reason": "condition_false", "source_complete": True},
            },
            "fallbacks": {},
            "requested": requested,
            "known_gaps": [],
        }
    )


def _stock(*, quantity: str = "100", unit: str = "EA", batch: str = "") -> dict[str, object]:
    return {
        "Material": "TG10",
        "Plant": "1710",
        "StorageLocation": "171A",
        "Batch": batch,
        "InventoryStockType": "01",
        "InventorySpecialStockType": "",
        "MatlWrhsStkQtyInMatlBaseUnit": quantity,
        "MaterialBaseUnit": unit,
    }


def test_window_uses_today_and_enables_only_supplied_checks() -> None:
    window = _window(obsolete_days=365, expiry_days=90)

    assert window["snapshot_date"] == date.today().isoformat()
    assert window["check_slow_moving"] is False
    assert window["check_obsolete"] is True
    assert window["check_expiry"] is True
    assert window["movement_history_required"] is True
    assert window["movement_date_from"] is None
    assert window["movement_history_to"] == date.today().isoformat()
    assert window["movement_years"] == []
    assert window["selected_checks"] == ["obsolete", "expiry"]


def test_input_schema_rejects_invalid_optional_days_and_order() -> None:
    manifest = AgentRepository(ROOT / "agents").get("inventory-health-balancing")
    schema = manifest["execution"]["inputSchema"]
    required = {"material": "TG10", "plant": "1710", "storage_location": "171A"}

    _validate_input(required, schema)
    _validate_input({**required, "slow_moving_days": 180, "obsolete_days": 365}, schema)
    for invalid in (
        {**required, "slow_moving_days": "180"},
        {**required, "slow_moving_days": ""},
        {**required, "slow_moving_days": 0},
        {**required, "slow_moving_days": -1},
        {**required, "slow_moving_days": 366},
        {**required, "slow_moving_days": 180.5},
        {**required, "slow_moving_days": 365, "obsolete_days": 365},
        {**required, "slow_moving_days": 365, "obsolete_days": 180},
    ):
        with pytest.raises(ValueError):
            _validate_input(invalid, schema)


def test_unrequested_evidence_is_complete_without_querying_that_topic() -> None:
    result = assess_api_evidence(
        {
            "checks": {
                "stock": _embedded(_stock()),
                "movement": {"status": "skipped", "reason": "condition_false"},
                "batch_expiry": {"status": "skipped", "reason": "condition_false"},
            },
            "requested": {"stock": True, "movement": False, "batch_expiry": False},
        }
    )

    assert result["status"] == "complete"
    assert result["api_complete"] == {"stock": True, "movement": True, "batch_expiry": True}
    assert result["needs_adt"] == {"stock": False, "movement": False, "batch_expiry": False}


def test_all_optional_checks_blank_returns_snapshot_only() -> None:
    run_input = {"material": "TG10", "plant": "1710", "storage_location": "171A"}
    result = _evaluate(run_input=run_input, stock=_embedded(_stock()))
    output = result["workflow_output"]

    assert output["business_status"] == "snapshot_only"
    assert output["selected_checks"] == []
    assert output["slow_moving_status"] == "not_requested"
    assert output["obsolete_status"] == "not_requested"
    assert output["expiry_status"] == "not_requested"
    assert output["source_complete"] is True
    assert output["evidence_complete"] is True
    assert result["business_report"]["records"][0]["source_complete"] is True
    assert result["business_report"]["records"][0]["evidence_complete"] is True
    stages = {item["id"]: item for item in result["business_report"]["stages"]}
    assert stages["slow_moving"]["state_label"] == {"zh": "未启用", "en": "Not enabled"}
    assert stages["obsolete"]["state_label"] == {"zh": "未启用", "en": "Not enabled"}
    assert stages["expiry"]["state_label"] == {"zh": "未启用", "en": "Not enabled"}
    assert "未执行健康检查" in result["summary"]["zh"]


def test_empty_movement_history_cannot_age_positive_stock() -> None:
    optional_input = {"obsolete_days": 365}
    run_input = {"material": "TG10", "plant": "1710", "storage_location": "171A", **optional_input}
    result = _evaluate(run_input=run_input, stock=_embedded(_stock()), movement=_embedded())
    output = result["workflow_output"]

    assert output["last_movement_date"] is None
    assert output["stock_age_days"] is None
    assert output["stock_age_lower_bound_days"] is None
    assert output["obsolete_status"] == "unknown"
    assert output["aging_complete"] is False
    assert output["business_status"] == "inconclusive"
    assert "aging_reconciliation_gap" in result["missing_evidence"]


def test_obsolete_and_expiry_can_run_without_slow_moving_check() -> None:
    run_input = {
        "material": "TG10", "plant": "1710", "storage_location": "171A",
        "obsolete_days": 365, "expiry_days": 90,
    }
    expiry = date.today() + timedelta(days=30)
    result = _evaluate(
        run_input=run_input,
        stock=_embedded(_stock(batch="B1")),
        movement=_movement_history(
            (date.today() - timedelta(days=400), "100", "S", "B1", "090000")
        ),
        batch=_embedded(
            {
                "Material": "TG10", "BatchIdentifyingPlant": "1710", "Batch": "B1",
                "ShelfLifeExpirationDate": expiry.isoformat(),
            }
        ),
    )
    output = result["workflow_output"]

    assert output["selected_checks"] == ["obsolete", "expiry"]
    assert output["slow_moving_status"] == "not_requested"
    assert output["obsolete_status"] == "candidate"
    assert output["expiry_status"] == "candidate"
    assert output["expiry_candidate_count"] == 1


def test_expiry_ignores_batch_master_rows_without_current_positive_batch_stock() -> None:
    run_input = {
        "material": "TG10", "plant": "1710", "storage_location": "171A",
        "expiry_days": 90,
    }
    result = _evaluate(
        run_input=run_input,
        stock=_embedded(_stock(batch="")),
        batch=_embedded(
            {
                "Material": "TG10",
                "BatchIdentifyingPlant": "1710",
                "Batch": "B-WITHOUT-STOCK",
                "ShelfLifeExpirationDate": (date.today() + timedelta(days=10)).isoformat(),
            }
        ),
    )

    assert result["workflow_output"]["expiry_status"] == "not_candidate"
    assert result["workflow_output"]["expiry_candidate_count"] == 0
    assert result["workflow_output"]["evidence_complete"] is True


def test_positive_stock_batch_without_expiry_is_unknown() -> None:
    result = _evaluate(
        run_input={
            "material": "TG10",
            "plant": "1710",
            "storage_location": "171A",
            "expiry_days": 90,
        },
        stock=_embedded(_stock(batch="B1")),
        batch=_embedded(
            {"Material": "TG10", "BatchIdentifyingPlant": "1710", "Batch": "B1"}
        ),
    )

    assert result["workflow_output"]["expiry_status"] == "unknown"
    assert result["workflow_output"]["business_status"] == "inconclusive"
    assert result["workflow_output"]["evidence_complete"] is False


def test_complete_empty_stock_result_is_no_stock() -> None:
    result = _evaluate(
        run_input={"material": "TG10", "plant": "1710", "storage_location": "171A"},
        stock=_embedded(),
    )

    assert result["workflow_output"]["business_status"] == "no_stock"
    assert result["workflow_output"]["current_unrestricted_stock"] == "0"
    assert result["workflow_output"]["source_complete"] is True
    assert result["workflow_output"]["evidence_complete"] is True


def test_all_checks_use_fifo_remaining_layer_age() -> None:
    run_input = {
        "material": "TG10", "plant": "1710", "storage_location": "171A",
        "slow_moving_days": 180, "obsolete_days": 365, "expiry_days": 90,
    }
    posting = date.today() - timedelta(days=200)
    expiry = date.today() + timedelta(days=60)
    result = _evaluate(
        run_input=run_input,
        stock=_embedded(_stock(batch="B1")),
        movement=_movement_history((posting, "100", "S", "B1", "090000")),
        batch=_embedded({"Material": "TG10", "BatchIdentifyingPlant": "1710", "Batch": "B1", "ShelfLifeExpirationDate": expiry.isoformat()}),
    )
    output = result["workflow_output"]

    assert output["stock_age_days"] == 200
    assert output["stock_age_lower_bound_days"] is None
    assert output["aging_method"] == "fifo_movement_layers"
    assert output["aging_complete"] is True
    assert output["oldest_remaining_layer_age_days"] == 200
    assert output["slow_moving_status"] == "candidate"
    assert output["obsolete_status"] == "not_candidate"
    assert output["expiry_status"] == "candidate"
    assert output["business_status"] == "attention"


def test_incomplete_enabled_topic_and_mixed_units_are_inconclusive() -> None:
    run_input = {
        "material": "TG10", "plant": "1710", "storage_location": "171A",
        "slow_moving_days": 180,
    }
    incomplete = _evaluate(
        run_input=run_input,
        stock=_embedded(_stock()),
        movement=_embedded(complete=False),
        api_complete={"stock": True, "movement": False, "batch_expiry": True},
    )
    assert incomplete["workflow_output"]["business_status"] == "inconclusive"
    assert incomplete["workflow_output"]["source_complete"] is False

    mixed = _evaluate(
        run_input={"material": "TG10", "plant": "1710", "storage_location": "171A"},
        stock=_embedded(_stock(quantity="10", unit="EA"), _stock(quantity="2", unit="KG")),
    )
    assert mixed["workflow_output"]["current_unrestricted_stock"] is None
    assert mixed["workflow_output"]["business_status"] == "inconclusive"
    assert mixed["workflow_output"]["source_complete"] is True
    assert mixed["workflow_output"]["evidence_complete"] is False


def test_fifo_small_recent_receipt_does_not_reset_old_inventory_age() -> None:
    run_input = {
        "material": "TG10",
        "plant": "1710",
        "storage_location": "171A",
        "slow_moving_days": 90,
        "obsolete_days": 180,
        "expiry_days": 90,
    }
    old = date.today() - timedelta(days=365)
    result = _evaluate(
        run_input=run_input,
        stock=_embedded(_stock(quantity="4500")),
        movement=_movement_history(
            (old, "5000", "S", "", "090000"),
            (old + timedelta(days=1), "600", "H", "", "100000"),
            (date.today(), "100", "S", "", "080000"),
        ),
        batch=_embedded(),
    )
    output = result["workflow_output"]
    buckets = {row["bucket_id"]: row["quantity"] for row in output["aging_buckets"]}

    assert output["current_unrestricted_stock"] == "4500"
    assert output["last_movement_activity_date"] == date.today().isoformat()
    assert output["days_since_last_movement_activity"] == 0
    assert output["below_threshold_stock_quantity"] == "100"
    assert output["slow_moving_only_stock_quantity"] == "0"
    assert output["obsolete_stock_quantity"] == "4400"
    assert buckets == {
        "below_slow_moving": "100",
        "slow_moving_only": "0",
        "obsolete": "4400",
    }
    assert output["slow_moving_status"] == "candidate"
    assert output["obsolete_status"] == "candidate"
    assert output["aging_complete"] is True
    metrics = {
        item["id"]: item["value"]
        for item in result["business_report"]["metrics"]
    }
    assert metrics["days_since_last_movement_activity"] == 0
    assert metrics["oldest_remaining_layer_age_days"] == 365


def test_fifo_balance_mismatch_is_inconclusive_not_zero_risk() -> None:
    result = _evaluate(
        run_input={
            "material": "TG10",
            "plant": "1710",
            "storage_location": "171A",
            "obsolete_days": 180,
        },
        stock=_embedded(_stock(quantity="100")),
        movement=_movement_history(
            (date.today() - timedelta(days=365), "90", "S", "", "090000")
        ),
    )

    assert result["workflow_output"]["aging_complete"] is False
    assert result["workflow_output"]["obsolete_status"] == "unknown"
    assert "aging_reconciliation_gap" in result["missing_evidence"]


def test_fifo_same_timestamp_processes_receipt_before_issue() -> None:
    posting = date.today() - timedelta(days=200)
    result = _evaluate(
        run_input={
            "material": "TG10",
            "plant": "1710",
            "storage_location": "171A",
            "obsolete_days": 180,
        },
        stock=_embedded(_stock(quantity="50")),
        movement=_movement_history(
            (posting, "50", "H", "", "090000"),
            (posting, "100", "S", "", "090000"),
        ),
    )

    assert result["workflow_output"]["aging_complete"] is True
    assert result["workflow_output"]["obsolete_stock_quantity"] == "50"


def test_fifo_reconstructs_batches_independently() -> None:
    posting = date.today() - timedelta(days=200)
    stock = _embedded(
        _stock(quantity="50", batch="B1"),
        _stock(quantity="30", batch="B2"),
    )
    result = _evaluate(
        run_input={
            "material": "TG10",
            "plant": "1710",
            "storage_location": "171A",
            "obsolete_days": 180,
        },
        stock=stock,
        movement=_movement_history(
            (posting, "100", "S", "B1", "080000"),
            (posting + timedelta(days=1), "50", "H", "B1", "080000"),
            (posting + timedelta(days=2), "30", "S", "B2", "080000"),
        ),
    )
    layers = result["workflow_output"]["remaining_fifo_layers"]

    assert result["workflow_output"]["aging_complete"] is True
    assert {(row["batch"], row["remaining_quantity"]) for row in layers} == {
        ("B1", "50"),
        ("B2", "30"),
    }


def test_fifo_duplicate_key_and_unknown_direction_are_inconclusive() -> None:
    movement = _movement_history(
        (date.today() - timedelta(days=200), "100", "X", "", "090000")
    )
    item = movement["step_results"]["movement_date_items"]["results"][0]
    movement["step_results"]["movement_date_items"]["results"].append(dict(item))
    result = _evaluate(
        run_input={
            "material": "TG10",
            "plant": "1710",
            "storage_location": "171A",
            "obsolete_days": 180,
        },
        stock=_embedded(_stock(quantity="100")),
        movement=movement,
    )

    assert result["workflow_output"]["aging_complete"] is False
    assert "movement_stable_key_evidence" in result["missing_evidence"]
    assert "movement_quantity_or_direction_evidence" in result["missing_evidence"]


def test_inventory_presentation_uses_manifest_titles_and_localized_codes() -> None:
    run_input = {
        "material": "TG10",
        "plant": "1710",
        "storage_location": "171A",
        "slow_moving_days": 90,
        "obsolete_days": 180,
        "expiry_days": 90,
    }
    rule_result = _evaluate(
        run_input=run_input,
        stock=_embedded(_stock(quantity="100")),
        movement=_movement_history(
            (date.today() - timedelta(days=200), "100", "S", "", "090000")
        ),
        batch=_embedded(),
    )
    manifest = AgentRepository(ROOT / "agents").get("inventory-health-balancing")
    presentation = _default_presentation(
        RunResult(
            run_id="run-inventory-localization",
            mode=RunMode.agent,
            agent_id="inventory-health-balancing",
            rule_results=[rule_result],
            completeness=Completeness(
                source_complete=True,
                business_complete=True,
                reason="fixture",
            ),
            summary=rule_result["summary"],
        ),
        output_schema=manifest["execution"]["outputSchema"],
    )
    record = next(block for block in presentation.blocks if block.type == "key_value")
    values = {entry.label.zh: entry.value.zh for entry in record.entries}

    assert values["快照日期"] == date.today().isoformat()
    assert values["本次检查项目"] == "慢动检查、呆滞检查、临期检查"
    assert values["慢动状态"] == "风险候选"
    assert values["呆滞状态"] == "风险候选"
    assert values["临期状态"] == "未发现风险"
    assert "snapshot date" not in {label.lower() for label in values}
    bucket_table = next(
        block
        for block in presentation.blocks
        if block.type == "table" and block.title and block.title.zh == "库存账龄分布"
    )
    assert [column.label.zh for column in bucket_table.columns] == [
        "账龄分类",
        "最小账龄（天）",
        "最大账龄（天）",
        "数量",
        "单位",
    ]


def test_public_enum_requires_bilingual_display_labels() -> None:
    manifest = json.loads(
        (ROOT / "agents" / "MM" / "inventory-health-balancing" / "agent.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["execution"]["outputSchema"]["properties"]["obsolete_status"][
        "x-sapba-display"
    ].pop("labels")

    with pytest.raises(ManifestError, match="bilingual text"):
        validate_execution(manifest)


def test_stock_filters_exclude_other_stock_types_and_special_stock() -> None:
    unrestricted = _stock(quantity="10")
    quality = {**_stock(quantity="50"), "InventoryStockType": "02"}
    special = {**_stock(quantity="70"), "InventorySpecialStockType": "E"}
    result = _evaluate(
        run_input={"material": "TG10", "plant": "1710", "storage_location": "171A"},
        stock=_embedded(unrestricted, quality, special),
    )

    assert result["workflow_output"]["current_unrestricted_stock"] == "10"


def test_manifest_and_frontend_remove_historical_transfer_claims_and_blank_values() -> None:
    manifest_path = ROOT / "agents" / "MM" / "inventory-health-balancing" / "agent.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_text = json.dumps(manifest, ensure_ascii=False).lower()
    frontend = (ROOT / "site" / "src" / "components" / "AgentRunPanel.astro").read_text(encoding="utf-8")
    policy = (ROOT / "agents" / "MM" / "inventory-health-balancing" / "config" / "policy.toml").read_text(encoding="utf-8")
    package = (ROOT / "agents" / "MM" / "inventory-health-balancing" / "pyproject.toml").read_text(encoding="utf-8")

    assert manifest["title"] == {"zh": "库存健康检查", "en": "Inventory Health Check"}
    assert manifest["version"] == "0.3.0"
    assert manifest["validation"]["verdict"] == "PASS"
    assert manifest["validation"]["executable"] is True
    assert manifest["execution"]["acceptance"]["codeSetFields"] == []
    assert "confirmed_transfer_quantity" not in manifest_text
    assert "historical_stock_balance_evidence" not in manifest_text
    assert "mb5b" not in manifest_text
    assert "slow_moving_days" not in policy
    assert "obsolete_days" not in policy
    assert "expiry_days" not in policy
    assert 'version = "0.3.0"' in package
    assert "if (!value) return" in frontend
    assert "Number(value)" in frontend


def test_inventory_manifest_uses_supported_grouped_movement_binding() -> None:
    manifest = json.loads(
        (ROOT / "agents" / "MM" / "inventory-health-balancing" / "agent.json").read_text(
            encoding="utf-8"
        )
    )
    movement = next(
        step for step in manifest["execution"]["steps"] if step["id"] == "read_movement_dates"
    )
    header = movement["request"]["plan"]["steps"][1]
    item = movement["request"]["plan"]["steps"][0]

    assert "bindings" not in header
    assert "QuantityInBaseUnit" in item["select_fields"]
    assert "DebitCreditCode" in item["select_fields"]
    assert "CreationTime" in header["select_fields"]
    assert not any(filter_item["field"] == "PostingDate" for filter_item in item["filters"])
    assert header["filter_from_previous"] == [
        {
            "field": "MaterialDocumentYear",
            "source_step_id": "movement_date_items",
            "source_field": "MaterialDocumentYear",
        },
        {
            "field": "MaterialDocument",
            "source_step_id": "movement_date_items",
            "source_field": "MaterialDocument",
        },
    ]


def test_embedded_provider_rejects_unknown_bindings_contract() -> None:
    provider = EmbeddedODataProvider(
        base_url="https://sap.example.test",
        username="fixture-user",
        password="fixture-password",
    )
    result = asyncio.run(
        provider.validate_plan(
            {
                "schema_version": "1.0",
                "plan_kind": "multi_step",
                "steps": [
                    {
                        "step_id": "items",
                        "service_name": "API_MATERIAL_DOCUMENT_SRV",
                        "odata_version": "2.0",
                        "entity_set": "A_MaterialDocumentItem",
                        "http_method": "GET",
                    },
                    {
                        "step_id": "headers",
                        "service_name": "API_MATERIAL_DOCUMENT_SRV",
                        "odata_version": "2.0",
                        "entity_set": "A_MaterialDocumentHeader",
                        "http_method": "GET",
                        "bindings": [
                            {
                                "source_step_id": "items",
                                "field": "MaterialDocument",
                                "source_field": "MaterialDocument",
                            }
                        ],
                    },
                ],
            }
        )
    )

    assert result["ok"] is False
    assert any(
        issue["code"] == "unsupported_binding_contract"
        for issue in result["validation_issues"]
    )


def test_harness_prompt_renders_exact_supported_multi_step_contract() -> None:
    prompt = _turn_prompt("check inventory", continuing=False)

    assert '{"schema_version":"1.0","plan_kind":"multi_step","steps":[...]}' in prompt
    assert "two header-step `filter_from_previous` items" in prompt
    assert "Never invent `bindings`" in prompt
