"""Revision-bound formal acceptance campaigns for managed Agent drafts."""
from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from .acceptance import CanonicalTestCase, agent_execution_digest, canonical_hash, validate_direct_baseline
from .acceptance_projection import AcceptanceProjectionSpec
from .acceptance_contract import BusinessContractError, readiness, normalized_issues
from .agent_lifecycle import AgentLifecycleError
from .managed_rules import source_digest
from .models import RunCreate, RunMode, RunStatus, TERMINAL_STATUSES, utc_now
from .security import secret_domain, sensitive_input_properties
from .harness_diagnostics import run_diagnostics


BASELINE_SECONDS = 600
FREE_QUERY_SECONDS = 1800
FIXED_AGENT_SECONDS = 600
FINALIZE_SECONDS = 120
TERMINAL_CAMPAIGN_STATUSES = {
    "passed", "failed", "blocked", "cancelled", "interrupted", "superseded",
}


def _acceptance_mode(manifest: dict[str, Any]) -> str:
    """Resolve the campaign mode without weakening legacy candidates.

    Older Agent packages predate the explicit acceptanceMode field.  The
    authoring UI has always presented those candidates as three-stage
    acceptance, which is the stricter option because it includes the free-query
    comparison.  Keep that compatibility default in the controller as well;
    an explicitly supplied unknown value remains an error.
    """
    raw = (manifest.get("validation") or {}).get("acceptanceMode")
    if raw is None or raw == "":
        return "three_stage"
    mode = str(raw)
    if mode not in {"three_stage", "deterministic_runtime"}:
        raise AgentLifecycleError(
            "The Agent acceptance mode is invalid.",
            code="agent_acceptance_mode_invalid",
        )
    return mode


class AcceptanceCampaignOperationalError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str = "Formal acceptance did not complete.",
        *,
        failure_category: str = "environment",
        failure_stage: str | None = None,
        issues: list[dict[str, Any]] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.failure_category = failure_category
        self.failure_stage = failure_stage
        self.issues = list(issues or [])
        self.diagnostics = dict(diagnostics or {})


