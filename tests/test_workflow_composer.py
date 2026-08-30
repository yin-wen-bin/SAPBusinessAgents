from __future__ import annotations

from copy import deepcopy
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.workflow_composer import (
    WorkflowCompositionError,
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
            "requested_outputs": ["purchase_orders"],
        },
        {
            "id": "ap",
            "capability": {"zh": "应付付款复核", "en": "AP payment review"},
            "agent_id": "ap-payment",
            "confidence": "high",
            "reason": {"zh": "覆盖供应商付款状态", "en": "Covers supplier payment status"},
            "bindings": [
                {
                    "input_port": "ap_payment_scopes",
                    "source_stage_id": "p2p",
                    "source_output_port": "ap_payment_scopes",
                },
            ],
            "requested_outputs": [
                "scope_results",
                "business_status",
                "source_complete",
                "evidence_complete",
                "payment_run_evidence_complete",
                "bank_master_evidence_complete",
                "bank_settlement_evidence_complete",
                "bank_settlement_status",
                "business_report",
            ],
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
        "validation_defaults": {"purchase_orders": ["4500000030"], "as_of": "2026-08-25"},
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


class EchoOutputCompositionPlanner(CompositionPlanner):
    async def compose_workflow(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        proposal = _proposal(with_gap=self.with_gap)
        proposal["stages"][1]["requested_outputs"].extend(["query_mode", "as_of"])
        return {
            "needs_clarification": False,
            "clarification_question": "",
            "proposal": proposal,
            "thread_id": "workflow-echo-thread",
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
    assert ("p2p", "ap_payment_scopes", "ap_payment_scopes") in node_bindings
    assert set(workflow["inputSchema"]["required"]) == {"purchase_orders", "as_of"}
    assert set(workflow["inputSchema"]["properties"]) == {"purchase_orders", "as_of"}
    ap_node = next(item for item in workflow["nodes"] if item["id"] == "ap")
    assert ap_node["runIf"] == {
        "source": {"scope": "node_output", "nodeId": "p2p", "port": "ap_payment_scopes"},
        "operator": "non_empty",
    }
    assert ap_node["onSkip"]["reasonCode"] == "no_ap_payment_scopes"
    assert ap_node["onSkip"]["outputs"] == {
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
    }
    ap_required = {
        f"ap_{port}"
        for port in ap_node["onSkip"]["outputs"]
    }
    assert ap_required.issubset(set(workflow["outputSchema"]["required"]))
    query_mode = next(
        item
        for item in workflow["connections"]
        if item["to"] == {"nodeId": "ap", "port": "query_mode"}
    )
    assert query_mode["from"] == {"scope": "constant", "value": "p2p_evidence"}
    assert composition["compiler_version"] == 4
    assert composition["validation_defaults"] == {
        "purchase_orders": ["4500000030"],
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
    assert ("p2p", "ap_payment_scopes", "ap_payment_scopes") in node_bindings


def test_compiler_selects_direct_branch_for_standalone_ap() -> None:
    root = Path(__file__).resolve().parents[1]
    agents = AgentRepository(root / "agents")
    proposal = _proposal()
    proposal["stages"] = [proposal["stages"][1]]
    proposal["stages"][0]["bindings"] = []
    workflow, _composition = compile_workflow_proposal(
        workflow_id="generated-direct-ap-review",
        requirement="按公司代码和供应商复核应付付款",
        locale="zh",
        proposal=proposal,
        catalog=compact_agent_catalog(agents),
        agents=agents,
    )

    assert set(workflow["inputSchema"]["properties"]) == {
        "company_code",
        "supplier",
        "as_of",
    }
    assert set(workflow["inputSchema"]["required"]) == {
        "company_code",
        "supplier",
        "as_of",
    }
    assert "runIf" not in workflow["nodes"][0]
    query_mode = next(
        item for item in workflow["connections"] if item["to"]["port"] == "query_mode"
    )
    assert query_mode["from"] == {"scope": "constant", "value": "direct"}


def test_compiler_dismisses_unconsumed_conditional_input_echo_outputs() -> None:
    root = Path(__file__).resolve().parents[1]
    agents = AgentRepository(root / "agents")
    proposal = _proposal()
    proposal["stages"][1]["requested_outputs"].extend(["query_mode", "as_of"])

    workflow, composition = compile_workflow_proposal(
        workflow_id="generated-normalized-skip-output",
        requirement="检查采购订单付款状态",
        locale="zh",
        proposal=proposal,
        catalog=compact_agent_catalog(agents),
        agents=agents,
    )

    ap_node = next(item for item in workflow["nodes"] if item["id"] == "ap")
    assert "query_mode" not in ap_node["onSkip"]["outputs"]
    assert "as_of" not in ap_node["onSkip"]["outputs"]
    assert "ap_query_mode" not in workflow["outputSchema"]["properties"]
    assert "ap_as_of" not in workflow["outputSchema"]["properties"]
    assert composition["proposal_snapshot"] == proposal
    assert composition["output_normalization"]["dismissed_requested_outputs"] == [
        {
            "stage_id": "ap",
            "port": "query_mode",
            "reason_code": "conditional_context_echo_not_terminal",
        },
        {
            "stage_id": "ap",
            "port": "as_of",
            "reason_code": "conditional_context_echo_not_terminal",
        },
    ]


def test_compiler_still_rejects_an_unsafe_required_business_output() -> None:
    root = Path(__file__).resolve().parents[1]
    base = AgentRepository(root / "agents")

    class UnsafeBusinessOutputRepository:
        def _agent(self, agent_id: str) -> dict[str, Any]:
            agent = deepcopy(base.get(agent_id))
            if agent_id == "ap-payment":
                output_schema = agent["execution"]["outputSchema"]
                output_schema["properties"]["decision_reason"] = {"type": "string"}
                output_schema["required"].append("decision_reason")
            return agent

        def get(self, agent_id: str) -> dict[str, Any]:
            return self._agent(agent_id)

        def executable(self) -> list[dict[str, Any]]:
            return [self._agent(item["slug"]) for item in base.executable()]

    agents = UnsafeBusinessOutputRepository()
    try:
        compile_workflow_proposal(
            workflow_id="generated-unsafe-business-output",
            requirement="检查采购订单付款状态",
            locale="zh",
            proposal=_proposal(),
            catalog=compact_agent_catalog(agents),
            agents=agents,
        )
    except WorkflowCompositionError as exc:
        assert exc.code == "workflow_conditional_skip_output_unavailable"
        assert exc.detail == {"node_id": "ap", "port": "decision_reason"}
    else:
        raise AssertionError("An unsafe required business output was accepted")


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


def test_composition_api_normalizes_runtime_input_echo_outputs_and_keeps_audit(
    tmp_path: Path,
) -> None:
    planner = EchoOutputCompositionPlanner()
    app = create_app(_settings(tmp_path), planner=planner)
    with TestClient(app) as client:
        response = client.post(
            "/api/authoring/workflows/compose",
            json={"requirement": "检查采购订单付款状态", "locale": "zh"},
        )
        assert response.status_code == 202, response.text
        generated = _wait_draft(client, response.json()["draft_id"])
        assert generated["status"] == "draft"
        assert generated["thread_id"] == "workflow-echo-thread"
        assert generated["composition"]["compiler_version"] == 4
        assert generated["composition"]["proposal_snapshot"]["stages"][1][
            "requested_outputs"
        ][-2:] == ["query_mode", "as_of"]
        assert [
            item["port"]
            for item in generated["composition"]["output_normalization"][
                "dismissed_requested_outputs"
            ]
        ] == ["query_mode", "as_of"]


def test_legacy_failed_composition_reconciles_in_place_with_current_compiler(
    tmp_path: Path,
) -> None:
    planner = EchoOutputCompositionPlanner()
    app = create_app(_settings(tmp_path), planner=planner)
    with TestClient(app) as client:
        legacy = app.state.workflow_drafts.create(
            {"zh": "旧草稿", "en": "Legacy draft"},
            {"zh": "检查采购订单付款状态", "en": "Review PO payment status"},
            None,
        )
        legacy.status = "needs_review"
        legacy.composition = {
            "requirement": "检查采购订单付款状态",
            "locale": "zh",
            "runtime_provider_id": "codex",
            "compiler_version": 3,
            "stages": [],
            "gaps": [],
            "error": {
                "code": "workflow_conditional_skip_output_unavailable",
                "message": "Node assess_payment_readiness cannot derive a safe skipped value for output query_mode.",
                "type": "WorkflowCompositionError",
            },
        }
        app.state.store.save_workflow_draft(legacy)

        retry = client.post(
            f"/api/authoring/workflows/{legacy.draft_id}/reconcile"
        )
        assert retry.status_code == 202, retry.text
        assert retry.json()["draft_id"] == legacy.draft_id
        assert retry.json()["status"] == "planning"
        generated = _wait_draft(client, legacy.draft_id)
        assert generated["draft_id"] == legacy.draft_id
        assert generated["status"] == "draft"
        assert generated["composition"]["compiler_version"] == 4
        assert len(generated["workflow"]["nodes"]) == 2
        assert planner.calls == 1


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
