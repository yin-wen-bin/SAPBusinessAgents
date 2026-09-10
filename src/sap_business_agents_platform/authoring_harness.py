"""Controller-owned authoring checkpoints and bounded repair orchestration.

The controller is deliberately separate from the candidate's editable code.
An SDK response can propose changes; only controller observations are checks.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .authoring_checks import candidate_input_issues
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
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


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
        issues = candidate_input_issues(manifest)
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
                  assert_current: Callable[[], None]) -> dict:
        started = self.clock()
        candidate = copy.deepcopy(initial)
        issues: tuple[dict, ...] = ()
        previous_failure = None
        repeats = 0

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
                raise AuthoringHarnessError(str(outcome.issues[0].get("code", "agent_harness_blocked"))
                                            if outcome.issues else "agent_harness_blocked")
            issues = outcome.issues
            # A cosmetic file change is not progress on the same failed checks.
            failure = content_digest(issues)
            repeats = repeats + 1 if failure == previous_failure else 1
            previous_failure = failure
            if repeats >= 2:
                raise AuthoringHarnessError("agent_harness_no_progress")
        raise AuthoringHarnessError("agent_harness_round_limit")
