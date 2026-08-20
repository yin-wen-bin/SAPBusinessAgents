from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.codex_planner import _planner_prompt, _run_plan_turn
from sap_business_agents_platform.engine import (
    RunExecutionError,
    _canonicalize_plan_order_by,
)
from sap_business_agents_platform.models import PlannerDecision


class FakeThread:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt: str, *, output_schema: dict[str, object]):
        assert output_schema["type"] == "object"
        self.prompts.append(prompt)
        return SimpleNamespace(final_response=json.dumps(self.responses.pop(0)))


def test_plan_turn_repairs_malformed_plan_json_exactly_once() -> None:
    thread = FakeThread(
        [
            {
                "intent": "fixture",
                "needs_clarification": False,
                "clarification_question": "",
                "plan_json": '{"service_name":"API_FIXTURE_SRV","odata_version":"2.0" "entity_set":"A_Fixture"}',
            },
            {
                "intent": "fixture",
                "needs_clarification": False,
                "clarification_question": "",
                "plan_json": '{"service_name":"API_FIXTURE_SRV","odata_version":"2.0","entity_set":"A_Fixture"}',
            },
        ]
    )
    raw, plan = asyncio.run(_run_plan_turn(thread, "initial", phase="test"))
    assert raw["intent"] == "fixture"
    assert plan == {
        "service_name": "API_FIXTURE_SRV",
        "odata_version": "2.0",
        "entity_set": "A_Fixture",
    }
    assert len(thread.prompts) == 2
    assert "Change only the JSON syntax" in thread.prompts[1]


def test_order_by_is_canonicalized_to_guarded_bare_field_contract() -> None:
    decision = PlannerDecision(
        intent="fixture",
        plan={
            "service_name": "API_FIXTURE_SRV",
            "odata_version": "2.0",
            "entity_set": "A_Fixture",
            "order_by": ["Document asc", "Document asc", "Item"],
        },
    )
    normalized, count = _canonicalize_plan_order_by(decision)
    assert normalized.plan is not None
    assert normalized.plan["order_by"] == ["Document", "Item"]
    assert count == 2


def test_order_by_desc_fails_closed_instead_of_changing_semantics() -> None:
    decision = PlannerDecision(
        intent="fixture",
        plan={
            "service_name": "API_FIXTURE_SRV",
            "odata_version": "2.0",
            "entity_set": "A_Fixture",
            "order_by": ["Document desc"],
        },
    )
    with pytest.raises(RunExecutionError, match="Descending order"):
        _canonicalize_plan_order_by(decision)


def test_initial_planner_receives_business_relationship_contract() -> None:
    prompt = _planner_prompt(
        "fixture",
        {"data": {"items": []}},
        {
            "data": {
                "business_relationship_contract": {
                    "field_semantics": [
                        {
                            "entity_set": "A_OperationalAcctgDocItemCube",
                            "field": "BillingDocument",
                            "semantic": "billing_document_id",
                        }
                    ],
                    "relationships": [{"id": "o2c-billing-operational-fi"}],
                }
            }
        },
        [],
        continuing=False,
    )
    assert "o2c-billing-operational-fi" in prompt
    assert "semantic compatibility" in prompt
