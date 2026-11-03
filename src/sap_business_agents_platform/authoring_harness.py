"""Controller-owned authoring checkpoints and bounded repair orchestration.

The controller is deliberately separate from the candidate's editable code.
An SDK response can propose changes; only controller observations are checks.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .authoring_checks import (business_output_coverage, candidate_business_output_issues,
                               candidate_input_issues, revision_business_output_issues)
from .managed_rules import ManagedRuleError, validate_managed_rule
from .manifests import ManifestError, derive_input_display, validate_execution


def content_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class CheckOutcome:
    status: str  # passed, repairable, blocked
    issues: tuple[dict[str, Any], ...] = ()
    evidence_refs: tuple[str, ...] = ()


class AuthoringHarnessError(ValueError):
    def __init__(self, code: str, *, detail: dict | None = None):
        self.code = code
        self.detail = detail or {}
        super().__init__(code)


def public_issues(issues) -> list[dict[str, str]]:
    """Bounded structural facts only; never SDK text, defaults or candidate rows."""
    result = []
    for issue in issues[:20] if isinstance(issues, (list, tuple)) else ():
        if not isinstance(issue, dict):
            continue
        code, path = issue.get("code"), issue.get("path")
        item = {"code": code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) else "definition_invalid",
            "path": path if isinstance(path, str) and len(path) <= 512 and re.fullmatch(
                r"/manifest(?:/(?:[A-Za-z_][A-Za-z0-9_-]{0,79}|[0-9]+))*", path) else "/manifest"}
        if item not in result:
            result.append(item)
    return result


def modification_preflight(package: dict) -> dict:
    """Observe existing blockers without guessing the user's authorized scope.

    This is conditional guidance, not permission to edit or a save certificate.
    The complete candidate and save-time validators remain authoritative.
    """
    try:
        issues = public_issues(candidate_business_output_issues(package.get("manifest") or {}))
    except (TypeError, AttributeError, ValueError):
        # A manually saved incomplete structure must remain repairable. This
        # limited inspection cannot claim that the original manifest is valid.
        return {"version": 1, "status": "not_evaluated", "existing_issues": [],
            "metadata_exception": "unchanged_executable_contract_only"}
    return {"version": 1, "status": "existing_blockers" if issues else "no_known_blockers",
        "existing_issues": issues, "metadata_exception": "unchanged_executable_contract_only"}


def check_modification_candidate(package: dict, original: dict) -> CheckOutcome:
    outcome = check_candidate(package, original)
    if outcome.status != "repairable" or not any(
            issue.get("code") == "agent_business_output_contract_missing" for issue in outcome.issues):
        return outcome
    try:
        before = business_output_coverage(original.get("manifest") or {})
    except (TypeError, AttributeError, ValueError):
        return outcome
    if before is not None and not all(before.values()):
        after = business_output_coverage(package.get("manifest") or {})
        # Input/default/filter/text edits cannot repair an unchanged business
        # blocker. Genuine progress on either gate obligation may still iterate.
        progress = after is not None and any(after[key] and not before[key] for key in before)
        if not progress:
            return CheckOutcome("blocked", outcome.issues, outcome.evidence_refs)
    return outcome


def public_checkpoints(records) -> list[dict]:
    result = []
    for record in records[:5] if isinstance(records, list) else ():
        if (not isinstance(record, dict) or type(record.get("round")) is not int
                or not 1 <= record["round"] <= 5 or not isinstance(record.get("status"), str)
                or record["status"] not in {"passed", "repairable", "blocked"}):
            continue
        item = {"round": record["round"], "status": record["status"], "issues": public_issues(record.get("issues"))}
        digest = record.get("candidate_digest")
        if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest):
            item["candidate_digest"] = digest
        result.append(item)
    return result


def check_candidate(package: dict[str, Any], original: dict[str, Any]) -> CheckOutcome:
    """No SAP, arbitrary candidate imports or execution of candidate tests here."""
    try:
        manifest = package["manifest"]
        if not isinstance(manifest, dict):
            return CheckOutcome("repairable", ({"code": "agent_candidate_invalid"},))
        for key in ("slug", "module", "version", "validation"):
            if manifest.get(key) != original["manifest"].get(key):
                return CheckOutcome("blocked", ({"code": "agent_authoring_identity_or_acceptance_changed"},))
        manifest["inputs"] = derive_input_display(manifest["execution"]["inputSchema"])
        validate_execution(manifest)
        issues = candidate_input_issues(manifest) + revision_business_output_issues(package, original)
        if issues:
            return CheckOutcome("repairable", tuple(issues))
        if package.get("rules"):
            validate_managed_rule(package["rules"], expected_digest=(manifest.get("managedRule") or {}).get("sha256"))
        elif manifest.get("managedRule"):
            return CheckOutcome("repairable", ({"code": "agent_managed_rule_missing"},))
    except ManifestError as exc:
        return CheckOutcome("repairable", (exc.public_issue(),))
    except ManagedRuleError:
        return CheckOutcome("repairable", ({"code": "agent_managed_rule_invalid"},))
    except (ValueError, TypeError, KeyError) as exc:
        # Neither candidate code nor validation exception text is safe to log.
        return CheckOutcome("repairable", ({"code": str(getattr(exc, "code", "agent_candidate_invalid"))},))
    return CheckOutcome("passed")


class RepairLoop:
    """All callbacks are controller-owned; a candidate cannot supply a checker.

    Each immutable checkpoint records the actual evaluated candidate digest.
    Subtasks must implement cancellation/owned-process cleanup before returning.
    """

    def __init__(self, *, budget_seconds: float = 3600, max_rounds: int = 5,
                 clock: Callable[[], float] = time.monotonic):
        self.budget = min(3600.0, max(0.001, budget_seconds))
        self.max_rounds = min(5, max(1, max_rounds))
        self.clock = clock

    async def run(self, initial: dict, *,
                  revise: Callable[[dict, tuple[dict, ...], float], Awaitable[dict]],
                  check: Callable[[dict, float], Awaitable[CheckOutcome]],
                  checkpoint: Callable[[dict], None],
                  assert_current: Callable[[], None],
                  preflight: dict | None = None) -> dict:
        started = self.clock()
        candidate = copy.deepcopy(initial)
        issues: tuple[dict, ...] = ()
        previous_failure = None
        repeats = 0

        def failed(code, outcome, number, reason):
            issues = public_issues(outcome.issues)
            return AuthoringHarnessError(code, detail={"validation_issues": issues,
                "authoring_convergence": {"stop_reason": reason, "attempts": number,
                    "cause_code": issues[0]["code"] if issues else "definition_invalid"}})

        def remaining() -> float:
            assert_current()
            value = self.budget - (self.clock() - started)
            if value <= 0:
                raise AuthoringHarnessError("agent_feedback_timeout")
            return value

        for number in range(1, self.max_rounds + 1):
            proposal = await asyncio.wait_for(revise(copy.deepcopy(candidate), issues, remaining()), remaining())
            remaining()  # Discard a late result even if a callback ignored cancellation.
            if proposal.get("action") in {"clarify", "reply"}:
                return proposal
            candidate = copy.deepcopy(proposal["package"])
            outcome = await asyncio.wait_for(check(candidate, remaining()), remaining())
            remaining()
            if outcome.status not in {"passed", "repairable", "blocked"}:
                raise AuthoringHarnessError("agent_harness_check_invalid")
            checkpoint({"round": number, "candidate_digest": content_digest(candidate),
                        "status": outcome.status, "issues": list(outcome.issues),
                        "evidence_refs": list(outcome.evidence_refs),
                        "elapsed_seconds": max(0, self.clock() - started)})
            remaining()
            if outcome.status == "passed":
                return {**proposal, "package": candidate}
            if outcome.status == "blocked":
                raise failed(str(outcome.issues[0].get("code", "agent_harness_blocked"))
                    if outcome.issues else "agent_harness_blocked", outcome, number,
                    "preexisting_blocker_unchanged" if preflight and any(
                        issue in preflight.get("existing_issues", []) for issue in public_issues(outcome.issues)) else "blocked")
            issues = outcome.issues
            # A cosmetic file change is not progress on the same failed checks.
            failure = content_digest(issues)
            repeats = repeats + 1 if failure == previous_failure else 1
            previous_failure = failure
            if repeats >= 2:
                raise failed("agent_harness_no_progress", outcome, number, "repeated_validation_failure")
        raise failed("agent_harness_round_limit", outcome, number, "round_limit")
