from __future__ import annotations

import os
import json
import uuid

from .common import TestAgentError, digest, now, read_json, safe_path, write_json
from .inventory import discover, source_files
from .plans import checked_plan
from .workspace import changes, snapshot


TERMINAL = {"passed", "failed", "blocked", "inconclusive", "skipped", "cancelled"}


def start(store, plan_id, expected_digest, request_id=None):
    plan = checked_plan(store, plan_id, expected_digest)
    request_key = digest({"plan": plan_id, "request": request_id or f"start:{plan_id}"})
    task_id = "task_" + request_key[:24]
    with store.transaction() as db:
        try:
            reference = store.get("start_request", request_key, db)
            return store.get("task", reference["task_id"], db)
        except TestAgentError as error:
            if error.code != "record_not_found":
                raise
        try:
            return store.get("task", task_id, db)
        except TestAgentError as error:
            if error.code != "record_not_found":
                raise
        if discover(store.repository)["digest"] != plan["inventory_digest"]:
            raise TestAgentError("plan_stale")
        for row in db.execute("SELECT payload FROM documents WHERE kind='task'"):
            active = json.loads(row[0])
            if active["plan_id"] == plan_id and active["phase"] not in {"completed", "cancelled"}:
                store.put("start_request", request_key, {"task_id": active["id"]}, db)
                return active
        workspace = snapshot(store, task_id, plan["source_digest"])
        task = {"id": task_id, "plan_id": plan_id, "plan_digest": expected_digest,
                "phase": "collecting", "round": 0, "max_repairs": plan["max_repairs"],
                "queue": [c["id"] for c in plan["cases"]], "results": {"0": {}},
                "created_at": now(), "cancel_requested": False, "workspace": workspace,
                "round_source_digest": plan["source_digest"], "repair_history": [], "claims": {}}
        store.put("task", task_id, task, db)
        store.put("start_request", request_key, {"task_id": task_id}, db)
        store.event(task_id, "task_started", {"plan_id": plan_id, "case_count": len(task["queue"])}, db)
    return task


def task_plan(store, task):
    return checked_plan(store, task["plan_id"], task["plan_digest"])


def current_results(task):
    return task["results"][str(task["round"])]


def all_results(task):
    result = {}
    for values in task["results"].values():
        result.update(values)
    return result


def round_complete(task):
    results = current_results(task)
    return all(results.get(key, {}).get("status") in TERMINAL for key in task["queue"])


def claim(store, task_id, case_id):
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        if task["cancel_requested"] or task["phase"] not in {"collecting", "verifying", "finalizing"}:
            raise TestAgentError("task_not_executing")
        if case_id not in task["queue"]:
            raise TestAgentError("case_not_in_round")
        if case_id in current_results(task):
            return {"already_recorded": True, "result": current_results(task)[case_id]}
        if case_id in task["claims"]:
            raise TestAgentError("case_already_running", claim=task["claims"][case_id])
        plan = task_plan(store, task)
        case = next(c for c in plan["cases"] if c["id"] == case_id)
        if digest(source_files(store.task_dir(task_id) / "source")) != task["round_source_digest"]:
            raise TestAgentError("source_changed_mid_round")
        prior = all_results(task)
        pending = [d for d in case.get("depends_on", []) if d in task["queue"] and d not in current_results(task)]
        if pending:
            raise TestAgentError("dependencies_pending", case_ids=pending)
        dependencies = [d for d in case.get("depends_on", []) if prior.get(d, {}).get("status") != "passed"]
        ticket = {"token": uuid.uuid4().hex, "pid": os.getpid(), "started_at": now(), "round": task["round"]}
        task["claims"][case_id] = ticket
        store.put("task", task_id, task, db)
        store.event(task_id, "case_started", {"case_id": case_id, "round": task["round"]}, db)
        return {"ticket": ticket, "case": case, "blocked_dependencies": dependencies}


