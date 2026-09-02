from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any, Protocol

from .models import PlannerDecision, RunPresentation


PLANNER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "needs_clarification": {"type": "boolean"},
        "clarification_question": {"type": "string"},
        "plan_json": {"type": "string"},
    },
    "required": ["intent", "needs_clarification", "clarification_question", "plan_json"],
    "additionalProperties": False,
}

SUMMARY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "zh": {"type": "string"},
        "en": {"type": "string"},
    },
    "required": ["zh", "en"],
    "additionalProperties": False,
}

AUTHOR_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_zh": {"type": "string"},
        "content_en": {"type": "string"},
        "rule_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["content_zh", "content_en", "rule_notes"],
    "additionalProperties": False,
}

WORKFLOW_REVIEW_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "block"]},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "minLength": 1},
                    "severity": {
                        "type": "string",
                        "enum": ["error", "warning", "info"],
                    },
                    "node_id": {"type": ["string", "null"]},
                    "port": {"type": ["string", "null"]},
                    "message": {
                        "type": "object",
                        "properties": {
                            "zh": {"type": "string", "minLength": 1},
                            "en": {"type": "string", "minLength": 1},
                        },
                        "required": ["zh", "en"],
                        "additionalProperties": False,
                    },
                },
                "required": ["code", "severity", "node_id", "port", "message"],
                "additionalProperties": False,
            },
        },
        "summary": {
            "type": "object",
            "properties": {"zh": {"type": "string"}, "en": {"type": "string"}},
            "required": ["zh", "en"],
            "additionalProperties": False,
        },
    },
    "required": ["verdict", "issues", "summary"],
    "additionalProperties": False,
}

AGENT_FEEDBACK_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "object",
            "properties": {"zh": {"type": "string"}, "en": {"type": "string"}},
            "required": ["zh", "en"],
            "additionalProperties": False,
        },
        "required_changes": {"type": "array", "items": {"type": "string"}},
        "manifest_json": {"type": "string"},
        "readme": {"type": "string"},
        "rules_source": {"type": "string"},
    },
    "required": ["summary", "required_changes", "manifest_json", "readme", "rules_source"],
    "additionalProperties": False,
}

FREE_QUERY_FEEDBACK_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "feedback_type", "action", "revised_intent", "revised_query",
        "required_changes", "preserved_scope", "candidate_expectations",
        "clarification_question", "reason",
    ],
    "properties": {
        "feedback_type": {
            "type": "string",
            "enum": [
                "scope_or_filter", "relationship", "missing_evidence",
                "business_rule", "presentation", "new_intent", "unclear",
            ],
        },
        "action": {
            "type": "string",
            "enum": ["requery", "reinterpret", "clarify", "start_new_session"],
        },
        "revised_intent": {"type": "string"},
        "revised_query": {"type": "string"},
        "required_changes": {"type": "array", "items": {"type": "string"}},
        "preserved_scope": {"type": "array", "items": {"type": "string"}},
        "candidate_expectations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement", "status", "evidence_refs"],
                "properties": {
                    "statement": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["confirmed", "mismatch", "not_verifiable"],
                    },
                    "evidence_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
        "clarification_question": {"type": "string"},
        "reason": {"type": "string"},
    },
}

def _strict_response_schema(value: Any) -> Any:
    """Make a Pydantic schema acceptable to strict Runtime structured output."""
    if isinstance(value, dict):
        normalized = {key: _strict_response_schema(item) for key, item in value.items()}
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            normalized["required"] = list(properties)
            normalized.setdefault("additionalProperties", False)
        return normalized
    if isinstance(value, list):
        return [_strict_response_schema(item) for item in value]
    return value


_RUN_PRESENTATION_SCHEMA = _strict_response_schema(
    RunPresentation.model_json_schema()
)

FREE_QUERY_PRESENTATION_REVISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "presentation"],
    # Pydantic emits local references such as ``#/$defs/LocalizedText``.  The
    # presentation schema is embedded below another object, so its definitions
    # must live at the response schema root or the Runtime rejects the response
    # format before a turn starts.
    "$defs": _RUN_PRESENTATION_SCHEMA.get("$defs", {}),
    "properties": {
        "summary": {
            "type": "object",
            "additionalProperties": False,
            "required": ["zh", "en"],
            "properties": {"zh": {"type": "string"}, "en": {"type": "string"}},
        },
        "presentation": {
            key: value
            for key, value in _RUN_PRESENTATION_SCHEMA.items()
            if key != "$defs"
        },
    },
}

