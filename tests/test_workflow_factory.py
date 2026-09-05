from __future__ import annotations

from copy import deepcopy
from datetime import date
import time
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.models import PlannerDecision
from sap_business_agents_platform.workflow_factory import (
    _evaluate_expectation,
    _reconcile_runtime_review,
)
from sap_business_agents_platform.workflows import (
    WorkflowError,
    agent_digest,
    topological_order,
    validate_workflow,
    workflow_review_contract,
)


def test_decimal_validation_expectation_uses_exact_decimal_tolerance() -> None:
    expectation = {
        "output": "amount",
        "operator": "decimal_within",
        "expected": "10.00",
        "tolerance": "0.01",
    }
    assert _evaluate_expectation(expectation, {"amount": "10.01"})["status"] == "pass"
    assert _evaluate_expectation(expectation, {"amount": "10.011"})["status"] == "fail"


class WorkflowSapProvider:
    purchase_order_candidates = ["4500000001", "4500000002"]
    sales_order_candidates = ["5814", "5837"]

    def __init__(self) -> None:
        self.execute_count = 0

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "data": {"runtime_enabled": True, "read_only": True}}

    async def catalog(self, query: str = "", skip: int = 0, limit: int = 100) -> dict[str, Any]:
        return {"ok": True, "data": {"items": [], "query": query, "skip": skip, "limit": limit}}

    async def guidance(self, query: str) -> dict[str, Any]:
        return {"ok": True, "data": {"query": query}}

    async def schema(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "data": {"entities": [], "fields": []}}

    async def validate_plan(self, plan: dict[str, Any], query: str = "") -> dict[str, Any]:
        assert plan.get("http_method") == "GET"
        if plan.get("plan_kind") == "direct" and query == "workflow validation candidate":
            expected_field = {
                "A_PurchaseOrder": "PurchaseOrder",
                "A_SalesOrder": "SalesOrder",
            }[plan["entity_set"]]
            assert plan.get("order_by") == [expected_field]
            assert 1 <= int(plan.get("top") or 0) <= 50
        return {"ok": True, "status": "validated", "query": query}

    async def execute_plan(
        self, plan: dict[str, Any], query: str = "", conversation_id: str | None = None
    ) -> dict[str, Any]:
        del query, conversation_id
        self.execute_count += 1
        entity = plan.get("entity_set")
        if entity == "A_PurchaseOrder" and plan.get("plan_kind") == "direct":
            return {
                "ok": True,
                "data": {
                    "results": [
                        {
                            "PurchaseOrder": purchase_order,
                            "CompanyCode": "1010",
                            "Supplier": "1000123",
                        }
                        for purchase_order in self.purchase_order_candidates
                    ],
                    "source_complete": False,
                },
                "pagination": {"has_next": False, "total_count_known": False},
            }
        if entity == "A_SalesOrder" and plan.get("plan_kind") == "direct":
            return {
                "ok": True,
                "data": {
                    "results": [
                        {"SalesOrder": sales_order, "SoldToParty": "17100001"}
                        for sales_order in self.sales_order_candidates
                    ],
                    "source_complete": False,
                },
                "pagination": {"has_next": False, "total_count_known": False},
            }
        if entity == "A_PurchaseOrder" and plan.get("plan_kind") == "multi_step":
            invoice_key = {
                "CompanyCode": "1010",
                "FiscalYear": "2026",
                "AccountingDocument": "5100000001",
                "AccountingDocumentItem": "1",
                "AccountingDocumentType": "RE",
                "PurchasingDocument": "4500000001",
                "PurchasingDocumentItem": "10",
            }
            step_results = {
                "purchase_order": {
                    "results": [{"PurchaseOrder": "4500000001", "CompanyCode": "1010", "Supplier": "1000123"}],
                    "source_complete": True,
                },
                "purchase_order_items": {"results": [{"PurchaseOrder": "4500000001"}], "source_complete": True},
                "material_documents": {"results": [{"PurchaseOrder": "4500000001", "GoodsMovementType": "101"}], "source_complete": True},
                "material_document_headers": {"results": [], "source_complete": True},
                "supplier_invoice_items": {"results": [{"PurchaseOrder": "4500000001", "SupplierInvoice": "5100000001", "FiscalYear": "2026"}], "source_complete": True},
                "supplier_invoice_headers": {"results": [{"SupplierInvoice": "5100000001", "FiscalYear": "2026"}], "source_complete": True},
                "accounting_items": {
                    "results": [invoice_key],
                    "source_complete": True,
                },
                "full_accounting_documents": {
                    "results": [{**invoice_key, "FinancialAccountType": "K", "Supplier": "1000123", "PostingDate": "2026-08-01", "NetDueDate": "2026-08-31", "AmountInTransactionCurrency": "100.00", "TransactionCurrency": "CNY", "IsCleared": True, "ClearingAccountingDocument": "9", "ClearingDocFiscalYear": "2026", "ClearingDate": "2026-08-15"}],
                    "source_complete": True,
                },
                "clearing_documents": {
                    "results": [{"CompanyCode": "1010", "FiscalYear": "2026", "AccountingDocument": "9", "Supplier": "1000123", "AccountingDocumentType": "ZP", "HouseBank": "HB1", "PaymentMethod": "T"}],
                    "source_complete": True,
                },
            }
        elif entity == "A_SalesOrder" and plan.get("plan_kind") == "multi_step":
            step_results = {
                "sales_order": {
                    "results": [{"SalesOrder": "5814", "SoldToParty": "17100001"}],
                    "source_complete": True,
                },
                "sales_order_items": {"results": [{"SalesOrder": "5814"}], "source_complete": True},
                "delivery_items": {
                    "results": [{"ReferenceSDDocument": "5814", "DeliveryDocument": "80000023", "GoodsMovementStatus": "C"}],
                    "source_complete": True,
                },
                "delivery_headers": {"results": [{"DeliveryDocument": "80000023", "ActualGoodsMovementDate": "2026-08-10"}], "source_complete": True},
                "billing_items": {"results": [{"ReferenceSDDocument": "80000023", "BillingDocument": "90000025"}], "source_complete": True},
                "billing_headers": {"results": [{"BillingDocument": "90000025"}], "source_complete": True},
                "accounting_items": {
                    "results": [{"CompanyCode": "1010", "Customer": "17100001", "IsCleared": True, "ClearingAccountingDocument": "14"}],
                    "source_complete": True,
                },
            }
        elif any(item.get("field") == "Supplier" for item in plan.get("filters") or []):
            step_results = {
                "supplier_items": {
                    "results": [{"CompanyCode": "1010", "Supplier": "1000123", "IsCleared": False}],
                    "source_complete": True,
                },
                "clearing_documents": {"results": [], "source_complete": True},
            }
        elif any(item.get("field") == "Customer" for item in plan.get("filters") or []):
            step_results = {
                "customer_items": {
                    "results": [{"CompanyCode": "1010", "Customer": "17100001", "IsCleared": False}],
                    "source_complete": True,
                },
                "clearing_documents": {"results": [], "source_complete": True},
            }
        else:
            step_results = {"results": {"results": [], "source_complete": True}}
        return {
            "ok": True,
            "case_id": "workflow-case",
            "step_results": step_results,
            "source_complete": True,
            "pagination": {"has_next": False, "total_count_known": True},
        }

    async def execute_get(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self.execute_plan(request)


class WorkflowPlanner:
    async def plan(self, *args: Any, **kwargs: Any) -> PlannerDecision:
        raise AssertionError("Published workflow execution must not invoke Codex planning")

    async def review_workflow(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "verdict": "pass",
            "issues": [],
            "summary": {"zh": "工作流契约通过复核。", "en": "Workflow contract passed review."},
            "thread_id": "workflow-review-thread",
        }


class EmptyWorkflowDiscoveryProvider(WorkflowSapProvider):
    purchase_order_candidates: list[str] = []
    sales_order_candidates: list[str] = []


class EmptyP2PEvidenceProvider(WorkflowSapProvider):
    async def execute_plan(
        self, plan: dict[str, Any], query: str = "", conversation_id: str | None = None
    ) -> dict[str, Any]:
        if plan.get("entity_set") == "A_PurchaseOrder" and plan.get("plan_kind") == "multi_step":
            self.execute_count += 1
            step_ids = [str(item.get("step_id") or "") for item in plan.get("steps") or []]
            return {
                "ok": True,
                "case_id": "empty-p2p-evidence",
                "step_results": {
                    step_id: {"results": [], "source_complete": True}
                    for step_id in step_ids
                    if step_id
                },
                "source_complete": True,
                "pagination": {"has_next": False, "total_count_known": True},
            }
        return await super().execute_plan(plan, query, conversation_id)


class BlockingWorkflowPlanner(WorkflowPlanner):
    async def review_workflow(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "verdict": "block",
            "issues": [
                {
                    "code": "workflow_mapping_review_failed",
                    "severity": "error",
                    "node_id": "ap",
                    "port": "ap_payment_scopes",
                    "message": {"zh": "应付证据映射不明确。", "en": "AP evidence mapping is ambiguous."},
                }
            ],
            "summary": {"zh": "必须先修复映射。", "en": "Fix the mapping before validation."},
            "thread_id": "blocked-review-thread",
        }


class InvalidReviewPlanner(WorkflowPlanner):
    async def review_workflow(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"zh": "旧格式", "en": "Legacy format"}


class FalsePositiveSkipReviewPlanner(WorkflowPlanner):
    def __init__(self) -> None:
        self.review_contract: dict[str, Any] | None = None

    async def review_workflow(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.review_contract = kwargs.get("review_contract")
        return {
            "verdict": "block",
            "issues": [
                {
                    "code": "workflow_conditional_skip_output_missing",
                    "severity": "error",
                    "node_id": "ap",
                    "port": port,
                    "message": {
                        "zh": f"跳过路径缺少{port}。",
                        "en": f"The skip path is missing {port}.",
                    },
                }
                for port in ("query_mode", "as_of")
            ],
            "summary": {
                "zh": "错误地要求完整Agent输出。",
                "en": "Incorrectly required the full Agent output contract.",
            },
            "thread_id": "false-positive-review-thread",
        }


def _settings(tmp_path: Path) -> Settings:
    root = Path(__file__).resolve().parents[1]
    return Settings(
        repository_root=root,
        data_root=tmp_path / "data",
        draft_root=tmp_path / "drafts",
        skillhub_root=tmp_path / "skillhub",
        max_run_seconds=10,
        enforce_agent_acceptance=False,
    )


def _p2p_ap_workflow() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    agents = AgentRepository(root / "agents")
    p2p = agents.get("procure-to-pay-status")
    ap = agents.get("ap-payment")
    return {
        "schemaVersion": 2,
        "id": "p2p-payment-review",
        "version": "0.1.0",
        "title": {"zh": "采购到付款复核", "en": "P2P payment review"},
        "description": {"zh": "", "en": ""},
        "mode": "deterministic",
        "readOnly": True,
        "inputSchema": {
            "type": "object",
            "properties": {
                "purchase_orders": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "uniqueItems": True,
                    "items": {"type": "string", "pattern": "^[0-9]{1,10}$"},
                },
                "as_of": {"type": "string", "format": "date"},
            },
            "required": ["purchase_orders", "as_of"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "payment_status": {"type": "string"},
                "payment_report": {"type": "object"},
                "ap_details": {"type": "array", "items": {"type": "object"}},
                "p2p_details": {"type": "array", "items": {"type": "object"}},
                "source_complete": {"type": "boolean"},
                "evidence_complete": {"type": "boolean"},
                "payment_run_evidence_complete": {"type": "boolean"},
                "bank_master_evidence_complete": {"type": "boolean"},
                "bank_settlement_evidence_complete": {"type": "boolean"},
                "bank_settlement_status": {"type": "string", "const": "not_assessed"},
            },
            "required": [
                "p2p_details",
                "ap_details",
                "payment_status",
                "payment_report",
                "source_complete",
                "evidence_complete",
                "payment_run_evidence_complete",
                "bank_master_evidence_complete",
                "bank_settlement_evidence_complete",
                "bank_settlement_status",
            ],
            "additionalProperties": False,
        },
        "nodes": [
            {"id": "p2p", "agentId": p2p["slug"], "agentVersion": p2p["version"], "agentDigest": agent_digest(p2p)},
            {
                "id": "ap",
                "agentId": ap["slug"],
                "agentVersion": ap["version"],
                "agentDigest": agent_digest(ap),
                "runIf": {
                    "source": {"scope": "node_output", "nodeId": "p2p", "port": "ap_payment_scopes"},
                    "operator": "non_empty",
                },
                "onSkip": {
                    "reasonCode": "no_ap_payment_scopes",
                    "outputs": {
                        "scope_results": [],
                        "business_status": "inconclusive",
                        "source_complete": False,
                        "evidence_complete": False,
                        "payment_run_evidence_complete": False,
                        "bank_master_evidence_complete": False,
                        "bank_settlement_evidence_complete": False,
                        "bank_settlement_status": "not_assessed",
                        "business_report": {
                            "status": "inconclusive",
                            "reason_code": "no_ap_payment_scopes",
                            "summary": {
                                "zh": "P2P 未生成可供付款准备复核的应付证据分组，AP 阶段未执行。",
                                "en": "P2P produced no AP evidence scope for payment-readiness review, so the AP stage was not executed.",
                            },
                        },
                    },
                },
            },
        ],
        "connections": [
            {"from": {"scope": "workflow_input", "port": "purchase_orders"}, "to": {"nodeId": "p2p", "port": "purchase_orders"}, "transform": {"type": "identity"}},
            {"from": {"scope": "constant", "value": "p2p_evidence"}, "to": {"nodeId": "ap", "port": "query_mode"}, "transform": {"type": "identity"}},
            {"from": {"scope": "node_output", "nodeId": "p2p", "port": "ap_payment_scopes"}, "to": {"nodeId": "ap", "port": "ap_payment_scopes"}, "transform": {"type": "identity"}},
            {"from": {"scope": "workflow_input", "port": "as_of"}, "to": {"nodeId": "ap", "port": "as_of"}, "transform": {"type": "identity"}},
        ],
        "outputs": [
            {"name": "p2p_details", "source": {"scope": "node_output", "nodeId": "p2p", "port": "po_results"}, "transform": {"type": "identity"}},
            {"name": "ap_details", "source": {"scope": "node_output", "nodeId": "ap", "port": "scope_results"}, "transform": {"type": "identity"}},
            {
                "name": "source_complete",
                "aggregate": {
                    "operator": "all_true",
                    "sources": [
                        {"scope": "node_output", "nodeId": "p2p", "port": "source_complete"},
                        {"scope": "node_output", "nodeId": "ap", "port": "source_complete"},
                    ],
                },
            },
            {
                "name": "evidence_complete",
                "aggregate": {
                    "operator": "all_true",
                    "sources": [
                        {"scope": "node_output", "nodeId": "p2p", "port": "evidence_complete"},
                        {"scope": "node_output", "nodeId": "ap", "port": "evidence_complete"},
                    ],
                },
            },
            {"name": "payment_status", "source": {"scope": "node_output", "nodeId": "ap", "port": "business_status"}, "transform": {"type": "identity"}},
            {"name": "payment_report", "source": {"scope": "node_output", "nodeId": "ap", "port": "business_report"}, "transform": {"type": "identity"}},
            {"name": "payment_run_evidence_complete", "source": {"scope": "node_output", "nodeId": "ap", "port": "payment_run_evidence_complete"}, "transform": {"type": "identity"}},
            {"name": "bank_master_evidence_complete", "source": {"scope": "node_output", "nodeId": "ap", "port": "bank_master_evidence_complete"}, "transform": {"type": "identity"}},
            {"name": "bank_settlement_evidence_complete", "source": {"scope": "node_output", "nodeId": "ap", "port": "bank_settlement_evidence_complete"}, "transform": {"type": "identity"}},
            {"name": "bank_settlement_status", "source": {"scope": "node_output", "nodeId": "ap", "port": "bank_settlement_status"}, "transform": {"type": "identity"}},
        ],
        "policies": {"onInconclusive": "continue_if_required_outputs_present"},
    }


def _o2c_ar_workflow() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    agents = AgentRepository(root / "agents")
    o2c = agents.get("order-to-cash-status")
    ar = agents.get("ar-collection")
    return {
        "schemaVersion": 1,
        "id": "o2c-collection-review",
        "version": "0.1.0",
        "title": {"zh": "订单到收款复核", "en": "O2C collection review"},
        "description": {"zh": "", "en": ""},
        "mode": "deterministic",
        "readOnly": True,
        "inputSchema": {
            "type": "object",
            "properties": {
                "sales_order": {"type": "string", "pattern": "^[0-9]+$"},
                "as_of": {"type": "string", "format": "date"},
            },
            "required": ["sales_order", "as_of"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "collection_status": {"type": "string"},
                "collection_report": {"type": "object"},
            },
            "required": ["collection_status", "collection_report"],
            "additionalProperties": False,
        },
        "nodes": [
            {"id": "o2c", "agentId": o2c["slug"], "agentVersion": o2c["version"], "agentDigest": agent_digest(o2c)},
            {"id": "ar", "agentId": ar["slug"], "agentVersion": ar["version"], "agentDigest": agent_digest(ar)},
        ],
        "connections": [
            {"from": {"scope": "workflow_input", "port": "sales_order"}, "to": {"nodeId": "o2c", "port": "sales_order"}, "transform": {"type": "identity"}},
            {"from": {"scope": "node_output", "nodeId": "o2c", "port": "company_code"}, "to": {"nodeId": "ar", "port": "company_code"}, "transform": {"type": "identity"}},
            {"from": {"scope": "node_output", "nodeId": "o2c", "port": "customer"}, "to": {"nodeId": "ar", "port": "customers"}, "transform": {"type": "wrap_array"}},
            {"from": {"scope": "workflow_input", "port": "as_of"}, "to": {"nodeId": "ar", "port": "as_of"}, "transform": {"type": "identity"}},
        ],
        "outputs": [
            {"name": "collection_status", "source": {"scope": "node_output", "nodeId": "ar", "port": "business_status"}, "transform": {"type": "identity"}},
            {"name": "collection_report", "source": {"scope": "node_output", "nodeId": "ar", "port": "business_report"}, "transform": {"type": "identity"}},
        ],
        "policies": {"onInconclusive": "continue_if_required_outputs_present"},
    }


def _wait(client: TestClient, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        value = client.get(f"/api/runs/{run_id}").json()
        if value["status"] in {"completed", "inconclusive", "failed", "cancelled"}:
            return value
        time.sleep(0.03)
    raise AssertionError("Workflow run did not finish")


def _wait_draft(client: TestClient, draft_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        value = client.get(f"/api/authoring/workflows/{draft_id}").json()
        if value["status"] in {"validated", "inconclusive", "invalid", "needs_review", "published"}:
            return value
        time.sleep(0.03)
    raise AssertionError("Workflow draft status did not settle")


def test_workflow_schema_validates_ports_order_and_cycles() -> None:
    root = Path(__file__).resolve().parents[1]
    agents = AgentRepository(root / "agents")
    workflow = _p2p_ap_workflow()
    validate_workflow(workflow, agents)
    assert topological_order(workflow) == ["p2p", "ap"]
    workflow["connections"].append(
        {"from": {"scope": "node_output", "nodeId": "ap", "port": "company_code"}, "to": {"nodeId": "p2p", "port": "purchase_orders"}, "transform": {"type": "identity"}}
    )
    try:
        validate_workflow(workflow, agents)
    except WorkflowError:
        pass
    else:
        raise AssertionError("Cycle or duplicate target mapping should be rejected")


def test_workflow_one_of_branch_requires_explicit_constant_and_rejects_conflict() -> None:
    root = Path(__file__).resolve().parents[1]
    agents = AgentRepository(root / "agents")
    workflow = _p2p_ap_workflow()
    workflow["connections"] = [
        item for item in workflow["connections"] if item["to"]["port"] != "query_mode"
    ]
    try:
        validate_workflow(workflow, agents)
    except WorkflowError as exc:
        assert exc.code == "workflow_branch_unmatched"
    else:
        raise AssertionError("Implicit oneOf mode selection must be rejected")

    workflow = _p2p_ap_workflow()
    query_mode = next(item for item in workflow["connections"] if item["to"]["port"] == "query_mode")
    query_mode["from"]["value"] = "direct"
    try:
        validate_workflow(workflow, agents)
    except WorkflowError as exc:
        assert exc.code == "workflow_branch_unmatched"
    else:
        raise AssertionError("A conflicting oneOf branch must be rejected")


def test_workflow_v2_foreach_grouping_and_aggregates_execute_in_stable_order(
    tmp_path: Path,
) -> None:
    workflow = _p2p_ap_foreach_workflow()
    agents = AgentRepository(Path(__file__).resolve().parents[1] / "agents")
    validate_workflow(workflow, agents)
    assert topological_order(workflow) == ["p2p", "ap"]

    app = create_app(
        _settings(tmp_path),
        planner=WorkflowPlanner(),
        embedded_provider=WorkflowSapProvider(),
    )
    with TestClient(app) as client:
        created = client.post("/api/authoring/workflows", json={"workflow": workflow})
        assert created.status_code == 201, created.text
        validation = client.post(
            f"/api/authoring/workflows/{created.json()['draft_id']}/validate",
            json={
                "autoDiscover": False,
                "input": {"purchase_orders": ["4500000001"], "as_of": "2026-08-17"},
            },
        )
        assert validation.status_code == 202, validation.text
        run = _wait(client, validation.json()["validation_run_id"])
        assert run["status"] == "inconclusive"
        foreach_result = run["result"]["node_results"][1]
        assert foreach_result["iterations"][0]["iteration_index"] == 0
        assert foreach_result["iterations"][0]["input"]["ap_payment_scopes"][0][
            "scope_id"
        ] == "1010:1000123"
        assert run["result"]["workflow_output"] == {
            "payment_status": "complete",
            "source_complete": True,
            "evidence_complete": True,
            "scope_results": [
                {
                    "scope_id": "1010:1000123",
                    "company_code": "1010",
                    "supplier": "1000123",
                    "purchase_orders": ["4500000001"],
                    "business_status": "complete",
                    "open_item_count": 0,
                    "payment_blocked_count": 0,
                        "source_complete": True,
                        "evidence_complete": True,
                        "payment_run_evidence_complete": False,
                        "bank_master_evidence_complete": False,
                        "bank_settlement_evidence_complete": False,
                        "bank_settlement_status": "not_assessed",
                }
            ],
        }
        child_runs = [
            item
            for item in client.get("/api/runs").json()
            if item["parent_run_id"] == run["run_id"] and item["node_id"] == "ap"
        ]
        assert len(child_runs) == 1


def test_conditional_required_outputs_require_valid_on_skip_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    agents = AgentRepository(root / "agents")

    missing = _p2p_ap_workflow()
    missing["nodes"][1].pop("onSkip")
    try:
        validate_workflow(missing, agents)
    except WorkflowError as exc:
        assert exc.code == "workflow_conditional_skip_output_missing"
    else:
        raise AssertionError("A required conditional output without onSkip was accepted")

    invalid_type = _p2p_ap_workflow()
    invalid_type["nodes"][1]["onSkip"]["outputs"]["scope_results"] = "none"
    try:
        validate_workflow(invalid_type, agents)
    except WorkflowError as exc:
        assert exc.code == "workflow_conditional_skip_output_invalid"
        assert exc.detail == {"node_id": "ap", "port": "scope_results"}
    else:
        raise AssertionError("An invalid onSkip output type was accepted")

    unknown_port = _p2p_ap_workflow()
    unknown_port["nodes"][1]["onSkip"]["outputs"]["undeclared"] = False
    try:
        validate_workflow(unknown_port, agents)
    except WorkflowError as exc:
        assert exc.code == "workflow_conditional_skip_output_invalid"
        assert exc.detail == {"node_id": "ap", "port": "undeclared"}
    else:
        raise AssertionError("An undeclared onSkip output was accepted")


def _p2p_ap_foreach_workflow() -> dict[str, Any]:
    workflow = deepcopy(_p2p_ap_workflow())
    workflow["id"] = "p2p-payment-review-foreach"
    workflow["nodes"][1]["forEach"] = {
        "source": {
            "scope": "node_output",
            "nodeId": "p2p",
            "port": "ap_payment_scopes",
        },
        "groupBy": {
            "company_code": "/company_code",
            "supplier": "/supplier",
        },
        "maxItems": 50,
        "maxConcurrency": 4,
        "onItemError": "collect_inconclusive",
    }
    workflow["connections"] = [
        workflow["connections"][0],
        workflow["connections"][1],
        {
            "from": {"scope": "iteration_item", "pointer": "/items"},
            "to": {"nodeId": "ap", "port": "ap_payment_scopes"},
            "transform": {"type": "identity"},
        },
        workflow["connections"][-1],
    ]
    workflow["outputSchema"] = {
        "type": "object",
        "properties": {
            "payment_status": {
                "type": "string",
                "enum": ["inconclusive", "blocked", "in_progress", "complete"],
            },
            "source_complete": {"type": "boolean"},
            "evidence_complete": {"type": "boolean"},
            "scope_results": {"type": "array", "items": {"type": "object"}},
        },
        "required": [],
        "additionalProperties": False,
    }
    workflow["outputs"] = [
        {
            "name": "payment_status",
            "aggregate": {
                "operator": "status_precedence",
                "sources": [
                    {"scope": "node_output", "nodeId": "ap", "port": "business_status"}
                ],
                "precedence": ["inconclusive", "blocked", "in_progress", "complete"],
            },
        },
        {
            "name": "source_complete",
            "aggregate": {
                "operator": "all_true",
                "sources": [
                    {"scope": "node_output", "nodeId": "ap", "port": "source_complete"}
                ],
            },
        },
        {
            "name": "evidence_complete",
            "aggregate": {
                "operator": "all_true",
                "sources": [
                    {"scope": "node_output", "nodeId": "ap", "port": "evidence_complete"}
                ],
            },
        },
        {
            "name": "scope_results",
            "aggregate": {
                "operator": "collect",
                "sources": [
                    {"scope": "node_output", "nodeId": "ap", "port": "scope_results"}
                ],
            },
        },
    ]
    return workflow


def test_workflow_allows_unmapped_required_inputs_with_server_defaults_only() -> None:
    root = Path(__file__).resolve().parents[1]
    agents = AgentRepository(root / "agents")
    agent = agents.get("material-shortage-procurement-response")
    input_properties = {
        name: agent["execution"]["inputSchema"]["properties"][name]
        for name in (
            "material",
            "plant",
            "mrp_area",
            "purchasing_organization",
            "as_of",
        )
    }
    workflow = {
        "schemaVersion": 1,
        "id": "shortage-default-profile",
        "version": "0.1.0",
        "title": {"zh": "短缺默认参数", "en": "Shortage defaults"},
        "description": {"zh": "", "en": ""},
        "mode": "deterministic",
        "readOnly": True,
        "inputSchema": {
            "type": "object",
            "properties": input_properties,
            "required": list(input_properties),
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"business_status": {"type": "string"}},
            "required": ["business_status"],
            "additionalProperties": False,
        },
        "nodes": [
            {
                "id": "shortage",
                "agentId": agent["slug"],
                "agentVersion": agent["version"],
                "agentDigest": agent_digest(agent),
            }
        ],
        "connections": [
            {
                "from": {"scope": "workflow_input", "port": name},
                "to": {"nodeId": "shortage", "port": name},
                "transform": {"type": "identity"},
            }
            for name in input_properties
        ],
        "outputs": [
            {
                "name": "business_status",
                "source": {
                    "scope": "node_output",
                    "nodeId": "shortage",
                    "port": "business_status",
                },
                "transform": {"type": "identity"},
            }
        ],
        "policies": {"onInconclusive": "continue_if_required_outputs_present"},
    }

    validate_workflow(workflow, agents)

    workflow["connections"] = [
        item
        for item in workflow["connections"]
        if item["to"]["port"] != "material"
    ]
    try:
        validate_workflow(workflow, agents)
    except WorkflowError as exc:
        assert exc.code == "workflow_required_input_unmapped"
        assert "material" in str(exc)
    else:
        raise AssertionError("A required input without a server default was not rejected")


def test_workflow_draft_live_validation_executes_pinned_agents_without_codex_runtime_planning(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path),
        planner=WorkflowPlanner(),
        embedded_provider=WorkflowSapProvider(),
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/authoring/workflows",
            json={"workflow": _p2p_ap_workflow()},
        )
        assert created.status_code == 201, created.text
        draft = created.json()
        initial_revision = client.get(
            f"/api/authoring/workflows/{draft['draft_id']}/revisions"
        ).json()["items"][0]
        assert initial_revision["diff"] == [{"op": "create", "path": "/"}]
        validation = client.post(
            f"/api/authoring/workflows/{draft['draft_id']}/validate",
            json={
                "autoDiscover": False,
                "input": {"purchase_orders": ["4500000001"], "as_of": "2026-08-17"},
            },
        )
        assert validation.status_code == 202, validation.text
        run_id = validation.json()["validation_run_id"]
        run = _wait(client, run_id)
        assert run["status"] == "inconclusive"
        assert [item["agent_id"] for item in run["result"]["node_results"]] == [
            "procure-to-pay-status",
            "ap-payment",
        ]
        ap_input = run["result"]["node_results"][1]["input"]
        assert ap_input["as_of"] == "2026-08-17"
        assert ap_input["ap_payment_scopes"][0]["scope_id"] == "1010:1000123"
        assert ap_input["ap_payment_scopes"][0]["purchase_orders"] == ["4500000001"]
        workflow_output = run["result"]["workflow_output"]
        assert workflow_output["payment_status"] == "complete"
        assert workflow_output["ap_details"][0]["scope_id"] == "1010:1000123"
        assert workflow_output["payment_report"].get("reason_code") != "no_ap_payment_scopes"
        assert run["result"]["node_results"][1]["status"] != "skipped"
        events = [item.type for item in app.state.store.events_after(run_id)]
        assert "workflow_started" in events
        assert "node_inconclusive" in events
        children = [item for item in client.get("/api/runs").json() if item["parent_run_id"] == run_id]
        assert {item["node_id"] for item in children} == {"p2p", "ap"}
        retained_revision = client.get(
            f"/api/authoring/workflows/{draft['draft_id']}/revisions"
        ).json()["items"][0]
        assert retained_revision["diff"] == initial_revision["diff"]


def test_workflow_live_validation_auto_discovers_required_array_and_date(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path),
        planner=WorkflowPlanner(),
        embedded_provider=WorkflowSapProvider(),
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/authoring/workflows",
            json={"workflow": _p2p_ap_workflow()},
        ).json()
        validation = client.post(
            f"/api/authoring/workflows/{created['draft_id']}/validate",
            json={"autoDiscover": True, "input": {"purchase_orders": []}},
        )
        assert validation.status_code == 202, validation.text
        run = _wait(client, validation.json()["validation_run_id"])
        p2p_input = run["result"]["node_results"][0]["input"]
        ap_input = run["result"]["node_results"][1]["input"]
        assert p2p_input["purchase_orders"] == ["4500000001"]
        assert ap_input["as_of"]
        events = app.state.store.events_after(validation.json()["validation_run_id"])
        discovery = next(item for item in events if item.type == "candidate_discovery_completed")
        assert discovery.data == {
            "auto_discover": True,
            "input_fields": ["as_of", "purchase_orders"],
        }


