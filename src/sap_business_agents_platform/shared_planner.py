"""One authority for business prompts, phase ordering and output application.
Native SDKs are reachable only through the injected execution driver.
"""
from __future__ import annotations
import json
import copy
import asyncio
import time
from .runtime_contract import deadline_scope
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any
from .models import PlannerDecision, RunPresentation
from .runtime_contract import ApprovalMode, Sandbox
from .runtime_workflow_contract import PROPOSAL_SCHEMA, EXPECTATIONS_SCHEMA, decode as decode_workflow, input_patch_schema
from .runtime_role_consolidation import (
    SCHEMA as ROLE_CONSOLIDATION_SCHEMA, VERSION as ROLE_CONSOLIDATION_VERSION,
    decode as decode_consolidation, candidate_support_projection, candidate_support_instructions,
)
from .runtime_prompts import (
    PLANNER_OUTPUT_SCHEMA,
    SUMMARY_OUTPUT_SCHEMA,
    AUTHOR_OUTPUT_SCHEMA,
    WORKFLOW_REVIEW_OUTPUT_SCHEMA,
    AGENT_FEEDBACK_OUTPUT_SCHEMA,
    FREE_QUERY_FEEDBACK_REVIEW_SCHEMA,
    _strict_response_schema,
    _RUN_PRESENTATION_SCHEMA,
    FREE_QUERY_PRESENTATION_REVISION_SCHEMA,
    WORKFLOW_REPAIR_OUTPUT_SCHEMA,
    WORKFLOW_COMPOSITION_OUTPUT_SCHEMA,
    WORKFLOW_FEEDBACK_OUTPUT_SCHEMA,
    ROLE_MATCHING_OUTPUT_SCHEMA,
    ROLE_MATCHING_RUNTIME_TURN_SECONDS,
    _consume_background_task,
    _await_with_hard_timeout,
    _workflow_assistant_tool_catalog,
    _workflow_feedback_prompt,
    _workflow_composition_prompt,
    _planner_prompt,
    _run_plan_turn,
    _decode_plan_json,
    _grounding_prompt,
    _schema_snapshot,
    _RUNTIME_PLAN_CONTRACT,
    _SECRET_KEYS,
    _decode_role_matching_output,
    _role_matching_thread_can_restart,
    _exact_json,
    _compact_role_match_records,
    _safe_json,
)