def record(store, task_id, case_id, token, result):
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        existing = current_results(task).get(case_id)
        if existing:
            if existing.get("claim_token") == token:
                return existing
            raise TestAgentError("case_result_conflict")
        ticket = task["claims"].get(case_id)
        if not ticket or ticket["token"] != token:
            raise TestAgentError("case_claim_invalid")
        if result.get("status") not in TERMINAL:
            raise TestAgentError("case_status_invalid")
        if task["cancel_requested"]:
            result = {**result, "status": "cancelled", "code": "task_cancelled"}
        if digest(source_files(store.task_dir(task_id) / "source")) != task["round_source_digest"]:
            result = {**result, "status": "blocked", "code": "source_changed_mid_round"}
        case = next(c for c in task_plan(store, task)["cases"] if c["id"] == case_id)
        from .runner import check_scripts
        check_scripts(store, task_plan(store, task))
        result = {**result, "case_id": case_id, "mode": case["mode"], "round": task["round"],
                  "claim_token": token, "completed_at": now(), "source_digest": task["round_source_digest"]}
        if result["status"] == "passed" and case["mode"] == "live_sap":
            from .sap import verify_observation
            if not result.get("observation"):
                raise TestAgentError("live_comparison_required")
            comparison = verify_observation(store, task, case, result["observation"])
            result.update(comparison, comparison_verified=True)
        if not result.get("evidence"):
            raise TestAgentError("case_evidence_required")
        evidence = []
        for ref in result["evidence"]:
            path = safe_path(store.task_dir(task_id), ref)
            if not path.is_file():
                raise TestAgentError("evidence_missing")
            evidence.append({"path": ref, "digest": digest(path.read_bytes())})
        result["evidence_manifest"] = evidence
        current_results(task)[case_id] = result
        task["claims"].pop(case_id)
        if task["cancel_requested"] and not task["claims"]:
            task["phase"] = "cancelled"
        elif round_complete(task) and task["phase"] != "finalizing":
            task["phase"] = "awaiting_repair"
            if task["repair_history"] and task["round"] > 0:
                remaining = {g["id"]: set(g["case_ids"]) for g in failure_groups(store, task)}
                stagnation = task.setdefault("stagnation", {})
                for group in task["repair_history"][-1]["groups"]:
                    same = remaining.get(group["id"], set()) >= set(group["case_ids"])
                    stagnation[group["id"]] = stagnation.get(group["id"], 0) + 1 if same else 0
                task["stalled_groups"] = [key for key, count in stagnation.items() if count >= 2]
        store.put("task", task_id, task, db)
        store.event(task_id, "case_completed", {"case_id": case_id, "status": result["status"], "round": task["round"]}, db)
        return result


def cancel(store, task_id):
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        task["cancel_requested"] = True
        task["phase"] = "cancelling" if task["claims"] else "cancelled"
        store.put("task", task_id, task, db)
        store.event(task_id, "cancel_requested", {}, db)
    return task


def resume(store, task_id, interrupted_case_ids=None):
    """Explicitly reconcile uncertain cases; never replay a previously owned operation."""
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        if task["phase"] in {"completed", "repairing", "awaiting_repair"}:
            return task
        for key in interrupted_case_ids or []:
            ticket = task["claims"].pop(key, None)
            if ticket:
                current_results(task)[key] = {"case_id": key, "status": "blocked", "code": "interrupted_execution_unknown",
                    "mode": next(c["mode"] for c in task_plan(store, task)["cases"] if c["id"] == key),
                    "round": task["round"], "completed_at": now(), "claim_token": ticket["token"]}
        if task["claims"]:
            raise TestAgentError("owned_cases_require_reconciliation", cases=sorted(task["claims"]))
        task["cancel_requested"] = False
        task["phase"] = "awaiting_repair" if round_complete(task) else "collecting" if task["round"] == 0 else "verifying"
        store.put("task", task_id, task, db)
        store.event(task_id, "task_resumed", {"uncertain_cases": interrupted_case_ids or []}, db)
        return task


def failure_groups(store, task):
    cases = {c["id"]: c for c in task_plan(store, task)["cases"]}
    groups = {}
    for key, result in all_results(task).items():
        if result["status"] not in {"failed", "blocked", "inconclusive"}:
            continue
        diagnosis = task.get("diagnoses", {}).get(key, {})
        if diagnosis.get("observed_code") != result.get("code"):
            diagnosis = {}
        category = diagnosis.get("category", "unclassified")
        signature = diagnosis.get("root_cause_id") or digest({"code": result.get("code", "unknown"), "layer": result.get("layer", cases[key]["kind"]),
                            "location": result.get("location", "")})[:16]
        group = groups.setdefault(signature, {"id": signature, "code": result.get("code", "unknown"),
                    "status": "blocked" if category == "environment" else "inconclusive" if category == "data" else result["status"],
                    "observed_status": result["status"], "case_ids": [], "features": [],
                    "category": category,
                    "automatic_repair": result["status"] == "failed" and category not in {"environment", "data"}})
        group["case_ids"].append(key)
        if cases[key]["feature"] not in group["features"]:
            group["features"].append(cases[key]["feature"])
    return list(groups.values())