def test_workflow_live_validation_reports_structured_missing_discovery_fields(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path),
        planner=WorkflowPlanner(),
        embedded_provider=EmptyWorkflowDiscoveryProvider(),
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/authoring/workflows",
            json={"workflow": _p2p_ap_workflow()},
        ).json()
        validation = client.post(
            f"/api/authoring/workflows/{created['draft_id']}/validate",
            json={"autoDiscover": True, "input": {}},
        )
        assert validation.status_code == 409
        envelope = validation.json()["detail"]
        assert envelope["code"] == "workflow_validation_input_unavailable"
        assert envelope["detail"]["missing_fields"] == ["purchase_orders"]
        assert "purchase_orders" in envelope["detail"]["supported_fields"]


def test_empty_upstream_collection_skips_ap_without_creating_child_run(tmp_path: Path) -> None:
    provider = EmptyP2PEvidenceProvider()
    app = create_app(
        _settings(tmp_path),
        planner=WorkflowPlanner(),
        embedded_provider=provider,
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/authoring/workflows", json={"workflow": _p2p_ap_workflow()}
        ).json()
        validation = client.post(
            f"/api/authoring/workflows/{created['draft_id']}/validate",
            json={
                "autoDiscover": False,
                "input": {"purchase_orders": ["9999999999"], "as_of": "2026-08-29"},
            },
        )
        assert validation.status_code == 202, validation.text
        run = _wait(client, validation.json()["validation_run_id"])
        assert run["status"] == "inconclusive"
        workflow_output = run["result"]["workflow_output"]
        assert workflow_output["p2p_details"]
        assert workflow_output["ap_details"] == []
        assert workflow_output["payment_status"] == "inconclusive"
        assert workflow_output["source_complete"] is False
        assert workflow_output["evidence_complete"] is False
        assert workflow_output["payment_run_evidence_complete"] is False
        assert workflow_output["bank_master_evidence_complete"] is False
        assert workflow_output["bank_settlement_evidence_complete"] is False
        assert workflow_output["bank_settlement_status"] == "not_assessed"
        assert workflow_output["payment_report"]["reason_code"] == "no_ap_payment_scopes"
        assert run["result"]["completeness"]["source_complete"] is False
        assert run["result"]["completeness"]["business_complete"] is False
        skipped = run["result"]["node_results"][1]
        assert skipped["status"] == "skipped"
        assert skipped["error"]["code"] == "node_skipped_empty_input"
        assert skipped["error"]["reason_code"] == "no_ap_payment_scopes"
        assert skipped["output"]["scope_results"] == []
        children = [
            item
            for item in client.get("/api/runs").json()
            if item["parent_run_id"] == run["run_id"] and item["node_id"] == "ap"
        ]
        assert children == []
        events = [item.type for item in app.state.store.events_after(run["run_id"])]
        assert "node_skipped_empty_input" in events


