"""Offline definition/diagnostic replay: never loads an SDK or reads SAP."""
import argparse
import ast
import json
import sqlite3
import subprocess
from pathlib import Path

from sap_business_agents_platform.acceptance import agent_execution_digest
from sap_business_agents_platform.manifests import AgentRepository
from sap_business_agents_platform.runtime_policy import orchestration_digest
from sap_business_agents_platform.sample_discovery import SampleDiscoveryContext, _PRIVATE, _INPUT
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest, file_hash
from sap_business_agents_platform.workflows import agent_digest

ROOT = Path(__file__).resolve().parents[1]


def diagnose(batch):
    batch = Path(batch).resolve()
    allowed = (ROOT / ".local-data/workbuddy-validation").resolve()
    if batch == allowed or not batch.is_relative_to(allowed):
        raise ValueError("diagnosis_batch_outside_validation_root")
    frozen = json.loads((batch / "freeze.json").read_text(encoding="utf-8"))
    bound = json.loads((batch / "runtime-snapshot.json").read_text(encoding="utf-8"))
    parent = ROOT / frozen["parent_batch"]
    preserved = [batch / name for name in (
        "freeze.json", "runtime-snapshot.json", "result-summary.json", "sample-summary.json",
        "sample-data/report.json", "sample-data/sample-result.json")]
    preserved.extend(parent / name for name in frozen["parent_files"])
    before = {str(path.relative_to(ROOT)): file_hash(path) for path in preserved}
    assert all(file_hash(parent / name) == value for name, value in frozen["parent_files"].items())
    assert all(file_hash(ROOT / name) == value for name, value in frozen["controls"]["files"].items())
    assert file_hash(ROOT / "src/sap_business_agents_platform/workbuddy_worker.py") == frozen["controls"]["worker_digest"]
    source_path = "src/sap_business_agents_platform/sample_discovery.py"
    original = subprocess.check_output(["git", "show", "HEAD:" + source_path], cwd=ROOT).decode("utf-8")
    current = (ROOT / source_path).read_text(encoding="utf-8")
    guards = {"check_plan", "allow_tool", "check_skill", "check_stable_keys", "project_evidence", "validate_result"}
    def guard_bodies(text):
        return {node.name: ast.dump(node, include_attributes=False) for node in ast.walk(ast.parse(text))
                if isinstance(node, ast.FunctionDef) and node.name in guards}
    assert guard_bodies(original) == guard_bodies(current)
    repo = AgentRepository(ROOT / "agents")
    source = frozen["source_agent"]
    manifest = repo.get(source["agent_id"])
    package = repo.package(source["agent_id"])
    assert agent_digest(manifest) == source["agent_digest"]
    assert agent_execution_digest(manifest, package["rules_source"]) == source["execution_digest"]
    params = frozen["case_parameters"]["sample"]
    supplied = {k: v for k, v in params.items() if k != "selected_fields"}
    context = SampleDiscoveryContext(manifest, supplied, 1, selected_fields=params["selected_fields"])
    mapped = {name for plan in context.plans for _, name, _ in context._bindings(plan)}
    scope = set(supplied) & mapped
    projections = []
    for declaration in context.plans:
        fields = [name for name in declaration.get("select_fields", []) if not _PRIVATE.search(name)]
        filters = []
        for original in declaration.get("filters", []):
            item = dict(original)
            match = _INPUT.fullmatch(str(item.get("value") or ""))
            if match and match.group(1) not in supplied:
                continue
            if match:
                item["value"] = supplied[match.group(1)]
            filters.append(item)
        plan = {**{k: declaration[k] for k in ("service_name", "odata_version", "entity_set")},
                "http_method": "GET", "top": 1, "select_fields": fields,
                "order_by": fields[:1], "filters": filters}
        code = None
        try:
            context.check_plan(plan)
        except ValueError as exc:
            code = getattr(exc, "code", "diagnosis_plan_error")
        projections.append({"source": [declaration[k] for k in ("service_name", "odata_version", "entity_set")],
            "missing_scope_inputs": sorted(scope - {name for _, name, _ in context._bindings(declaration)}),
            "scope_guard_result": code or "accepted"})
    assert projections and all(item["scope_guard_result"] == "sample_scope_not_preserved" for item in projections)
    database = batch / "sample-data/data/platform.sqlite3"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT event_type FROM events WHERE run_id=?", ("wb_live_c71be1ec05774844",)).fetchall()
        events = [row[0] for row in rows]
        tool_names = [row[0] for row in connection.execute("SELECT tool_name FROM harness_tool_calls")]
    assert not any(name in {"sap_query_execute", "sap_skill_execute"} for name in tool_names)
    history = json.loads((batch / "result-summary.json").read_text(encoding="utf-8"))
    report = {
        "phase": "confirmed_platform_preflight_gap", "model_calls": 0, "sap_calls": 0,
        "model": bound["model"], "environment_digest": bound["environment_digest"],
        "requested_fields": params["selected_fields"], "supplied_scope_names": sorted(scope),
        "projection_origin": "saved_draft_definition_not_original_model_arguments",
        "declared_source_checks": projections, "current_readiness": context.readiness(),
        "historical_early_denial_event_count": events.count("sample_tool_denied"),
        "original_denied_model_arguments_available": False,
        "historical_business_reads": 0, "history_preserved": before,
        "source_execution_and_rules_unchanged": True, "codex_control_files_unchanged": True,
        "contract_weakened": False, "worker_or_sdk_changed": False,
        "existing_scope_evidence_and_budget_guard_bodies_unchanged": True,
        "qualification_policy": "new_orchestration_binding_requires_fresh_validation",
        "old_validation_record_count": history["valid_operations"],
        "old_orchestration_bindings_still_current": sum(
            orchestration_digest(op) == value for op, value in frozen["operation_digests"].items()),
        "blocking_scope_requires_separately_validated_cross_source_proof": True,
    }
    assert before == {str(path.relative_to(ROOT)): file_hash(path) for path in preserved}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    allowed = (ROOT / ".local-data/workbuddy-validation").resolve()
    if not destination.is_relative_to(allowed) or destination.exists():
        raise ValueError("diagnosis_new_output_required")
    report = diagnose(args.batch)
    atomic_json(destination, report)
    print(json.dumps({k: report[k] for k in ("phase", "model_calls", "sap_calls", "requested_fields", "old_orchestration_bindings_still_current")}))


if __name__ == "__main__":
    main()
