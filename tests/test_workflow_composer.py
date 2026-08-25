from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.workflow_composer import (
    compact_agent_catalog,
    compile_workflow_proposal,
)


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


def _proposal(*, with_gap: bool = False) -> dict[str, Any]:
    stages: list[dict[str, Any]] = [
        {
            "id": "p2p",
            "capability": {"zh": "采购到付款状态", "en": "P2P status"},
            "agent_id": "procure-to-pay-status",
            "confidence": "high",
            "reason": {"zh": "覆盖采购凭证链", "en": "Covers the procurement chain"},
            "bindings": [],
            "requested_outputs": ["purchase_order"],
        },
        {
            "id": "ap",
            "capability": {"zh": "应付付款复核", "en": "AP payment review"},
            "agent_id": "ap-payment",
            "confidence": "high",
            "reason": {"zh": "覆盖供应商付款状态", "en": "Covers supplier payment status"},
            "bindings": [
                {
                    "input_port": "company_code",
                    "source_stage_id": "p2p",
                    "source_output_port": "company_code",
                },
                {
                    "input_port": "supplier",
                    "source_stage_id": "p2p",
                    "source_output_port": "supplier",
                },
            ],
            "requested_outputs": [],
        },
    ]
    if with_gap:
        stages.append(
            {
                "id": "bank_settlement",
                "capability": {"zh": "银行扣款核验", "en": "Bank settlement verification"},
                "agent_id": "",
                "confidence": "low",
                "reason": {"zh": "目录无匹配 Agent", "en": "No catalog match"},
                "gap_title": {"zh": "银行扣款核验 Agent", "en": "Bank settlement Agent"},
                "gap_description": {
                    "zh": "以付款凭证核验银行结算证据",
                    "en": "Verify bank settlement evidence from payment documents",
                },
                "required_inputs": [
                    {
                        "name": "payment_document",
                        "type": "string",
                        "required": True,
                        "description": {"zh": "付款凭证", "en": "Payment document"},
                    }
                ],
                "required_outputs": [
                    {
                        "name": "settlement_status",
                        "type": "string",
                        "required": True,
                        "description": {"zh": "结算状态", "en": "Settlement status"},
                    }
                ],
                "guardrails": {"zh": ["严格只读"], "en": ["Strictly read-only"]},
                "acceptance": {"zh": "三级真机验收", "en": "Three-stage live acceptance"},
                "bindings": [],
                "requested_outputs": [],
            }
        )
    return {
        "intent": {"zh": "复核采购付款", "en": "Review procurement payment"},
        "title": {"zh": "采购付款复核", "en": "Procurement payment review"},
        "description": {"zh": "只读复核采购付款", "en": "Read-only payment review"},
        "validation_defaults": {"purchase_order": "4500000030", "as_of": "2026-08-25"},
        "stages": stages,
    }