WORKFLOW_REPAIR_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "connections_json": {"type": "string"},
    },
    "required": ["reason", "connections_json"],
    "additionalProperties": False,
}

WORKFLOW_COMPOSITION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "needs_clarification": {"type": "boolean"},
        "clarification_question": {"type": "string"},
        "proposal_json": {"type": "string"},
    },
    "required": ["needs_clarification", "clarification_question", "proposal_json"],
    "additionalProperties": False,
}

WORKFLOW_FEEDBACK_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "feedback_type": {
            "type": "string",
            "enum": [
                "goal_scope", "stage_or_agent", "mapping", "condition",
                "output_or_completeness", "validation_input", "validation_expectation",
                "agent_capability", "presentation", "new_intent", "unclear",
            ],
        },
        "action": {
            "type": "string",
            "enum": ["revise_workflow", "rerun_validation", "clarify", "start_new_workflow"],
        },
        "revised_requirement": {"type": "string"},
        "required_changes": {"type": "array", "items": {"type": "string"}},
        "preserved_behavior": {"type": "array", "items": {"type": "string"}},
        "validation_input_patch_json": {"type": "string"},
        "candidate_expectations_json": {"type": "string"},
        "clarification_question": {"type": "string"},
        "reason": {"type": "string"},
        "proposal_json": {"type": "string"},
    },
    "required": [
        "feedback_type", "action", "revised_requirement", "required_changes",
        "preserved_behavior", "validation_input_patch_json",
        "candidate_expectations_json", "clarification_question", "reason", "proposal_json",
    ],
    "additionalProperties": False,
}

ROLE_MATCHING_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "analysis_json": {"type": "string"},
        "summary_zh": {"type": "string"},
        "summary_en": {"type": "string"},
    },
    "required": ["analysis_json", "summary_zh", "summary_en"],
    "additionalProperties": False,
}

ROLE_MATCHING_RUNTIME_TURN_SECONDS = 300