def diagnose(store, task_id, diagnoses):
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        if task["phase"] != "awaiting_repair" or not round_complete(task):
            raise TestAgentError("round_not_finished")
        results = all_results(task)
        if not isinstance(diagnoses, list):
            raise TestAgentError("diagnoses_invalid")
        for entry in diagnoses:
            if entry.get("category") not in {"product", "test", "environment", "data"} or not entry.get("reason"):
                raise TestAgentError("diagnosis_category_invalid")
            from .plans import CASE_ID
            if not CASE_ID.fullmatch(str(entry.get("root_cause_id", ""))):
                raise TestAgentError("root_cause_id_invalid")
            for case_id in entry.get("case_ids", []):
                if results.get(case_id, {}).get("status") not in {"failed", "blocked", "inconclusive"}:
                    raise TestAgentError("diagnosis_case_invalid")
                task.setdefault("diagnoses", {})[case_id] = {**{k: entry[k] for k in ("root_cause_id", "category", "reason")},
                                                              "observed_code": results[case_id].get("code")}
        store.put("task", task_id, task, db)
        store.event(task_id, "failure_diagnoses_saved", {"count": len(diagnoses)}, db)
        return failure_groups(store, task)


def begin_repair(store, task_id):
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        if task["phase"] == "repairing":
            return task
        if task["phase"] != "awaiting_repair" or not round_complete(task) or task["claims"]:
            raise TestAgentError("round_not_finished")
        if len(task["repair_history"]) >= task["max_repairs"]:
            raise TestAgentError("repair_limit_reached")
        groups = [g for g in failure_groups(store, task) if g["automatic_repair"]]
        stalled = set(task.get("stalled_groups", []))
        groups = [g for g in groups if g["id"] not in stalled]
        if not groups:
            raise TestAgentError("no_repairable_failures")
        task["phase"] = "repairing"
        task["repair_history"].append({"round": task["round"] + 1, "groups": groups, "started_at": now(),
                                       "before_patch": changes(store, task_id)})
        store.put("task", task_id, task, db)
        store.event(task_id, "batch_repair_started", {"groups": [g["id"] for g in groups]}, db)
        return task


def verify_repair(store, task_id):
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        if task["phase"] != "repairing":
            raise TestAgentError("task_not_repairing")
        patch = changes(store, task_id)
        protected = ("src/sap_business_agents_test_agent/", "skills/sapba-auto-test/")
        if any(n.startswith(protected) for n in patch):
            raise TestAgentError("test_controller_modified")
        # Freeze the immutable plan/oracle in the controller store, never in the candidate tree.
        prior_patch = task["repair_history"][-1]["before_patch"]
        touched = [n for n in patch.keys() | prior_patch.keys() if patch.get(n) != prior_patch.get(n)]
        test_revisions = set(task["repair_history"][-1].get("test_revision_case_ids", []))
        if not touched and not test_revisions:
            raise TestAgentError("repair_has_no_changes")
        plan = task_plan(store, task)
        frozen_tests = {reference.split("::", 1)[0] for case in plan["cases"] for reference in case.get("test_refs", [])}
        if any(path in frozen_tests for path in touched):
            raise TestAgentError("frozen_regression_test_modified")
        failed = {case_id for group in task["repair_history"][-1]["groups"] for case_id in group["case_ids"]}
        impacted = test_revisions | {c["id"] for c in plan["cases"] if any(
            n.startswith(prefix) for n in touched for prefix in c.get("impact_paths", []))}
        # Unknown dependencies require a complete selected-suite retest, never a guessed subset.
        if touched and (not impacted or any(not c.get("impact_paths") for c in plan["cases"])):
            impacted = {c["id"] for c in plan["cases"]}
        task["round"] += 1
        task["queue"] = [c["id"] for c in plan["cases"] if c["id"] in failed | impacted]
        # Include prerequisite chains in the same order.
        required = set(task["queue"])
        for case in reversed(plan["cases"]):
            if case["id"] in required:
                required.update(case.get("depends_on", []))
        task["queue"] = [c["id"] for c in plan["cases"] if c["id"] in required]
        task["results"][str(task["round"])] = {}
        task["claims"] = {}
        task["phase"] = "verifying"
        task["round_source_digest"] = digest(source_files(store.task_dir(task_id) / "source"))
        task["repair_history"][-1].update({"paths": touched, "case_ids": task["queue"], "patch_digest": digest(patch)})
        store.put("task", task_id, task, db)
        store.event(task_id, "targeted_retest_started", {"case_ids": task["queue"], "round": task["round"]}, db)
        return task


