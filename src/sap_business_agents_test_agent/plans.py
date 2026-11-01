from __future__ import annotations

import copy
import re

from .common import TestAgentError, digest, now, write_json
from .inventory import discover


CASE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}$")
KINDS = {"pytest", "node-test", "browser", "native"}
MODES = {"offline", "simulated", "live_sap"}


def validate_cases(cases, selected, inventory, operation_selection=None, object_selection=None):
    if not isinstance(cases, list) or not cases:
        raise TestAgentError("cases_required")
    ids, covered, indexed = set(), set(), {f["id"]: f for f in inventory["features"]}
    for case in cases:
        if not isinstance(case, dict) or not CASE_ID.fullmatch(str(case.get("id", ""))) or case["id"] in ids:
            raise TestAgentError("case_id_invalid")
        ids.add(case["id"])
        if case["id"].endswith(".") or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", case["id"]):
            raise TestAgentError("case_id_invalid")
        feature = case.get("feature")
        if feature not in selected:
            raise TestAgentError("case_outside_selection", case_id=case["id"])
        covered.add(feature)
        if case.get("kind") not in KINDS or case.get("mode") not in MODES:
            raise TestAgentError("case_adapter_invalid", case_id=case["id"])
        if case.get("operation") not in indexed[feature]["operations"]:
            raise TestAgentError("case_operation_invalid", case_id=case["id"])
        if operation_selection and case["operation"] not in operation_selection[feature]:
            raise TestAgentError("case_outside_operation_selection", case_id=case["id"])
        if object_selection and object_selection.get(feature) and case.get("object_id") not in object_selection[feature]:
            raise TestAgentError("case_outside_object_selection", case_id=case["id"])
        if not case.get("assertions") or not case.get("scenario"):
            raise TestAgentError("case_contract_required", case_id=case["id"])
        if "timeout_seconds" in case and (type(case["timeout_seconds"]) is not int or not 1 <= case["timeout_seconds"] <= 3600):
            raise TestAgentError("case_timeout_invalid")
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


def resolve_selection(tokens, inventory):
    indexed = {f["id"]: f for f in inventory["features"]}
    groups, operations, objects = set(), {}, {}
    full = {token for token in tokens if token in indexed}
    for token in tokens:
        if token in indexed:
            groups.add(token)
            continue
        if "@" in token:
            feature, key = token.split("@", 1)
            if feature not in indexed or key not in {str(c["id"]) for c in indexed[feature]["children"]}:
                raise TestAgentError("selection_invalid")
            groups.add(feature)
            objects.setdefault(feature, set()).add(key)
        elif "/" in token:
            feature, operation = token.split("/", 1)
            if feature not in indexed or operation not in indexed[feature]["operations"]:
                raise TestAgentError("selection_invalid")
            groups.add(feature)
            operations.setdefault(feature, set()).add(operation)
        else:
            raise TestAgentError("selection_invalid")
    if not groups:
        raise TestAgentError("selection_invalid")
    return sorted(groups), {f: indexed[f]["operations"] if f in full or f not in operations else sorted(operations[f]) for f in sorted(groups)}, {
        f: sorted(objects[f]) for f in objects if f not in full}


def prepare(store, selected=None, cases=None, inventory_digest=None, max_repairs=3, parent_task=None, revision_reason=None):
    inventory = discover(store.repository)
    if inventory_digest is not None and inventory_digest != inventory["digest"]:
        raise TestAgentError("selection_stale")
    tokens = sorted(set(selected if selected is not None else [f["id"] for f in inventory["features"] if f["default"]]))
    selected, operations, objects = resolve_selection(tokens, inventory)
    if type(max_repairs) is not int or not 0 <= max_repairs <= 3:
        raise TestAgentError("repair_limit_invalid")
    cases = copy.deepcopy(cases if cases is not None else offline_cases(inventory, selected))
    validate_cases(cases, selected, inventory, operations, objects)
    body = {"schema_version": 1, "inventory_digest": inventory["digest"], "source_digest": inventory["source_digest"],
            "selected": selected, "selection": tokens, "operation_selection": operations, "object_selection": objects,
            "cases": cases, "max_repairs": max_repairs, "repair_policy": "after_round"}
    if parent_task:
        from .revisions import fixture_revision
        body["test_revision"] = fixture_revision(store, parent_task, tokens, cases, revision_reason)
    plan_digest = digest(body)
    plan = {**body, "id": "plan_" + plan_digest[:24], "digest": plan_digest, "created_at": now()}
    with store.transaction() as db:
        try:
            existing = store.get("plan", plan["id"], db)
            from .runner import generate_scripts
            generate_scripts(store, existing, db)
            return existing
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


