"""One SDK-independent, persisted report repair state machine."""
from __future__ import annotations

import copy
import re
from collections import Counter
from .runtime_report_contract import digest

VERSION = "sapba.report-convergence/1"


class ReportCorrectionStopped(RuntimeError):
    code = "report_correction_stalled"

    def __init__(self, state):
        self.detail = {"validation_issues": state.get("validation_issues", []),
            "attempts_without_progress": state.get("without_progress", 0)}
        super().__init__(self.code)


def _signatures(issues):
    values = Counter()
    for issue in issues:
        path = str(issue.get("path") or "/")
        path = re.sub(r"/\d+(?=/|$)", "/*", path)
        if issue.get("constraint") == "extra_forbidden":
            path = path.rsplit("/", 1)[0] + "/<extra>"
        # Referenced identifiers/values are deliberately NOT part of a signature.
        values[digest((str(issue.get("code") or "schema"), path, str(issue.get("constraint") or "business")))] += 1
    return values


def advance(previous, *, issues, acceptance, legacy_progress, relevant_generation):
    if previous.get("terminal") is True:
        return copy.deepcopy(previous)
    if not issues:
        return {"version": VERSION, "without_progress": 0, "resolved": [], "active": [],
                "validation_issues": [], "terminal": False, "generation": relevant_generation}
    counts = _signatures(issues)
    current = set(counts)
    old = set(previous.get("active", []))
    resolved_before = set(previous.get("resolved", []))
    newly_resolved = (old - current) - resolved_before
    best = previous.get("best_counts", {})
    reduced = any(key in best and count < best[key] for key, count in counts.items())
    # A new evidence ID or assessment call is audit metadata, not proof that a
    # blocking requirement improved. Only an improved checker result counts.
    progressed = bool(newly_resolved) or reduced
    count = 1 if progressed else int(previous.get("without_progress", 0)) + 1
    legacy_key = digest({"issues": issues, "progress": legacy_progress})
    legacy_count = int(previous.get("legacy_consecutive", 0)) + 1 if previous.get("legacy_key") == legacy_key else 1
    terminal = count >= 3 or acceptance and legacy_count >= 2
    code = "acceptance_report_validation_failed" if acceptance and legacy_count >= 2 else "report_correction_stalled"
    return {"version": VERSION, "active": sorted(current), "resolved": sorted(resolved_before | newly_resolved),
        "best_counts": {key: min(best.get(key, counts.get(key, 0)), counts.get(key, 0)) for key in set(best) | current},
        "without_progress": count, "generation": relevant_generation, "legacy_key": legacy_key,
        "legacy_consecutive": legacy_count, "consecutive": legacy_count,
        "validation_issues": copy.deepcopy(issues), "terminal": terminal,
        "stop_code": code if terminal else None}


def stop_state(state):
    failure = state.get("report_convergence") or state.get("acceptance_validation_failure") or {}
    return failure if failure.get("terminal") is True else None


def raise_if_stopped(state):
    failure = stop_state(state)
    if failure:
        if failure.get("stop_code") in {None, "acceptance_report_validation_failed"}:
            from .harness import AcceptanceReportValidationError
            raise AcceptanceReportValidationError(failure.get("validation_issues") or [])
        raise ReportCorrectionStopped(failure)
