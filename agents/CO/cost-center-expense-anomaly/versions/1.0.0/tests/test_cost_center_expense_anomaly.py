import json
from pathlib import Path

from cost_center_expense_anomaly import analyze
from cost_center_expense_anomaly.cli import main
from cost_center_expense_anomaly.fixture import demo_path
from sap_business_agents_platform.acceptance_contract import contract_issues, readiness
from sap_business_agents_platform.managed_rules import execute_managed_rule
from sap_business_agents_platform.manifests import validate_manifest


def _package_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "rules.py").is_file() and (
            (parent / "manifest.json").is_file() or (parent / "agent.json").is_file()
        ):
            return parent
    raise AssertionError("Agent package root not found")


def _manifest() -> dict:
    root = _package_root()
    path = root / "manifest.json"
    if not path.is_file():
        path = root / "agent.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _project(payload: dict) -> dict:
    root = _package_root()
    manifest = _manifest()
    return execute_managed_rule(
        (root / "rules.py").read_text(encoding="utf-8"),
        {"run_input": payload["run_input"], "evaluation": analyze(payload)},
        expected_digest=manifest["managedRule"]["sha256"],
    )


def test_demo_is_deterministic_and_complete() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    assert result["rule_id"] == "cost_center_expense_anomaly_deterministic_v1"
    assert result["status"] == "complete"
    assert result["source_complete"] is True
    assert result["business_report"]["headline"]["zh"]


def test_cli(capsys) -> None:
    assert main([]) == 0
    assert "cost_center_expense_anomaly_deterministic_v1" in capsys.readouterr().out


def test_complete_empty_plan_is_missing_evidence_not_zero_plan() -> None:
    payload = json.loads(demo_path().read_text(encoding="utf-8"))
    payload["evidence"]["plan_items"]["step_results"]["plan_items"]["results"] = []

    result = analyze(payload)
    metrics = {item["id"]: item["value"] for item in result["metrics"]}

    assert result["business_status"] == "capability_blocked"
    assert metrics["plan_amount"] is None
    assert metrics["variance_amount"] is None
    assert "plan_evidence_missing" in result["missing_evidence"]


def test_acceptance_contract_and_managed_projection_are_ready_for_a_current_trial() -> None:
    manifest = _manifest()
    validate_manifest(manifest, "cost-center-expense-anomaly")
    assert contract_issues(manifest) == []

    projected = _project(json.loads(demo_path().read_text(encoding="utf-8")))
    output = projected["workflow_output"]

    assert output["records"] == output["business_report"]["records"]
    assert output["evidence_complete"] is True
    assert output["business_complete"] is True
    assert output["actual_amount"] == "120"
    assert output["plan_amount"] == "100"
    assert readiness(manifest, 1, result=projected)["status"] == "ready"


def test_projection_keeps_complete_empty_plan_unknown() -> None:
    payload = json.loads(demo_path().read_text(encoding="utf-8"))
    payload["evidence"]["plan_items"]["step_results"]["plan_items"]["results"] = []

    output = _project(payload)["workflow_output"]

    assert output["source_complete"] is True
    assert output["evidence_complete"] is False
    assert output["business_complete"] is False
    assert output["actual_amount"] == "120"
    assert output["plan_amount"] is None
    assert output["variance_amount"] is None
    assert output["variance_pct"] is None
    assert "plan_evidence_missing" in output["business_report"]["limitations"]
