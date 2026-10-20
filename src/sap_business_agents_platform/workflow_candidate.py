"""Candidate-only workflow checks. Historical definitions are never rewritten."""
from __future__ import annotations

import re
from typing import Any

from .runtime_contract import RuntimeContractError

ROOT_FIELDS = frozenset({"schemaVersion", "id", "version", "title", "description", "mode", "readOnly",
    "inputSchema", "outputSchema", "nodes", "connections", "outputs", "policies",
    "integrationInputs", "outputActions"})
PLATFORM_FIELDS = frozenset({"status", "validation", "acceptance", "publication", "publishedAt",
    "published_at", "acceptanceReport", "validationRunId"})


def pointer_parts(path: str) -> tuple[str, ...]:
    if (not isinstance(path, str) or len(path) > 500 or not path.startswith("/") or path == "/"
            or "*" in path or re.search(r"~(?![01])", path)):
        raise RuntimeContractError("workflow_change_scope_invalid")
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/"))


def _pointer(parts: tuple[str, ...]) -> str:
    return "/" + "/".join(p.replace("~", "~0").replace("/", "~1") for p in parts)


def change_paths(before: Any, after: Any, parts: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Structural leaves, not the coarse array replacements used by display Diff."""
    if type(before) is not type(after):
        return [parts]
    if isinstance(before, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            child = (*parts, key)
            result.extend(change_paths(before[key], after[key], child)
                if key in before and key in after else [child])
        return result
    if isinstance(before, list):
        # Changing length redefines the collection, not just one selected node.
        if len(before) != len(after):
            return [parts]
        return [path for i, (left, right) in enumerate(zip(before, after))
                for path in change_paths(left, right, (*parts, str(i)))]
    return [] if before == after else [parts]


def checked_scope(paths: list[str] | None, references: list[dict], *, intent: str) -> list[str] | None:
    if paths is None:
        return None
    if intent != "revise" or not isinstance(paths, list) or not 1 <= len(paths) <= 20:
        raise RuntimeContractError("workflow_change_scope_invalid")
    ref_paths = {r["path"] for r in references if r.get("kind") == "workflow_field"}
    for path in paths:
        parts = pointer_parts(path)
        if path not in ref_paths or parts[0] not in ROOT_FIELDS or parts[0] in {"id", "version", "schemaVersion"}:
            raise RuntimeContractError("workflow_change_scope_invalid")
    if len(set(paths)) != len(paths):
        raise RuntimeContractError("workflow_change_scope_invalid")
    return list(paths)


def check_candidate(source: dict, candidate: dict, *, allowed_paths: list[str] | None = None,
                    agents=None) -> None:
    """One SDK-independent gate before application; no normalization or stripping."""
    issues = []
    if (not isinstance(candidate, dict) or type(candidate.get("schemaVersion")) is not int
            or candidate["schemaVersion"] not in {1, 2}):
        raise RuntimeContractError("workflow_candidate_invalid")
    for key in source.keys() | candidate.keys():
        if key in PLATFORM_FIELDS or key not in ROOT_FIELDS:
            if key not in source or key not in candidate or change_paths(source[key], candidate[key]):
                issues.append({"path": _pointer((key,)), "constraint": "platform_managed" if key in PLATFORM_FIELDS else "unknown_root_field"})
    if issues:
        raise RuntimeContractError("workflow_candidate_contract_invalid", issues[:50])
    if allowed_paths is not None:
        allowed = [pointer_parts(p) for p in allowed_paths]
        outside = [p for p in change_paths(source, candidate) if not any(p[:len(a)] == a for a in allowed)]
        if outside:
            raise RuntimeContractError("workflow_change_scope_violation", [
                {"path": _pointer(p), "constraint": "outside_allowed_paths"} for p in outside[:50]])
    if agents is not None:
        from .workflows import validate_workflow
        validate_workflow(candidate, agents, require_pins=True)