def test_runtime_review_block_and_invalid_contract_fail_closed_before_sap(tmp_path: Path) -> None:
    for planner in (BlockingWorkflowPlanner(), InvalidReviewPlanner()):
        case_root = tmp_path / type(planner).__name__
        provider = WorkflowSapProvider()
        app = create_app(
            _settings(case_root),
            planner=planner,
            embedded_provider=provider,
        )
        with TestClient(app) as client:
            created = client.post(
                "/api/authoring/workflows", json={"workflow": _p2p_ap_workflow()}
            ).json()
            response = client.post(
                f"/api/authoring/workflows/{created['draft_id']}/validate",
                json={
                    "autoDiscover": True,
                    "input": {},
                },
            )
            assert response.status_code == 409
            expected = (
                "workflow_runtime_review_blocked"
                if isinstance(planner, BlockingWorkflowPlanner)
                else "workflow_runtime_review_unavailable"
            )
            assert response.json()["detail"]["code"] == expected
            latest = client.get(
                f"/api/authoring/workflows/{created['draft_id']}"
            ).json()
            assert latest["status"] == "needs_review"
            assert latest["validation_run_id"] is None
            assert latest["validation"]["runtime_review"]["verdict"] == "block"
            assert client.get("/api/runs").json() == []
            assert provider.execute_count == 0


