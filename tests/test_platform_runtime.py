from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.engine import (
    _completeness_evidence_scope,
    _count_free_query_top_bounds,
    _validate_input,
)
from sap_business_agents_platform.manifests import AgentRepository, ManifestError, validate_execution
from sap_business_agents_platform.models import (
    HarnessResult,
    LocalizedText,
    PlannerDecision,
    PresentationBlock,
    RunCreate,
    RunMode,
    RunPresentation,
    RunResult,
)
from sap_business_agents_platform.skills import SkillError, SkillRegistry


def test_validate_input_enforces_string_length_and_pattern() -> None:
    schema = {
        "type": "object",
        "properties": {
            "sales_order": {
                "type": "string",
                "minLength": 1,
                "maxLength": 10,
                "pattern": r"^[0-9]+$",
            }
        },
        "required": ["sales_order"],
        "additionalProperties": False,
    }

    _validate_input({"sales_order": "5814"}, schema)

    for invalid in ("查询订单5814的状态", "12345678901", "SO-5814"):
        try:
            _validate_input({"sales_order": invalid}, schema)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected invalid sales-order input to be rejected: {invalid}")


def test_validate_input_enforces_iso_date_and_bounded_date_range() -> None:
    schema = {
        "type": "object",
        "properties": {
            "date_from": {"type": "string", "format": "date"},
            "date_to": {"type": "string", "format": "date"},
        },
        "required": ["date_from", "date_to"],
        "additionalProperties": False,
        "dateRangePairs": [{"from": "date_from", "to": "date_to", "maxDays": 31}],
    }

    _validate_input({"date_from": "2026-08-01", "date_to": "2026-08-17"}, schema)
    for invalid in (
        {"date_from": "2026/08/01", "date_to": "2026-08-17"},
        {"date_from": "2026-08-17", "date_to": "2026-08-01"},
        {"date_from": "2026-01-01", "date_to": "2026-08-17"},
    ):
        try:
            _validate_input(invalid, schema)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected invalid date range to be rejected: {invalid}")