def _consume_background_task(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except BaseException:
        pass


async def _await_with_hard_timeout(awaitable: Any, *, timeout: float) -> Any:
    """Stop waiting at the deadline even when an SDK coroutine ignores cancellation."""
    task = asyncio.ensure_future(awaitable)
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if task not in done:
        task.cancel()
        task.add_done_callback(_consume_background_task)
        raise TimeoutError(f"Runtime operation exceeded {timeout:g} seconds.")
    return task.result()


class Planner(Protocol):
    async def plan(
        self,
        query: str,
        catalog: dict[str, Any],
        guidance: dict[str, Any],
        skills: list[dict[str, Any]],
        thread_id: str | None = None,
    ) -> PlannerDecision: ...

    async def ground_plan(
        self,
        *,
        query: str,
        decision: PlannerDecision,
        schemas: list[dict[str, Any]],
        relationships: dict[str, Any] | None = None,
        validation_failures: list[dict[str, Any]] | None = None,
        repair_attempt: int = 0,
    ) -> PlannerDecision: ...

    async def compose_workflow(
        self,
        *,
        requirement: str,
        catalog: dict[str, Any],
        locale: str,
        thread_id: str | None = None,
        clarification_input: str | None = None,
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def review_workflow_feedback(self, **kwargs: Any) -> dict[str, Any]: ...

    async def review_agent_feedback(self, **kwargs: Any) -> dict[str, Any]: ...

    async def analyze_role_matching(self, **kwargs: Any) -> dict[str, Any]: ...


class CodexPlanner:
    def __init__(self, repository_root: Path, model: str | None = None) -> None:
        self.repository_root = repository_root
        self.model = model

    async def plan(
        self,
        query: str,
        catalog: dict[str, Any],
        guidance: dict[str, Any],
        skills: list[dict[str, Any]],
        thread_id: str | None = None,
    ) -> PlannerDecision:
        try:
            from openai_codex import ApprovalMode, AsyncCodex, Sandbox
        except ImportError as exc:  # pragma: no cover - exercised in installations without the optional runtime
            raise RuntimeError(
                "Codex Python SDK is unavailable. Install the project dependencies with pip install -e ."
            ) from exc

        prompt = _planner_prompt(query, catalog, guidance, skills, continuing=bool(thread_id))
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
                    service_name="sap_business_agents_local",
                    developer_instructions=(
                        "You are a read-only SAP query planner. Never execute shell commands, edit files, "
                        "or invent SAP services. Return only the requested structured output."
                    ),
                )
            raw, plan = await _run_plan_turn(thread, prompt, phase="initial planning")
            return PlannerDecision(
                intent=str(raw.get("intent") or query),
                needs_clarification=bool(raw.get("needs_clarification")),
                clarification_question=str(raw.get("clarification_question") or ""),
                plan=plan,
                thread_id=thread.id,
            )

    async def ground_plan(
        self,
        *,
        query: str,
        decision: PlannerDecision,
        schemas: list[dict[str, Any]],
        relationships: dict[str, Any] | None = None,
        validation_failures: list[dict[str, Any]] | None = None,
        repair_attempt: int = 0,
    ) -> PlannerDecision:
        if not decision.thread_id or not decision.plan:
            raise ValueError("A resumable Codex thread and candidate plan are required for grounding.")
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox

        prompt = _grounding_prompt(
            query,
            decision.plan,
            schemas,
            relationships or {},
            validation_failures or [],
            repair_attempt=repair_attempt,
        )
        async with AsyncCodex() as codex:
            thread = await codex.thread_resume(
                decision.thread_id,
                cwd=str(self.repository_root),
                sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all,
                model=self.model,
            )
            raw, plan = await _run_plan_turn(thread, prompt, phase="schema grounding")
            return PlannerDecision(
                intent=str(raw.get("intent") or decision.intent or query),
                needs_clarification=bool(raw.get("needs_clarification")),
                clarification_question=str(raw.get("clarification_question") or ""),
                plan=plan,
                thread_id=thread.id,
            )

    async def summarize(
        self,
        *,
        thread_id: str,
        query: str,
        plan: dict[str, Any],
        evidence: list[dict[str, Any]],
        rule_results: list[dict[str, Any]],
    ) -> dict[str, str]:
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox

        prompt = f"""
Explain the validated read-only SAP evidence for the user's question in concise Chinese and English.

Question: {query}
Executed plan: {_safe_json(plan, limit=20_000)}
Evidence: {_safe_json(evidence, limit=80_000)}
Deterministic rule results: {_safe_json(rule_results, limit=20_000)}

Rules:
1. Never alter, override, or contradict a deterministic rule result.
2. Clearly say when evidence is bounded, incomplete, or insufficient.
3. Do not infer that a business process is complete unless a deterministic rule explicitly supports it.
4. Do not call tools. Return only the requested bilingual structured output.
""".strip()
        async with AsyncCodex() as codex:
            thread = await codex.thread_resume(
                thread_id,
                cwd=str(self.repository_root),
                sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all,
                model=self.model,
            )
            result = await thread.run(prompt, output_schema=SUMMARY_OUTPUT_SCHEMA)
            raw = json.loads(result.final_response)
            return {"zh": str(raw["zh"]), "en": str(raw["en"])}

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
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox

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
            result = await thread.run(prompt, output_schema=FREE_QUERY_FEEDBACK_REVIEW_SCHEMA)
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
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox

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
                prompt, output_schema=FREE_QUERY_PRESENTATION_REVISION_SCHEMA
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
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox

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
            result = await thread.run(prompt, output_schema=AUTHOR_OUTPUT_SCHEMA)
            raw = json.loads(result.final_response)
            return {
                "content_zh": str(raw["content_zh"]),
                "content_en": str(raw["content_en"]),
                "rule_notes": [str(item) for item in raw["rule_notes"]],
            }

    async def review_agent_feedback(
        self,
        *,
        feedback: str,
        locale: str,
        package: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox

        prompt = f"""
Revise one isolated SAPBusinessAgents deterministic fixed-Agent draft from user feedback.

Preferred language: {locale}
User feedback: {feedback}
Current immutable draft package:
{_safe_json(package, limit=300_000)}

Return the complete revised agent manifest as manifest_json, complete bilingual README, and the
complete managed rules.py source (empty string if no managed rule is needed). Preserve the Agent
ID. SAP access must remain GET-only; Skills must remain registered, read_only and validated. Do
not modify platform code or other Agents. A managed rule must expose evaluate(inputs), operate only
on supplied structured evidence, and must not access files, network, processes, environment, eval,
exec, dynamic imports or reflection. Do not claim validation has passed: set changed behavior to
NOT_TESTED/executable=false. Return JSON only.
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
                    service_name="sap_business_agents_agent_authoring",
                    developer_instructions=(
                        "Revise only the supplied isolated Agent package. Never call tools, inspect "
                        "files, run commands, contact SAP, or edit the repository."
                    ),
                )
            result = await thread.run(prompt, output_schema=AGENT_FEEDBACK_OUTPUT_SCHEMA)
            raw = json.loads(result.final_response)
            try:
                manifest = json.loads(str(raw.get("manifest_json") or "{}"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Agent feedback returned invalid manifest JSON: {exc}") from exc
            if not isinstance(manifest, dict):
                raise ValueError("Agent feedback manifest must be an object.")
            rules_source = str(raw.get("rules_source") or "")
            if rules_source:
                managed = manifest.setdefault("managedRule", {})
                managed["entrypoint"] = "evaluate"
                from .managed_rules import source_digest

                managed["sha256"] = source_digest(rules_source)
            return {
                "summary": raw["summary"],
                "required_changes": [str(item) for item in raw.get("required_changes") or []],
                "package": {
                    "manifest": manifest,
                    "readme": str(raw.get("readme") or ""),
                    "rules": rules_source or None,
                    "files": copy.deepcopy(package.get("files") or {}),
                },
                "thread_id": thread.id,
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
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox

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
                        thread.run(understanding_prompt, output_schema=ROLE_MATCHING_OUTPUT_SCHEMA),
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
                                    page_prompt, output_schema=ROLE_MATCHING_OUTPUT_SCHEMA
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

Return analysis_json with agent_matches containing only full/partial candidates,
rejected_candidates containing none candidates, workflow_suggestions using only executable PASS
Agents, and agent_gaps only where neither one Agent nor a valid combination covers the operation.
Do not describe FI clearing as independent bank settlement evidence.

Every workflow suggestion must be a complete compiler proposal: bilingual title, description and
intent; ordered stages; executable agent_id; confidence=high; bilingual reason; declared bindings;
and requested_outputs using only supplied ports. Cross-stage ports must have compatible types.
Every conclusion must reuse an existing operation evidence_ref. Do not call tools, inspect files,
execute SAP, edit files or invent Agents.
""".strip()
                    final_analysis = _decode_role_matching_output(
                        await _await_with_hard_timeout(
                            thread.run(final_prompt, output_schema=ROLE_MATCHING_OUTPUT_SCHEMA),
                            timeout=ROLE_MATCHING_RUNTIME_TURN_SECONDS,
                        )
                    )
                    consolidation_complete = True
                except Exception:
                    final_analysis = {}

            analysis = {
                **canonical,
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
                    final_analysis.get("summary")
                    if consolidation_complete else understanding_summary
                ) or {"zh": "", "en": ""},
            }
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
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox

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
            result = await thread.run(prompt, output_schema=WORKFLOW_REVIEW_OUTPUT_SCHEMA)
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
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox

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
            result = await thread.run(prompt, output_schema=WORKFLOW_REPAIR_OUTPUT_SCHEMA)
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
    ) -> dict[str, Any]:
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox

        prompt = _workflow_composition_prompt(
            requirement=requirement,
            catalog=catalog,
            locale=locale,
            clarification_input=clarification_input,
            previous=previous or {},
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
                        "executable Agent catalog. Never call tools, inspect other files, execute SAP, edit "
                        "files, invent Agent IDs, or propose write operations."
                    ),
                )
            result = await thread.run(prompt, output_schema=WORKFLOW_COMPOSITION_OUTPUT_SCHEMA)
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
                )
                raw = json.loads(repair.final_response)
                proposal = json.loads(str(raw.get("proposal_json") or "{}"))
            if not isinstance(proposal, dict):
                raise ValueError("Codex workflow composition did not return a JSON object.")
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
    ) -> dict[str, Any]:
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox

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
            result = await thread.run(prompt, output_schema=WORKFLOW_FEEDBACK_OUTPUT_SCHEMA)
            raw = json.loads(result.final_response)
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