def test_runtime_review_contract_dismisses_unconsumed_skip_output_false_positives(
    tmp_path: Path,
) -> None:
    workflow = _p2p_ap_workflow()
    agents = AgentRepository(Path(__file__).resolve().parents[1] / "agents")
    contract = workflow_review_contract(workflow, agents)
    assert contract["review_policy_version"] == 2
    assert contract["selected_one_of_branches"]["ap"]["branch_index"] == 1
    assert contract["selected_one_of_branches"]["ap"]["constant_inputs"] == {
        "query_mode": "p2p_evidence"
    }
    assert set(contract["required_on_skip_outputs_by_node"]["ap"]) == {
        "scope_results",
        "business_status",
        "source_complete",
        "evidence_complete",
        "payment_run_evidence_complete",
        "bank_master_evidence_complete",
        "bank_settlement_evidence_complete",
        "bank_settlement_status",
        "business_report",
    }
    assert "query_mode" not in contract["required_on_skip_outputs_by_node"]["ap"]
    assert "as_of" not in contract["required_on_skip_outputs_by_node"]["ap"]

    planner = FalsePositiveSkipReviewPlanner()
    app = create_app(
        _settings(tmp_path),
        planner=planner,
        embedded_provider=WorkflowSapProvider(),
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/authoring/workflows", json={"workflow": workflow}
        ).json()
        response = client.post(
            f"/api/authoring/workflows/{created['draft_id']}/validate",
            json={
                "autoDiscover": False,
                "input": {"purchase_orders": ["4500000001"], "as_of": "2026-08-29"},
            },
        )
        assert response.status_code == 202
        review = response.json()["validation"]["runtime_review"]
        assert review["raw_verdict"] == "block"
        assert review["verdict"] == "pass"
        assert review["issues"] == []
        assert [item["port"] for item in review["dismissed_issues"]] == [
            "query_mode",
            "as_of",
        ]
        assert response.json()["validation_run_id"]
        assert planner.review_contract == contract