class FakeEmbeddedProvider:
    def __init__(self, *, complete: bool = True) -> None:
        self.complete = complete
        self.schema_calls: list[tuple[str, tuple[str, ...]]] = []
        self.validated_plans: list[dict[str, Any]] = []
        self.executed_plans: list[dict[str, Any]] = []

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "data": {"runtime_enabled": True, "read_only": True}}

    async def catalog(self, query: str = "", skip: int = 0, limit: int = 100) -> dict[str, Any]:
        del skip, limit
        return {
            "ok": True,
            "data": {
                "items": [
                    {
                        "service_name": "API_PURCHASEORDER_PROCESS_SRV",
                        "odata_version": "2.0",
                        "entity_sets": ["A_PurchaseOrder"],
                        "read_only": True,
                        "query": query,
                    }
                ]
            },
        }

    async def guidance(self, query: str) -> dict[str, Any]:
        return {"ok": True, "data": {"query": query, "evidence_policy": "advisory_only"}}

    async def schema(
        self,
        service_name: str,
        entity_sets: list[str] | str,
        query: str = "",
        *,
        odata_version: str,
        include_fields: bool = True,
        max_fields: int = 5000,
    ) -> dict[str, Any]:
        del query, include_fields, max_fields
        entities = [entity_sets] if isinstance(entity_sets, str) else list(entity_sets)
        self.schema_calls.append((service_name, tuple(entities)))
        return {
            "ok": True,
            "data": {
                "service": {"service_name": service_name, "odata_version": odata_version},
                "entities": [
                    {
                        "service_name": service_name,
                        "odata_version": odata_version,
                        "entity_set": entity,
                        "runtime_available": True,
                        "executable": True,
                    }
                    for entity in entities
                ],
                "fields": [
                    {
                        "service_name": service_name,
                        "odata_version": odata_version,
                        "entity_set": entity,
                        "field_name": "PurchaseOrder",
                        "selectable": True,
                        "filterable": True,
                        "sortable": True,
                        "runtime_available": True,
                        "executable": True,
                    }
                    for entity in entities
                ],
                "schema_authority": True,
                "fields_truncated": False,
                "compatibility_status": "compatible",
            },
            "validation_issues": [],
        }

    async def validate_plan(self, plan: dict[str, Any], query: str = "") -> dict[str, Any]:
        del query
        self.validated_plans.append(plan)
        return {"ok": True, "status": "validated"}

    async def execute_plan(
        self,
        plan: dict[str, Any],
        query: str = "",
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        del query, conversation_id
        self.executed_plans.append(plan)
        return {
            "ok": True,
            "case_id": "case-001",
            "data": {
                "results": [{"PurchaseOrder": "4500000001"}],
                "source_complete": self.complete,
            },
            "pagination": {
                "total_count": 1,
                "total_count_known": True,
                "has_next": False,
            },
            "presentation": {"text": "One read-only SAP record was found."},
        }

    async def execute_get(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self.execute_plan(request)


class FakePlanner:
    def __init__(
        self,
        *,
        clarify_once: bool = False,
        method: str = "GET",
        top: int | None = None,
    ) -> None:
        self.clarify_once = clarify_once
        self.method = method
        self.top = top
        self.calls = 0
        self.queries: list[str] = []

    async def plan(
        self,
        query: str,
        catalog: dict[str, Any],
        guidance: dict[str, Any],
        skills: list[dict[str, Any]],
        thread_id: str | None = None,
    ) -> PlannerDecision:
        del catalog, guidance, skills
        self.calls += 1
        self.queries.append(query)
        if self.clarify_once and self.calls == 1:
            return PlannerDecision(
                intent="Find a purchase order",
                needs_clarification=True,
                clarification_question="Which purchase order?",
                thread_id="thread-001",
            )
        plan = {
            "service_name": "API_PURCHASEORDER_PROCESS_SRV",
            "odata_version": "2.0",
            "entity_set": "A_PurchaseOrder",
            "http_method": self.method,
            "plan_kind": "direct",
            "filters": [
                {"field": "PurchaseOrder", "operator": "eq", "value": "4500000001", "value_type": "string"}
            ],
            "rationale": query,
        }
        if self.top is not None:
            plan["top"] = self.top
        return PlannerDecision(
            intent="Find a purchase order",
            plan=plan,
            thread_id=thread_id or "thread-001",
        )


class SlowSummaryPlanner(FakePlanner):
    async def summarize(self, **_kwargs: Any) -> dict[str, str]:
        await asyncio.sleep(5)
        return {"zh": "不应到达", "en": "Should not complete"}


class HarnessPlanner(FakePlanner):
    async def plan(
        self,
        query: str,
        catalog: dict[str, Any],
        guidance: dict[str, Any],
        skills: list[dict[str, Any]],
        thread_id: str | None = None,
    ) -> PlannerDecision:
        del catalog, guidance
        assert [item["skill_id"] for item in skills] == ["fixture-read-only"]
        return PlannerDecision(
            intent="Query SAP then run an approved read-only Skill",
            plan={
                "kind": "sap_business_agents_harness",
                "steps": [
                    {
                        "id": "query_sap",
                        "tool": "sap_read",
                        "reason": "Collect source evidence",
                        "plan": {
                            "service_name": "API_PURCHASEORDER_PROCESS_SRV",
                            "odata_version": "2.0",
                            "entity_set": "A_PurchaseOrder",
                            "http_method": "GET",
                            "plan_kind": "direct",
                        },
                    },
                    {
                        "id": "review_fixture",
                        "tool": "skill",
                        "skill_id": "fixture-read-only",
                        "reason": "Exercise the approved structured Skill contract",
                        "input": {"sap": "{{steps.query_sap.output}}"},
                    },
                ],
            },
            thread_id=thread_id or "thread-harness",
        )


class SchemaRejectingEmbeddedProvider(FakeEmbeddedProvider):
    async def validate_plan(self, plan: dict[str, Any], query: str = "") -> dict[str, Any]:
        del query
        self.validated_plans.append(plan)
        encoded = json.dumps(plan)
        invalid = "ObsoleteField" in encoded
        return {
            "ok": not invalid,
            "status": "validated" if not invalid else "rejected",
            "validation_issues": (
                []
                if not invalid
                else [
                    {
                        "code": "schema_drift_field_unavailable",
                        "entity_set": "A_PurchaseOrder",
                        "field": "ObsoleteField",
                    }
                ]
            ),
        }


class GroundingPlanner(FakePlanner):
    def __init__(self, *, needs_repair: bool = False) -> None:
        super().__init__()
        self.needs_repair = needs_repair
        self.ground_calls: list[int] = []

    async def plan(
        self,
        query: str,
        catalog: dict[str, Any],
        guidance: dict[str, Any],
        skills: list[dict[str, Any]],
        thread_id: str | None = None,
    ) -> PlannerDecision:
        decision = await super().plan(query, catalog, guidance, skills, thread_id)
        assert decision.plan is not None
        decision.plan["select_fields"] = ["PurchaseOrder", "ObsoleteField"]
        return decision

    async def ground_plan(
        self,
        *,
        query: str,
        decision: PlannerDecision,
        schemas: list[dict[str, Any]],
        relationships: dict[str, Any] | None = None,
        validation_failures: list[dict[str, Any]] | None = None,
        repair_attempt: int = 0,
    ) -> PlannerDecision:
        del query
        assert relationships is not None
        self.ground_calls.append(repair_attempt)
        assert schemas[0]["data"]["schema_authority"] is True
        assert any(
            item["field_name"] == "PurchaseOrder"
            for item in schemas[0]["data"]["fields"]
        )
        if repair_attempt:
            assert validation_failures
        keep_invalid = self.needs_repair
        assert decision.plan is not None
        plan = {**decision.plan, "select_fields": ["PurchaseOrder"]}
        if keep_invalid:
            plan["select_fields"].append("ObsoleteField")
        return decision.model_copy(update={"plan": plan})


class O2CRelationshipEmbeddedProvider(FakeEmbeddedProvider):
    async def schema(
        self,
        service_name: str,
        entity_sets: list[str] | str,
        query: str = "",
        *,
        odata_version: str,
        include_fields: bool = True,
        max_fields: int = 5000,
    ) -> dict[str, Any]:
        del query, include_fields, max_fields
        entities = [entity_sets] if isinstance(entity_sets, str) else list(entity_sets)
        self.schema_calls.append((service_name, tuple(entities)))
        fields_by_entity = {
            "A_SalesOrder": ["SalesOrder"],
            "A_OperationalAcctgDocItemCube": ["OrderID", "SalesDocument"],
        }
        return {
            "ok": True,
            "data": {
                "service": {"service_name": service_name, "odata_version": odata_version},
                "entities": [
                    {
                        "service_name": service_name,
                        "odata_version": odata_version,
                        "entity_set": entity,
                        "runtime_available": True,
                        "executable": True,
                    }
                    for entity in entities
                ],
                "fields": [
                    {
                        "service_name": service_name,
                        "odata_version": odata_version,
                        "entity_set": entity,
                        "field_name": field,
                        "selectable": True,
                        "filterable": True,
                        "sortable": True,
                        "runtime_available": True,
                        "executable": True,
                    }
                    for entity in entities
                    for field in fields_by_entity[entity]
                ],
                "schema_authority": True,
                "fields_truncated": False,
                "compatibility_status": "compatible",
            },
            "validation_issues": [],
        }


class O2CRelationshipPlanner(FakePlanner):
    def __init__(self, *, repair: bool = True) -> None:
        super().__init__()
        self.repair = repair
        self.ground_calls: list[int] = []
        self.relationship_snapshots: list[dict[str, Any]] = []

    @staticmethod
    def _plan(field: str) -> dict[str, Any]:
        return {
            "kind": "sap_business_agents_harness",
            "steps": [
                {
                    "id": "o2c_evidence",
                    "tool": "sap_read",
                    "reason": "Collect O2C evidence",
                    "plan": {
                        "service_name": "API_SALES_ORDER_SRV",
                        "odata_version": "2.0",
                        "entity_set": "A_SalesOrder",
                        "http_method": "GET",
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
                                        "value": "SO_FIXTURE",
                                        "value_type": "string",
                                    }
                                ],
                            },
                            {
                                "step_id": "fi_evidence",
                                "service_name": "API_OPLACCTGDOCITEMCUBE_SRV",
                                "odata_version": "2.0",
                                "entity_set": "A_OperationalAcctgDocItemCube",
                                "http_method": "GET",
                                "filters": [
                                    {
                                        "field": field,
                                        "operator": "eq",
                                        "value": "SO_FIXTURE",
                                        "value_type": "string",
                                    }
                                ],
                            },
                        ],
                    },
                }
            ],
        }

    async def plan(
        self,
        query: str,
        catalog: dict[str, Any],
        guidance: dict[str, Any],
        skills: list[dict[str, Any]],
        thread_id: str | None = None,
    ) -> PlannerDecision:
        del query, catalog, guidance, skills
        return PlannerDecision(
            intent="Trace O2C evidence",
            plan=self._plan("OrderID"),
            thread_id=thread_id or "thread-o2c-relationships",
        )

    async def ground_plan(
        self,
        *,
        query: str,
        decision: PlannerDecision,
        schemas: list[dict[str, Any]],
        relationships: dict[str, Any] | None = None,
        validation_failures: list[dict[str, Any]] | None = None,
        repair_attempt: int = 0,
    ) -> PlannerDecision:
        del query, schemas
        self.ground_calls.append(repair_attempt)
        self.relationship_snapshots.append(relationships or {})
        if repair_attempt:
            assert validation_failures
            codes = {
                issue["code"]
                for failure in validation_failures
                for issue in failure.get("validation_issues", [])
            }
            assert "relationship_literal_semantic_mismatch" in codes
        field = "SalesDocument" if repair_attempt and self.repair else "OrderID"
        return decision.model_copy(update={"plan": self._plan(field)})