def _workflow_feedback_prompt(
    *,
    requirement: str,
    feedback: str,
    feedback_type_hint: str | None,
    locale: str,
    workflow: dict[str, Any],
    previous_proposal: dict[str, Any],
    catalog: dict[str, Any],
    validation_report: dict[str, Any] | None,
    clarification_input: str | None,
) -> str:
    return f"""
Review one user's feedback about an SAPBusinessAgents workflow conversation.

Original or latest requirement: {requirement}
User feedback: {feedback}
Feedback category hint: {feedback_type_hint or 'none'}
Clarification answer: {clarification_input or 'none'}
Preferred UI language: {locale}

Current deterministic workflow:
{_safe_json(workflow, limit=80_000)}

Previous full stage proposal:
{_safe_json(previous_proposal, limit=60_000)}

Latest validation summary, when feedback follows validation:
{_safe_json(validation_report or {}, limit=50_000)}

Executable Agent catalog, which is the only allowed Agent source:
{_safe_json(catalog, limit=140_000)}

Decide exactly one action:
- revise_workflow: the goal, stages, Agent selection, bindings, conditions, terminal outputs,
  completeness propagation, or published presentation contract must change. Return a complete
  proposal_json using the same conceptual shape as the previous proposal, not a patch.
- rerun_validation: only validation input or a terminal-output expectation changes. Do not change
  the workflow. Return validation_input_patch_json and candidate_expectations_json.
- clarify: the feedback is materially ambiguous. Ask exactly one concrete question and do no work.
- start_new_workflow: the user is asking for a different business intent.

Safety rules:
1. Never treat the user's expected business value as SAP evidence or modify a fixed Agent rule.
2. Never invent an Agent, port, tool, SAP field, or write operation.
3. Preserve unrelated verified behavior and list it in preserved_behavior.
4. A revise_workflow proposal must be complete and use exact catalog Agent IDs and exact declared ports.
5. Keep source completeness, evidence completeness, and business completion independent.
6. Keep oneOf selection explicit, array cardinalities compatible, and conditional skip outputs honest.
7. If a required capability is absent, classify agent_capability and return a complete proposal with
   an uncovered stage; do not substitute a semantically different Agent.
8. proposal_json must be JSON `null` for every action except revise_workflow.
9. validation_input_patch_json must be `{{}}` unless rerun_validation.
10. candidate_expectations_json must be `[]` unless rerun_validation. Each expectation must follow
    the platform contract: output, operator, and optional expected/tolerance.
""".strip()


