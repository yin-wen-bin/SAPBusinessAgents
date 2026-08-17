from __future__ import annotations

from typing import Any

from sap_business_agents_platform.agent_rules import evaluate_business_agent


def analyze(evidence: dict[str, Any]) -> dict[str, Any]:
    payload = dict(evidence)
    payload["agent_id"] = "internal-order-project-control"
    return evaluate_business_agent(payload)