def _settings(tmp_path: Path) -> Settings:
    repository = Path(__file__).resolve().parents[1]
    return Settings(
        repository_root=repository,
        data_root=tmp_path / "data",
        draft_root=tmp_path / "drafts",
        skillhub_root=tmp_path / "skillhub",
        max_run_seconds=10,
        enforce_agent_acceptance=False,
    )


def _wait(client: TestClient, run_id: str, statuses: set[str] | None = None) -> dict[str, Any]:
    target = statuses or {"completed", "inconclusive", "failed", "cancelled", "waiting_input"}
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in target:
            return run
        time.sleep(0.02)
    raise AssertionError(f"Run {run_id} did not reach {target}")


def test_repository_exposes_all_schema_v2_deterministic_agents() -> None:
    root = Path(__file__).resolve().parents[1]
    repository = AgentRepository(root / "agents")
    records = repository.list()
    assert [record["slug"] for record in repository.executable()] == [
        "ap-payment",
        "ar-collection",
            "gr-ir-clearing",
            "month-end-closing",
            "intelligent-sourcing-rfq",
            "procure-to-pay-status",
            "supplier-performance-risk",
            "mrp-exception-analysis",
            "production-order-monitoring",
            "billing-completeness-check",
        "delivered-not-billed",
        "delivery-delay-prediction",
        "due-delivery-prioritization",
        "order-to-cash-status",
    ]
    assert {record["slug"] for record in records} == {
        "ap-payment",
        "ar-collection",
        "billing-block-diagnosis",
        "billing-completeness-check",
        "billing-dispute-classification",
        "billing-output-monitor",
        "budget-rolling-forecast",
        "co-month-end-allocation-settlement",
        "cost-center-expense-anomaly",
        "delivered-not-billed",
        "delivery-delay-prediction",
        "demand-forecast-planning",
        "due-delivery-prioritization",
        "gr-ir-clearing",
        "intelligent-sourcing-rfq",
        "inventory-health-balancing",
        "internal-order-project-control",
        "material-shortage-procurement-response",
        "month-end-closing",
        "mrp-exception-analysis",
        "order-to-cash-anomaly-monitor",
        "procure-to-pay-status",
        "order-to-cash-status",
        "production-order-monitoring",
        "production-scheduling-capacity",
        "production-variance-analysis",
        "product-cost-variance",
        "returns-credit-anomaly",
        "shortage-allocation-advisor",
        "supplier-performance-risk",
    }
    for record in records:
        assert record["schemaVersion"] == 2
        assert record["execution"]["mode"] == "deterministic"
        assert all(
            step.get("readOnly") is True
            for step in record["execution"]["steps"]
            if step["executor"] in {"sap_read", "skill"}
        )