def begin_final(store, task_id, reuse_complete_round=False):
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        if task["phase"] not in {"awaiting_repair", "finalizing"} or not round_complete(task):
            raise TestAgentError("round_not_finished")
        if task.get("final_round") is not None:
            raise TestAgentError("final_review_already_started")
        if reuse_complete_round:
            cases = task_plan(store, task)["cases"]
            expected = {case["id"] for case in cases}
            current = current_results(task)
            if set(task["queue"]) != expected or set(current) != expected:
                raise TestAgentError("final_review_requires_complete_current_round")
            if any(result["status"] != "passed" or result.get("round") != task["round"]
                   or result.get("source_digest") != task["round_source_digest"] for result in current.values()):
                raise TestAgentError("final_review_requires_verified_current_round")
            if any(case["mode"] == "live_sap" and current[case["id"]].get("comparison_verified") is not True for case in cases):
                raise TestAgentError("live_comparison_required")
            if digest(source_files(store.task_dir(task_id) / "source")) != task["round_source_digest"]:
                raise TestAgentError("source_changed_after_verification")
            patch_digest = digest(changes(store, task_id))
            expected_patch = task["repair_history"][-1]["patch_digest"] if task["repair_history"] else digest({})
            if patch_digest != expected_patch:
                raise TestAgentError("patch_changed_after_verification")
            from .runner import check_scripts
            check_scripts(store, task_plan(store, task))
            for result in current.values():
                for evidence in result.get("evidence_manifest", []):
                    path = safe_path(store.task_dir(task_id), evidence["path"])
                    if not path.is_file() or digest(path.read_bytes()) != evidence["digest"]:
                        raise TestAgentError("evidence_changed_after_verification")
            task.update(final_round=task["round"], phase="finalizing", final_patch_digest=patch_digest,
                        final_review={"mode": "completed_full_scope_round", "round": task["round"],
                                      "source_digest": task["round_source_digest"], "case_ids": sorted(expected), "at": now()})
            store.put("task", task_id, task, db)
            store.event(task_id, "complete_scope_round_frozen_for_final_review", task["final_review"], db)
            return task
        task["round"] += 1
        task["final_round"] = task["round"]
        task["phase"] = "finalizing"
        task["queue"] = [c["id"] for c in task_plan(store, task)["cases"]]
        task["results"][str(task["round"])] = {}
        task["round_source_digest"] = digest(source_files(store.task_dir(task_id) / "source"))
        task["final_patch_digest"] = digest(changes(store, task_id))
        store.put("task", task_id, task, db)
        store.event(task_id, "final_review_started", {}, db)
        return task


def complete(store, task_id):
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        if task["phase"] == "completed":
            return task
        if task["phase"] != "finalizing" or not round_complete(task):
            raise TestAgentError("final_review_incomplete")
        if digest(changes(store, task_id)) != task["final_patch_digest"]:
            raise TestAgentError("patch_changed_after_verification")
        initial, final = task["results"]["0"], current_results(task)
        task["regressions"] = [k for k, r in initial.items() if r["status"] == "passed" and final[k]["status"] != "passed"]
        task["phase"], task["completed_at"] = "completed", now()
        task["verified_patch_digest"] = task["final_patch_digest"]
        store.put("task", task_id, task, db)
        store.event(task_id, "task_completed", {"regressions": task["regressions"]}, db)
        return task


def application_gate(store, task):
    if task["phase"] != "completed" or task.get("regressions"):
        raise TestAgentError("application_not_verified")
    final = current_results(task)
    affected = {key for batch in task["repair_history"] for key in batch.get("case_ids", [])}
    if not affected or any(final[key]["status"] != "passed" for key in affected):
        raise TestAgentError("application_affected_cases_not_passed")
    if digest(source_files(store.task_dir(task["id"]) / "source")) != task["round_source_digest"]:
        raise TestAgentError("source_changed_after_verification")
    from .runner import check_scripts
    check_scripts(store, task_plan(store, task))
    for result in final.values():
        for evidence in result.get("evidence_manifest", []):
            path = safe_path(store.task_dir(task["id"]), evidence["path"])
            if not path.is_file() or digest(path.read_bytes()) != evidence["digest"]:
                raise TestAgentError("evidence_changed_after_verification")


def retest(store, task_id):
    with store.transaction() as db:
        task = store.get("task", task_id, db)
        if task["claims"] or task["phase"] not in {"awaiting_repair", "completed"}:
            raise TestAgentError("round_not_finished")
        failed = {k for k, result in all_results(task).items() if result["status"] in {"failed", "blocked", "inconclusive", "cancelled"}}
        if not failed:
            raise TestAgentError("no_failed_cases")
        cases = task_plan(store, task)["cases"]
        for case in reversed(cases):
            if case["id"] in failed:
                failed.update(case.get("depends_on", []))
        task["round"] += 1
        task["queue"] = [case["id"] for case in cases if case["id"] in failed]
        task["results"][str(task["round"])] = {}
        task["phase"] = "verifying"
        task.pop("final_round", None)
        store.put("task", task_id, task, db)
        store.event(task_id, "failed_cases_retest_started", {"case_ids": task["queue"]}, db)
        return task