class AgentAcceptanceJobs:
    def __init__(self, settings: Any, store: Any, lifecycle: Any, coordinator: Any, sdk_manager: Any) -> None:
        self.settings = settings
        self.store = store
        self.lifecycle = lifecycle
        self.coordinator = coordinator
        self.sdk_manager = sdk_manager
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.active_runs: dict[str, str] = {}

    def start(self, draft_id: str, payload: Any) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        self.lifecycle.require_technical_identity(draft)
        revision = int(payload.expected_revision)
        if int(draft["revision"]) != revision:
            raise AgentLifecycleError("The Agent draft changed.", code="agent_draft_conflict")
        if draft.get("status") == "published":
            raise AgentLifecycleError("The Agent draft is already published.", code="agent_draft_published")
        package = self.store.get_agent_authoring_revision(draft_id, revision)["package"]
        manifest = copy.deepcopy(package["manifest"])
        mode = _acceptance_mode(manifest)
        contract = _contract(manifest)
        if not contract.get("business_keys"):
            raise AgentLifecycleError("The Agent acceptance contract is missing.", code="agent_acceptance_contract_missing")

        # Canonicalize the request before any mutable preflight so a network
        # retry can recover an already running campaign without colliding with
        # its draft operation lock.
        schema = (manifest.get("execution") or {}).get("inputSchema") or {}
        cases: list[dict[str, Any]] = []
        secret_definitions = sensitive_input_properties(schema)
        descriptor = getattr(self.coordinator.secret_protector, "hmac_descriptor", None)
        for item in payload.cases:
            public_input = copy.deepcopy(item.input)
            sensitive = dict(item.sensitive_inputs)
            misplaced = sorted(set(public_input).intersection(secret_definitions))
            unknown = sorted(set(sensitive).difference(secret_definitions))
            if misplaced:
                raise AgentLifecycleError("Sensitive inputs require the protected channel.", code="sensitive_input_channel_required")
            if unknown:
                raise AgentLifecycleError("Unknown sensitive acceptance input.", code="agent_input_invalid", detail={"fields": unknown})
            _validate_case_input({**public_input, **sensitive}, schema)
            if sensitive and not callable(descriptor):
                raise AgentLifecycleError(
                    "Protected acceptance inputs are unavailable.",
                    code="sensitive_input_protection_unavailable",
                )
            fingerprints = {
                field: descriptor(value, domain=secret_domain(secret_definitions[field], field))
                for field, value in sensitive.items()
            }
            cases.append({
                "case_id": item.case_id, "input": public_input,
                "sensitive_inputs": sensitive, "sensitive_fingerprints": fingerprints,
            })
        request_hash = canonical_hash({
            "revision": revision,
            "cases": [{"case_id": item["case_id"], "input": item["input"],
                       "sensitive_fingerprints": item["sensitive_fingerprints"]} for item in cases],
        })
        for prior in self.store.list_agent_acceptance_campaigns(draft_id):
            if prior["request_id"] == payload.request_id:
                if prior["request_hash"] != request_hash:
                    raise AgentLifecycleError("The request ID was reused with different cases.", code="agent_acceptance_request_conflict")
                return self.get(draft_id, prior["campaign_id"])

        # Static validation is refreshed before the long-lived operation lock is acquired.
        checked = self.lifecycle.validate(draft_id, expected_revision=revision)
        static = checked.get("static_checks") or {}
        if static.get("errors"):
            raise AgentLifecycleError("Automatic Agent checks must pass first.", code="agent_static_validation_failed")
        if (static.get("presentation_contract") or {}).get("status") != "ready":
            raise AgentLifecycleError(
                "Complete the Agent business domain, SAP scope and business-step documentation first.",
                code="agent_presentation_contract_invalid",
                detail={"issues": (static.get("presentation_contract") or {}).get("issues", [])},
            )
        draft = self.store.get_agent_authoring_draft(draft_id)
        reusable = self.lifecycle._formal_acceptance(draft, package)
        if reusable and reusable.get("reused_validation"):
            raise AgentLifecycleError(
                "This documentation-only revision already reuses the source version PASS acceptance.",
                code="agent_acceptance_reused",
            )
        trial = copy.deepcopy((draft.get("metadata") or {}).get("effective_trial") or {})
        trial_revision = trial.get(
            "catalog_metadata_revision",
            trial.get("revision", trial.get("draft_revision")),
        )
        if int(trial_revision or 0) != revision:
            raise AgentLifecycleError("A trial for the current revision is required.", code="agent_trial_required")
        if (
            trial.get("agent_id") != draft.get("agent_id")
            or trial.get("execution_digest") != agent_execution_digest(manifest, package.get("rules"))
            or trial.get("acceptance_contract_digest")
            != canonical_hash((manifest.get("execution") or {}).get("acceptance") or {})
        ):
            raise AgentLifecycleError("A trial bound to the current contract is required.", code="agent_trial_required")
        if trial.get("verdict") not in {"PASS", "INCONCLUSIVE"}:
            raise AgentLifecycleError("A completed read-only trial is required.", code="agent_trial_required")
        if trial.get("business_output_available") is not True or trial.get("output_schema_valid") is not True or trial.get("read_only_audit") is not True:
            raise AgentLifecycleError("The latest trial has no valid business output.", code="agent_trial_business_output_missing")
        try:
            trial_run = self.store.get_run(str(trial.get("run_id") or "")) if trial.get("run_id") else None
        except KeyError:
            trial_run = None
        trial_result = trial_run.result.model_dump(mode="json") if trial_run and trial_run.result else None
        preflight = readiness(manifest, revision, result=trial_result)
        if preflight["status"] != "ready":
            raise AgentLifecycleError(
                "Complete the business contract and check the current trial output before formal acceptance.",
                code="agent_acceptance_contract_not_ready", detail=preflight,
            )
        if self.lifecycle._platform_changes_pending(draft_id):
            raise AgentLifecycleError(
                "Apply and independently verify the required platform changes first.",
                code="runtime_changeset_integration_verification_required",
            )
        try:
            runtime = self.sdk_manager.runtime_snapshot()
        except Exception as exc:
            raise AgentLifecycleError(str(exc), code=str(getattr(exc, "code", "runtime_not_selectable"))) from exc

        try:
            operation = self.store.reserve_agent_operation(
                draft_id, revision, "formal_acceptance", payload.request_id, request_hash,
            )
        except ValueError as exc:
            raise AgentLifecycleError("This Agent draft is busy or changed.", code=str(exc)) from exc
        campaign_id = f"campaign_{uuid.uuid4().hex[:20]}"
        artifact_dir = self.settings.data_root / "agent-acceptance" / campaign_id
        digests = {
            "execution_digest": agent_execution_digest(manifest, package.get("rules")),
            "rules_digest": source_digest(package.get("rules")) if package.get("rules") else None,
            "acceptance_contract_digest": canonical_hash((manifest.get("execution") or {}).get("acceptance") or {}),
            "runtime_digest": canonical_hash(runtime),
        }
        try:
            self.store.create_agent_acceptance_campaign(
                campaign={
                    "campaign_id": campaign_id, "draft_id": draft_id, "revision": revision,
                    "operation_id": operation["operation_id"], "request_id": payload.request_id,
                    "request_hash": request_hash, "agent_id": draft["agent_id"],
                    "acceptance_mode": mode, "status": "queued", "phase": "preparing",
                    "runtime": runtime, "digests": digests, "artifact_dir": str(artifact_dir),
                },
                cases=[{key: item[key] for key in ("case_id", "input", "sensitive_fingerprints")} for item in cases],
            )
            self.store.update_agent_operation(draft_id, operation["operation_id"], detail={
                "campaign_id": campaign_id, "status": "queued", "phase": "preparing",
                "case_count": len(cases), "acceptance_mode": mode,
            })
            task = asyncio.create_task(
                self._run(campaign_id, draft_id, revision, operation["operation_id"], package,
                          contract, runtime, cases),
                name=f"agent-acceptance-{campaign_id}",
            )
            self.tasks[campaign_id] = task
            task.add_done_callback(lambda _task: self.tasks.pop(campaign_id, None))
        except BaseException:
            self.store.finish_agent_operation(draft_id, operation["operation_id"], "failed")
            raise
        return self.get(draft_id, campaign_id)

    async def _run(
        self, campaign_id: str, draft_id: str, revision: int, operation_id: str,
        package: dict[str, Any], contract: dict[str, Any], runtime: dict[str, Any],
        cases: list[dict[str, Any]],
    ) -> None:
        started_at = utc_now()
        self.store.update_agent_acceptance_campaign(
            draft_id, campaign_id, status="running", phase="preparing", started_at=started_at,
            event_type="campaign_started", event_data={"case_count": len(cases)},
        )
        results: list[dict[str, Any]] = []
        try:
            for index, case_value in enumerate(cases):
                self._assert_active(draft_id, campaign_id, operation_id, revision)
                result = await self._run_case(
                    campaign_id, draft_id, revision, index, case_value,
                    package, contract, runtime,
                )
                results.append(result)
            report = self._aggregate(
                campaign_id, draft_id, revision, package, contract, runtime, results, started_at,
            )
            current = self.store.get_agent_authoring_draft(draft_id)
            current_package = self.store.get_agent_authoring_revision(draft_id, int(current["revision"]))["package"]
            current_digests = {
                "execution_digest": agent_execution_digest(current_package["manifest"], current_package.get("rules")),
                "rules_digest": source_digest(current_package.get("rules")) if current_package.get("rules") else None,
                "acceptance_contract_digest": canonical_hash((current_package["manifest"].get("execution") or {}).get("acceptance") or {}),
            }
            expected = self.store.get_agent_acceptance_campaign(draft_id, campaign_id)["digests"]
            if int(current["revision"]) != revision or any(current_digests[key] != expected.get(key) for key in current_digests):
                report["applicable_to_current_revision"] = False
                self._write_artifacts(Path(self.get(draft_id, campaign_id)["artifact_dir"]), report)
                self.store.update_agent_acceptance_campaign(
                    draft_id, campaign_id, status="superseded", phase="completed",
                    report=report, report_digest=canonical_hash(report), completed_at=utc_now(),
                    event_type="campaign_superseded", event_data={"code": "agent_acceptance_revision_conflict"},
                )
                self.store.finish_agent_operation(draft_id, operation_id, "superseded")
                return
            report["applicable_to_current_revision"] = True
            report_digest = canonical_hash({key: value for key, value in report.items() if key != "report_digest"})
            report["report_digest"] = report_digest
            self._write_artifacts(Path(self.get(draft_id, campaign_id)["artifact_dir"]), report)
            status = {"PASS": "passed", "FAIL": "failed", "BLOCKED": "blocked"}[report["verdict"]]
            if not self.store.bind_agent_acceptance_report(
                draft_id=draft_id, campaign_id=campaign_id, revision=revision,
                operation_id=operation_id, report=report, report_digest=report_digest,
                campaign_status=status,
            ):
                raise AcceptanceCampaignOperationalError("agent_acceptance_revision_conflict")
        except asyncio.CancelledError:
            await self._finish_not_tested(draft_id, campaign_id, operation_id, "cancelled", "agent_acceptance_cancelled")
        except BusinessContractError as exc:
            await self._finish_not_tested(draft_id, campaign_id, operation_id, "failed", exc.code,
                                         failure_category="contract", failure_stage=exc.stage, issues=exc.issues)
        except AcceptanceCampaignOperationalError as exc:
            status = "failed" if exc.failure_category == "contract" else "interrupted"
            await self._finish_not_tested(
                draft_id,
                campaign_id,
                operation_id,
                status,
                exc.code,
                failure_category=exc.failure_category,
                failure_stage=exc.failure_stage,
                issues=exc.issues,
                diagnostics=exc.diagnostics,
            )
        except Exception as exc:
            code = str(getattr(exc, "code", "") or "agent_acceptance_internal_error")
            if not code.replace("_", "").isalnum():
                code = "agent_acceptance_internal_error"
            await self._finish_not_tested(draft_id, campaign_id, operation_id, "interrupted", code)

    async def _run_case(
        self, campaign_id: str, draft_id: str, revision: int, index: int,
        case_value: dict[str, Any], package: dict[str, Any], contract: dict[str, Any],
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        from scripts.run_three_stage_acceptance import (
            _acceptance_prompt, _compare, _normalize_acceptance_projection,
            _normalize_run, _projection_spec, _validate_projection_matches_visible_report,
        )

        case_id = case_value["case_id"]
        manifest = package["manifest"]
        case = _canonical_case(manifest, case_value["input"], case_id, contract)
        self.store.update_agent_acceptance_campaign(
            draft_id, campaign_id, phase="baseline",
        )
        self.store.update_agent_acceptance_case(
            campaign_id, case_id, status="running", phase="baseline", started_at=utc_now(),
            event_type="case_started", event_data={"case_index": index + 1},
        )
        projection_spec = _projection_spec(contract)
        public_inputs = json.dumps(case_value["input"], ensure_ascii=False, sort_keys=True)
        protected_notice = (
            "\n\nProtected confirmed input fields are available only through the secure tool channel: "
            + ", ".join(sorted(case_value["sensitive_inputs"]))
            if case_value["sensitive_inputs"] else ""
        )
        baseline_query = (
            _acceptance_prompt(case, contract)
            + "\n\nIndependent baseline rules: You have no access to the candidate Agent, its manifest, "
              "rules, trial output, source checkout, prior baseline, or free-query result. Read current "
              "SAP metadata, validate every query, use only this run's read-only SAP evidence, and do not "
              "infer missing facts. The candidate must not be used to select or calculate the answer."
            + "\n\nConfirmed public inputs: " + public_inputs
            + protected_notice
        )
        baseline_id = await self.coordinator.submit_acceptance_query(
            RunCreate(mode=RunMode.free_query, query=baseline_query,
                      acceptanceSpec=AcceptanceProjectionSpec.model_validate(projection_spec),
                      sensitiveInputs=case_value["sensitive_inputs"]),
            runtime=runtime, direct_baseline=True, hard_limit_seconds=BASELINE_SECONDS,
            acceptance_contract_digest=canonical_hash(
                (manifest.get("execution") or {}).get("acceptance") or {}
            ),
            acceptance_revision=revision,
        )
        self.active_runs[campaign_id] = baseline_id
        self.store.update_agent_acceptance_case(
            campaign_id, case_id, baseline_run_id=baseline_id,
            event_type="case_stage_started", event_data={"stage": "baseline", "run_id": baseline_id},
        )
        baseline_run = await self._wait_run(
            draft_id, campaign_id, baseline_id, BASELINE_SECONDS,
            return_waiting_input=True,
        )
        if baseline_run.get("status") == RunStatus.waiting_input.value:
            if self._proven_evidence_gap(baseline_id):
                return self._complete_blocked_case(campaign_id, case_id, baseline_id, code="independent_baseline_evidence_incomplete")
            raise AcceptanceCampaignOperationalError("agent_acceptance_business_input_required", failure_stage="baseline")
        baseline_projection = _required_projection(baseline_run, "baseline", contract=contract)
        try:
            baseline_normalized = _normalize_acceptance_projection(baseline_projection, case, contract)
        except BusinessContractError as exc:
            exc.stage = "baseline"
            raise
        if contract.get("contract_version"):
            issues = normalized_issues(baseline_normalized, contract)
            if issues:
                raise BusinessContractError(issues, "independent_baseline")
            if not all(baseline_normalized.get(name) is True for name in ("source_complete", "evidence_complete", "business_complete")) or baseline_normalized.get("evidence_gap_codes"):
                if self._proven_evidence_gap(baseline_id):
                    return self._complete_blocked_case(campaign_id, case_id, baseline_id, code="independent_baseline_evidence_incomplete")
                raise AcceptanceCampaignOperationalError("independent_baseline_gap_unverified", failure_stage="baseline",
                                                        diagnostics=self._run_diagnostics(baseline_id))
        baseline_payload = _baseline_payload(baseline_run, baseline_normalized, runtime)
        validate_direct_baseline(baseline_payload, case)
        anchor_before = await self._capture_source_anchor(
            draft_id, campaign_id, baseline_id, baseline_run, position="before",
        )
        if anchor_before.get("verdict") != "PASS":
            code = str(anchor_before.get("reason_code") or "sap_source_changed_during_acceptance")
            if code in {"source_anchor_coverage_missing", "source_anchor_method_not_supported", "sap_source_changed_during_acceptance"}:
                return self._complete_blocked_case(campaign_id, case_id, baseline_id, code=code)
            raise AcceptanceCampaignOperationalError(code, failure_stage="source_anchor_before",
                                                    diagnostics=self._run_diagnostics(baseline_id))

        free_run: dict[str, Any] | None = None
        free_comparison = None
        if _acceptance_mode(manifest) == "three_stage":
            self.store.update_agent_acceptance_campaign(
                draft_id, campaign_id, phase="free_query",
            )
            self.store.update_agent_acceptance_case(
                campaign_id, case_id, phase="free_query",
                event_type="case_stage_started", event_data={"stage": "free_query"},
            )
            free_query = (
                _acceptance_prompt(case, contract)
                + "\n\nCanonical query inputs: " + public_inputs
                + protected_notice
            )
            free_id = await self.coordinator.submit_acceptance_query(
                RunCreate(mode=RunMode.free_query, query=free_query,
                          acceptanceSpec=AcceptanceProjectionSpec.model_validate(projection_spec),
                          sensitiveInputs=case_value["sensitive_inputs"]),
                runtime=runtime, direct_baseline=False, hard_limit_seconds=FREE_QUERY_SECONDS,
                acceptance_contract_digest=canonical_hash(
                    (manifest.get("execution") or {}).get("acceptance") or {}
                ),
                acceptance_revision=revision,
            )
            self.active_runs[campaign_id] = free_id
            self.store.update_agent_acceptance_case(campaign_id, case_id, free_query_run_id=free_id)
            free_run = await self._wait_run(
                draft_id, campaign_id, free_id, FREE_QUERY_SECONDS,
                return_waiting_input=True,
            )
            if free_run.get("status") == RunStatus.waiting_input.value:
                from .acceptance import SemanticComparison
                free_comparison = SemanticComparison(
                    verdict="BLOCKED",
                    expected_hash=canonical_hash(baseline_normalized),
                    actual_hash=canonical_hash({}),
                    differences=({"code": "free_query_waiting_input"},),
                )
            else:
                try:
                    free_normalized = _normalize_run(free_run, case, contract)
                except BusinessContractError as exc:
                    exc.stage = "free_query"
                    raise
                free_comparison = _compare(baseline_normalized, free_normalized, contract)
                projection = _required_projection(free_run, "free_query", contract=contract)
                visible = _validate_projection_matches_visible_report(free_run, projection, case, contract)
                if visible.verdict != "MATCH":
                    if contract.get("contract_version"):
                        raise BusinessContractError([{"code": "contract_report_records_mismatch", "path": "/acceptance_projection", "message": "Visible report and canonical projection disagree."}], "free_query")
                    free_comparison = _with_difference(
                        free_comparison, {"code": "acceptance_projection_report_mismatch",
                                          "differences": list(visible.differences)}
                    )

        self.store.update_agent_acceptance_campaign(
            draft_id, campaign_id, phase="fixed_agent",
        )
        self.store.update_agent_acceptance_case(
            campaign_id, case_id, phase="fixed_agent",
            event_type="case_stage_started", event_data={"stage": "fixed_agent"},
        )
        fixed_id = await self.coordinator.submit_agent_snapshot(
            manifest, case_value["input"], sensitive_inputs=case_value["sensitive_inputs"],
            rules_source=package.get("rules"), draft_id=draft_id, revision=revision,
        )
        self.active_runs[campaign_id] = fixed_id
        self.store.update_agent_acceptance_case(campaign_id, case_id, fixed_agent_run_id=fixed_id)
        fixed_run = await self._wait_run(draft_id, campaign_id, fixed_id, FIXED_AGENT_SECONDS)
        fixed_normalized = _normalize_run(fixed_run, case, contract)
        self.store.update_agent_acceptance_campaign(
            draft_id, campaign_id, phase="comparing",
        )
        self.store.update_agent_acceptance_case(
            campaign_id, case_id, phase="comparing",
            event_type="case_stage_started", event_data={"stage": "comparing"},
        )
        fixed_comparison = _compare(baseline_normalized, fixed_normalized, contract)
        anchor_after = await self._capture_source_anchor(
            draft_id, campaign_id, baseline_id, baseline_run, position="after",
        )
        if anchor_after.get("verdict") == "UNAVAILABLE" and anchor_after.get("reason_code") not in {
            "source_anchor_coverage_missing", "source_anchor_method_not_supported"
        }:
            raise AcceptanceCampaignOperationalError(str(anchor_after.get("reason_code") or "source_anchor_read_failed"),
                                                    failure_stage="source_anchor_after", diagnostics=self._run_diagnostics(baseline_id))
        anchors_stable = bool(
            anchor_before.get("verdict") == "PASS"
            and anchor_after.get("verdict") == "PASS"
            and anchor_before.get("observed_hash") == anchor_after.get("observed_hash")
        )
        anchor_verdict = (
            "PASS" if anchors_stable
            else "UNAVAILABLE" if "UNAVAILABLE" in {
                anchor_before.get("verdict"), anchor_after.get("verdict")
            }
            else "CHANGED"
        )
        anchor_reason = None if anchors_stable else (
            anchor_before.get("reason_code") or anchor_after.get("reason_code")
            or "sap_source_changed_during_acceptance"
        )

        comparisons = [fixed_comparison.verdict]
        if free_comparison is not None:
            comparisons.append(free_comparison.verdict)
        blocking = sorted(set(contract.get("blocking_limitations") or []).intersection(
            set(baseline_normalized.get("limitations") or [])
        ))
        complete = bool(
            baseline_normalized.get("source_complete")
            and baseline_normalized.get("evidence_complete", True)
            and baseline_normalized.get("business_complete", True)
        )
        if contract.get("contract_version"):
            complete = complete and all(
                item.get("source_complete") is True and item.get("evidence_complete") is True
                and item.get("business_complete") is True
                for item in ([fixed_normalized] + ([free_normalized] if free_run and free_run.get("status") != "waiting_input" else []))
            )
        verdict = (
            "BLOCKED" if not anchors_stable or (contract.get("contract_version") and (not complete or blocking))
            else "FAIL" if "MISMATCH" in comparisons
            else "BLOCKED" if "BLOCKED" in comparisons or blocking or not complete
            else "PASS"
        )
        result = {
            "case_id": case_id, "verdict": verdict,
            "failure_category": "business" if verdict == "FAIL" else "evidence" if verdict == "BLOCKED" else None,
            "baseline": {"schema_version": baseline_payload["schema_version"],
                         "run_id": baseline_id, "runtime": baseline_payload["runtime"],
                         "runtime_snapshot": baseline_payload["runtime_snapshot"],
                         "candidate_access": False,
                         "sources": baseline_payload["sources"],
                         "tool_summary": baseline_payload["tool_summary"],
                         "result_hash": baseline_payload["result_hash"],
                         "normalized_result": baseline_normalized},
            "free_query": ({"run_id": free_run.get("run_id"), "status": free_run.get("status"),
                            "comparison": free_comparison.as_dict()}
                           if free_run is not None and free_comparison is not None
                           else {"run_id": None, "status": "not_applicable", "comparison": "NOT_APPLICABLE"}),
            "fixed_agent": {"run_id": fixed_id, "status": fixed_run.get("status"),
                            "comparison": fixed_comparison.as_dict()},
            "source_anchors": {
                "verdict": anchor_verdict,
                "method": "controller_replay_of_independent_baseline_get_plans",
                "before": anchor_before,
                "after": anchor_after,
                "reason_code": anchor_reason,
            },
            "blocking_limitations": blocking,
            "validation_issues": ([] if anchors_stable else [{
                "code": anchor_reason,
                "classification": "environment",
            }]),
            "completed_at": utc_now(),
        }
        self.store.update_agent_acceptance_case(
            campaign_id, case_id, status=verdict.lower(), phase="completed", result=result,
            completed_at=result["completed_at"], event_type="case_completed",
            event_data={"verdict": verdict},
        )
        return result

    async def _capture_source_anchor(
        self, draft_id: str, campaign_id: str, baseline_run_id: str,
        baseline_run: dict[str, Any], *, position: str,
    ) -> dict[str, Any]:
        """Replay every independent baseline GET plan and retain hashes only."""
        try:
            async with asyncio.timeout(FINALIZE_SECONDS):
                return await self._capture_source_anchor_within_deadline(
                    draft_id, campaign_id, baseline_run_id, baseline_run, position=position,
                )
        except TimeoutError:
            return {"verdict": "UNAVAILABLE", "reason_code": "source_anchor_timeout"}

    async def _capture_source_anchor_within_deadline(
        self, draft_id: str, campaign_id: str, baseline_run_id: str,
        baseline_run: dict[str, Any], *, position: str,
    ) -> dict[str, Any]:
        result = baseline_run.get("result") or {}
        harness = getattr(self.coordinator, "harness", None)
        broker = getattr(harness, "broker", None)
        read_evidence = getattr(broker, "_read_evidence", None)
        sap_read = getattr(self.coordinator, "sap_read", None)
        if not callable(read_evidence) or sap_read is None:
            return {"verdict": "UNAVAILABLE", "reason_code": "source_anchor_runtime_unavailable"}
        expected: list[dict[str, Any]] = []
        observed: list[dict[str, Any]] = []
        # Check full coverage before replaying even one source or starting the
        # expensive comparison stages. Skill anchors are not implemented yet.
        source_calls = [call for call in result.get("tool_calls") or []
                        if isinstance(call, dict) and call.get("evidence_ref") and call.get("tool") != "sap_evidence_read"]
        if not source_calls or any(call.get("tool") != "sap_query_execute" for call in source_calls):
            return {"verdict": "UNAVAILABLE", "reason_code": "source_anchor_coverage_missing"}
        try:
            for call in source_calls:
                if not isinstance(call, dict) or not call.get("evidence_ref"):
                    continue
                if call.get("tool") != "sap_query_execute":
                    return {"verdict": "UNAVAILABLE", "reason_code": "source_anchor_coverage_missing"}
                safe_input = call.get("input") if isinstance(call.get("input"), dict) else {}
                plan = safe_input.get("plan") if isinstance(safe_input.get("plan"), dict) else None
                if plan is None or not _get_only_plan(plan):
                    return {"verdict": "UNAVAILABLE", "reason_code": "source_anchor_method_not_supported"}
                raw, _metadata = read_evidence(baseline_run_id, str(call["evidence_ref"]))
                expected.append(_anchor_value(raw, str(call["evidence_ref"])))
                query = str(safe_input.get("query") or "")
                validation = await sap_read.validate_plan(plan, query)
                if validation.get("ok") is not True:
                    return {"verdict": "UNAVAILABLE", "reason_code": "source_anchor_plan_rejected"}
                replay = await sap_read.execute_plan(
                    validation.get("normalized_plan") or plan,
                    query,
                    conversation_id=f"{campaign_id}:anchor:{position}",
                )
                observed.append(_anchor_value(replay, str(call["evidence_ref"])))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = str(getattr(exc, "code", "") or "source_anchor_read_failed")
            return {"verdict": "UNAVAILABLE", "reason_code": code}
        if not expected:
            return {"verdict": "UNAVAILABLE", "reason_code": "source_anchor_coverage_missing"}
        expected_hash = canonical_hash(expected)
        observed_hash = canonical_hash(observed)
        value = {
            "verdict": "PASS" if expected_hash == observed_hash else "CHANGED",
            "expected_hash": expected_hash,
            "observed_hash": observed_hash,
            "source_count": len(expected),
            "captured_at": utc_now(),
        }
        self.store.update_agent_acceptance_campaign(
            draft_id, campaign_id,
            event_type="source_anchor_captured",
            event_data={"position": position, "verdict": value["verdict"], "source_count": len(expected)},
        )
        return value

    def _complete_blocked_case(
        self, campaign_id: str, case_id: str, baseline_run_id: str, *, code: str,
    ) -> dict[str, Any]:
        completed_at = utc_now()
        blocked_comparison = {
            "verdict": "BLOCKED", "expected_hash": "", "actual_hash": "",
            "differences": [{"code": code}],
        }
        result = {
            "case_id": case_id, "verdict": "BLOCKED",
            "failure_category": "evidence", "failure_stage": "baseline",
            "baseline": {"run_id": baseline_run_id, "status": "waiting_input" if code == "test_data_gap" else "inconclusive"},
            "free_query": {"run_id": None, "status": "not_run", "comparison": blocked_comparison},
            "fixed_agent": {"run_id": None, "status": "not_run", "comparison": blocked_comparison},
            "source_anchors": {"verdict": "UNAVAILABLE", "reason_code": code},
            "blocking_limitations": [code],
            "validation_issues": [{"code": code, "classification": "evidence"}],
            "completed_at": completed_at,
        }
        self.store.update_agent_acceptance_case(
            campaign_id, case_id, status="blocked", phase="completed", result=result,
            completed_at=completed_at, event_type="case_completed",
            event_data={"verdict": "BLOCKED", "code": code},
        )
        return result

    async def _wait_run(
        self, draft_id: str, campaign_id: str, run_id: str, timeout: int,
        *, return_waiting_input: bool = False,
    ) -> dict[str, Any]:
        queue_deadline = time.monotonic() + max(timeout * 5, 1800)
        active_deadline: float | None = None
        while True:
            self._assert_not_cancelled(draft_id, campaign_id)
            record = self.store.get_run(run_id)
            if record.status in TERMINAL_STATUSES:
                value = record.model_dump(mode="json")
                result = value.get("result") or {}
                stop_reason = (result.get("harness") or {}).get("stop_reason")
                if stop_reason in {"limit_reached", "interrupted", "capability_unavailable"}:
                    raise AcceptanceCampaignOperationalError(
                        "agent_acceptance_stage_timeout" if stop_reason == "limit_reached" else "agent_acceptance_stage_interrupted",
                        failure_stage=self.get(draft_id, campaign_id).get("phase"), diagnostics=self._run_diagnostics(run_id))
                if record.status in {RunStatus.failed, RunStatus.cancelled}:
                    code = str((record.error or {}).get("code") or "agent_acceptance_stage_failed")
                    detail = (record.error or {}).get("detail") or {}
                    issues = detail.get("validation_issues") if isinstance(detail, dict) else []
                    category = "contract" if code == "acceptance_report_validation_failed" else "environment"
                    raise AcceptanceCampaignOperationalError(
                        code,
                        failure_category=category,
                        failure_stage=self.get(draft_id, campaign_id).get("phase"),
                        issues=list(issues or []),
                        diagnostics=self._run_diagnostics(run_id),
                    )
                return value
            if return_waiting_input and record.status == RunStatus.waiting_input:
                return record.model_dump(mode="json")
            now = time.monotonic()
            if record.status == RunStatus.queued:
                if now >= queue_deadline:
                    raise AcceptanceCampaignOperationalError("agent_acceptance_queue_timeout")
            else:
                broker = getattr(getattr(self.coordinator, "harness", None), "broker", None)
                if broker and record.mode == RunMode.free_query:
                    budget = broker.review_deadline(run_id)
                    expired = budget["elapsed_seconds"] >= budget["hard_limit_seconds"]
                else:
                    if active_deadline is None:
                        elapsed = 0
                        if record.started_at:
                            started = datetime.fromisoformat(str(record.started_at).replace("Z", "+00:00"))
                            elapsed = max(0, (datetime.now(timezone.utc) - started).total_seconds())
                        active_deadline = now + max(0, timeout - elapsed)
                    expired = now >= active_deadline
                if expired:
                    failure = self.store.get_harness_state(run_id).get(
                        "acceptance_validation_failure"
                    ) or {}
                    raise AcceptanceCampaignOperationalError(
                        "agent_acceptance_stage_timeout",
                        failure_stage=self.get(draft_id, campaign_id).get("phase"),
                        issues=list(failure.get("validation_issues") or []),
                        diagnostics=self._run_diagnostics(run_id),
                    )
            await asyncio.sleep(0.5)

    def _run_diagnostics(self, run_id: str) -> dict[str, Any]:
        return run_diagnostics(self.store, run_id)

    def _proven_evidence_gap(self, run_id: str) -> bool:
        broker = getattr(getattr(self.coordinator, "harness", None), "broker", None)
        for call in self.store.list_harness_tool_calls(run_id):
            if call.get("tool_name") not in {"sap_query_execute", "sap_skill_execute"}:
                continue
            output = call.get("output") or {}
            if output.get("code") == "sap_read_http_error" and (output.get("detail") or {}).get("http_status") in {401, 403}:
                return True
            if broker and call.get("evidence_ref"):
                raw, metadata = broker._read_evidence(run_id, call["evidence_ref"])
                if metadata.get("source_complete") is False and raw.get("ok") is not False:
                    return True
        return False

    def _aggregate(
        self, campaign_id: str, draft_id: str, revision: int, package: dict[str, Any],
        contract: dict[str, Any], runtime: dict[str, Any], results: list[dict[str, Any]],
        started_at: str,
    ) -> dict[str, Any]:
        manifest = package["manifest"]
        mode = _acceptance_mode(manifest)
        verdicts = {item["verdict"] for item in results}
        verdict = "FAIL" if "FAIL" in verdicts else "BLOCKED" if "BLOCKED" in verdicts else "PASS"
        fixed_verdicts = {
            item["fixed_agent"]["comparison"]["verdict"] for item in results
        }
        fixed = (
            "MATCH" if fixed_verdicts == {"MATCH"}
            else "MISMATCH" if "MISMATCH" in fixed_verdicts
            else "BLOCKED"
        )
        if mode == "three_stage":
            free_verdicts = {
                item["free_query"]["comparison"]["verdict"] for item in results
            }
            free = (
                "MATCH" if free_verdicts == {"MATCH"}
                else "MISMATCH" if "MISMATCH" in free_verdicts
                else "BLOCKED"
            )
        else:
            free = "NOT_APPLICABLE"
        blockers = sorted({code for item in results for code in item.get("blocking_limitations") or []})
        now = utc_now()
        return {
            "schema_version": "1.0", "type": "formal_acceptance",
            "campaign_id": campaign_id, "draft_id": draft_id,
            "agent_id": manifest.get("slug"), "revision": revision,
            "tested_at": now, "started_at": started_at, "completed_at": now,
            "execution_digest": agent_execution_digest(manifest, package.get("rules")),
            "managed_rule_digest": source_digest(package.get("rules")) if package.get("rules") else None,
            "acceptance_contract_digest": canonical_hash((manifest.get("execution") or {}).get("acceptance") or {}),
            "acceptanceMode": mode, "verdict": verdict, "executable": verdict == "PASS",
            "fixedAgentComparison": fixed, "freeQueryComparison": free,
            "blockingLimitations": blockers, "runtime": copy.deepcopy(runtime),
            "case_count": len(results), "cases": results,
            "failure_category": "business" if verdict == "FAIL" else "evidence" if verdict == "BLOCKED" else None,
            "read_only_audit": True,
        }

    async def _finish_not_tested(
        self, draft_id: str, campaign_id: str, operation_id: str, status: str, code: str,
        *, failure_category: str = "environment", failure_stage: str | None = None,
        issues: list[dict[str, Any]] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        active_run = self.active_runs.get(campaign_id)
        cleanup_complete = True
        if active_run:
            diagnostics = diagnostics or self._run_diagnostics(active_run)
            try:
                cleanup = getattr(self.coordinator, "cancel_acceptance_run", None)
                if callable(cleanup):
                    cleanup_complete = await cleanup(active_run, timeout=10)
                else:
                    async with asyncio.timeout(10):
                        await self.coordinator.cancel(active_run)
            except Exception:
                cleanup_complete = False
        diagnostics = {**(diagnostics or {}), "cleanup_complete": cleanup_complete}
        if cleanup_complete:
            self.active_runs.pop(campaign_id, None)
        current = self.get(draft_id, campaign_id)
        completed_at = utc_now()
        for item in current.get("cases") or []:
            if item.get("status") not in {"pass", "fail", "failed", "blocked", "cancelled", "interrupted"}:
                self.store.update_agent_acceptance_case(
                    campaign_id, item["case_id"], status=status, phase="completed",
                    result={"case_id": item["case_id"], "verdict": "NOT_TESTED", "error": {"code": code},
                            "failure_category": failure_category, "failure_stage": failure_stage or item.get("phase"),
                            "validation_issues": issues or [], "diagnostics": diagnostics},
                    completed_at=completed_at,
                )
        report = {
            "schema_version": "1.0", "type": "formal_acceptance_attempt",
            "draft_id": draft_id, "agent_id": current.get("agent_id"),
            "revision": current.get("revision"),
            "acceptanceMode": current.get("acceptance_mode"),
            "campaign_id": campaign_id, "verdict": "NOT_TESTED", "executable": False,
            "error": {"code": code}, "completed_at": completed_at,
            "failure_category": failure_category, "failure_stage": failure_stage or current.get("phase"),
            "validation_issues": issues or [],
            "diagnostics": diagnostics,
        }
        self._write_artifacts(Path(current["artifact_dir"]), report)
        self.store.update_agent_acceptance_campaign(
            draft_id, campaign_id, status=status if cleanup_complete else "cancelling", phase="completed" if cleanup_complete else "cleanup", report=report,
            report_digest=canonical_hash(report), completed_at=report["completed_at"],
            event_type="campaign_finished_without_certificate", event_data={"code": code},
        )
        try:
            if cleanup_complete:
                self.store.finish_agent_operation(draft_id, operation_id, status)
        except Exception:
            pass

    def _assert_active(self, draft_id: str, campaign_id: str, operation_id: str, revision: int) -> None:
        if not self.store.assert_agent_operation(draft_id, operation_id, revision):
            raise AcceptanceCampaignOperationalError("agent_acceptance_revision_conflict")
        self._assert_not_cancelled(draft_id, campaign_id)

    def _assert_not_cancelled(self, draft_id: str, campaign_id: str) -> None:
        if self.store.get_agent_acceptance_campaign(draft_id, campaign_id)["cancel_requested"]:
            raise asyncio.CancelledError()

    def get(self, draft_id: str, campaign_id: str) -> dict[str, Any]:
        value = self.store.get_agent_acceptance_campaign(draft_id, campaign_id)
        value["estimated_max_seconds"] = len(value["cases"]) * (
            BASELINE_SECONDS + FIXED_AGENT_SECONDS + FINALIZE_SECONDS
            + (FREE_QUERY_SECONDS if value["acceptance_mode"] == "three_stage" else 0)
        )
        value["active"] = value["status"] not in TERMINAL_CAMPAIGN_STATUSES
        current = next(
            (item for item in value["cases"] if item.get("status") == "running"),
            None,
        )
        value["current_case_id"] = current.get("case_id") if current else None
        value["current_case_index"] = (int(current["case_order"]) + 1) if current else None
        artifact_root = Path(value["artifact_dir"]) if value.get("artifact_dir") else None
        value["artifacts_available"] = bool(
            artifact_root
            and (artifact_root / "report.json").is_file()
            and (artifact_root / "report.md").is_file()
        )
        return value

    def list(self, draft_id: str) -> list[dict[str, Any]]:
        return self.store.list_agent_acceptance_campaigns(draft_id)

    def events(self, draft_id: str, campaign_id: str, sequence: int = 0) -> list[dict[str, Any]]:
        self.store.get_agent_acceptance_campaign(draft_id, campaign_id)
        return self.store.agent_acceptance_events_after(campaign_id, sequence)

    async def cancel(self, draft_id: str, campaign_id: str) -> dict[str, Any]:
        current = self.get(draft_id, campaign_id)
        if not current["active"]:
            return current
        self.store.update_agent_acceptance_campaign(
            draft_id, campaign_id, status="cancelling", cancel_requested=True,
            event_type="campaign_cancelling", event_data={},
        )
        task = self.tasks.get(campaign_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        latest = self.get(draft_id, campaign_id)
        if latest["active"] and latest.get("phase") != "cleanup":
            await self._finish_not_tested(
                draft_id, campaign_id, current["operation_id"],
                "cancelled", "agent_acceptance_cancelled",
            )
        return self.get(draft_id, campaign_id)

    def artifact(self, draft_id: str, campaign_id: str, name: str) -> Path:
        if name not in {"report.json", "report.md"}:
            raise KeyError(name)
        campaign = self.get(draft_id, campaign_id)
        path = (Path(campaign["artifact_dir"]) / name).resolve()
        root = Path(campaign["artifact_dir"]).resolve()
        if root not in path.parents or not path.is_file():
            raise KeyError(name)
        return path

    async def stop(self) -> None:
        current_tasks = list(self.tasks.items())
        for campaign_id, task in current_tasks:
            if not task.done():
                task.cancel()
        if current_tasks:
            await asyncio.gather(*(task for _, task in current_tasks), return_exceptions=True)
        for campaign_id, _task in current_tasks:
            try:
                campaign = self.store.get_agent_acceptance_campaign_by_id(campaign_id)
            except (AttributeError, KeyError):
                continue
            if campaign.get("status") not in TERMINAL_CAMPAIGN_STATUSES:
                await self._finish_not_tested(
                    campaign["draft_id"], campaign_id, campaign["operation_id"],
                    "interrupted", "agent_acceptance_interrupted",
                )

    @staticmethod
    def _write_artifacts(directory: Path, report: dict[str, Any]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        json_path = directory / "report.json"
        markdown_path = directory / "report.md"
        json_content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        lines = [
            f"# Agent formal acceptance: {report.get('agent_id')}", "",
            f"- Campaign: `{report.get('campaign_id')}`",
            f"- Revision: {report.get('revision')}",
            f"- Mode: `{report.get('acceptanceMode')}`",
            f"- Verdict: **{report.get('verdict')}**", "", "## Cases", "",
        ]
        if report.get("error") or report.get("diagnostics"):
            diagnostics = report.get("diagnostics") or {}
            lines[6:6] = [
                f"- Failure code: `{(report.get('error') or {}).get('code') or 'not_recorded'}`",
                f"- Actual stage: `{report.get('failure_stage') or 'not_recorded'}`",
                f"- Last failed tool: `{diagnostics.get('last_failed_tool') or 'not_recorded'}`",
                f"- Last tool error: `{diagnostics.get('last_error_code') or 'not_recorded'}`",
                f"- Last completed tool: `{diagnostics.get('last_completed_tool') or 'not_recorded'}`",
                f"- Cleanup confirmed: `{diagnostics.get('cleanup_complete', 'not_recorded')}`",
                "- Unresolved issues: " + ", ".join(str(item.get("code")) for item in diagnostics.get("unresolved_issues") or []),
            ]
        for item in report.get("cases") or []:
            lines.extend([
                f"### `{item.get('case_id')}`", "",
                f"- Verdict: **{item.get('verdict')}**",
                f"- Independent baseline: `{(item.get('baseline') or {}).get('result_hash') or 'unavailable'}`",
                f"- Free-query comparison: `{((item.get('free_query') or {}).get('comparison') or {}).get('verdict') if isinstance((item.get('free_query') or {}).get('comparison'), dict) else (item.get('free_query') or {}).get('comparison')}`",
                f"- Fixed-Agent comparison: `{(((item.get('fixed_agent') or {}).get('comparison') or {}).get('verdict')) or 'unavailable'}`",
                f"- SAP source stability: `{((item.get('source_anchors') or {}).get('verdict')) or 'unavailable'}`",
            ])
            differences = []
            for stage in ("free_query", "fixed_agent"):
                comparison = (item.get(stage) or {}).get("comparison")
                if isinstance(comparison, dict):
                    differences.extend(
                        str(difference.get("code") or "difference")
                        for difference in comparison.get("differences") or []
                        if isinstance(difference, dict)
                    )
            if differences:
                lines.extend(["- Differences: " + ", ".join(f"`{code}`" for code in differences)])
            issues = [
                str(issue.get("code") or "acceptance_issue")
                for issue in item.get("validation_issues") or []
                if isinstance(issue, dict)
            ]
            if issues:
                lines.extend(["- Environment or evidence issues: " + ", ".join(f"`{code}`" for code in issues)])
            lines.append("")
        markdown_content = "\n".join(lines) + "\n"
        json_temporary = directory / f".{json_path.name}.{uuid.uuid4().hex}.tmp"
        markdown_temporary = directory / f".{markdown_path.name}.{uuid.uuid4().hex}.tmp"
        try:
            json_temporary.write_text(json_content, encoding="utf-8")
            markdown_temporary.write_text(markdown_content, encoding="utf-8")
            json_temporary.replace(json_path)
            markdown_temporary.replace(markdown_path)
        finally:
            json_temporary.unlink(missing_ok=True)
            markdown_temporary.unlink(missing_ok=True)


def _validate_case_input(value: dict[str, Any], schema: dict[str, Any]) -> None:
    from jsonschema import Draft202012Validator, FormatChecker
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        raise AgentLifecycleError(
            "Acceptance case input does not match the Agent input contract.",
            code="agent_input_invalid",
            detail={"fields": [".".join(str(part) for part in item.absolute_path) for item in errors]},
        )


def _contract(manifest: dict[str, Any]) -> dict[str, Any]:
    from .acceptance_contract import compile_contract
    return compile_contract(manifest)


def _canonical_case(
    manifest: dict[str, Any], input_value: dict[str, Any], case_id: str, contract: dict[str, Any],
) -> CanonicalTestCase:
    title = manifest.get("title") or {}
    purpose = manifest.get("purpose") or {}
    question = {
        locale: f"{title.get(locale) or manifest.get('slug')}: {purpose.get(locale) or title.get(locale) or ''}".strip()
        for locale in ("zh", "en")
    }
    fields = list(dict.fromkeys(
        str(item) for key in ("business_keys", "facts", "decimal_fields", "currency_fields", "unit_fields", "date_fields")
        for item in contract.get(key) or []
    ))
    return CanonicalTestCase.from_dict({
        "schema_version": "2.0", "case_id": case_id,
        "agent_id": str(manifest.get("slug") or ""), "question": question,
        "input": copy.deepcopy(input_value), "business_conditions": copy.deepcopy(input_value),
        "expected_grain": list(contract["business_keys"]),
        "expected_output": {
            "record_fields": fields, "metric_ids": list(contract.get("metrics") or []),
            "minimum_primary_evidence_rows": 0, "allow_empty_result": True,
            "evidence_scope": "complete",
        },
    })


def _required_projection(run: dict[str, Any], stage: str, *, contract: dict[str, Any] | None = None) -> dict[str, Any]:
    projection = ((run.get("result") or {}).get("acceptance_projection"))
    if not isinstance(projection, dict):
        if contract and contract.get("contract_version"):
            raise BusinessContractError([{"code": "contract_records_missing", "path": "/acceptance_projection", "message": "No comparable evidence-bound projection was produced."}], stage)
        raise AcceptanceCampaignOperationalError(f"{stage}_acceptance_projection_missing")
    return projection


def _baseline_payload(run: dict[str, Any], normalized: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    result = run.get("result") or {}
    sources: list[dict[str, Any]] = []
    for call in result.get("tool_calls") or []:
        if not isinstance(call, dict) or not call.get("evidence_ref"):
            continue
        plan = (call.get("input") or {}).get("plan") if isinstance(call.get("input"), dict) else {}
        steps = plan.get("steps") if isinstance(plan, dict) and isinstance(plan.get("steps"), list) else [plan]
        methods = [str(step.get("http_method") or "GET").upper() for step in steps if isinstance(step, dict)]
        semantic = call.get("tool") == "sap_skill_execute"
        if semantic and not methods:
            methods = ["POST"]
        read_only = bool(methods) and all(method == "GET" or (method == "POST" and semantic) for method in methods)
        scope = [
            {
                "service_name": step.get("service_name"),
                "odata_version": step.get("odata_version"),
                "entity_set": step.get("entity_set"),
            }
            for step in steps if isinstance(step, dict)
        ]
        sources.append({
            "tool": call.get("tool"), "http_method": "POST" if semantic else "GET",
            "semantic_read_only": semantic, "read_only": read_only,
            "evidence_ref": call.get("evidence_ref"),
            "plan_digest": canonical_hash(plan) if isinstance(plan, dict) and plan else None,
            "scope": scope,
        })
    if not sources or not all(item["read_only"] for item in sources):
        raise AcceptanceCampaignOperationalError("independent_baseline_read_only_audit_failed")
    payload = {
        "schema_version": "4.0", "runtime": "codex_sdk_direct_sap",
        "used_sap_business_agents": False, "candidate_access": False,
        "runtime_snapshot": copy.deepcopy(runtime), "sources": sources,
        "tool_summary": [
            {"tool": tool, "count": sum(item["tool"] == tool for item in sources)}
            for tool in sorted({str(item["tool"]) for item in sources})
        ],
        "normalized_result": normalized, "result_hash": canonical_hash(normalized),
    }
    return payload


def _with_difference(comparison: Any, difference: dict[str, Any]) -> Any:
    from .acceptance import SemanticComparison
    return SemanticComparison(
        verdict="MISMATCH", expected_hash=comparison.expected_hash,
        actual_hash=comparison.actual_hash,
        differences=tuple([*comparison.differences, difference]),
    )


def _get_only_plan(plan: dict[str, Any]) -> bool:
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else [plan]
    return bool(steps) and all(
        isinstance(step, dict) and str(step.get("http_method") or "GET").upper() == "GET"
        for step in steps
    )


def _anchor_value(raw: dict[str, Any], evidence_ref: str) -> dict[str, Any]:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    rows = data.get("results") if isinstance(data.get("results"), list) else raw.get("results")
    return {
        "evidence_ref": evidence_ref,
        "result_hash": canonical_hash(_stable_anchor_payload(raw)),
        "row_count": len(rows) if isinstance(rows, list) else None,
        "source_complete": bool(raw.get("source_complete", data.get("source_complete", False))),
    }


def _stable_anchor_payload(value: Any) -> Any:
    volatile = {
        "case_id", "elapsed_ms", "duration_ms", "started_at", "completed_at",
        "observed_at", "created_at", "updated_at",
    }
    if isinstance(value, dict):
        return {
            str(key): _stable_anchor_payload(child)
            for key, child in value.items()
            if str(key) not in volatile
        }
    if isinstance(value, list):
        return [_stable_anchor_payload(item) for item in value]
    return value