def test_public_runtime_rejects_agent_without_three_stage_acceptance(tmp_path: Path) -> None:
    settings = replace(_settings(tmp_path), enforce_agent_acceptance=True)
    app = create_app(
        settings,
        planner=FakePlanner(),
        embedded_provider=FakeEmbeddedProvider(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "mode": "agent",
                "agentId": "billing-block-diagnosis",
                "input": {
                    "sales_order": "2",
                },
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "agent_live_validation_required"


def test_manifest_rejects_non_get_plan() -> None:
    manifest = {
        "execution": {
            "mode": "deterministic",
            "inputSchema": {"type": "object", "properties": {}},
            "steps": [
                {
                    "id": "write",
                    "executor": "sap_read",
                    "operation": "execute_plan",
                    "readOnly": True,
                    "request": {"http_method": "POST"},
                }
            ],
        }
    }
    try:
        validate_execution(manifest)
    except ManifestError as exc:
        assert "non-GET" in str(exc)
    else:
        raise AssertionError("A non-GET plan passed manifest validation")


def test_manifest_rejects_non_get_inside_multi_step_array() -> None:
    manifest = {
        "execution": {
            "mode": "deterministic",
            "inputSchema": {"type": "object", "properties": {}},
            "steps": [
                {
                    "id": "write",
                    "executor": "sap_read",
                    "operation": "execute_plan",
                    "readOnly": True,
                    "request": {
                        "plan_kind": "multi_step",
                        "steps": [
                            {
                                "service_name": "API_TEST_SRV",
                                "odata_version": "2.0",
                                "entity_set": "A_Test",
                                "http_method": "PATCH",
                            }
                        ],
                    },
                }
            ],
        }
    }
    try:
        validate_execution(manifest)
    except ManifestError as exc:
        assert "non-GET" in str(exc)
    else:
        raise AssertionError("A nested non-GET plan passed manifest validation")


def test_fixed_agent_runs_without_codex_and_persists_events(tmp_path: Path) -> None:
    embedded = FakeEmbeddedProvider()
    planner = FakePlanner()
    app = create_app(_settings(tmp_path), planner=planner, embedded_provider=embedded)
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "mode": "agent",
                "agentId": "procure-to-pay-status",
                "input": {"purchase_order": "4500000001"},
            },
        )
        assert response.status_code == 202
        run = _wait(client, response.json()["run_id"])
        assert run["status"] == "completed"
        assert run["result"]["completeness"]["source_complete"] is True
        assert len(embedded.executed_plans) == 1
        assert planner.calls == 0
        assert run["result"]["rule_results"][0]["rule_id"] == "p2p_deterministic_status_v1"
        assert run["result"]["summary"]["zh"]
        assert run["result"]["presentation"]["schema_version"] == "1.0"
        assert any(
            block["type"] == "table"
            for block in run["result"]["presentation"]["blocks"]
        )
        assert run["result"]["tool_calls"][0]["plugin_id"] == "embedded-sap-odata"
        assert run["result"]["tool_calls"][0]["capability"] == "sap_read.v2"
        assert run["result"]["tool_calls"][0]["odata_versions"] == ["2.0"]
        assert run["result"]["evidence"][0]["call_id"].startswith("call_")
        artifact_names = {artifact["name"] for artifact in run["result"]["artifacts"]}
        assert {"report.md", "business-stages.csv", "evidence.csv", "result.json"}.issubset(
            artifact_names
        )
        report = client.get(f"/api/runs/{run['run_id']}/artifacts/report.md")
        assert report.status_code == 200
        assert "## 业务结论" in report.text
        assert "## 各阶段结果" in report.text
        assert "## 建议下一步" in report.text
        events = app.state.store.events_after(run["run_id"])
        assert "step_started" in {event.type for event in events}
        assert "rule_completed" in {event.type for event in events}


def test_embedded_provider_is_the_only_sap_read_capability(
    tmp_path: Path,
) -> None:
    embedded = FakeEmbeddedProvider()
    app = create_app(
        _settings(tmp_path),
        planner=FakePlanner(),
        embedded_provider=embedded,
    )
    with TestClient(app) as client:
        providers = client.get("/api/providers/sap-read").json()
        assert providers["selected_provider"] == "embedded"
        assert providers["selected_plugin_id"] == "embedded-sap-odata"
        assert providers["automatic_fallback"] is False
        response = client.post(
            "/api/runs",
            json={
                "mode": "agent",
                "agentId": "procure-to-pay-status",
                "input": {"purchase_order": "4500000001"},
            },
        )
        run = _wait(client, response.json()["run_id"])
        assert run["status"] == "completed"
        assert run["result"]["tool_calls"][0]["plugin_id"] == "embedded-sap-odata"
        assert len(embedded.executed_plans) == 1


def test_complete_mm_api_evidence_skips_every_conditional_adt_step(
    tmp_path: Path,
) -> None:
    embedded = FakeEmbeddedProvider()
    app = create_app(
        _settings(tmp_path),
        planner=FakePlanner(),
        embedded_provider=embedded,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "mode": "agent",
                "agentId": "material-shortage-procurement-response",
                "input": {
                    "material": "MAT001",
                    "plant": "1010",
                    "mrp_area": "1010",
                    "purchasing_organization": "1010",
                    "shortage_profile": "SAP000000001",
                    "shortage_counter": "001",
                    "as_of": "2026-08-17",
                },
            },
        )
        run = _wait(client, response.json()["run_id"])
        assert run["status"] == "completed"
        assert len(embedded.executed_plans) == 6
        adt_steps = {
            "adt_mrp", "adt_pr", "adt_po_scope", "adt_po",
            "adt_source_scope", "adt_source",
        }
        skipped = {
            item["step_id"]
            for item in run["result"]["steps"]
            if item["status"] == "skipped"
        }
        assert skipped == adt_steps
        assert all(call.get("skill_id") != "sap-adt-table-export" for call in run["result"]["tool_calls"])
        events = app.state.store.events_after(run["run_id"])
        assert sum(event.type == "step_skipped" for event in events) == 6