class SharedPlanner:
    async def review_agent_feedback(self, **kwargs):
        from .runtime_agent_authoring import run_feedback
        return await run_feedback(self, **kwargs)

    @property
    def current_operation(self):
        return _operation_context.get()

    def _runtime_client(self, **options):
        if getattr(self._driver, "provider_id", None) == "workbuddy":
            # These methods build complete prompts from saved platform context;
            # reconnect is not native SDK thread continuation.
            options["platform_context_restored"] = True
        if self.current_operation in {"compose_workflow", "review_workflow_feedback"}:
            from .runtime_workflow_contract import instructions
            options["format_instructions"] = instructions(feedback=self.current_operation == "review_workflow_feedback")
        return self._driver.client(**options)

    async def plan(self, *args, **kwargs):
        from .runtime_contract import RuntimeContractError
        raise RuntimeContractError("runtime_operation_retired")

    ground_plan = plan
    summarize = plan

    async def review_free_query_feedback(
        self,
        *,
        thread_id: str,
        original_query: str,
        previous_query: str,
        previous_plan: dict[str, Any] | None,
        previous_summary: dict[str, str],
        previous_presentation: dict[str, Any] | None,
        deterministic_rule_results: list[dict[str, Any]],
        available_evidence_refs: list[str],
        completeness: dict[str, Any],
        feedback: str,
        feedback_type_hint: str | None = None,
        supplemental_input: str | None = None,
    ) -> dict[str, Any]:
        AsyncCodex = self._runtime_client

        prompt = f"""
Classify a user's correction to a completed read-only SAP query. Do not call tools.

Original question: {original_query}
Previous effective question: {previous_query}
Previous validated plan shape: {_safe_json(previous_plan, limit=20_000)}
Previous bilingual summary: {_safe_json(previous_summary, limit=5_000)}
Previous validated presentation: {_safe_json(previous_presentation, limit=40_000)}
Deterministic rule results: {_safe_json(deterministic_rule_results, limit=20_000)}
Available evidence references: {_safe_json(available_evidence_refs, limit=20_000)}
Previous completeness: {_safe_json(completeness, limit=5_000)}
User feedback: {feedback}
UI feedback hint: {feedback_type_hint or 'None'}
Supplemental clarification: {supplemental_input or 'None'}

Rules:
1. User expectations are hypotheses, never SAP facts. Mark each expectation confirmed or
   mismatch only when the supplied validated presentation or deterministic rules directly prove
   it, and include the supporting evidence_refs. Otherwise use not_verifiable with no references.
2. Choose requery when filters, scope, dates, fields, entities, relationships, business keys,
   evidence requirements, or a fact-affecting business rule may change.
3. Choose reinterpret only for wording, language, ordering, or layout changes that require no
   new facts and no plan change.
4. Choose clarify only when one concise answer is required before either action is safe.
5. Choose start_new_session when the feedback is a materially different business question.
Return only the required structured object.
""".strip()
        async with AsyncCodex() as codex:
            thread = await codex.thread_resume(
                thread_id,
                cwd=str(self.repository_root),
                sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all,
                model=self.model,
            )
            result = await thread.run(prompt, output_schema=FREE_QUERY_FEEDBACK_REVIEW_SCHEMA, effort=self.reasoning_effort)
            return json.loads(result.final_response)


    async def revise_free_query_presentation(
        self,
        *,
        thread_id: str,
        query: str,
        feedback: str,
        previous_presentation: dict[str, Any],
        allowed_evidence_refs: list[str],
        completeness: dict[str, Any],
        rule_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        AsyncCodex = self._runtime_client

        prompt = f"""
Revise only the presentation of an already validated read-only SAP result.

Question: {query}
User presentation feedback: {feedback}
Previous presentation with current-run evidence aliases:
{_safe_json(previous_presentation, limit=80_000)}
Allowed evidence aliases: {_safe_json(allowed_evidence_refs, limit=20_000)}
Completeness (must not change): {_safe_json(completeness, limit=5_000)}
Deterministic rule results (must not change or contradict):
{_safe_json(rule_results, limit=20_000)}

Do not call tools, add facts, change completeness, or cite any evidence reference outside the
allowed list. Change only wording, language, ordering, or table layout. Return JSON only.
""".strip()
        async with AsyncCodex() as codex:
            thread = await codex.thread_resume(
                thread_id,
                cwd=str(self.repository_root),
                sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all,
                model=self.model,
            )
            result = await thread.run(
                prompt, output_schema=FREE_QUERY_PRESENTATION_REVISION_SCHEMA, effort=self.reasoning_effort
            )
            raw = json.loads(result.final_response)
            presentation = RunPresentation.model_validate(raw["presentation"])
            return {
                "summary": {
                    "zh": str(raw["summary"]["zh"]),
                    "en": str(raw["summary"]["en"]),
                },
                "presentation": presentation.model_dump(mode="json"),
            }


    async def author_draft(
        self,
        *,
        thread_id: str,
        query: str,
        plan: dict[str, Any],
        evidence: list[dict[str, Any]],
        completeness: dict[str, Any],
        correction: str,
    ) -> dict[str, Any]:
        AsyncCodex = self._runtime_client

        prompt = f"""
Prepare bilingual Agent-detail content and deterministic-rule review notes from this completed
read-only SAP query. Do not generate executable code, commands, credentials, or new tools.

Question: {query}
Validated plan: {_safe_json(plan, limit=30_000)}
Evidence shape: {_safe_json(evidence, limit=50_000)}
Completeness: {_safe_json(completeness, limit=5_000)}
User correction: {correction or 'None'}

The Chinese and English content must explain purpose, inputs, fixed steps, evidence provenance,
read-only boundary, and completeness limitations. Rule notes must be auditable business-rule
requirements; never claim a rule has been implemented or a process completed.
""".strip()
        async with AsyncCodex() as codex:
            thread = await codex.thread_resume(
                thread_id,
                cwd=str(self.repository_root),
                sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all,
                model=self.model,
            )
            result = await thread.run(prompt, output_schema=AUTHOR_OUTPUT_SCHEMA, effort=self.reasoning_effort)
            raw = json.loads(result.final_response)
            return {
                "content_zh": str(raw["content_zh"]),
                "content_en": str(raw["content_en"]),
                "rule_notes": [str(item) for item in raw["rule_notes"]],
            }


    async def analyze_role_matching(
        self,
        *,
        documents: list[dict[str, Any]],
        agent_catalog: dict[str, Any],
        previous_result: dict[str, Any] | None,
        user_context: str,
        rematch_mode: str,
        locale: str,
        reuse_business_understanding: bool = False,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        AsyncCodex = self._runtime_client

        runtime_catalog = agent_catalog.get("runtime_catalog") or {}
        pages = runtime_catalog.get("pages") or []
        if not pages:
            raise ValueError("Role matching requires a complete paged Runtime catalog.")
        material_json = _safe_json(documents, limit=2_500_000)
        previous_context = None
        if previous_result and rematch_mode == "incremental":
            previous_context = {
                key: previous_result.get(key)
                for key in (
                    "roles", "processes", "operations", "document_issues",
                    "non_sap_operation_count",
                )
            }
        previous_json = _safe_json(previous_context, limit=80_000)
        understanding_prompt = f"""
Analyze the supplied user-selected business material. Do not match Agents yet. Source objects use
opaque IDs and contain no local paths. A user_description is user-provided evidence, not a verified
SAP fact or formal policy.

Locale: {locale}
Rematch mode: {rematch_mode}
User context (not document evidence): {user_context or 'None'}
Previous immutable result: {previous_json}
Material sources: {material_json}

Return analysis_json with roles, processes, operations and document_issues. Set agent_matches,
rejected_candidates, workflow_suggestions and agent_gaps to empty arrays. Every SAP operation must
contain operation_id, role, department, process, name, description, trigger, inputs, outputs,
sap_system_or_module, frequency, controls and evidence_refs. Unknown values remain empty. Evidence
refs must use supplied document_id/chunk_id/locator only. Analyze only SAP-related operations and
return non_sap_operation_count. Do not call tools, inspect files, execute SAP or edit files.
""".strip()
        async with AsyncCodex() as codex:
            # A full rematch must not inherit a previous turn whose catalog may have been
            # truncated or whose conclusions were based on an older catalog digest. Start a
            # clean read-only thread while keeping the same Runtime snapshot at the session
            # level. Incremental rematches may resume the existing conversation.
            with deadline_scope(time.monotonic() + ROLE_MATCHING_RUNTIME_TURN_SECONDS):
                if thread_id and rematch_mode != "full":
                    try:
                        thread = await _await_with_hard_timeout(
                            codex.thread_resume(
                                thread_id, cwd=str(self.repository_root), sandbox=Sandbox.read_only,
                                approval_mode=ApprovalMode.deny_all, model=self.model,
                            ),
                            timeout=ROLE_MATCHING_RUNTIME_TURN_SECONDS,
                        )
                    except Exception as exc:
                        if not isinstance(exc, TimeoutError) and not _role_matching_thread_can_restart(exc):
                            raise
                        thread = await _await_with_hard_timeout(
                            codex.thread_start(
                                cwd=str(self.repository_root), sandbox=Sandbox.read_only,
                                approval_mode=ApprovalMode.deny_all, model=self.model,
                                service_name="sap_business_agents_role_matching",
                                developer_instructions=(
                                    "Analyze only supplied document text and Agent catalog. Never call "
                                    "tools, read local paths, execute SAP, or modify files."
                                ),
                            ),
                            timeout=ROLE_MATCHING_RUNTIME_TURN_SECONDS,
                        )
                else:
                    thread = await _await_with_hard_timeout(
                        codex.thread_start(
                            cwd=str(self.repository_root), sandbox=Sandbox.read_only,
                            approval_mode=ApprovalMode.deny_all, model=self.model,
                            service_name="sap_business_agents_role_matching",
                            developer_instructions=(
                                "Analyze only supplied document text and Agent catalog. Never call tools, "
                                "read local paths, execute SAP, or modify files."
                            ),
                        ),
                        timeout=ROLE_MATCHING_RUNTIME_TURN_SECONDS,
                    )
                if reuse_business_understanding and previous_result:
                    canonical = {
                        key: copy.deepcopy(previous_result.get(key) or [])
                        for key in ("roles", "processes", "operations", "document_issues")
                    }
                    canonical["non_sap_operation_count"] = int(
                        previous_result.get("non_sap_operation_count") or 0
                    )
                    understanding_summary = copy.deepcopy(
                        previous_result.get("summary") or {"zh": "", "en": ""}
                    )
                else:
                    understanding = _decode_role_matching_output(
                        await _await_with_hard_timeout(
                            thread.run(understanding_prompt, output_schema=ROLE_MATCHING_OUTPUT_SCHEMA, effort=self.reasoning_effort),
                            timeout=ROLE_MATCHING_RUNTIME_TURN_SECONDS,
                        )
                    )
                    canonical = {
                        key: understanding.get(key) or []
                        for key in ("roles", "processes", "operations", "document_issues")
                    }
                    canonical["non_sap_operation_count"] = int(
                        understanding.get("non_sap_operation_count") or 0
                    )
                    understanding_summary = copy.deepcopy(
                        understanding.get("summary") or {"zh": "", "en": ""}
                    )
            canonical_json = _exact_json(canonical, limit=220_000, label="role understanding")
            candidate_matches: list[dict[str, Any]] = []
            rejected_candidates: list[dict[str, Any]] = []
            evaluated_agent_ids: list[str] = []
            evaluated_pair_count = 0
            failed_pages: list[int] = []
            for page in pages:
                page_index = int(page.get("page_index") or 0)
                page_json = _exact_json(page, limit=60_000, label="Agent catalog page")
                page_agent_ids = [
                    str(item.get("agent_id") or "") for item in page.get("items") or []
                ]
                expected_pair_count = len(canonical["operations"]) * len(page_agent_ids)
                page_prompt = f"""
Evaluate every Agent in this complete catalog page against every canonical SAP operation. The
canonical business understanding is immutable; do not rename, add or remove its operations.

Canonical understanding: {canonical_json}
Agent catalog page: {page_json}

Evaluate all {expected_pair_count} operation/Agent pairs, but return agent_matches only for full or
partial coverage. Put only semantically plausible candidates that were explicitly considered and
rejected into rejected_candidates with coverage=none; do not emit routine unrelated pairs. Each
returned candidate contains operation_id, agent_id, coverage, confidence, reason,
uncovered_capabilities and the operation's existing evidence_refs. Keep reason under 40 words and
each uncovered capability under 20 words. Set workflow_suggestions,
agent_gaps and other business arrays empty.

Prove the exhaustive page check with catalog_evaluation exactly as follows:
- catalog_digest: {runtime_catalog.get('digest')}
- total_agent_count: {runtime_catalog.get('total_agent_count')}
- evaluated_agent_count: {len(page_agent_ids)}
- evaluated_pair_count: {expected_pair_count}
- evaluated_agent_ids: {_exact_json(page_agent_ids, limit=20_000, label='page Agent IDs')}
- catalog_page_count: {runtime_catalog.get('page_count')}
- agent_catalog_complete: true
- matching_complete: true
- failed_pages: []

Judge declared capability and ports; do not invent Agents or ports. Do not call tools or inspect
files.
""".strip()
                expected_pairs = {
                    (str(operation.get("operation_id") or ""), str(agent.get("agent_id") or ""))
                    for operation in canonical["operations"]
                    for agent in page.get("items") or []
                }
                accepted_page_records: list[dict[str, Any]] | None = None
                with deadline_scope(time.monotonic() + ROLE_MATCHING_RUNTIME_TURN_SECONDS):
                    for _attempt in range(2):
                        try:
                            page_thread = await _await_with_hard_timeout(
                                codex.thread_start(
                                    cwd=str(self.repository_root), sandbox=Sandbox.read_only,
                                    approval_mode=ApprovalMode.deny_all, model=self.model,
                                    service_name="sap_business_agents_role_matching_catalog_page",
                                    developer_instructions=(
                                        "Evaluate only the supplied canonical operations and complete Agent "
                                        "catalog page. Never call tools, read files, execute SAP, or modify files."
                                    ),
                                ),
                                timeout=ROLE_MATCHING_RUNTIME_TURN_SECONDS,
                            )
                            page_analysis = _decode_role_matching_output(
                                await _await_with_hard_timeout(
                                    page_thread.run(
                                        page_prompt, output_schema=ROLE_MATCHING_OUTPUT_SCHEMA, effort=self.reasoning_effort
                                    ),
                                    timeout=ROLE_MATCHING_RUNTIME_TURN_SECONDS,
                                )
                            )
                        except Exception:
                            continue
                        page_records = [
                            item
                            for item in [
                                *(page_analysis.get("agent_matches") or []),
                                *(page_analysis.get("rejected_candidates") or []),
                            ]
                            if isinstance(item, dict)
                        ]
                        actual_pairs = [
                            (str(item.get("operation_id") or ""), str(item.get("agent_id") or ""))
                            for item in page_records
                        ]
                        page_evaluation = page_analysis.get("catalog_evaluation") or {}
                        evaluation_valid = (
                            str(page_evaluation.get("catalog_digest") or "")
                            == str(runtime_catalog.get("digest") or "")
                            and int(page_evaluation.get("evaluated_agent_count") or 0)
                            == len(page_agent_ids)
                            and int(page_evaluation.get("evaluated_pair_count") or 0)
                            == len(expected_pairs)
                            and set(str(item) for item in page_evaluation.get("evaluated_agent_ids") or [])
                            == set(page_agent_ids)
                            and int(page_evaluation.get("catalog_page_count") or 0)
                            == int(runtime_catalog.get("page_count") or 0)
                            and bool(page_evaluation.get("agent_catalog_complete"))
                            and bool(page_evaluation.get("matching_complete"))
                            and not (page_evaluation.get("failed_pages") or [])
                        )
                        if (
                            evaluation_valid
                            and len(actual_pairs) == len(set(actual_pairs))
                            and set(actual_pairs).issubset(expected_pairs)
                        ):
                            accepted_page_records = page_records
                            break
                if accepted_page_records is None:
                    failed_pages.append(page_index)
                    continue
                evaluated_agent_ids.extend(page_agent_ids)
                evaluated_pair_count += len(expected_pairs)
                for match in accepted_page_records:
                    if str(match.get("coverage") or "") == "none":
                        rejected_candidates.append(match)
                    else:
                        candidate_matches.append(match)

            catalog_complete = not failed_pages and len(set(evaluated_agent_ids)) == int(
                runtime_catalog.get("total_agent_count") or 0
            )
            matching_complete = catalog_complete
            consolidation_complete = False
            final_analysis: dict[str, Any] = {}
            consolidation_failure = None
            if catalog_complete:
                accepted_ids = {
                    str(item.get("agent_id") or "") for item in candidate_matches
                }
                candidate_contracts = [
                    item
                    for page in pages
                    for item in page.get("items") or []
                    if str(item.get("agent_id") or "") in accepted_ids
                ]
                evaluations = {
                    "accepted": _compact_role_match_records(candidate_matches),
                    "rejected": _compact_role_match_records(rejected_candidates),
                }
                candidate_support = candidate_support_projection(
                    canonical["operations"], candidate_matches, agent_catalog,
                )
                final_prompt = None
                consolidation_started = time.monotonic()
                with deadline_scope(time.monotonic() + ROLE_MATCHING_RUNTIME_TURN_SECONDS):
                    try:
                        evaluation_json = _exact_json(
                            evaluations, limit=220_000, label="Agent candidate evaluations"
                        )
                        contracts_json = _exact_json(
                            candidate_contracts, limit=120_000, label="candidate Agent contracts"
                        )
                        final_prompt = f"""
    Finalize role-to-Agent matching from a complete catalog evaluation. Preserve the canonical roles,
    processes, operations, document issues and evidence references exactly. Use accepted candidates as
    full or partial matches and rejected candidates only for audit.

    Canonical understanding: {canonical_json}
    Candidate evaluations: {evaluation_json}
    Detailed accepted Agent contracts: {contracts_json}
    Candidate support projection: {_exact_json(candidate_support, limit=120_000, label='candidate support projection')}

    {candidate_support_instructions()}

    Final-phase contract: {ROLE_CONSOLIDATION_VERSION}.
    Return exactly summary_zh, summary_en, workflow_suggestions and agent_gaps. Do not return
    analysis_json or reproduce canonical or matching records: the platform preserves them verbatim.
    Account for every operation_id with a full single-Agent match, a supported executable combination,
    or an explicit capability gap. Suggestions and gaps must declare the covered operation_ids and
    cite those operations' existing evidence_refs. Empty arrays are valid only when all operations
    already have full single-Agent coverage. Coverage is distinct from executable availability.
    Use only platform-eligible Agents in suggestions. Gaps belong only where neither one Agent nor
    a valid combination covers the operation. Explain partial coverage without hiding requirements.
    Do not describe FI clearing as independent bank settlement evidence.

    Every workflow suggestion must be a complete compiler proposal: bilingual title, description and
    intent; ordered stages; executable agent_id; confidence=high; bilingual reason; declared bindings;
    and requested_outputs using only supplied ports. Cross-stage ports must have compatible types.
    Every conclusion must reuse an existing operation evidence_ref. Do not call tools, inspect files,
    execute SAP, edit files or invent Agents.

    Applicable user feedback: {_exact_json(user_context, limit=30_000, label='role user feedback')}
    Output Schema: {_exact_json(ROLE_CONSOLIDATION_SCHEMA, limit=30_000, label='role consolidation Schema')}
    """.strip()
                        # A separate phase cannot inherit the previous phase's full-report
                        # output instruction. All required business context is supplied above.
                        final_thread = await _await_with_hard_timeout(
                            codex.thread_start(cwd=str(self.repository_root), sandbox=Sandbox.read_only,
                                approval_mode=ApprovalMode.deny_all, model=self.model,
                                service_name="sap_business_agents_role_consolidation",
                                developer_instructions="Summarize only supplied frozen role understanding and catalog matches. Never call tools, read files, execute SAP, or modify files."),
                            timeout=ROLE_MATCHING_RUNTIME_TURN_SECONDS)
                        final_analysis = decode_consolidation(
                            await _await_with_hard_timeout(
                                final_thread.run(final_prompt, output_schema=ROLE_CONSOLIDATION_SCHEMA, effort=self.reasoning_effort),
                                timeout=ROLE_MATCHING_RUNTIME_TURN_SECONDS,
                            ), operation=self.current_operation,
                        )
                        consolidation_complete = True
                    except Exception as exc:
                        from .runtime_diagnostics import stage_failure
                        consolidation_failure = stage_failure(exc,
                            schema=ROLE_CONSOLIDATION_SCHEMA, prompt=final_prompt,
                            budget_seconds=ROLE_MATCHING_RUNTIME_TURN_SECONDS,
                            elapsed_ms=int((time.monotonic() - consolidation_started) * 1000))
                        final_analysis = {}

            analysis = {
                **canonical,
                "consolidation_contract": ROLE_CONSOLIDATION_VERSION,
                # Page evaluation is the authoritative exhaustive match set. The final turn may
                # explain and compose it, but cannot silently drop a candidate from another page.
                "agent_matches": candidate_matches,
                "rejected_candidates": rejected_candidates,
                "workflow_suggestions": (
                    final_analysis.get("workflow_suggestions") if consolidation_complete else []
                ) or [],
                "agent_gaps": (
                    final_analysis.get("agent_gaps") if consolidation_complete else []
                ) or [],
                "catalog_evaluation": {
                    "catalog_digest": str(runtime_catalog.get("digest") or ""),
                    "total_agent_count": int(runtime_catalog.get("total_agent_count") or 0),
                    "evaluated_agent_count": len(set(evaluated_agent_ids)),
                    "evaluated_pair_count": evaluated_pair_count,
                    "evaluated_agent_ids": sorted(set(evaluated_agent_ids)),
                    "catalog_page_count": int(runtime_catalog.get("page_count") or 0),
                    "agent_catalog_complete": catalog_complete,
                    "matching_complete": matching_complete,
                    "consolidation_complete": consolidation_complete,
                    "failed_pages": failed_pages,
                },
                "summary": (
                    {"zh": final_analysis["summary_zh"], "en": final_analysis["summary_en"]}
                    if consolidation_complete else understanding_summary
                ) or {"zh": "", "en": ""},
            }
            if consolidation_failure:
                analysis["runtime_diagnostics"] = [consolidation_failure]
            return {"analysis": analysis, "thread_id": thread.id}


    async def review_role_matching_feedback(self, **kwargs: Any) -> dict[str, Any]:
        return await self.analyze_role_matching(**kwargs)


    async def review_workflow(
        self,
        *,
        workflow: dict[str, Any],
        agent_contracts: list[dict[str, Any]],
        validation_input: dict[str, Any],
        review_contract: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        AsyncCodex = self._runtime_client

        prompt = f"""
Review this strictly read-only deterministic SAPBusinessAgents workflow before live validation.
Check only graph intent, declared input/output ports, mapping clarity, and completeness propagation.
Do not call tools, execute SAP, edit files, invent fields, or propose write operations.

Workflow: {_safe_json(workflow, limit=40_000)}
Pinned Agent contracts: {_safe_json(agent_contracts, limit=30_000)}
Validation input shape: {_safe_json(validation_input, limit=5_000)}
Authoritative platform review contract: {_safe_json(review_contract, limit=15_000)}

Return the structured verdict, issues and bilingual summary. Use verdict=block for any ambiguous
oneOf branch, implicit mode selection, incompatible cardinality, missing required mapping, unsafe
operation, optional terminal output, missing conditional onSkip output, or incomplete completeness
propagation. Use issue codes workflow_terminal_output_optional,
workflow_conditional_skip_output_missing, and workflow_completeness_propagation_missing for those
three contract failures. Use verdict=pass only when no blocking issue remains. A bounded candidate
does not prove source completeness. The authoritative platform review contract defines the only
Agent output ports that a conditional skip path must synthesize. Do not require other Agent output
fields merely because they appear in the Agent output schema; unconsumed execution-context outputs
are not workflow terminal outputs.
""".strip()
        async with AsyncCodex() as codex:
            if thread_id:
                thread = await codex.thread_resume(
                    thread_id,
                    cwd=str(self.repository_root),
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                    model=self.model,
                )
            else:
                thread = await codex.thread_start(
                    cwd=str(self.repository_root),
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                    model=self.model,
                    service_name="sap_business_agents_workflow_authoring",
                    developer_instructions=(
                        "You review read-only deterministic workflow contracts. Never call tools, "
                        "edit files, execute SAP, or create write-capable steps."
                    ),
                )
            result = await thread.run(prompt, output_schema=WORKFLOW_REVIEW_OUTPUT_SCHEMA, effort=self.reasoning_effort)
            raw = json.loads(result.final_response)
            return {
                "verdict": str(raw["verdict"]),
                "issues": list(raw["issues"]),
                "summary": dict(raw["summary"]),
                "thread_id": thread.id,
            }


    async def repair_workflow(
        self,
        *,
        workflow: dict[str, Any],
        agent_contracts: list[dict[str, Any]],
        error: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        if not thread_id:
            raise ValueError("Workflow repair requires the existing reviewed Codex thread.")
        AsyncCodex = self._runtime_client

        prompt = f"""
Repair only the `connections` array of this read-only deterministic workflow after validation failed.

Workflow: {_safe_json(workflow, limit=40_000)}
Pinned Agent contracts: {_safe_json(agent_contracts, limit=30_000)}
Sanitized validation error: {_safe_json(error, limit=10_000)}

Rules:
1. Keep exactly the same nodes, Agent IDs, versions and digests.
2. Do not change public input/output schemas, tools, business rules, or SAP operations.
3. Use only declared ports and the transforms identity, to_string, to_integer, format_date, first, join.
4. Produce an acyclic graph and map every required Agent input exactly once.
5. Return `connections_json` as a JSON array string and a short reason. Do not call tools.
""".strip()
        async with AsyncCodex() as codex:
            thread = await codex.thread_resume(
                thread_id,
                cwd=str(self.repository_root),
                sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all,
                model=self.model,
            )
            result = await thread.run(prompt, output_schema=WORKFLOW_REPAIR_OUTPUT_SCHEMA, effort=self.reasoning_effort)
            raw = json.loads(result.final_response)
            connections = json.loads(str(raw["connections_json"]))
            if not isinstance(connections, list):
                raise ValueError("Codex workflow repair did not return a JSON array.")
            return {
                "reason": str(raw["reason"]),
                "connections": connections,
                "thread_id": thread.id,
            }


    async def compose_workflow(
        self,
        *,
        requirement: str,
        catalog: dict[str, Any],
        locale: str,
        thread_id: str | None = None,
        clarification_input: str | None = None,
        previous: dict[str, Any] | None = None,
        integration_catalog: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        AsyncCodex = self._runtime_client

        prompt = _workflow_composition_prompt(
            requirement=requirement,
            catalog=catalog,
            locale=locale,
            clarification_input=clarification_input,
            previous=previous or {},
            integration_catalog=integration_catalog or {"items": [], "bindings": []},
        )
        prompt += "\n\nPlatform tool discovery (not permission to execute): " + _workflow_assistant_tool_catalog(
            requirement, integration_catalog or {}
        )
        async with AsyncCodex() as codex:
            if thread_id:
                thread = await codex.thread_resume(
                    thread_id,
                    cwd=str(self.repository_root),
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                    model=self.model,
                )
            else:
                thread = await codex.thread_start(
                    cwd=str(self.repository_root),
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                    model=self.model,
                    service_name="sap_business_agents_workflow_composer",
                    developer_instructions=(
                        "You compose reusable deterministic read-only workflows only from the supplied "
                        "executable Agent and integration catalogs. Never call tools, inspect other files, "
                        "execute SAP, edit files, invent catalog IDs, or directly perform an external action."
                    ),
                )
            result = await thread.run(prompt, output_schema=WORKFLOW_COMPOSITION_OUTPUT_SCHEMA, effort=self.reasoning_effort)
            raw = json.loads(result.final_response)
            try:
                proposal = json.loads(str(raw.get("proposal_json") or "{}"))
            except json.JSONDecodeError as exc:
                repair = await thread.run(
                    (
                        "Your proposal_json was not valid JSON: "
                        f"{exc.msg} at character {exc.pos}. Re-emit the same workflow proposal and "
                        "change only the JSON syntax. Do not change Agent IDs, stages, bindings, "
                        "defaults, gaps, scope, or safety boundaries."
                    ),
                    output_schema=WORKFLOW_COMPOSITION_OUTPUT_SCHEMA,
                    effort=self.reasoning_effort,
                )
                raw = json.loads(repair.final_response)
                proposal = json.loads(str(raw.get("proposal_json") or "{}"))
            if not isinstance(proposal, dict):
                raise ValueError("Codex workflow composition did not return a JSON object.")
            if not raw.get("needs_clarification"):
                decode_workflow(json.dumps(proposal), PROPOSAL_SCHEMA,
                                operation="compose_workflow", path="/proposal_json")
            return {
                "needs_clarification": bool(raw.get("needs_clarification")),
                "clarification_question": str(raw.get("clarification_question") or ""),
                "proposal": proposal,
                "thread_id": thread.id,
            }


    async def review_workflow_feedback(
        self,
        *,
        requirement: str,
        feedback: str,
        feedback_type_hint: str | None,
        locale: str,
        workflow: dict[str, Any],
        previous_proposal: dict[str, Any],
        catalog: dict[str, Any],
        validation_report: dict[str, Any] | None,
        thread_id: str | None,
        clarification_input: str | None = None,
        integration_catalog: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        AsyncCodex = self._runtime_client

        prompt = _workflow_feedback_prompt(
            requirement=requirement,
            feedback=feedback,
            feedback_type_hint=feedback_type_hint,
            locale=locale,
            workflow=workflow,
            previous_proposal=previous_proposal,
            catalog=catalog,
            validation_report=validation_report,
            clarification_input=clarification_input,
        )
        prompt += "\n\nPlatform tool discovery (not permission to execute): " + _workflow_assistant_tool_catalog(
            requirement + " " + feedback, integration_catalog or {}
        )
        async with AsyncCodex() as codex:
            if thread_id:
                thread = await codex.thread_resume(
                    thread_id,
                    cwd=str(self.repository_root),
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                    model=self.model,
                )
            else:
                thread = await codex.thread_start(
                    cwd=str(self.repository_root),
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                    model=self.model,
                    service_name="sap_business_agents_workflow_feedback",
                    developer_instructions=(
                        "Review workflow feedback only from the supplied workflow and executable Agent "
                        "catalog. Never call tools, inspect files, execute SAP, edit files, invent Agents, "
                        "or weaken deterministic safety and completeness contracts."
                    ),
                )
            result = await thread.run(prompt, output_schema=WORKFLOW_FEEDBACK_OUTPUT_SCHEMA, effort=self.reasoning_effort)
            raw = json.loads(result.final_response)
            schema = PROPOSAL_SCHEMA if raw.get("action") == "revise_workflow" else {"type": "null"}
            decode_workflow(str(raw.get("proposal_json") or "null"), schema,
                            operation="review_workflow_feedback", path="/proposal_json")
            decode_workflow(str(raw.get("validation_input_patch_json") or "{}"), input_patch_schema(workflow),
                            operation="review_workflow_feedback", path="/validation_input_patch_json")
            decode_workflow(str(raw.get("candidate_expectations_json") or "[]"), EXPECTATIONS_SCHEMA,
                            operation="review_workflow_feedback", path="/candidate_expectations_json")
            try:
                proposal = json.loads(str(raw.get("proposal_json") or "null"))
                validation_input_patch = json.loads(
                    str(raw.get("validation_input_patch_json") or "{}")
                )
                candidate_expectations = json.loads(
                    str(raw.get("candidate_expectations_json") or "[]")
                )
            except json.JSONDecodeError as exc:
                raise ValueError(f"Workflow feedback returned invalid embedded JSON: {exc}") from exc
            if proposal is not None and not isinstance(proposal, dict):
                raise ValueError("Workflow feedback proposal_json must be an object or null.")
            if not isinstance(validation_input_patch, dict) or not isinstance(
                candidate_expectations, list
            ):
                raise ValueError("Workflow feedback validation patches have an invalid shape.")
            return {
                "feedback_type": str(raw.get("feedback_type") or ""),
                "action": str(raw.get("action") or ""),
                "revised_requirement": str(raw.get("revised_requirement") or ""),
                "required_changes": list(raw.get("required_changes") or []),
                "preserved_behavior": list(raw.get("preserved_behavior") or []),
                "validation_input_patch": validation_input_patch,
                "candidate_expectations": candidate_expectations,
                "clarification_question": str(raw.get("clarification_question") or ""),
                "reason": str(raw.get("reason") or ""),
                "proposal": proposal,
                "thread_id": thread.id,
            }


_operation_context = ContextVar("runtime_business_operation", default=None)


def _business_operation(name, method):
    @wraps(method)
    async def call(self, *args, **kwargs):
        # Nested phases stay bound to their original operation, not a new task.
        token = _operation_context.set(_operation_context.get() or name)
        try:
            from .runtime_contract import business_scope, await_business
            async with business_scope(self, _operation_context.get()):
                if name in {"analyze_role_matching", "review_role_matching_feedback"}:
                    return await method(self, *args, **kwargs)
                return await await_business(method(self, *args, **kwargs))
        finally:
            _operation_context.reset(token)
    return call


for _name in ("author_draft", "review_agent_feedback", "review_free_query_feedback",
              "revise_free_query_presentation", "analyze_role_matching", "review_role_matching_feedback",
              "compose_workflow", "review_workflow", "repair_workflow", "review_workflow_feedback"):
    setattr(SharedPlanner, _name, _business_operation(_name, getattr(SharedPlanner, _name)))