def _workflow_composition_prompt(
    *,
    requirement: str,
    catalog: dict[str, Any],
    locale: str,
    clarification_input: str | None,
    previous: dict[str, Any],
) -> str:
    if clarification_input:
        follow_up = (
            f"User clarification: {clarification_input}\n"
            f"Previous composition: {_safe_json(previous, limit=30_000)}"
        )
    elif previous.get("stages") or previous.get("gaps"):
        follow_up = (
            "Reconcile the existing proposal against the new executable catalog snapshot. "
            "Replace a gap only when a new catalog Agent is a high-confidence contract match.\n"
            f"Previous composition: {_safe_json(previous, limit=30_000)}"
        )
    else:
        follow_up = "This is the initial composition turn."
    return f"""
Turn the business user's requirement into a reusable SAPBusinessAgents workflow proposal.

Requirement: {requirement}
Preferred UI language: {locale}
{follow_up}

Executable Agent catalog snapshot (the only Agents you may select):
{_safe_json(catalog, limit=140_000)}

Rules:
1. Decompose the complete requested outcome into ordered business capability stages.
2. Select an Agent only when one catalog entry is a high-confidence semantic match. Use its exact agent_id.
3. If multiple Agents would materially change the business meaning, set needs_clarification=true and ask exactly one concise question. Do not ask for identifiers that can remain reusable workflow inputs.
4. If no Agent covers a stage, keep agent_id empty and describe one missing Agent contract. Never hide or merge away an uncovered stage.
5. A binding may connect only an earlier selected stage output to a later input with the exact same port name. Otherwise omit the binding; the server will expose a workflow input.
6. Concrete identifiers and dates in the requirement belong only in validation_defaults. Never make them workflow constants.
7. Select no tools and describe no SAP write operation. Source completeness and business completion remain separate concepts.
8. requested_outputs must contain business results and completeness results only. Do not request input-context echoes such as query_mode, dates, company codes, or identifiers unless a later stage actually consumes that exact output port.

Return proposal_json as a JSON object with exactly this conceptual shape:
{{
  "intent": {{"zh":"...","en":"..."}},
  "title": {{"zh":"...","en":"..."}},
  "description": {{"zh":"...","en":"..."}},
  "validation_defaults": {{"declared_workflow_input_name":"value"}},
  "stages": [{{
    "id":"lower_snake_case",
    "capability":{{"zh":"...","en":"..."}},
    "agent_id":"exact catalog id or empty",
    "confidence":"high|medium|low",
    "reason":{{"zh":"...","en":"..."}},
    "bindings":[{{"input_port":"company_code","source_stage_id":"earlier_stage","source_output_port":"company_code"}}],
    "requested_outputs":["declared_output_port"],
    "gap_title":{{"zh":"...","en":"..."}},
    "gap_description":{{"zh":"...","en":"..."}},
    "required_inputs":[{{"name":"snake_case","type":"string|integer|number|boolean|object|array","required":true,"description":{{"zh":"...","en":"..."}}}}],
    "required_outputs":[{{"name":"snake_case","type":"string|integer|number|boolean|object|array","required":true,"description":{{"zh":"...","en":"..."}}}}],
    "guardrails":{{"zh":["..."],"en":["..."]}},
    "acceptance":{{"zh":"...","en":"..."}}
  }}]
}}

For a selected Agent, gap fields may be empty. For an uncovered stage, required_inputs, required_outputs, guardrails, and acceptance must be specific enough for an Agent author to implement and test it.
""".strip()