def test_old_adt_contract_gap_is_recorded_and_mm_result_remains_inconclusive(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    class CapabilityGapSap(FakeEmbeddedProvider):
        async def execute_plan(
            self,
            plan: dict[str, Any],
            query: str = "",
            conversation_id: str | None = None,
        ) -> dict[str, Any]:
            result = await super().execute_plan(plan, query, conversation_id)
            result["ok"] = False
            result["error"] = {
                "code": "schema_drift_field_unavailable",
                "message": "Fixture simulates a released API evidence gap.",
            }
            result["data"]["source_complete"] = False
            return result

    async def incompatible_contract(
        _registry: SkillRegistry,
        skill_id: str,
        input_payload: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append(input_payload)
        raise SkillError(
            "Installed sap-adt-table-export contract still exposes caller-managed connection selection.",
            code="skill_contract_incompatible",
            detail={
                "skill_id": skill_id,
                "expected_contract": "skill_managed_default_connection",
            },
        )

    monkeypatch.setattr(SkillRegistry, "execute", incompatible_contract)
    embedded = CapabilityGapSap(complete=False)
    app = create_app(
        _settings(tmp_path),
        planner=FakePlanner(),
        embedded_provider=embedded,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "mode": "agent",
                "agentId": "material-shortage-procurement-response",
                "input": {
                    "material": "MAT001",
                    "plant": "1010",
                    "mrp_area": "1010",
                    "purchasing_organization": "1010",
                    "shortage_profile": "SAP000000001",
                    "shortage_counter": "001",
                    "as_of": "2026-08-17",
                },
            },
        )
        run = _wait(client, response.json()["run_id"])

    assert run["status"] == "inconclusive"
    assert calls
    assert all("connection_profile" not in payload for payload in calls)
    assert any(
        error["code"] == "skill_contract_incompatible"
        for error in run["result"]["errors"]
    )
    assert any(
        step.get("status") == "capability_blocked"
        and step.get("error", {}).get("code") == "skill_contract_incompatible"
        for step in run["result"]["tool_calls"]
    )


def test_skillhub_local_configuration_endpoint_is_not_writable(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings, planner=FakePlanner(), embedded_provider=FakeEmbeddedProvider())
    with TestClient(app) as client:
        response = client.put(
            "/api/config/skillhub",
            json={"values": {"SAP_USERNAME": "caller-must-not-configure-skillhub"}},
        )
    assert response.status_code == 404
    assert not (settings.skillhub_root / ".env").exists()


def test_plugin_api_exposes_lifecycle_and_capability_inventory(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), planner=FakePlanner(), embedded_provider=FakeEmbeddedProvider())
    with TestClient(app) as client:
        plugins = client.get("/api/plugins")
        assert plugins.status_code == 200
        assert {item["plugin_id"] for item in plugins.json()} == {
            "business-agent-catalog",
            "codex-runtime",
            "embedded-sap-odata",
            "sapskillhub",
        }
        capabilities = client.get("/api/capabilities").json()
        assert {
            "agent_runtime.v2",
            "agent_runtime.v1",
            "authoring.v1",
            "business_agent.v1",
            "sap_read.v2",
            "skill_catalog.v1",
            "skill_execute.v1",
        }.issubset({item["capability"] for item in capabilities})
        health = client.post("/api/plugins/embedded-sap-odata/health")
        assert health.status_code == 200
        assert health.json()["plugin"]["status"] == "ready"
        catalog = client.get("/api/tools/sap-read")
        assert catalog.status_code == 200


def test_disabling_codex_does_not_block_fixed_agent_but_blocks_free_query(
    tmp_path: Path,
) -> None:
    planner = FakePlanner()
    app = create_app(_settings(tmp_path), planner=planner, embedded_provider=FakeEmbeddedProvider())
    with TestClient(app) as client:
        disabled = client.put(
            "/api/plugins/codex-runtime/enabled", json={"enabled": False}
        )
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"

        fixed = client.post(
            "/api/runs",
            json={
                "mode": "agent",
                "agentId": "procure-to-pay-status",
                "input": {"purchase_order": "4500000001"},
            },
        )
        fixed_run = _wait(client, fixed.json()["run_id"])
        assert fixed_run["status"] == "completed"
        assert planner.calls == 0

        free = client.post(
            "/api/runs", json={"mode": "free_query", "query": "查询采购订单 4500000001"}
        )
        free_run = _wait(client, free.json()["run_id"])
        assert free_run["status"] == "failed"
        assert free_run["error"]["code"] == "capability_unavailable"


