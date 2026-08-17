from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from .models import PlannerDecision


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
    "properties": {"zh": {"type": "string"}, "en": {"type": "string"}},
    "required": ["zh", "en"],
    "additionalProperties": False,
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

    async def review_workflow(
        self,
        *,
        workflow: dict[str, Any],
        agent_contracts: list[dict[str, Any]],
        validation_input: dict[str, Any],
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

Return a concise Chinese and English review. A bounded candidate does not prove source completeness.
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
            return {"zh": str(raw["zh"]), "en": str(raw["en"]), "thread_id": thread.id}

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
1. Use only service_name/entity_set/fields evidenced above.
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
1. Return the same harness shape and preserve the candidate service_name/entity_set pairs. Never
   add a service or entity that is absent from the authoritative schemas.
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
Top level required: service_name:string, entity_set:string. Optional:
http_method:"GET"; select_fields:string[]; response_summary_fields:string[];
filters:[{field:string, operator:eq|ne|gt|ge|lt|le|contains|in, value:string,
value_type:string}]; order_by:string[]; top:positive integer; plan_kind:direct|lookup|multi_step|
function_import; response_directive:string; rationale:string.
lookup/multi_step also require steps:[{step_id:string, entity_set:string,
service_name:string|null, http_method:"GET", select_fields:string[],
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