def _planner_prompt(
    query: str,
    catalog: dict[str, Any],
    guidance: dict[str, Any],
    skills: list[dict[str, Any]],
    *,
    continuing: bool,
) -> str:
    items = ((catalog.get("data") or {}).get("items") or [])[:40]
    safe_skills = [
        {
            "skill_id": item.get("skill_id"),
            "description": item.get("description"),
            "input_schema": item.get("input_schema"),
        }
        for item in skills
        if item.get("read_only") is True and item.get("validated") is True and item.get("available") is True
    ]
    return f"""
Create a strict SAPBusinessAgents harness plan for the user's read-only SAP question.

User question:
{query}

This is {'a continuation after clarification' if continuing else 'a new query'}.

Available SAP read catalog evidence (advisory only; the selected Provider will validate the plan):
{_safe_json(items)}

SAP read guidance evidence:
{_safe_json(guidance.get('data') or {})}

Approved cross-entity business relationship contract:
{_safe_json((guidance.get('data') or {}).get('business_relationship_contract') or {}, limit=60_000)}

Approved machine-callable read-only skills:
{_safe_json(safe_skills)}

Exact SAP read query-plan contract (extra fields are rejected):
{_RUNTIME_PLAN_CONTRACT}

Rules:
1. Use only service_name/odata_version/entity_set/fields evidenced above. Preserve the
   catalog-declared OData protocol version exactly; never infer it from the service name.
2. Build cross-entity filters and bindings only from the approved relationship contract. Field
   existence alone is not semantic compatibility. Prefer the listed delivery-to-billing and
   billing-to-FI chain when the question asks for O2C billing or receivables evidence.
3. Every SAP HTTP method must be GET.
4. Prefer server-side filters and bounded output. Do not claim completeness for a bounded top query.
5. If a business identifier, date range, company code, or other essential filter is missing, set
   needs_clarification=true, ask exactly one concise question, and return an empty plan_json.
6. Otherwise set needs_clarification=false and put this object into plan_json:
   {{"kind":"sap_business_agents_harness","steps":[...]}}.
7. A SAP step is {{"id":"step_1","tool":"sap_read","reason":"...","plan":<one complete
   SAP read plan matching the exact contract above>}}. Use plan_kind=multi_step inside
   that plan for cross-API chains. Never use plan_kind=single_step. Include an output_contract.
8. A Skill step is {{"id":"step_2","tool":"skill","skill_id":"an approved id","reason":"...",
   "input":{{...}}}}. Use a Skill only when it is listed above and its complete required input is known.
   A later Skill input may reference an earlier whole output as {{{{steps.step_1.output}}}}.
9. Do not select shell, Python, filesystem, network, or any unregistered tool. Do not call tools or
   execute the plan yourself. Do not output credentials or secrets.
10. The total number of nested SAP query steps plus Skill steps must not exceed
    max_tool_calls from the SAP read guidance evidence. Combine compatible fields in one entity
    query instead of creating duplicate calls.
""".strip()