def test_free_query_uses_codex_plan_then_embedded_validation(tmp_path: Path) -> None:
    embedded = FakeEmbeddedProvider(complete=False)
    planner = FakePlanner()
    app = create_app(_settings(tmp_path), planner=planner, embedded_provider=embedded)
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={"mode": "free_query", "query": "查询采购订单 4500000001"},
        )
        run = _wait(client, response.json()["run_id"])
        assert run["status"] == "inconclusive"
        assert run["thread_id"] == "thread-001"
        assert planner.calls == 1
        assert len(embedded.validated_plans) == 2
        assert embedded.validated_plans[-1:] == embedded.executed_plans
        assert embedded.schema_calls == [
            ("API_PURCHASEORDER_PROCESS_SRV", ("A_PurchaseOrder",))
        ]
        assert run["result"]["summary"]["zh"] == "One read-only SAP record was found."
        assert run["result"]["presentation"]["blocks"][0]["type"] == "text"
        assert run["result"]["tool_calls"][0]["odata_version"] == "2.0"
        assert client.get(
            f"/api/runs/{run['run_id']}/artifacts/result.json"
        ).status_code == 200
        assert client.get(
            f"/api/runs/{run['run_id']}/artifacts/evidence.csv"
        ).status_code == 200


def test_free_query_preserves_evidence_when_codex_summary_times_out(tmp_path: Path) -> None:
    settings = replace(_settings(tmp_path), max_run_seconds=3)
    app = create_app(settings, planner=SlowSummaryPlanner(), embedded_provider=FakeEmbeddedProvider())
    with TestClient(app) as client:
        response = client.post(
            "/api/runs", json={"mode": "free_query", "query": "查询采购订单 4500000001"}
        )
        run = _wait(client, response.json()["run_id"])
        assert run["status"] == "completed"
        assert run["result"]["evidence"]
        assert run["result"]["summary"]["zh"] == "One read-only SAP record was found."
        assert run["result"]["errors"][0]["code"] == "codex_summary_timeout"


def test_free_query_with_explicit_top_is_inconclusive_even_when_embedded_is_complete(
    tmp_path: Path,
) -> None:
    embedded = FakeEmbeddedProvider(complete=True)
    app = create_app(_settings(tmp_path), planner=FakePlanner(top=1), embedded_provider=embedded)
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={"mode": "free_query", "query": "查询采购订单 4500000001"},
        )
        run = _wait(client, response.json()["run_id"])
        assert run["status"] == "inconclusive"
        assert run["result"]["completeness"]["source_complete"] is False
        assert "1 explicit top bound" in run["result"]["completeness"]["reason"]
        assert embedded.executed_plans[0]["top"] == 1


def test_harness_completeness_uses_validated_final_evidence_scope(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path),
        planner=FakePlanner(),
        embedded_provider=FakeEmbeddedProvider(),
    )
    run_id = "run_final_scope"
    app.state.store.create_run(
        run_id,
        RunCreate(mode=RunMode.free_query, query="supplier open items"),
    )
    final_ref = "ev_111111111111111111111111"
    diagnostic_ref = "ev_222222222222222222222222"
    result = RunResult(
        run_id=run_id,
        mode=RunMode.free_query,
        query="supplier open items",
        plan={
            "kind": "sap_business_agents_harness",
            "steps": [
                {
                    "id": "final_query",
                    "tool": "sap_read",
                    "plan": {
                        "service_name": "API_TEST_SRV",
                        "odata_version": "2.0",
                        "entity_set": "A_Test",
                    },
                }
            ],
        },
        evidence=[
            {
                "evidence_ref": diagnostic_ref,
                "source_type": "sap_live",
                "source_complete": False,
                "row_count": 1000,
            },
            {
                "evidence_ref": final_ref,
                "source_type": "sap_live",
                "source_complete": True,
                "row_count": 6,
            },
        ],
        rule_results=[
            {
                "rule_id": "harness_evidence_contract",
                "business_complete": True,
                "missing_evidence": [],
                "evidence_refs": [final_ref],
            }
        ],
        presentation=RunPresentation(
            title=LocalizedText(zh="供应商项目", en="Supplier items"),
            blocks=[
                PresentationBlock(
                    type="notice",
                    text=LocalizedText(zh="最终范围完整。", en="Final scope is complete."),
                    claim_scope="customer_business_fact",
                    evidence_refs=[final_ref],
                    source_complete=True,
                )
            ],
            validation_ref="validation_final_scope",
        ),
        harness=HarnessResult(stop_reason="completed"),
    )

    selected, scope = _completeness_evidence_scope(result)
    assert [item["evidence_ref"] for item in selected] == [final_ref]
    assert scope == {
        "final_report_scoped": True,
        "referenced_count": 1,
        "audit_only_count": 1,
        "missing_reference_count": 0,
    }
    unknown_reference = result.model_copy(deep=True)
    unknown_reference.rule_results[0]["evidence_refs"].append(
        "ev_999999999999999999999999"
    )
    _, unknown_scope = _completeness_evidence_scope(unknown_reference)
    assert unknown_scope["missing_reference_count"] == 1

    app.state.coordinator._complete_result(run_id, result)
    completed = app.state.store.get_run(run_id)
    assert completed.status.value == "completed"
    assert completed.result is not None
    assert completed.result.completeness.source_complete is True
    assert "final-report evidence sources" in completed.result.completeness.reason
    assert "1 non-final diagnostic evidence source" in completed.result.completeness.reason
    assert len(completed.result.evidence) == 2