def test_runtime_review_reconciliation_keeps_a_real_missing_skip_output() -> None:
    workflow = _p2p_ap_workflow()
    agents = AgentRepository(Path(__file__).resolve().parents[1] / "agents")
    contract = workflow_review_contract(workflow, agents)
    workflow["nodes"][1]["onSkip"]["outputs"].pop("scope_results")
    review = _reconcile_runtime_review(
        {
            "verdict": "block",
            "issues": [
                {
                    "code": "workflow_conditional_skip_output_missing",
                    "severity": "error",
                    "node_id": "ap",
                    "port": "scope_results",
                    "message": {"zh": "缺少结果。", "en": "Result missing."},
                }
            ],
            "summary": {"zh": "阻塞。", "en": "Blocked."},
        },
        review_contract=contract,
        workflow=workflow,
    )
    assert review["verdict"] == "block"
    assert review["dismissed_issues"] == []
    assert review["issues"][0]["port"] == "scope_results"


def test_workflow_agent_version_drift_fails_closed() -> None:
    root = Path(__file__).resolve().parents[1]
    agents = AgentRepository(root / "agents")
    workflow = _p2p_ap_workflow()
    workflow["nodes"][0]["agentDigest"] = "sha256:stale"
    try:
        validate_workflow(workflow, agents)
    except WorkflowError as exc:
        assert exc.code == "agent_version_mismatch"
    else:
        raise AssertionError("Agent digest drift must fail closed")