class CompositionPlanner:
    def __init__(self, *, clarify_once: bool = False, with_gap: bool = False) -> None:
        self.clarify_once = clarify_once
        self.with_gap = with_gap
        self.calls = 0

    async def compose_workflow(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        if self.clarify_once and not kwargs.get("clarification_input"):
            return {
                "needs_clarification": True,
                "clarification_question": "需要核验 SAP 付款凭证还是银行结算证据？",
                "proposal": {},
                "thread_id": "workflow-thread",
            }
        return {
            "needs_clarification": False,
            "clarification_question": "",
            "proposal": _proposal(with_gap=self.with_gap),
            "thread_id": "workflow-thread",
        }


def _wait_draft(client: TestClient, draft_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        value = client.get(f"/api/authoring/workflows/{draft_id}").json()
        if value["status"] not in {"planning"}:
            return value
        time.sleep(0.03)
    raise AssertionError("Workflow composition did not settle")


def test_compiler_pins_agents_and_connects_only_declared_same_name_ports() -> None:
    root = Path(__file__).resolve().parents[1]
    agents = AgentRepository(root / "agents")
    catalog = compact_agent_catalog(agents)
    workflow, composition = compile_workflow_proposal(
        workflow_id="generated-payment-review",
        requirement="检查采购订单付款状态",
        locale="zh",
        proposal=_proposal(),
        catalog=catalog,
        agents=agents,
    )

    assert [item["agentId"] for item in workflow["nodes"]] == [
        "procure-to-pay-status",
        "ap-payment",
    ]
    assert all(item.get("agentVersion") and item.get("agentDigest") for item in workflow["nodes"])
    node_bindings = {
        (item["from"].get("nodeId"), item["from"].get("port"), item["to"]["port"])
        for item in workflow["connections"]
        if item["from"]["scope"] == "node_output"
    }
    assert ("p2p", "company_code", "company_code") in node_bindings
    assert ("p2p", "supplier", "supplier") in node_bindings
    assert set(workflow["inputSchema"]["required"]) == {"purchase_order", "as_of"}
    assert composition["validation_defaults"] == {
        "purchase_order": "4500000030",
        "as_of": "2026-08-25",
    }
    assert composition["gaps"] == []


def test_compiler_infers_one_unambiguous_same_name_binding() -> None:
    root = Path(__file__).resolve().parents[1]
    agents = AgentRepository(root / "agents")
    proposal = _proposal()
    proposal["stages"][1]["bindings"] = []
    workflow, _composition = compile_workflow_proposal(
        workflow_id="generated-auto-binding",
        requirement="检查采购订单付款状态",
        locale="zh",
        proposal=proposal,
        catalog=compact_agent_catalog(agents),
        agents=agents,
    )

    node_bindings = {
        (item["from"].get("nodeId"), item["from"].get("port"), item["to"]["port"])
        for item in workflow["connections"]
        if item["from"]["scope"] == "node_output"
    }
    assert ("p2p", "company_code", "company_code") in node_bindings
    assert ("p2p", "supplier", "supplier") in node_bindings


def test_composition_api_supports_one_clarification_then_generates_draft(tmp_path: Path) -> None:
    planner = CompositionPlanner(clarify_once=True)
    app = create_app(_settings(tmp_path), planner=planner)
    with TestClient(app) as client:
        response = client.post(
            "/api/authoring/workflows/compose",
            json={"requirement": "检查采购订单付款状态", "locale": "zh"},
        )
        assert response.status_code == 202, response.text
        waiting = _wait_draft(client, response.json()["draft_id"])
        assert waiting["status"] == "waiting_input"
        continued = client.post(
            f"/api/authoring/workflows/{waiting['draft_id']}/composition-input",
            json={"input": "只核验 SAP 付款凭证"},
        )
        assert continued.status_code == 202, continued.text
        generated = _wait_draft(client, waiting["draft_id"])
        assert generated["status"] == "draft"
        assert generated["composition"]["clarification_history"][0]["answer"] == "只核验 SAP 付款凭证"
        assert planner.calls == 2


def test_composition_rejects_blank_requirements(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), planner=CompositionPlanner())
    with TestClient(app) as client:
        response = client.post(
            "/api/authoring/workflows/compose", json={"requirement": "   ", "locale": "zh"}
        )
        assert response.status_code == 422


def test_gap_blocks_validation_and_exposes_free_query_contract(tmp_path: Path) -> None:
    planner = CompositionPlanner(with_gap=True)
    app = create_app(_settings(tmp_path), planner=planner)
    with TestClient(app) as client:
        response = client.post(
            "/api/authoring/workflows/compose",
            json={"requirement": "检查采购订单付款和银行结算", "locale": "zh"},
        )
        draft = _wait_draft(client, response.json()["draft_id"])
        assert draft["status"] == "needs_agents"
        gap = draft["composition"]["gaps"][0]
        blocked = client.post(
            f"/api/authoring/workflows/{draft['draft_id']}/validate",
            json={"autoDiscover": False, "input": {}},
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "workflow_gaps_unresolved"
        context = client.get(
            f"/api/authoring/workflows/{draft['draft_id']}/gaps/{gap['gap_id']}?locale=zh"
        )
        assert context.status_code == 200
        assert "严格只读" in context.json()["prompt"]
