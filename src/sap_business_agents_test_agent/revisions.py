"""Audited fixture revisions create a fresh plan; past suites stay immutable."""
from __future__ import annotations

import ast
import copy
from collections import Counter

from .common import TestAgentError, digest, read_json, safe_path
from .inventory import source_files


def assertion_contract(source):
    tree = ast.parse(source)
    result = {}
    dump = lambda value: ast.dump(value, include_attributes=False)
    def walk(node, context, assertions):
        if isinstance(node, ast.Assert):
            assertions.append((tuple(context), dump(node)))
        # Capture conditions enclosing assertions, including nested helper bodies.
        if isinstance(node, (ast.If, ast.While)):
            for child in node.body: walk(child, context + [dump(node.test)], assertions)
            for child in node.orelse: walk(child, context + ["else:" + dump(node.test)], assertions)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            for child in node.body: walk(child, context + ["for:" + dump(node.iter)], assertions)
            for child in node.orelse: walk(child, context + ["for-else:" + dump(node.iter)], assertions)
            return
        if isinstance(node, ast.Try):
            policy = "try:" + repr([dump(h.type) if h.type else "any" for h in node.handlers])
            for child in ast.iter_child_nodes(node): walk(child, context + [policy], assertions)
            return
        for child in ast.iter_child_nodes(node): walk(child, context, assertions)
    def returns(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return 0
        return int(isinstance(node, ast.Return)) + sum(returns(child) for child in ast.iter_child_nodes(node))
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
            continue
        assertions = []
        walk(node, [], assertions)
        guards = [dump(n) for n in ast.walk(node) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute) and n.func.attr in {"raises", "skip", "xfail"}]
        result[node.name] = {"decorators": [dump(d) for d in node.decorator_list],
                             "assertions": Counter(assertions), "guards": Counter(guards),
                             "returns": sum(returns(n) for n in node.body)}
    return result


def fixture_revision(store, parent_task_id, selected, cases, reason):
    if not reason or not reason.strip():
        raise TestAgentError("fixture_revision_reason_required")
    parent = store.get("task", parent_task_id)
    if parent.get("claims") or parent["phase"] not in {"completed", "awaiting_repair", "cancelled"}:
        raise TestAgentError("fixture_revision_parent_active")
    old = store.get("plan", parent["plan_id"])
    if sorted(selected) != sorted(old.get("selection", old["selected"])):
        raise TestAgentError("fixture_revision_scope_changed")
    protected = ("id", "feature", "operation", "assertions", "sample", "baseline", "comparison",
                 "question_template", "depends_on", "test_refs", "object_id")
    normalized = copy.deepcopy(cases)
    paging = []
    for previous, revised in zip(old["cases"], normalized):
        before_plan = previous.get("baseline", {}).get("plan", {})
        after_plan = revised.get("baseline", {}).get("plan", {})
        # The platform deliberately cannot prove completeness for explicit top.
        # Complete key-set comparisons may use its bounded full-source paging;
        # business filters, columns, questions and expected values stay fixed.
        if (type(before_plan.get("top")) is int and after_plan.get("top") is None
                and previous.get("comparison", {}).get("type") == "key_set"
                and previous.get("mode") == "live_sap" and before_plan.get("filters")):
            after_plan["top"] = before_plan["top"]
            paging.append({"case_id": previous["id"], "before_top": before_plan["top"],
                           "after": "platform paging bounded to 5000 rows and 10 business GETs"})
    if normalized != old["cases"]:
        raise TestAgentError("fixture_revision_contract_changed")
    before = read_json(store.task_dir(parent_task_id) / "base-files.json")
    after = source_files(store.repository)
    refs = sorted({r.split("::", 1)[0] for c in cases for r in c.get("test_refs", [])})
    revised = []
    for ref in refs:
        if before.get(ref) == after.get(ref):
            continue
        if not ref.endswith(".py") or not before.get(ref) or not after.get(ref):
            raise TestAgentError("fixture_revision_not_python_setup", path=ref)
        original = assertion_contract(safe_path(store.task_dir(parent_task_id) / "source", ref).read_text(encoding="utf-8-sig"))
        current = assertion_contract(safe_path(store.repository, ref).read_text(encoding="utf-8-sig"))
        if original.keys() != current.keys():
            raise TestAgentError("fixture_revision_tests_changed", path=ref)
        for name, contract in original.items():
            updated = current[name]
            if (contract["decorators"] != updated["decorators"] or contract["guards"] != updated["guards"]
                    or contract["returns"] != updated["returns"]
                    or contract["assertions"] - updated["assertions"]):
                raise TestAgentError("fixture_revision_assertions_changed", path=ref, test=name)
        revised.append({"path": ref, "before": before[ref], "after": after[ref]})
    return {"kind": "fixture-and-platform-transport" if paging else "fixture-setup", "parent_task_id": parent_task_id, "parent_plan_id": old["id"],
            "parent_plan_digest": old["digest"], "reason": reason.strip(), "files": revised,
            "paging_transport_corrections": paging,
            "protected_case_contract_digest": digest([{k:c.get(k) for k in protected} for c in normalized])}
