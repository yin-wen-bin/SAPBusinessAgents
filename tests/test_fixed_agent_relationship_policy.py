from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sap_business_agents_platform.engine import RunCoordinator, RunExecutionError
from sap_business_agents_platform.normalization import SapValueNormalizer
from sap_business_agents_platform.relationships import (
    ADVISORY_RELATIONSHIP_POLICY,
    LEGACY_RELATIONSHIP_POLICY,
    RelationshipCatalog,
)


ROOT = Path(__file__).resolve().parents[1]


class _Store:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def append_event(self, _run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))


class _SapRead:
    def __init__(self) -> None:
        self.executed: list[dict[str, Any]] = []

    async def validate_plan(self, plan: dict[str, Any], _query: str = "") -> dict[str, Any]:
        return {"ok": True, "normalized_plan": plan}

    async def execute_plan(self, plan: dict[str, Any], _query: str = "") -> dict[str, Any]:
        self.executed.append(plan)
        return {"ok": True, "data": {"results": [], "source_complete": True}}


def _coordinator() -> RunCoordinator:
    coordinator = RunCoordinator.__new__(RunCoordinator)
    coordinator.store = _Store()
    coordinator.sap_read = _SapRead()
    coordinator.relationships = RelationshipCatalog.load(
        ROOT / "config" / "business-relationships.json"
    )
    coordinator.normalizer = SapValueNormalizer(
        ROOT / "config" / "sap-value-normalization.json"
    )
    return coordinator


def _semantic_mismatch_plan() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "plan_kind": "multi_step",
        "steps": [
            {
                "step_id": "sales_order",
                "service_name": "API_SALES_ORDER_SRV",
                "odata_version": "2.0",
                "entity_set": "A_SalesOrder",
                "http_method": "GET",
                "filters": [
                    {
                        "field": "SalesOrder",
                        "operator": "eq",
                        "value": "BUSINESS_KEY_FIXTURE",
                        "value_type": "string",
                    }
                ],
            },
            {
                "step_id": "fi",
                "service_name": "API_OPLACCTGDOCITEMCUBE_SRV",
                "odata_version": "2.0",
                "entity_set": "A_OperationalAcctgDocItemCube",
                "http_method": "GET",
                "filters": [
                    {
                        "field": "OrderID",
                        "operator": "eq",
                        "value": "BUSINESS_KEY_FIXTURE",
                        "value_type": "string",
                    }
                ],
            },
        ],
    }


def test_legacy_fixed_agent_policy_keeps_existing_relationship_rejection() -> None:
    async def scenario() -> None:
        coordinator = _coordinator()
        with pytest.raises(RunExecutionError) as caught:
            await coordinator._execute_step(
                "run_legacy",
                {"id": "read", "executor": "sap_read", "operation": "execute_plan"},
                {"plan": _semantic_mismatch_plan()},
                "fixture",
                relationship_policy_value=LEGACY_RELATIONSHIP_POLICY,
            )
        assert caught.value.code == "agent_relationship_rejected"
        assert coordinator.sap_read.executed == []

    asyncio.run(scenario())


def test_advisory_fixed_agent_policy_executes_without_codex_and_records_guidance() -> None:
    async def scenario() -> None:
        coordinator = _coordinator()
        advisories: list[dict[str, Any]] = []
        output = await coordinator._execute_step(
            "run_advisory",
            {"id": "read", "executor": "sap_read", "operation": "execute_plan"},
            {"plan": _semantic_mismatch_plan()},
            "fixture",
            relationship_policy_value=ADVISORY_RELATIONSHIP_POLICY,
            relationship_advisories=advisories,
        )

        assert output["ok"] is True
        assert len(coordinator.sap_read.executed) == 1
        assert advisories[0]["code"] == "relationship_literal_semantic_mismatch"
        assert coordinator.store.events[-1][0] == "relationship_advisories_recorded"

    asyncio.run(scenario())


def test_advisory_policy_still_blocks_invalid_cross_step_dependency() -> None:
    async def scenario() -> None:
        coordinator = _coordinator()
        plan = _semantic_mismatch_plan()
        plan["steps"][1]["filters"] = []
        plan["steps"][1]["filter_from_previous"] = [
            {
                "field": "AccountingDocument",
                "source_step_id": "missing_step",
                "source_field": "AccountingDocument",
            }
        ]
        with pytest.raises(RunExecutionError) as caught:
            await coordinator._execute_step(
                "run_invalid",
                {"id": "read", "executor": "sap_read", "operation": "execute_plan"},
                {"plan": plan},
                "fixture",
                relationship_policy_value=ADVISORY_RELATIONSHIP_POLICY,
                relationship_advisories=[],
            )
        assert caught.value.code == "agent_relationship_invalid"
        assert coordinator.sap_read.executed == []

    asyncio.run(scenario())
