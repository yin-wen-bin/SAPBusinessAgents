"""One run-scoped evidence and report finalizer for all Runtime drivers."""
import time
from .models import RunPresentation

def finalize(settings, store, broker, run_id, payload, *, thread_id, turn_count, run_started):
    from .harness import (HarnessOutcome, _persistent_harness_counts, _budgeted_tool_call_count,
        _evidence_sources_complete, _effective_missing_evidence, _validated_presentation_snapshot)
    state = store.get_harness_state(run_id)
    calls, evidence = broker.snapshot(run_id)
    raw_calls = store.list_harness_tool_calls(run_id)
    verified_rule_results = [
        dict(call["output"]["rule_result"])
        for call in raw_calls
        if call.get("status") == "completed"
        and isinstance(call.get("output"), dict)
        and isinstance(call["output"].get("rule_result"), dict)
    ]
    web_search_count, discovered, activated = _persistent_harness_counts(
        store, run_id
    )
    status = str(payload.get("status") or "inconclusive")
    source_complete = (
        _evidence_sources_complete(evidence)
        if evidence
        else payload.get("source_complete") is True
    )
    business_complete = payload.get("business_complete") is True
    missing_evidence = _effective_missing_evidence(
        payload.get("missing_evidence"), raw_calls
    )
    known_evidence = {
        str(item.get("evidence_ref")) for item in evidence if item.get("evidence_ref")
    }
    evidence_refs = [
        str(item) for item in payload.get("evidence_refs") or [] if str(item) in known_evidence
    ]
    acceptance_projection = None
    if state.get("acceptance_spec"):
        # Only the exact, successfully validated report/projection pair is
        # accepted. A later model final answer cannot replace either one.
        for call in reversed(raw_calls):
            output = call.get("output") or {}
            if (call.get("tool_name") == "sap_final_report_validate"
                    and call.get("status") == "completed" and output.get("ok") is True
                    and output.get("_validated_acceptance_projection") is not None):
                acceptance_projection = output["_validated_acceptance_projection"]
                break
        if acceptance_projection is None:
            missing_evidence.append("acceptance_projection_not_validated")
    execute_evidence = {
        str(call.get("evidence_ref"))
        for call in calls
        if call.get("tool") == "sap_query_execute"
        and call.get("status") == "completed"
        and call.get("evidence_ref")
    }
    executed_plans = [
        item
        for item in payload.get("executed_plans") or []
        if isinstance(item, dict) and str(item.get("evidence_ref") or "") in execute_evidence
    ]
    presentation: RunPresentation | None = None
    presentation_error: str | None = None
    if payload.get("presentation") is not None:
        try:
            presentation = RunPresentation.model_validate(payload.get("presentation"))
        except Exception:
            presentation_error = "presentation_schema_invalid"
    final_report_validated = False
    if presentation is not None and presentation.validation_ref:
        validated_snapshot = _validated_presentation_snapshot(
            presentation.validation_ref, raw_calls
        )
        if validated_snapshot is not None:
            presentation = validated_snapshot
            final_report_validated = True
    if len(evidence_refs) != len(payload.get("evidence_refs") or []):
        missing_evidence.append("unknown_evidence_reference_rejected")
    if len(executed_plans) != len(payload.get("executed_plans") or []):
        missing_evidence.append("unexecuted_plan_claim_rejected")
    if presentation_error:
        missing_evidence.append(presentation_error)
    if status != "waiting_input" and not final_report_validated:
        missing_evidence.append("final_report_validation_missing")
        presentation = None
        safe_summary = {
            "zh": "最终业务结论未通过运行内证据引用校验，未展示未经验证的业务事实。",
            "en": "The final business conclusion did not pass run-scoped evidence-reference validation; unvalidated business facts were withheld.",
        }
    else:
        safe_summary = {
            "zh": str((payload.get("summary") or {}).get("zh") or "查询未得出结论。"),
            "en": str((payload.get("summary") or {}).get("en") or "The query was inconclusive."),
        }
    if status == "completed" and (not source_complete or missing_evidence):
        status = "inconclusive"
    missing_evidence = list(dict.fromkeys(missing_evidence))
    stop_reason = "waiting_input" if status == "waiting_input" else "completed"
    budgeted_call_count = _budgeted_tool_call_count(calls)
    limit_kind: str | None = None
    if (
        status == "inconclusive"
        and settings.max_tool_calls is not None
        and budgeted_call_count >= settings.max_tool_calls
    ):
        stop_reason = "limit_reached"
        limit_kind = "tool_calls"
    store.update_harness_state(
        run_id,
        {"thread_id": thread_id, "turn_count": turn_count, "active_turn_id": None},
    )
    budget = broker.budget_snapshot(run_id)
    return HarnessOutcome(
        thread_id=thread_id,
        turn_count=turn_count,
        status=status,
        stop_reason=stop_reason,
        summary=safe_summary,
        source_complete=source_complete,
        business_complete=business_complete,
        missing_evidence=missing_evidence,
        evidence_refs=evidence_refs,
        executed_plans=executed_plans,
        clarification_question=str(payload.get("clarification_question") or ""),
        input_kind=str(payload.get("input_kind") or "") or None,
        input_field=str(payload.get("input_field") or "") or None,
        tool_calls=calls,
        evidence=evidence,
        web_search_count=web_search_count,
        discovered_tool_count=discovered,
        activated_tool_count=activated,
        presentation=presentation,
        acceptance_projection=acceptance_projection,
        verified_rule_results=verified_rule_results,
        budgeted_tool_call_count=budgeted_call_count,
        elapsed_seconds=int(time.monotonic() - run_started),
        limit_kind=limit_kind,
        hard_limit_seconds=budget["hard_limit_seconds"],
        query_seconds_granted=budget["query_seconds_granted"],
        finalization_seconds_reserved=budget["finalization_seconds_reserved"],
        extension_count=budget["extension_count"],
        extension_reasons=budget["extension_reasons"],
        deadline_phase="completed",
    )
