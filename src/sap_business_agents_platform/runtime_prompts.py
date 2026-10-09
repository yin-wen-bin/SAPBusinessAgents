from __future__ import annotations
import asyncio
import copy
import json
from typing import Any
from .models import RunPresentation



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
        "action": {"type": "string", "enum": ["clarify", "reply", "revise_agent"]},
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
        "files_json": {"type": "string"},
        "edits_json": {"type": "string"},
        "clarification_json": {"type": "string"},
    },
    "required": ["action", "summary", "required_changes", "manifest_json", "readme", "rules_source", "files_json", "edits_json", "clarification_json"],
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

from .runtime_policy import ROLE_MATCHING_RUNTIME_TURN_SECONDS

def _consume_background_task(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except BaseException:
        pass

async def _await_with_hard_timeout(awaitable: Any, *, timeout: float) -> Any:
    """Stop waiting at the deadline even when an SDK coroutine ignores cancellation."""
    import time
    from .runtime_contract import deadline_scope, await_business, RuntimeContractError
    with deadline_scope(time.monotonic() + timeout):
        try:
            return await await_business(awaitable)
        except RuntimeContractError as exc:
            if exc.code != "runtime_deadline_exceeded":
                raise
            failure = TimeoutError("Runtime stage deadline exceeded.")
            failure.detail = exc.detail
            raise failure from exc

def _workflow_assistant_tool_catalog(requirement: str, integrations: dict[str, Any]) -> str:
    """Supply one bounded discovery projection, never a runnable capability."""
    import hashlib
    from datetime import datetime, timedelta, timezone
    from .assistant_tools import AssistantToolContext, catalog as assistant_catalog
    context = AssistantToolContext(
        "workflow_authoring", hashlib.sha256(requirement.encode()).hexdigest(),
        "workflow-proposal", None, datetime.now(timezone.utc) + timedelta(minutes=3),
    )
    bindings = integrations.get("bindings") if isinstance(integrations, dict) else []
    entries = assistant_catalog(context, mail_bindings=bindings if isinstance(bindings, list) else [], limit=50)
    selected = [{key: item.get(key) for key in ("tool_id", "purpose", "effect", "available", "unavailable_reason")}
                for item in entries if item["tool_id"].startswith(("mail.v1/", "tool_catalog_"))]
    return json.dumps(selected, ensure_ascii=False, separators=(",", ":"))

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
    integration_catalog: dict[str, Any] | None = None,
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

Connected integration catalog snapshot (the only external bindings you may select):
{_safe_json(integration_catalog or {"items": [], "bindings": []}, limit=80_000)}

Rules:
1. Decompose the complete requested outcome into ordered business capability stages.
2. Select an Agent only when one catalog entry is a high-confidence semantic match. Use its exact agent_id.
3. If multiple Agents would materially change the business meaning, set needs_clarification=true and ask exactly one concise question. Do not ask for identifiers that can remain reusable workflow inputs.
4. If no Agent covers a stage, keep agent_id empty and describe one missing Agent contract. Never hide or merge away an uncovered stage.
5. A binding may connect only an earlier selected stage output to a later input with the exact same port name. Otherwise omit the binding; the server will expose a workflow input.
6. Concrete identifiers and dates in the requirement belong only in validation_defaults. Never make them workflow constants.
7. Select no SAP tools and describe no SAP write operation. Source completeness and business completion remain separate concepts.
8. requested_outputs must contain business results and completeness results only. Do not request input-context echoes such as query_mode, dates, company codes, or identifiers unless a later stage actually consumes that exact output port.
9. External mail reads may use only exact ready mail.v1/search or mail.v1/read binding_id values. Target one selected stage input by target_stage_id and target_input_port.
10. Mail draft is a platform-local output action. Mail send may use only an exact ready mail.v1/send binding_id and always remains a human-approved draft; never treat it as automatic execution.
11. If a required integration is absent or not ready, add an integration_gaps entry using plugin_missing, connection_required, reauthentication_required, permission_required, runtime_adapter_unavailable, or tool_contract_changed. Do not turn it into an Agent gap.

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
  }}],
  "integration_inputs": [{{
    "id":"mail_search",
    "operation":"search|read",
    "binding_id":"exact catalog binding id",
    "target_stage_id":"selected stage id",
    "target_input_port":"declared Agent input port",
    "arguments":{{"native_argument":"literal or {{{{input.workflow_port}}}}"}},
    "result_pointer":"optional JSON Pointer into normalized MailMessageRef output"
  }}],
  "output_actions": [{{
    "id":"prepare_email",
    "operation":"draft|send",
    "binding_id":"exact send binding id, empty for draft",
    "draft_mapping":{{"to":"{{{{output.some_port}}}}","subject":"...","body_text":"..."}}
  }}],
  "integration_gaps": [{{
    "id":"mail_connection",
    "gap_type":"plugin_missing|connection_required|reauthentication_required|permission_required|runtime_adapter_unavailable|tool_contract_changed",
    "operation":"search|read|send",
    "runtime_provider_id":"target Runtime when known",
    "title":{{"zh":"...","en":"..."}},
    "description":{{"zh":"...","en":"..."}}
  }}]
}}

For a selected Agent, gap fields may be empty. For an uncovered stage, required_inputs, required_outputs, guardrails, and acceptance must be specific enough for an Agent author to implement and test it.
""".strip()

_SECRET_KEYS = {"password", "api_key", "apikey", "authorization", "token", "secret"}

def _decode_role_matching_output(result: Any, documents=None, operation="analyze_role_matching") -> dict[str, Any]:
    from .runtime_diagnostics import checked_output
    from .runtime_role_contract import decode
    raw = checked_output(result.final_response, ROLE_MATCHING_OUTPUT_SCHEMA, operation=operation)
    analysis = decode(raw["analysis_json"], documents or {}, operation)
    analysis["summary"] = {
        "zh": raw["summary_zh"],
        "en": raw["summary_en"],
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
