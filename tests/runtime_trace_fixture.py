"""Synthetic SDK responses and requests. Never reads SAP or starts a model."""
import copy
import hashlib
import json
from enum import Enum
from types import SimpleNamespace

from sap_business_agents_platform.models import PlannerDecision

PLAN = {"service_name": "API_FIXTURE_SRV", "odata_version": "2.0", "entity_set": "A_Fixture",
        "http_method": "GET", "filters": [{"field": "Document", "operator": "eq", "value": "001"}], "top": 1}
TEXT = {"zh": "离线", "en": "Offline"}
PRESENTATION = {"schema_version": "1.0", "title": TEXT, "blocks": [], "validation_ref": None}
PROPOSAL = {"title": TEXT, "description": TEXT, "stages": [{"id": "s", "agent_id": "fixture",
             "confidence": "high", "capability": TEXT, "reason": TEXT, "bindings": [], "requested_outputs": []}]}
CATALOG = {"runtime_catalog": {"digest": "catalog", "total_agent_count": 1, "page_count": 1,
    "pages": [{"catalog_digest": "catalog", "page_index": 1, "page_count": 1,
               "total_agent_count": 1, "items": [{"agent_id": "fixture"}]}]}}


def cases():
    role = dict(documents=[], agent_catalog=CATALOG, previous_result=None, user_context="",
                rematch_mode="full", locale="zh")
    return {
        "plan": dict(query="Offline", catalog={}, guidance={}, skills=[]),
        "ground_plan": dict(query="Offline", decision=PlannerDecision(intent="Offline", plan=PLAN, thread_id="thread"), schemas=[]),
        "summarize": dict(thread_id="thread", query="Offline", plan=PLAN, evidence=[], rule_results=[]),
        "author_draft": dict(thread_id="thread", query="Offline", plan=PLAN, evidence=[], completeness={}, correction="Offline"),
        "review_free_query_feedback": dict(thread_id="thread", original_query="Offline", previous_query="Offline", previous_plan=PLAN,
            previous_summary=TEXT, previous_presentation=None, deterministic_rule_results=[], available_evidence_refs=[], completeness={}, feedback="Explain"),
        "revise_free_query_presentation": dict(thread_id="thread", query="Offline", feedback="Explain", previous_presentation=PRESENTATION,
            allowed_evidence_refs=[], completeness={}, rule_results=[]),
        "compose_workflow": dict(requirement="Offline", catalog={}, locale="zh"),
        "review_workflow": dict(workflow={}, agent_contracts=[], validation_input={}, review_contract={}),
        "repair_workflow": dict(workflow={}, agent_contracts=[], error={}, thread_id="thread"),
        "review_workflow_feedback": dict(requirement="Offline", feedback="Offline", feedback_type_hint=None, locale="zh",
            workflow={}, previous_proposal={}, catalog={}, validation_report=None, thread_id=None),
        "analyze_role_matching": role,
        "review_role_matching_feedback": role,
    }


def normalize(value):
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def sha(value):
    return hashlib.sha256(json.dumps(normalize(value), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class TraceClient:
    def __init__(self, trace, operation, **kwargs):
        self.trace, self.operation = trace, operation
        # SDK-specific formatter instructions are not business request mutations.
        kwargs.pop("format_instructions", None)
        kwargs.pop("platform_context_restored", None)
        trace.append({"call": "client", "options": normalize(kwargs)})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.trace.append({"call": "close"})

    async def thread_start(self, **kwargs):
        return self._thread("start", kwargs)

    async def thread_resume(self, thread_id, **kwargs):
        return self._thread("resume", {"thread_id": thread_id, **kwargs})

    def _thread(self, kind, kwargs):
        options = normalize(kwargs)
        if "cwd" in options:
            options["cwd"] = "<repository>"
        self.trace.append({"call": kind, "options": options})
        return SimpleNamespace(id="thread", run=self.run)

    async def run(self, prompt, *, output_schema, effort=None, **kwargs):
        self.trace.append({"call": "run", "prompt_sha256": sha(prompt), "schema_sha256": sha(output_schema),
                           "effort": effort, "options": normalize(kwargs)})
        raw = response(self.operation, prompt)
        return SimpleNamespace(final_response=json.dumps(raw, ensure_ascii=False))


def response(operation, prompt):
    if operation in {"plan", "ground_plan"}:
        return dict(intent="Offline", needs_clarification=False, clarification_question="", plan_json=json.dumps(PLAN))
    if operation == "summarize":
        return TEXT
    if operation == "author_draft":
        return dict(content_zh="离线", content_en="Offline", rule_notes=[])
    if operation == "review_free_query_feedback":
        return dict(feedback_type="presentation", action="reinterpret", revised_intent="Offline", revised_query="Offline",
            required_changes=[], preserved_scope=[], candidate_expectations=[], clarification_question="", reason="Offline")
    if operation == "revise_free_query_presentation":
        return dict(summary=TEXT, presentation=PRESENTATION)
    if operation == "compose_workflow":
        return dict(needs_clarification=False, clarification_question="", proposal_json=json.dumps(PROPOSAL))
    if operation == "review_workflow":
        return dict(verdict="pass", issues=[], summary=TEXT)
    if operation == "repair_workflow":
        return dict(reason="Offline", connections_json="[]")
    if operation == "review_workflow_feedback":
        return dict(feedback_type="unclear", action="clarify", revised_requirement="Offline", required_changes=[],
            preserved_behavior=[], validation_input_patch_json="{}", candidate_expectations_json="[]",
            clarification_question="Clarify", reason="Offline", proposal_json="null")
    if prompt.startswith("Finalize"):
        return {"summary_zh": "离线", "summary_en": "Offline", "workflow_suggestions": [], "agent_gaps": []}
    canonical = {name: [] for name in ("roles", "processes", "operations", "agent_matches", "rejected_candidates", "workflow_suggestions", "agent_gaps", "document_issues")}
    canonical["catalog_evaluation"] = dict(catalog_digest="catalog", total_agent_count=1, evaluated_agent_count=1,
        evaluated_pair_count=0, catalog_page_count=1, matched_agent_ids=[], rejected_agent_ids=["fixture"], evaluated_agent_ids=["fixture"],
        agent_catalog_complete=True, failed_pages=[], business_understanding_complete=True, matching_complete=True, consolidation_complete=True)
    return dict(analysis_json=json.dumps(canonical), summary_zh="离线", summary_en="Offline")