def test_o2c_ar_workflow_propagates_business_keys(tmp_path: Path) -> None:
    as_of = date.today().isoformat()
    app = create_app(
        _settings(tmp_path),
        planner=WorkflowPlanner(),
        embedded_provider=WorkflowSapProvider(),
    )
    with TestClient(app) as client:
        created = client.post("/api/authoring/workflows", json={"workflow": _o2c_ar_workflow()})
        draft = created.json()
        validation = client.post(
            f"/api/authoring/workflows/{draft['draft_id']}/validate",
            json={"autoDiscover": False, "input": {"sales_order": "5814", "as_of": as_of}},
        )
        assert validation.status_code == 202, validation.text
        run = _wait(client, validation.json()["validation_run_id"])
        assert run["status"] in {"completed", "inconclusive"}
        assert run["result"]["node_results"][1]["input"] == {
            "company_code": "1010",
            "customers": ["17100001"],
            "as_of": as_of,
            "business_date": as_of,
        }
        assert run["result"]["workflow_output"]["collection_report"]["headline"]["zh"]


def test_unknown_published_workflow_is_rejected_without_orphan_run(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path),
        planner=WorkflowPlanner(),
        embedded_provider=WorkflowSapProvider(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={"mode": "workflow", "workflowId": "missing-workflow", "input": {}},
        )
        assert response.status_code == 404
        assert client.get("/api/runs").json() == []


