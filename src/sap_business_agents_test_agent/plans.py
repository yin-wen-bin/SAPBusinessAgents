from __future__ import annotations

import copy
import re

from .common import TestAgentError, digest, now, write_json
from .inventory import discover


CASE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}$")
KINDS = {"pytest", "node-test", "browser", "native"}
MODES = {"offline", "simulated", "live_sap"}


def validate_cases(cases, selected, inventory):
    if not isinstance(cases, list) or not cases:
        raise TestAgentError("cases_required")
    ids, covered, indexed = set(), set(), {f["id"]: f for f in inventory["features"]}
    for case in cases:
        if not isinstance(case, dict) or not CASE_ID.fullmatch(str(case.get("id", ""))) or case["id"] in ids:
            raise TestAgentError("case_id_invalid")
        ids.add(case["id"])
        feature = case.get("feature")
        if feature not in selected:
            raise TestAgentError("case_outside_selection", case_id=case["id"])
        covered.add(feature)
        if case.get("kind") not in KINDS or case.get("mode") not in MODES:
            raise TestAgentError("case_adapter_invalid", case_id=case["id"])
        if case.get("operation") not in indexed[feature]["operations"]:
            raise TestAgentError("case_operation_invalid", case_id=case["id"])
        if not case.get("assertions") or not case.get("scenario"):
            raise TestAgentError("case_contract_required", case_id=case["id"])
        if case["kind"] in {"pytest", "node-test"} and (case["mode"] != "offline" or not case.get("test_refs")):
            raise TestAgentError("offline_adapter_contract_invalid")
        if case["kind"] == "browser" and not case.get("actions"):
            raise TestAgentError("browser_actions_required")
        if case["kind"] in {"browser", "native"} and feature in {"plugins", "settings"} and case["mode"] != "simulated":
            raise TestAgentError("external_actions_require_simulation")
        if case["mode"] == "live_sap" and (not case.get("baseline") or not case.get("comparison")):
            raise TestAgentError("independent_baseline_required", case_id=case["id"])
        for name in case.get("impact_paths", []):
            if not isinstance(name, str) or name.startswith(("/", "\\")) or ".." in name.split("/"):
                raise TestAgentError("impact_path_invalid")
    if covered != set(selected):
        raise TestAgentError("selection_coverage_missing", features=sorted(set(selected) - covered))
    # Dependencies must point backward: execution order is deterministic and acyclic.
    prior = set()
    for case in cases:
        if not set(case.get("depends_on", [])) <= prior:
            raise TestAgentError("case_dependency_invalid", case_id=case["id"])
        prior.add(case["id"])


def offline_cases(inventory, selected):
    """Useful runnable regression seed. Native Codex adds business/browser cases."""
    return [{"id": f["id"] + ".regression", "feature": f["id"], "operation": f["operations"][0],
             "scenario": f["title"] + "已有离线回归", "kind": "pytest", "mode": "offline",
             "test_refs": f["test_refs"], "assertions": ["existing_regressions_pass"],
             "impact_paths": ["src/sap_business_agents_platform/", "site/src/"], "depends_on": []}
            for f in inventory["features"] if f["id"] in selected and f["test_refs"]]


def prepare(store, selected=None, cases=None, inventory_digest=None, max_repairs=3):
    inventory = discover(store.repository)
    if inventory_digest is not None and inventory_digest != inventory["digest"]:
        raise TestAgentError("selection_stale")
    selected = sorted(set(selected if selected is not None else [f["id"] for f in inventory["features"] if f["default"]]))
    if not selected or not set(selected) <= {f["id"] for f in inventory["features"]}:
        raise TestAgentError("selection_invalid")
    if type(max_repairs) is not int or not 0 <= max_repairs <= 3:
        raise TestAgentError("repair_limit_invalid")
    cases = copy.deepcopy(cases if cases is not None else offline_cases(inventory, selected))
    validate_cases(cases, selected, inventory)
    body = {"schema_version": 1, "inventory_digest": inventory["digest"], "source_digest": inventory["source_digest"],
            "selected": selected, "cases": cases, "max_repairs": max_repairs, "repair_policy": "after_round"}
    plan_digest = digest(body)
    plan = {**body, "id": "plan_" + plan_digest[:24], "digest": plan_digest, "created_at": now()}
    with store.transaction() as db:
        try:
            return store.get("plan", plan["id"], db)
        except TestAgentError as error:
            if error.code != "record_not_found":
                raise
        store.put("plan", plan["id"], plan, db)
    write_json(store.root / "suites" / plan["id"] / "plan.json", plan)
    from .runner import generate_scripts
    generate_scripts(store, plan)
    return plan


def checked_plan(store, plan_id, expected_digest):
    plan = store.get("plan", plan_id)
    if plan["digest"] != expected_digest:
        raise TestAgentError("plan_digest_mismatch")
    body = {k: v for k, v in plan.items() if k not in {"id", "digest", "created_at"}}
    if digest(body) != expected_digest:
        raise TestAgentError("plan_corrupt")
    return plan
