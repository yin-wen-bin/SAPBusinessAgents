from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings


TERMINAL = {"completed", "inconclusive", "failed", "cancelled"}


def _wait(client: TestClient, run_id: str, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run.get("status") in TERMINAL:
            return run
        time.sleep(0.25)
    raise TimeoutError(f"run {run_id} did not finish within {timeout_seconds}s")


def _foreach_workflow(batch: dict[str, Any]) -> dict[str, Any]:
    workflow = copy.deepcopy(batch)
    workflow["id"] = "p2p-batch-payment-review-live-foreach"
    workflow["version"] = "0.0.0-live-validation"
    ap_node = next(node for node in workflow["nodes"] if node["id"] == "ap")
    ap_node["forEach"] = {
        "source": {
            "scope": "node_output",
            "nodeId": "p2p",
            "port": "ap_payment_scopes",
        },
        "groupBy": {"company_code": "/company_code", "supplier": "/supplier"},
        "maxItems": 50,
        "maxConcurrency": 4,
        "onItemError": "collect_inconclusive",
    }
    workflow["connections"] = [
        connection
        for connection in workflow["connections"]
        if not (
            connection["to"]["nodeId"] == "ap"
            and connection["to"]["port"] == "ap_payment_scopes"
        )
    ]
    workflow["connections"].append(
        {
            "from": {"scope": "iteration_item", "pointer": "/items"},
            "to": {"nodeId": "ap", "port": "ap_payment_scopes"},
            "transform": {"type": "identity"},
        }
    )
    for output in workflow["outputs"]:
        if output["name"] == "ap_details":
            output.pop("source", None)
            output.pop("transform", None)
            output["aggregate"] = {
                "operator": "collect",
                "sources": [
                    {
                        "scope": "node_output",
                        "nodeId": "ap",
                        "port": "scope_results",
                    }
                ],
            }
    return workflow


def _summary(run: dict[str, Any]) -> dict[str, Any]:
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    node_results = result.get("node_results") or []
    workflow_output = (
        result.get("workflow_output")
        if isinstance(result.get("workflow_output"), dict)
        else {}
    )
    return {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "error": run.get("error"),
        "workflow_output": {
            "batch_status": workflow_output.get("batch_status"),
            "source_complete": workflow_output.get("source_complete"),
            "evidence_complete": workflow_output.get("evidence_complete"),
            "p2p_details": [
                {
                    "purchase_order": item.get("purchase_order"),
                    "business_status": item.get("business_status"),
                    "source_complete": item.get("source_complete"),
                    "evidence_complete": item.get("evidence_complete"),
                }
                for item in workflow_output.get("p2p_details") or []
            ],
            "ap_details": [
                {
                    "scope_id": item.get("scope_id"),
                    "business_status": item.get("business_status"),
                    "open_item_count": item.get("open_item_count"),
                    "source_complete": item.get("source_complete"),
                    "evidence_complete": item.get("evidence_complete"),
                    "payment_run_evidence_complete": item.get(
                        "payment_run_evidence_complete"
                    ),
                    "bank_master_evidence_complete": item.get(
                        "bank_master_evidence_complete"
                    ),
                    "bank_settlement_evidence_complete": item.get(
                        "bank_settlement_evidence_complete"
                    ),
                }
                for item in workflow_output.get("ap_details") or []
            ],
        },
        "nodes": [
            {
                "node_id": node.get("node_id"),
                "agent_id": node.get("agent_id"),
                "status": node.get("status"),
                "child_run_id": node.get("child_run_id"),
                "iteration_count": len(node.get("iterations") or []),
                "error_count": len(node.get("errors") or []),
            }
            for node in node_results
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Execute the published batch and draft foreach P2P-to-AP workflows against live GET-only SAP."
    )
    parser.add_argument("purchase_orders", nargs="+", help="One to 50 SAP purchase-order numbers")
    parser.add_argument("--as-of", required=True, help="Cutoff date in YYYY-MM-DD format")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    batch = json.loads(
        (root / "workflows" / "Common" / "p2p-batch-payment-review" / "workflow.json").read_text(
            encoding="utf-8"
        )
    )
    payload = {"purchase_orders": args.purchase_orders, "as_of": args.as_of}
    app = create_app(Settings.from_env(root))
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "mode": "workflow",
                "workflowId": "p2p-batch-payment-review",
                "input": payload,
            },
        )
        response.raise_for_status()
        batch_run = _wait(client, response.json()["run_id"], args.timeout)

        draft_response = client.post(
            "/api/authoring/workflows", json={"workflow": _foreach_workflow(batch)}
        )
        draft_response.raise_for_status()
        draft_id = draft_response.json()["draft_id"]
        validation_response = client.post(
            f"/api/authoring/workflows/{draft_id}/validate",
            json={"autoDiscover": False, "input": payload},
        )
        validation_response.raise_for_status()
        foreach_run = _wait(
            client, validation_response.json()["validation_run_id"], args.timeout
        )

    print(
        json.dumps(
            {"batch": _summary(batch_run), "foreach": _summary(foreach_run)},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