def test_validated_workflow_publishes_to_new_local_branch_with_revision(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path),
        planner=WorkflowPlanner(),
        embedded_provider=WorkflowSapProvider(),
    )
    repository = tmp_path / "publish-repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repository, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Workflow Test"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "workflow@example.invalid"], cwd=repository, check=True)
    (repository / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repository, check=True, capture_output=True)
    app.state.workflow_drafts.settings = replace(
        app.state.workflow_drafts.settings, repository_root=repository
    )

    with TestClient(app) as client:
        created = client.post("/api/authoring/workflows", json={"workflow": _p2p_ap_workflow()}).json()
        validation = client.post(
            f"/api/authoring/workflows/{created['draft_id']}/validate",
            json={"autoDiscover": False, "input": {"purchase_orders": ["4500000001"], "as_of": "2026-08-17"}},
        )
        _wait(client, validation.json()["validation_run_id"])
        settled = _wait_draft(client, created["draft_id"])
        assert settled["status"] == "inconclusive"
        report_response = client.get(
            f"/api/authoring/workflows/{created['draft_id']}/validation-report"
        )
        assert report_response.status_code == 200, report_response.text
        report = report_response.json()
        assert report["phase"] == "completed"
        assert report["verdict"] == "inconclusive"
        gap_codes = [item["code"] for item in report["evidence_gaps"]]
        markdown = client.get(
            f"/api/authoring/workflows/{created['draft_id']}/validation-artifacts/workflow-validation-report.md"
        )
        assert markdown.status_code == 200
        assert "Workflow Live Validation Report" in markdown.text
        rejected = client.post(
            f"/api/authoring/workflows/{created['draft_id']}/publish",
            json={"acknowledgeInconclusive": True},
        )
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "inconclusive_acknowledgement_required"
        published = client.post(
            f"/api/authoring/workflows/{created['draft_id']}/publish",
            json={
                "acknowledgeInconclusive": True,
                "validationRunId": report["run_id"],
                "validationReportDigest": report["report_digest"],
                "acceptedGapCodes": gap_codes,
            },
        )
        assert published.status_code == 200, published.text
        payload = published.json()
        assert payload["status"] == "published"
        assert payload["revision"] == 2
        assert payload["validation"]["published_from_revision"] == 1
        assert payload["validation"]["acknowledgement"]["accepted_gap_codes"] == sorted(
            gap_codes
        )
        assert (repository / "workflows" / "Common" / "p2p-payment-review" / "workflow.json").is_file()
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=repository, check=True, capture_output=True, text=True
        ).stdout.strip()
        assert branch == "codex/workflow-p2p-payment-review-v1.0.0"
        assert len(client.get(f"/api/authoring/workflows/{created['draft_id']}/revisions").json()["items"]) == 2