def revise_locators(store, task_id, corrections, reason, evidence):
    """Audit a test-only revision; assertions, inputs, baseline and scope stay frozen."""
    from .runner import generate_scripts
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        if task["phase"] != "repairing":
            raise TestAgentError("task_not_repairing")
        if not reason or not evidence or not isinstance(corrections, list) or not corrections:
            raise TestAgentError("locator_revision_evidence_required")
        from .common import safe_path
        for reference in evidence:
            if not safe_path(store.task_dir(task_id), reference).is_file():
                raise TestAgentError("evidence_missing")
        old = checked_plan(store, task["plan_id"], task["plan_digest"])
        body = {k: copy.deepcopy(v) for k, v in old.items() if k not in {"id", "digest", "created_at"}}
        changed = set()
        for correction in corrections:
            case = next((c for c in body["cases"] if c["id"] == correction.get("case_id")), None)
            index, fields = correction.get("action_index"), correction.get("locator")
            if case is None or case["kind"] != "browser" or type(index) is not int or not 0 <= index < len(case["actions"]):
                raise TestAgentError("locator_revision_invalid")
            if not isinstance(fields, dict) or not fields or not set(fields) <= {"selector", "test_id", "role", "name", "label"}:
                raise TestAgentError("locator_revision_changes_oracle")
            if case["actions"][index].get("type") in {"navigate", "capture_json", "screenshot"}:
                raise TestAgentError("locator_revision_invalid")
            action = case["actions"][index]
            updated = copy.deepcopy(action)
            methods = set(fields) & {"selector", "test_id", "role", "label"}
            if len(methods) > 1:
                raise TestAgentError("locator_revision_invalid")
            if methods:
                for name in {"selector", "test_id", "role", "name", "label"}:
                    updated.pop(name, None)
            elif "role" not in updated:
                raise TestAgentError("locator_revision_invalid")
            updated.update(fields)
            if updated != action:
                case["actions"][index] = updated
                changed.add(case["id"])
        if not changed:
            return old
        body["test_revision"] = {"parent_plan_id": old["id"], "reason": reason, "evidence": evidence}
        value = digest(body)
        plan = {**body, "id": "plan_" + value[:24], "digest": value, "created_at": now()}
        store.put("plan", plan["id"], plan, db)
        write_json(store.root / "suites" / plan["id"] / "plan.json", plan)
        generate_scripts(store, plan, db)
        task.setdefault("plan_history", []).append({"id": old["id"], "digest": old["digest"]})
        task.update(plan_id=plan["id"], plan_digest=plan["digest"])
        task["repair_history"][-1].setdefault("test_revision_case_ids", []).extend(sorted(changed))
        store.put("task", task_id, task, db)
        store.event(task_id, "locator_revision_created", {"plan_id": plan["id"], "case_ids": sorted(changed)}, db)
        return plan


def revise_native_executor(store, task_id, case_ids, reason, script):
    """Audit host-browser transport fixes without changing any case/oracle."""
    from .common import safe_path
    from .runner import generate_scripts
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        if task["phase"] != "repairing":
            raise TestAgentError("task_not_repairing")
        if not reason or not case_ids:
            raise TestAgentError("native_revision_reason_required")
        old = checked_plan(store, task["plan_id"], task["plan_digest"])
        indexed = {case["id"]: case for case in old["cases"]}
        eligible = {key for group in task["repair_history"][-1]["groups"] for key in group["case_ids"]}
        if any(key not in eligible or indexed[key]["kind"] != "native" for key in case_ids):
            raise TestAgentError("native_revision_outside_failure_scope")
        path = safe_path(store.task_dir(task_id), script)
        if not path.is_file() or path.suffix != ".mjs":
            raise TestAgentError("native_revision_script_missing")
        body = {k: copy.deepcopy(v) for k, v in old.items() if k not in {"id", "digest", "created_at"}}
        body["native_execution_revision"] = {"task_id": task_id, "parent_plan_id": old["id"],
            "case_ids": sorted(set(case_ids)), "reason": reason, "script": script,
            "script_digest": digest(path.read_bytes())}
        value = digest(body)
        plan = {**body, "id": "plan_" + value[:24], "digest": value, "created_at": now()}
        store.put("plan", plan["id"], plan, db)
        write_json(store.root / "suites" / plan["id"] / "plan.json", plan)
        generate_scripts(store, plan, db)
        task.setdefault("plan_history", []).append({"id": old["id"], "digest": old["digest"]})
        task.update(plan_id=plan["id"], plan_digest=plan["digest"])
        task["repair_history"][-1].setdefault("test_revision_case_ids", []).extend(sorted(set(case_ids)))
        store.put("task", task_id, task, db)
        store.event(task_id, "native_executor_revision_created", {"plan_id": plan["id"], "case_ids": case_ids}, db)
        return plan
