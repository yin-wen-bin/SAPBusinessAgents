from __future__ import annotations

from collections import Counter, defaultdict

from .common import atomic_bytes, digest, safe_path, write_json
from .state import all_results, failure_groups, task_plan
from .inventory import FEATURES


def report(store, task_id):
    task = store.get("task", task_id)
    plan = task_plan(store, task)
    latest = all_results(task)
    by_mode = defaultdict(Counter)
    for case in plan["cases"]:
        by_mode[case["mode"]][latest.get(case["id"], {}).get("status", "not_run")] += 1
    invalid = []
    for key, result in latest.items():
        for evidence in result.get("evidence_manifest", []):
            path = safe_path(store.task_dir(task_id), evidence["path"])
            if not path.is_file() or digest(path.read_bytes()) != evidence["digest"]:
                invalid.append(key)
    counts = Counter(latest.get(case["id"], {}).get("status", "not_run") for case in plan["cases"])
    covered = {(case["feature"], case["operation"]) for case in plan["cases"]}
    coverage_gaps = [{"feature": feature[0], "operation": operation} for feature in FEATURES
                     if feature[0] in plan["selected"] for operation in feature[4]
                     if (feature[0], operation) not in covered]
    verdict = "failed" if counts["failed"] else "inconclusive" if invalid or coverage_gaps or task["phase"] != "completed" or any(
        counts[k] for k in ("blocked", "inconclusive", "not_run", "skipped", "cancelled")) else "passed"
    value = {"schema_version": 1, "task_id": task_id, "phase": task["phase"], "round": task["round"],
             "selected": plan["selected"], "verdict": verdict, "counts": dict(counts),
             "evidence_modes": {key: dict(value) for key, value in by_mode.items()},
             "cases": [{"id": case["id"], "feature": case["feature"], "operation": case["operation"],
                        "scenario": case["scenario"], "mode": case["mode"],
                        "status": latest.get(case["id"], {}).get("status", "not_run"),
                        "code": latest.get(case["id"], {}).get("code")} for case in plan["cases"]],
             "failure_groups": failure_groups(store, task), "repair_history": task["repair_history"],
             "stalled_groups": task.get("stalled_groups", []), "regressions": task.get("regressions", []),
             "evidence_invalid": sorted(set(invalid)), "application": task.get("application", {"status": "not_applied"}),
             "coverage_gaps": coverage_gaps,
             "formal_acceptance_granted": False}
    write_json(store.task_dir(task_id) / "report.json", value)
    lines = ["# SAPBusinessAgents 自动测试报告", "", f"任务：{task_id} · 阶段：{task['phase']} · 结论：{verdict}", "",
             "| 用例 | 功能 | 验证类型 | 状态 | 诊断码 |", "|---|---|---|---|---|"]
    lines.extend(f"| {c['id']} | {c['feature']} | {c['mode']} | {c['status']} | {c['code'] or ''} |" for c in value["cases"])
    lines.extend(["", "## 修复与限制", "", f"批量修复次数：{len(task['repair_history'])} / {task['max_repairs']}。",
                  "真实 SAP、离线和模拟证据分别计数；本报告不授予产品正式验收资格。",
                  f"新增回归：{len(value['regressions'])}；证据变更：{len(value['evidence_invalid'])}。"])
    if coverage_gaps:
        lines.extend(["", "未覆盖操作：" + "、".join(f"{gap['feature']}/{gap['operation']}" for gap in coverage_gaps)])
    atomic_bytes(store.task_dir(task_id) / "report.md", ("\n".join(lines) + "\n").encode("utf-8"))
    return value
