from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.engine import _validate_input
from sap_business_agents_platform.harness import _turn_prompt
from sap_business_agents_platform.manifests import AgentRepository
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
                "stock": stock if stock is not None else _embedded(),
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
    assert window["movement_lookback_days"] == 365
    assert window["movement_date_from"] == (date.today() - timedelta(days=365)).isoformat()
    assert window["movement_years"] == [
        str(year)
        for year in range((date.today() - timedelta(days=365)).year, date.today().year + 1)
    ]
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


@pytest.mark.parametrize(
    ("optional_input", "expected_slow", "expected_obsolete", "expected_expiry"),
    [
        ({"slow_moving_days": 180}, "candidate", "not_requested", "not_requested"),
        ({"obsolete_days": 365}, "not_requested", "candidate", "not_requested"),
    ],
)
def test_complete_empty_movement_window_uses_age_lower_bound(
    optional_input: dict[str, int],
    expected_slow: str,
    expected_obsolete: str,
    expected_expiry: str,
) -> None:
    run_input = {"material": "TG10", "plant": "1710", "storage_location": "171A", **optional_input}
    result = _evaluate(run_input=run_input, stock=_embedded(_stock()), movement=_embedded())
    output = result["workflow_output"]

    assert output["last_movement_date"] is None
    assert output["stock_age_days"] is None
    assert output["stock_age_lower_bound_days"] == max(optional_input.values())
    assert output["slow_moving_status"] == expected_slow
    assert output["obsolete_status"] == expected_obsolete
    assert output["expiry_status"] == expected_expiry
    assert output["business_status"] == "attention"


def test_obsolete_and_expiry_can_run_without_slow_moving_check() -> None:
    run_input = {
        "material": "TG10", "plant": "1710", "storage_location": "171A",
        "obsolete_days": 365, "expiry_days": 90,
    }
    expiry = date.today() + timedelta(days=30)
    result = _evaluate(
        run_input=run_input,
        stock=_embedded(_stock(batch="B1")),
        movement=_embedded(),
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


def test_all_checks_use_exact_last_movement_age() -> None:
    run_input = {
        "material": "TG10", "plant": "1710", "storage_location": "171A",
        "slow_moving_days": 180, "obsolete_days": 365, "expiry_days": 90,
    }
    posting = date.today() - timedelta(days=200)
    expiry = date.today() + timedelta(days=60)
    result = _evaluate(
        run_input=run_input,
        stock=_embedded(_stock(batch="B1")),
        movement=_embedded({"PostingDate": posting.isoformat()}),
        batch=_embedded({"Material": "TG10", "BatchIdentifyingPlant": "1710", "Batch": "B1", "ShelfLifeExpirationDate": expiry.isoformat()}),
    )
    output = result["workflow_output"]

    assert output["stock_age_days"] == 200
    assert output["stock_age_lower_bound_days"] is None
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
    assert manifest["version"] == "0.2.0"
    assert manifest["validation"]["verdict"] == "PASS"
    assert manifest["validation"]["executable"] is True
    assert manifest["execution"]["acceptance"]["codeSetFields"] == []
    assert "confirmed_transfer_quantity" not in manifest_text
    assert "historical_stock_balance_evidence" not in manifest_text
    assert "mb5b" not in manifest_text
    assert "slow_moving_days" not in policy
    assert "obsolete_days" not in policy
    assert "expiry_days" not in policy
    assert 'version = "0.2.0"' in package
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

    assert "bindings" not in header
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