async def _run_plan_turn(
    thread: Any,
    prompt: str,
    *,
    phase: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    result = await thread.run(prompt, output_schema=PLANNER_OUTPUT_SCHEMA)
    raw = json.loads(result.final_response)
    try:
        plan = _decode_plan_json(raw)
    except json.JSONDecodeError as exc:
        repair_prompt = f"""
Your previous structured response was accepted, but its plan_json string was not valid JSON during
{phase}: {exc.msg} at character {exc.pos}.

Re-emit the same intent, scope, GET-only methods, filters, bounds, services, entities and fields.
Change only the JSON syntax needed for plan_json to decode as one object. Do not add tools, calls,
fields, assumptions, or broader filters. Return only the requested structured output.
""".strip()
        result = await thread.run(repair_prompt, output_schema=PLANNER_OUTPUT_SCHEMA)
        raw = json.loads(result.final_response)
        plan = _decode_plan_json(raw)
    return raw, plan


def _decode_plan_json(raw: dict[str, Any]) -> dict[str, Any] | None:
    plan_json = str(raw.get("plan_json") or "").strip()
    plan = json.loads(plan_json) if plan_json else None
    if plan is not None and not isinstance(plan, dict):
        raise ValueError("Codex plan_json must decode to an object.")
    return plan


def _grounding_prompt(
    query: str,
    candidate_plan: dict[str, Any],
    schemas: list[dict[str, Any]],
    relationships: dict[str, Any],
    validation_failures: list[dict[str, Any]],
    *,
    repair_attempt: int,
) -> str:
    phase = "one bounded validation repair" if repair_attempt else "live-schema grounding"
    return f"""
Revise the candidate SAPBusinessAgents read-only harness plan using authoritative live SAP Provider
entity schemas. This turn is {phase}; do not execute tools.

User question:
{query}

Candidate plan:
{_safe_json(candidate_plan, limit=50_000)}

Authoritative live schemas, reduced to executable entity and field facts:
{_safe_json(_schema_snapshot(schemas), limit=100_000)}

Approved cross-entity business relationship contract:
{_safe_json(relationships, limit=60_000)}

SAP Provider validation failures from the grounded candidate, if any:
{_safe_json(validation_failures, limit=30_000)}

Exact SAP read query-plan contract (extra fields are rejected):
{_RUNTIME_PLAN_CONTRACT}

Rules:
1. Return the same harness shape and preserve the candidate service_name/odata_version/entity_set
   triples. Never add a service, protocol version, or entity absent from the authoritative schemas.
2. Every selected, summarized, filtered, ordered, output-contract, binding-source, and
   binding-target field must exist on its own entity in the authoritative schemas and support the
   requested use where that capability is supplied.
3. Cross-entity literal reuse and filter_from_previous bindings must follow the approved
   relationship contract. Matching field existence is not enough: field semantic types must match
   or an exact source-to-target relationship with the requested mode must be listed. In particular,
   an internal_order_id field is never a substitute for a sales_order_id field.
4. Remove unavailable optional fields. Replace a required business field only when an equivalent
   authoritative field is clearly evidenced. Never guess a field name.
5. Preserve GET-only methods, business identifiers, bounded limits, step dependencies and user
   intent. Do not broaden filters or result limits.
6. Set needs_clarification=false and return the full revised object as plan_json. If the available
   schemas cannot support the question, return an empty plan_json; do not invent another tool.
7. Do not call tools, shell, Python, filesystem, network, or SAP. Return only the requested
   structured output.
""".strip()


def _schema_snapshot(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    for response in schemas:
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, dict):
            snapshot.append(
                {
                    "ok": False,
                    "validation_issues": response.get("validation_issues", [])
                    if isinstance(response, dict)
                    else [],
                }
            )
            continue
        entities = [
            {
                "service_name": item.get("service_name"),
                "odata_version": item.get("odata_version"),
                "entity_set": item.get("entity_set"),
                "key_fields": item.get("key_fields") or [],
                "supports_filter": item.get("supports_filter"),
                "supports_orderby": item.get("supports_orderby"),
                "supports_top": item.get("supports_top"),
                "runtime_available": item.get("runtime_available"),
                "executable": item.get("executable"),
            }
            for item in (data.get("entities") or [])
            if isinstance(item, dict)
        ]
        field_types_by_entity: dict[str, dict[str, Any]] = {}
        restrictions_by_entity: dict[str, dict[str, list[str]]] = {}
        for field in data.get("fields") or []:
            if not isinstance(field, dict):
                continue
            entity_set = str(field.get("entity_set") or "")
            field_name = str(field.get("field_name") or "")
            if not entity_set or not field_name:
                continue
            field_types_by_entity.setdefault(entity_set, {})[field_name] = field.get(
                "data_type"
            )
            restrictions = restrictions_by_entity.setdefault(
                entity_set,
                {"not_selectable": [], "not_filterable": [], "not_sortable": []},
            )
            if field.get("selectable") is False:
                restrictions["not_selectable"].append(field_name)
            if field.get("filterable") is False:
                restrictions["not_filterable"].append(field_name)
            if field.get("sortable") is False:
                restrictions["not_sortable"].append(field_name)
        snapshot.append(
            {
                "ok": response.get("ok") is True,
                "service_name": ((data.get("service") or {}).get("service_name")),
                "odata_version": ((data.get("service") or {}).get("odata_version")),
                "schema_authority": data.get("schema_authority"),
                "compatibility_status": data.get("compatibility_status"),
                "fields_truncated": data.get("fields_truncated"),
                "entities": entities,
                "field_types_by_entity": field_types_by_entity,
                "field_restrictions_by_entity": restrictions_by_entity,
                "validation_issues": response.get("validation_issues") or [],
            }
        )
    return snapshot


_RUNTIME_PLAN_CONTRACT = """
Top level required: service_name:string, odata_version:"2.0"|"4.0", entity_set:string. Optional:
http_method:"GET"; select_fields:string[]; response_summary_fields:string[];
filters:[{field:string, operator:eq|ne|gt|ge|lt|le|contains|in, value:string,
value_type:string}]; order_by:string[]; top:positive integer; plan_kind:direct|lookup|multi_step|
function|function_import; response_directive:string; rationale:string.
lookup/multi_step also require steps:[{step_id:string, entity_set:string,
service_name:string, odata_version:"2.0"|"4.0", http_method:"GET", select_fields:string[],
response_summary_fields:string[], filters:<same filter objects>,
filter_from_previous:[{field:string,source_step_id:string,source_field:string,fanout:boolean,
fetch_all_for_binding:boolean}], order_by:string[], top:positive integer|null, rationale:string}].
Every order_by item must be a bare field name; ascending order is implicit. Never append asc or desc.
output_contract, when present, is exactly {mode:"explicit"|"inferred", display_grain:string,
requested_fields:string[], display_fields:non-empty string[], support_fields:string[], reason:string}.
For mode="explicit", requested_fields must equal display_fields in the same order.
Use the plural property names filters, select_fields, and response_summary_fields; use step_id,
never id. Do not add completeness or presentation fields to this plan.
""".strip()


_SECRET_KEYS = {"password", "api_key", "apikey", "authorization", "token", "secret"}


def _decode_role_matching_output(result: Any) -> dict[str, Any]:
    raw = json.loads(result.final_response)
    analysis = json.loads(str(raw.get("analysis_json") or "{}"))
    if not isinstance(analysis, dict):
        raise ValueError("Role matching analysis_json must decode to an object.")
    analysis["summary"] = {
        "zh": str(raw.get("summary_zh") or ""),
        "en": str(raw.get("summary_en") or ""),
    }
    return analysis


def _role_matching_thread_can_restart(exc: Exception) -> bool:
    message = str(exc).lower()
    return " is archived" in message or "archived session" in message


def _exact_json(value: Any, *, limit: int, label: str) -> str:
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded) > limit:
        raise ValueError(f"{label} exceeds its bounded Runtime context.")
    return encoded


def _compact_role_match_records(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: item.get(key)
            for key in (
                "operation_id", "agent_id", "coverage", "confidence", "reason",
                "uncovered_capabilities",
            )
        }
        for item in values
        if isinstance(item, dict)
    ]


def _safe_json(value: Any, *, limit: int = 60_000) -> str:
    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                str(key): "[REDACTED]" if str(key).lower() in _SECRET_KEYS else clean(child)
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [clean(child) for child in item]
        return item

    encoded = json.dumps(clean(value), ensure_ascii=False)
    return encoded if len(encoded) <= limit else encoded[:limit] + "…[TRUNCATED]"
