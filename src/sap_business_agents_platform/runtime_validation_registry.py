"""Versioned case declarations. Additions never change the shared checker digest."""
from .runtime_validation import ValidationCase

def cases() -> dict[str, ValidationCase]:
    def c(id, op, component, fixture, **kw):
        kw['requires'] = ('infrastructure.current', *kw.get('requires', ()))
        return ValidationCase(id, (op,), components=(component,), fixture=fixture, **kw)
    rows = [
        ValidationCase("environment.current", (), layer="environment", resources=(),
            assertions=("installation", "authentication", "checked_model"), fixture="saved_checks"),
        ValidationCase("infrastructure.current", (), layer="infrastructure", requires=("environment.current",), resources=(),
            assertions=("startup", "binding", "cleanup_complete"), fixture="saved_checks"),
        c("agent.create", "author_draft", "author", "author"),
        c("agent.explain", "review_agent_feedback", "author", "agent_explain"),
        c("agent.revise", "review_agent_feedback", "author", "agent_modify", mode="trusted_local"),
        c("workflow.compose", "compose_workflow", "workflow", "workflows"),
        c("workflow.review", "review_workflow", "workflow", "workflows"),
        c("workflow.repair", "repair_workflow", "workflow", "workflows", requires=("workflow.review",)),
        c("workflow.feedback", "review_workflow_feedback", "workflow", "workflows", requires=("workflow.compose",)),
        c("workflow.authoring", "workflow_authoring.v2", "workflow_v2", "workflow_v2", mode="trusted_local"),
        c("query.feedback", "review_free_query_feedback", "query", "feedback"),
        c("query.presentation", "revise_free_query_presentation", "query", "feedback"),
        c("role.analysis", "analyze_role_matching", "role", "roles"),
        c("role.feedback", "review_role_matching_feedback", "role", "roles", requires=("role.analysis",)),
        c("query.limited", "free_query", "query", "limited_sample", sap=True,
            assertions=("structured_terminal", "business_contract", "saved_result", "cleanup_complete", "model_identity", "limited_is_incomplete", "read_only")),
        c("query.complete", "free_query", "query", "complete_unique_key", sap=True,
            assertions=("structured_terminal", "business_contract", "saved_result", "cleanup_complete", "model_identity", "live_unique_key", "read_only")),
        c("sample.scope", "sample_discovery", "sample", "sample", sap=True,
            assertions=("structured_terminal", "business_contract", "saved_result", "cleanup_complete", "model_identity", "all_filters_proven", "read_only")),
        ValidationCase("acceptance.campaign", ("acceptance_baseline", "acceptance_free_query"), layer="acceptance",
            requires=('infrastructure.current',),
            components=("acceptance",), sap=True, fixture="acceptance",
            assertions=("structured_terminal", "business_contract", "saved_result", "cleanup_complete", "model_identity",
                "campaign_pass", "free_match", "fixed_match", "source_anchors_pass", "independent_baseline", "fresh_evidence", "read_only")),
    ]
    return {row.id: row for row in rows}