def test_harness_completeness_remains_inconclusive_for_cited_incomplete_evidence(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        planner=FakePlanner(),
        embedded_provider=FakeEmbeddedProvider(),
    )
    run_id = "run_incomplete_final_scope"
    app.state.store.create_run(
        run_id,
        RunCreate(mode=RunMode.free_query, query="supplier open items"),
    )
    incomplete_ref = "ev_333333333333333333333333"
    result = RunResult(
        run_id=run_id,
        mode=RunMode.free_query,
        plan={
            "kind": "sap_business_agents_harness",
            "steps": [
                {
                    "id": "final_query",
                    "tool": "sap_read",
                    "plan": {
                        "service_name": "API_TEST_SRV",
                        "odata_version": "2.0",
                        "entity_set": "A_Test",
                    },
                }
            ],
        },
        evidence=[
            {
                "evidence_ref": incomplete_ref,
                "source_type": "sap_live",
                "source_complete": False,
                "row_count": 1000,
            }
        ],
        rule_results=[
            {
                "rule_id": "harness_evidence_contract",
                "business_complete": True,
                "missing_evidence": [],
                "evidence_refs": [incomplete_ref],
            }
        ],
        presentation=RunPresentation(
            title=LocalizedText(zh="供应商项目", en="Supplier items"),
            blocks=[
                PresentationBlock(
                    type="notice",
                    text=LocalizedText(zh="范围不完整。", en="Scope is incomplete."),
                    claim_scope="customer_business_fact",
                    evidence_refs=[incomplete_ref],
                    source_complete=False,
                )
            ],
            validation_ref="validation_incomplete_scope",
        ),
        harness=HarnessResult(stop_reason="completed"),
    )

    app.state.coordinator._complete_result(run_id, result)
    completed = app.state.store.get_run(run_id)
    assert completed.status.value == "inconclusive"
    assert completed.result is not None
    assert completed.result.completeness.source_complete is False
    assert "final-report evidence source is bounded" in completed.result.completeness.reason


def test_nested_embedded_top_bounds_are_counted_without_counting_skill_limits() -> None:
    plan = {
        "kind": "sap_business_agents_harness",
        "steps": [
            {
                "id": "query_sap",
                "tool": "sap_read",
                "plan": {
                    "plan_kind": "multi_step",
                    "top": 2,
                    "steps": [{"id": "one", "top": 300}, {"id": "two", "$top": 300}],
                },
            },
            {
                "id": "review",
                "tool": "skill",
                "input": {"top": 999},
            },
        ],
    }
    assert _count_free_query_top_bounds(plan) == 3


def test_free_query_grounds_fields_in_live_schema_before_sap_get(tmp_path: Path) -> None:
    embedded = SchemaRejectingEmbeddedProvider()
    planner = GroundingPlanner()
    app = create_app(_settings(tmp_path), planner=planner, embedded_provider=embedded)
    with TestClient(app) as client:
        response = client.post(
            "/api/runs", json={"mode": "free_query", "query": "查询采购订单 4500000001"}
        )
        run = _wait(client, response.json()["run_id"])
        assert run["status"] == "completed"
        assert planner.ground_calls == [1]
        assert embedded.schema_calls
        assert len(embedded.executed_plans) == 1
        assert embedded.executed_plans[0]["select_fields"] == ["PurchaseOrder"]
        event_types = {event.type for event in app.state.store.events_after(run["run_id"])}
        assert {"schema_received", "plan_repaired", "plan_validated"}.issubset(event_types)


def test_free_query_rejects_after_only_one_bounded_schema_repair(tmp_path: Path) -> None:
    embedded = SchemaRejectingEmbeddedProvider()
    planner = GroundingPlanner(needs_repair=True)
    app = create_app(_settings(tmp_path), planner=planner, embedded_provider=embedded)
    with TestClient(app) as client:
        response = client.post(
            "/api/runs", json={"mode": "free_query", "query": "查询采购订单 4500000001"}
        )
        run = _wait(client, response.json()["run_id"])
        assert run["status"] == "failed"
        assert run["error"]["code"] == "free_query_plan_rejected"
        assert planner.ground_calls == [1]
        assert embedded.executed_plans == []
        event_types = {event.type for event in app.state.store.events_after(run["run_id"])}
        assert "plan_repaired" in event_types


def test_free_query_repairs_schema_valid_but_semantically_wrong_o2c_relation(
    tmp_path: Path,
) -> None:
    embedded = O2CRelationshipEmbeddedProvider()
    planner = O2CRelationshipPlanner()
    app = create_app(_settings(tmp_path), planner=planner, embedded_provider=embedded)
    with TestClient(app) as client:
        response = client.post(
            "/api/runs", json={"mode": "free_query", "query": "追踪销售订单的开票和清账"}
        )
        run = _wait(client, response.json()["run_id"])
        assert run["status"] == "completed"
        assert planner.ground_calls == [1]
        assert planner.relationship_snapshots[0]["relationships"]
        assert len(embedded.executed_plans) == 1
        encoded = json.dumps(embedded.executed_plans[0])
        assert "SalesDocument" in encoded
        assert "OrderID" not in encoded


def test_free_query_rejects_unrepaired_o2c_relation_before_sap_get(tmp_path: Path) -> None:
    embedded = O2CRelationshipEmbeddedProvider()
    planner = O2CRelationshipPlanner(repair=False)
    app = create_app(_settings(tmp_path), planner=planner, embedded_provider=embedded)
    with TestClient(app) as client:
        response = client.post(
            "/api/runs", json={"mode": "free_query", "query": "追踪销售订单的开票和清账"}
        )
        run = _wait(client, response.json()["run_id"])
        assert run["status"] == "failed"
        assert run["error"]["code"] == "free_query_relationship_rejected"
        assert planner.ground_calls == [1]
        assert embedded.executed_plans == []
        details = json.dumps(run["error"]["detail"])
        assert "relationship_literal_semantic_mismatch" in details
        assert "SO_FIXTURE" not in details


