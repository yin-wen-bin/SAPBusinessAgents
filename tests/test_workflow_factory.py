from __future__ import annotations

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
from sap_business_agents_platform.workflows import (
    WorkflowError,
    agent_digest,
    topological_order,
    validate_workflow,
)


class WorkflowSapProvider:
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
        return {"ok": True, "status": "validated", "query": query}

    async def execute_plan(
        self, plan: dict[str, Any], query: str = "", conversation_id: str | None = None
    ) -> dict[str, Any]:
        del query, conversation_id
        entity = plan.get("entity_set")
        if entity == "A_PurchaseOrder" and plan.get("plan_kind") == "multi_step":
            step_results = {
                "purchase_order": {
                    "results": [{"PurchaseOrder": "4500000001", "CompanyCode": "1010", "Supplier": "1000123"}],
                    "source_complete": True,
                },
                "purchase_order_items": {"results": [{"PurchaseOrder": "4500000001"}], "source_complete": True},
                "material_documents": {"results": [{"PurchaseOrder": "4500000001", "GoodsMovementType": "101"}], "source_complete": True},
                "supplier_invoice_items": {"results": [{"PurchaseOrder": "4500000001"}], "source_complete": True},
                "accounting_items": {
                    "results": [{"CompanyCode": "1010", "Supplier": "1000123", "IsCleared": True, "ClearingAccountingDocument": "9", "ClearingDocFiscalYear": "2026"}],
                    "source_complete": True,
                },
                "clearing_documents": {
                    "results": [{"CompanyCode": "1010", "Supplier": "1000123", "AccountingDocumentType": "ZP", "HouseBank": "HB1", "PaymentMethod": "T"}],
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
        "schemaVersion": 1,
        "id": "p2p-payment-review",
        "version": "0.1.0",
        "title": {"zh": "采购到付款复核", "en": "P2P payment review"},
        "description": {"zh": "", "en": ""},
        "mode": "deterministic",
        "readOnly": True,
        "inputSchema": {
            "type": "object",
            "properties": {
                "purchase_order": {"type": "string", "pattern": "^[0-9]+$"},
                "as_of": {"type": "string", "format": "date"},
            },
            "required": ["purchase_order", "as_of"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "payment_status": {"type": "string"},
                "payment_report": {"type": "object"},
            },
            "required": ["payment_status", "payment_report"],
            "additionalProperties": False,
        },
        "nodes": [
            {"id": "p2p", "agentId": p2p["slug"], "agentVersion": p2p["version"], "agentDigest": agent_digest(p2p)},
            {"id": "ap", "agentId": ap["slug"], "agentVersion": ap["version"], "agentDigest": agent_digest(ap)},
        ],
        "connections": [
            {"from": {"scope": "workflow_input", "port": "purchase_order"}, "to": {"nodeId": "p2p", "port": "purchase_order"}, "transform": {"type": "identity"}},
            {"from": {"scope": "node_output", "nodeId": "p2p", "port": "company_code"}, "to": {"nodeId": "ap", "port": "company_code"}, "transform": {"type": "identity"}},
            {"from": {"scope": "node_output", "nodeId": "p2p", "port": "supplier"}, "to": {"nodeId": "ap", "port": "supplier"}, "transform": {"type": "identity"}},
            {"from": {"scope": "workflow_input", "port": "as_of"}, "to": {"nodeId": "ap", "port": "as_of"}, "transform": {"type": "identity"}},
        ],
        "outputs": [
            {"name": "payment_status", "source": {"scope": "node_output", "nodeId": "ap", "port": "business_status"}, "transform": {"type": "identity"}},
            {"name": "payment_report", "source": {"scope": "node_output", "nodeId": "ap", "port": "business_report"}, "transform": {"type": "identity"}},
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
            {"from": {"scope": "node_output", "nodeId": "o2c", "port": "customer"}, "to": {"nodeId": "ar", "port": "customer"}, "transform": {"type": "identity"}},
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
        {"from": {"scope": "node_output", "nodeId": "ap", "port": "company_code"}, "to": {"nodeId": "p2p", "port": "purchase_order"}, "transform": {"type": "identity"}}
    )
    try:
        validate_workflow(workflow, agents)
    except WorkflowError:
        pass
    else:
        raise AssertionError("Cycle or duplicate target mapping should be rejected")


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
                "input": {"purchase_order": "4500000001", "as_of": "2026-08-17"},
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
        assert run["result"]["node_results"][1]["input"] == {
            "company_code": "1010",
            "supplier": "1000123",
            "as_of": "2026-08-17",
        }
        assert run["result"]["workflow_output"]["payment_status"] in {
            "attention",
            "capability_blocked",
        }
        events = [item.type for item in app.state.store.events_after(run_id)]
        assert "workflow_started" in events
        assert "node_inconclusive" in events
        children = [item for item in client.get("/api/runs").json() if item["parent_run_id"] == run_id]
        assert {item["node_id"] for item in children} == {"p2p", "ap"}
        retained_revision = client.get(
            f"/api/authoring/workflows/{draft['draft_id']}/revisions"
        ).json()["items"][0]
        assert retained_revision["diff"] == initial_revision["diff"]


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
            json={"autoDiscover": False, "input": {"sales_order": "5814", "as_of": "2026-08-17"}},
        )
        assert validation.status_code == 202, validation.text
        run = _wait(client, validation.json()["validation_run_id"])
        assert run["status"] == "completed"
        assert run["result"]["node_results"][1]["input"] == {
            "company_code": "1010",
            "customer": "17100001",
            "as_of": "2026-08-17",
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
            json={"autoDiscover": False, "input": {"purchase_order": "4500000001", "as_of": "2026-08-17"}},
        )
        _wait(client, validation.json()["validation_run_id"])
        settled = _wait_draft(client, created["draft_id"])
        assert settled["status"] == "inconclusive"
        published = client.post(
            f"/api/authoring/workflows/{created['draft_id']}/publish",
            json={"acknowledgeInconclusive": True},
        )
        assert published.status_code == 200, published.text
        payload = published.json()
        assert payload["status"] == "published"
        assert payload["revision"] == 2
        assert payload["validation"]["published_from_revision"] == 1
        assert (repository / "workflows" / "Common" / "p2p-payment-review" / "workflow.json").is_file()
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=repository, check=True, capture_output=True, text=True
        ).stdout.strip()
        assert branch == "codex/workflow-p2p-payment-review-v1.0.0"
        assert len(client.get(f"/api/authoring/workflows/{created['draft_id']}/revisions").json()["items"]) == 2