def test_workflow_validation_report_evaluates_user_expectations(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path),
        planner=WorkflowPlanner(),
        embedded_provider=WorkflowSapProvider(),
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/authoring/workflows", json={"workflow": _p2p_ap_workflow()}
        ).json()
        response = client.post(
            f"/api/authoring/workflows/{created['draft_id']}/validate",
            json={
                "autoDiscover": False,
                "input": {
                    "purchase_orders": ["4500000001"],
                    "as_of": "2026-08-17",
                },
                "expectations": [
                    {
                        "output": "payment_status",
                        "operator": "equals",
                        "expected": "blocked",
                    }
                ],
            },
        )
        assert response.status_code == 202, response.text
        _wait(client, response.json()["validation_run_id"])
        settled = _wait_draft(client, created["draft_id"])
        assert settled["status"] == "needs_review"
        report = client.get(
            f"/api/authoring/workflows/{created['draft_id']}/validation-report"
        ).json()
        assert report["verdict"] == "fail"
        assert report["user_expectations"] == [
            {
                "output": "payment_status",
                "operator": "equals",
                "expected": "blocked",
                "tolerance": None,
                "status": "fail",
                "actual": "complete",
                "present": True,
                "issue": None,
            }
        ]
        publish = client.post(
            f"/api/authoring/workflows/{created['draft_id']}/publish",
            json={"acknowledgeInconclusive": True},
        )
        assert publish.status_code == 409


def test_workflow_validation_rejects_unknown_expectation_output_before_run(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        planner=WorkflowPlanner(),
        embedded_provider=WorkflowSapProvider(),
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/authoring/workflows", json={"workflow": _p2p_ap_workflow()}
        ).json()
        response = client.post(
            f"/api/authoring/workflows/{created['draft_id']}/validate",
            json={
                "autoDiscover": False,
                "input": {
                    "purchase_orders": ["4500000001"],
                    "as_of": "2026-08-17",
                },
                "expectations": [
                    {"output": "unknown_output", "operator": "exists"}
                ],
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "workflow_validation_expectation_invalid"
        assert client.get("/api/runs").json() == []