def test_free_query_can_pause_for_clarification_and_resume_thread(tmp_path: Path) -> None:
    planner = FakePlanner(clarify_once=True)
    app = create_app(_settings(tmp_path), planner=planner, embedded_provider=FakeEmbeddedProvider())
    with TestClient(app) as client:
        response = client.post("/api/runs", json={"mode": "free_query", "query": "查询采购订单"})
        run_id = response.json()["run_id"]
        waiting = _wait(client, run_id, {"waiting_input"})
        assert waiting["thread_id"] == "thread-001"
        input_response = client.post(
            f"/api/runs/{run_id}/input", json={"input": "4500000001"}
        )
        assert input_response.status_code == 202
        assert input_response.json()["mode"] == "clarification"
        completed = _wait(client, run_id, {"completed", "inconclusive", "failed"})
        assert completed["status"] == "completed"
        assert planner.calls == 2


def test_non_deterministic_agent_can_start_a_guided_read_only_query(tmp_path: Path) -> None:
    planner = FakePlanner()
    app = create_app(_settings(tmp_path), planner=planner, embedded_provider=FakeEmbeddedProvider())
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "mode": "free_query",
                "agentId": "ap-payment",
                "query": "查询供应商发票是否已经付款",
            },
        )
        assert response.status_code == 202
        run = _wait(client, response.json()["run_id"])
        assert run["status"] == "completed"
        assert run["agent_id"] == "ap-payment"
        assert run["result"]["agent_id"] == "ap-payment"
        assert run["query"] == "查询供应商发票是否已经付款"
        assert len(planner.queries) == 1
        assert "advisory business context" in planner.queries[0]
        assert '"agent_id": "ap-payment"' in planner.queries[0]
        assert "Original user question:\n查询供应商发票是否已经付款" in planner.queries[0]


def test_free_query_rejects_codex_write_plan_before_sap(tmp_path: Path) -> None:
    embedded = FakeEmbeddedProvider()
    app = create_app(_settings(tmp_path), planner=FakePlanner(method="POST"), embedded_provider=embedded)
    with TestClient(app) as client:
        response = client.post("/api/runs", json={"mode": "free_query", "query": "创建采购订单"})
        run = _wait(client, response.json()["run_id"])
        assert run["status"] == "failed"
        assert run["error"]["code"] == "write_operation_rejected"
        assert embedded.executed_plans == []


def test_validated_free_query_creates_isolated_agent_draft(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), planner=FakePlanner(), embedded_provider=FakeEmbeddedProvider())
    with TestClient(app) as client:
        response = client.post(
            "/api/runs", json={"mode": "free_query", "query": "查询采购订单 4500000001"}
        )
        run = _wait(client, response.json()["run_id"])
        draft_response = client.post(
            f"/api/runs/{run['run_id']}/create-agent-draft", json={"correction": ""}
        )
        assert draft_response.status_code == 201
        draft = draft_response.json()
        assert draft["status"] == "validated"
        manifest = json.loads((Path(draft["path"]) / "agent.json").read_text(encoding="utf-8"))
        assert manifest["schemaVersion"] == 2
        assert manifest["execution"]["steps"][0]["readOnly"] is True
        assert (Path(draft["path"]) / "content.zh.md").is_file()
        assert (Path(draft["path"]) / "content.en.md").is_file()
        assert (Path(draft["path"]) / "src" / "rules.py").is_file()
        assert (Path(draft["path"]) / "docs" / "data-contract.json").is_file()


def test_standard_skill_contract_and_free_query_harness(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    skillhub = tmp_path / "skillhub"
    (repository / "agents").mkdir(parents=True)
    (repository / "config").mkdir()
    skillhub.mkdir()
    entrypoint = skillhub / "run.py"
    entrypoint.write_text(
        """import argparse, json\nfrom pathlib import Path\np = argparse.ArgumentParser()\np.add_argument('--input', required=True)\np.add_argument('--output', required=True)\na = p.parse_args()\ndata = json.loads(Path(a.input).read_text(encoding='utf-8'))\nPath(a.output).write_text(json.dumps({'ok': True, 'source_complete': True, 'received': bool(data['sap'])}), encoding='utf-8')\n""",
        encoding="utf-8",
    )
    allowlist = {
        "schema_version": "1.0",
        "skills": [
            {
                "skill_id": "fixture-read-only",
                "description": {"zh": "测试", "en": "Fixture"},
                "entrypoint": "run.py",
                "input_schema": {
                    "type": "object",
                    "properties": {"sap": {"type": "object"}},
                    "required": ["sap"],
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "source_complete": {"type": "boolean"},
                        "received": {"type": "boolean"},
                    },
                    "required": ["ok", "source_complete", "received"],
                    "additionalProperties": False,
                },
                "read_only": True,
                "validated": True,
                "timeout": 5,
            }
        ],
    }
    (repository / "config" / "skills.json").write_text(
        json.dumps(allowlist), encoding="utf-8"
    )
    settings = Settings(
        repository_root=repository,
        data_root=tmp_path / "data",
        draft_root=tmp_path / "drafts",
        skillhub_root=skillhub,
        max_run_seconds=10,
    )
    registry = SkillRegistry(skillhub, repository / "config" / "skills.json")
    assert registry.list()[0]["available"] is True
    app = create_app(settings, planner=HarnessPlanner(), embedded_provider=FakeEmbeddedProvider())
    with TestClient(app) as client:
        response = client.post(
            "/api/runs", json={"mode": "free_query", "query": "查询并复核采购订单"}
        )
        run = _wait(client, response.json()["run_id"])
        assert run["status"] == "completed"
        assert [item["source"] for item in run["result"]["evidence"]] == [
            "sap_read",
            "skill",
        ]
        assert run["result"]["tool_calls"][1]["skill_id"] == "fixture-read-only"
