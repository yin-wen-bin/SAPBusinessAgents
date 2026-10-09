from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.codex_planner import (
    FREE_QUERY_PRESENTATION_REVISION_SCHEMA,
    CodexPlanner,
)
from sap_business_agents_platform.harness_query_adapter import normalize_ascending_order
from sap_business_agents_platform.sap_read.base import SapReadError


class FakeThread:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt: str, *, output_schema: dict[str, object], effort: str | None = None):
        assert output_schema["type"] == "object"
        self.prompts.append(prompt)
        return SimpleNamespace(final_response=json.dumps(self.responses.pop(0)))


def test_presentation_revision_schema_keeps_definitions_at_response_root() -> None:
    schema = FREE_QUERY_PRESENTATION_REVISION_SCHEMA
    presentation = schema["properties"]["presentation"]
    localized = schema["$defs"]["LocalizedText"]

    assert "$defs" not in presentation
    assert presentation["properties"]["title"]["$ref"] == "#/$defs/LocalizedText"
    assert localized["required"] == ["zh", "en"]
    assert set(presentation["required"]) == set(presentation["properties"])


@pytest.mark.parametrize("operation", ["plan", "ground_plan", "summarize"])
def test_retired_query_operations_cannot_dispatch_a_model(tmp_path, operation):
    from sap_business_agents_platform.runtime_contract import RuntimeContractError
    planner = CodexPlanner(tmp_path, model="offline")
    class ForbiddenDriver:
        def client(self, **kwargs):
            raise AssertionError("retired operation dispatched a model")
    planner._driver = ForbiddenDriver()
    with pytest.raises(RuntimeContractError, match="runtime_operation_retired"):
        asyncio.run(getattr(planner, operation)("fixture"))


def test_order_by_is_canonicalized_to_guarded_bare_field_contract() -> None:
    plan = {
            "service_name": "API_FIXTURE_SRV",
            "odata_version": "2.0",
            "entity_set": "A_Fixture",
            "order_by": ["Document asc", {"field": "Item", "direction": "asc"}],
    }
    normalized, diagnostics = normalize_ascending_order(plan)
    assert normalized["order_by"] == ["Document", "Item"]
    assert len(diagnostics) == 2
    assert plan["order_by"][0] == "Document asc"


def test_order_by_desc_fails_closed_instead_of_changing_semantics() -> None:
    plan = {
            "service_name": "API_FIXTURE_SRV",
            "odata_version": "2.0",
            "entity_set": "A_Fixture",
            "order_by": ["Document desc"],
    }
    with pytest.raises(SapReadError, match="descending"):
        normalize_ascending_order(plan)


def test_retired_query_prompts_are_not_exported():
    from sap_business_agents_platform import runtime_prompts
    # Relationship semantics remain covered by the real Broker/catalog suite;
    # retired planner prompts must not remain as an alternative implementation.
    for name in ("_planner_prompt", "_grounding_prompt", "_run_plan_turn", "SUMMARY_OUTPUT_SCHEMA"):
        assert not hasattr(runtime_prompts, name)
